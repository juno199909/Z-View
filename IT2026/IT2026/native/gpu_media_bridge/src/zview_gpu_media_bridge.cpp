#include "zview_gpu_media_bridge.h"

#include <chrono>
#include <memory>
#include <string>
#include <vector>

#include <d3d11.h>
#include <dxgi1_2.h>
#include <wrl/client.h>

#include "NvEncoder/NvEncoderD3D11.h"

using Microsoft::WRL::ComPtr;

struct zv_gpu_session {
    zv_gpu_session_config config{};
    ComPtr<IDXGIOutput1> output;
    ComPtr<IDXGIOutputDuplication> duplication;
    ComPtr<ID3D11Device> device;
    ComPtr<ID3D11DeviceContext> context;
    ComPtr<ID3D11VideoDevice> video_device;
    ComPtr<ID3D11VideoContext> video_context;
    ComPtr<ID3D11VideoProcessorEnumerator> processor_enumerator;
    ComPtr<ID3D11VideoProcessor> processor;
    std::unique_ptr<NvEncoderD3D11> encoder;
    uint64_t frame_index = 0;
};

namespace {

constexpr uint32_t kAbiVersion = ZV_GPU_MEDIA_BRIDGE_ABI_VERSION;

double elapsed_ms(std::chrono::steady_clock::time_point start) {
    return std::chrono::duration<double, std::milli>(std::chrono::steady_clock::now() - start).count();
}

void set_detail(zv_gpu_capabilities* capabilities, const char* detail) {
    if (!capabilities) {
        return;
    }
    strncpy_s(capabilities->detail, sizeof(capabilities->detail), detail ? detail : "", _TRUNCATE);
}

bool contains_idr(const std::vector<uint8_t>& data) {
    for (size_t index = 0; index + 4 < data.size(); ++index) {
        const bool three_byte_start = data[index] == 0 && data[index + 1] == 0 && data[index + 2] == 1;
        const bool four_byte_start = three_byte_start == false && index + 5 < data.size() &&
            data[index] == 0 && data[index + 1] == 0 && data[index + 2] == 0 && data[index + 3] == 1;
        if (three_byte_start && (data[index + 3] & 0x1f) == 5) {
            return true;
        }
        if (four_byte_start && (data[index + 4] & 0x1f) == 5) {
            return true;
        }
    }
    return false;
}

zv_gpu_status status_from_hresult(HRESULT hr) {
    if (hr == DXGI_ERROR_ACCESS_LOST || hr == DXGI_ERROR_SESSION_DISCONNECTED) {
        return ZV_GPU_CAPTURE_ACCESS_LOST;
    }
    if (hr == DXGI_ERROR_DEVICE_REMOVED || hr == DXGI_ERROR_DEVICE_RESET) {
        return ZV_GPU_DEVICE_LOST;
    }
    return ZV_GPU_INTERNAL_ERROR;
}

HRESULT select_output_and_device(
    const zv_gpu_session_config& config,
    ComPtr<IDXGIOutput1>* output,
    ComPtr<ID3D11Device>* device,
    ComPtr<ID3D11DeviceContext>* context) {
    ComPtr<IDXGIFactory1> factory;
    HRESULT hr = CreateDXGIFactory1(IID_PPV_ARGS(&factory));
    if (FAILED(hr)) {
        return hr;
    }
    uint32_t remaining = config.monitor_index;
    for (UINT adapter_index = 0;; ++adapter_index) {
        ComPtr<IDXGIAdapter1> adapter;
        hr = factory->EnumAdapters1(adapter_index, &adapter);
        if (hr == DXGI_ERROR_NOT_FOUND) {
            break;
        }
        if (FAILED(hr)) {
            return hr;
        }
        for (UINT output_index = 0;; ++output_index) {
            ComPtr<IDXGIOutput> candidate_output;
            hr = adapter->EnumOutputs(output_index, &candidate_output);
            if (hr == DXGI_ERROR_NOT_FOUND) {
                break;
            }
            if (FAILED(hr)) {
                return hr;
            }
            if (remaining-- != 0) {
                continue;
            }
            D3D_FEATURE_LEVEL feature_level{};
            hr = D3D11CreateDevice(
                adapter.Get(), D3D_DRIVER_TYPE_UNKNOWN, nullptr,
                D3D11_CREATE_DEVICE_BGRA_SUPPORT | D3D11_CREATE_DEVICE_VIDEO_SUPPORT,
                nullptr, 0, D3D11_SDK_VERSION, device->GetAddressOf(), &feature_level, context->GetAddressOf());
            if (FAILED(hr)) {
                return hr;
            }
            return candidate_output.As(output);
        }
    }
    return DXGI_ERROR_NOT_FOUND;
}

HRESULT create_video_processor(zv_gpu_session* session) {
    HRESULT hr = session->device.As(&session->video_device);
    if (FAILED(hr)) {
        return hr;
    }
    hr = session->context.As(&session->video_context);
    if (FAILED(hr)) {
        return hr;
    }
    D3D11_VIDEO_PROCESSOR_CONTENT_DESC content{};
    content.InputFrameFormat = D3D11_VIDEO_FRAME_FORMAT_PROGRESSIVE;
    content.InputWidth = session->config.width;
    content.InputHeight = session->config.height;
    content.OutputWidth = session->config.width;
    content.OutputHeight = session->config.height;
    content.Usage = D3D11_VIDEO_USAGE_PLAYBACK_NORMAL;
    hr = session->video_device->CreateVideoProcessorEnumerator(&content, &session->processor_enumerator);
    if (FAILED(hr)) {
        return hr;
    }
    return session->video_device->CreateVideoProcessor(session->processor_enumerator.Get(), 0, &session->processor);
}

HRESULT convert_bgra_to_nv12(zv_gpu_session* session, ID3D11Texture2D* source, ID3D11Texture2D* target) {
    D3D11_VIDEO_PROCESSOR_INPUT_VIEW_DESC input_desc{};
    input_desc.FourCC = 0;
    input_desc.ViewDimension = D3D11_VPIV_DIMENSION_TEXTURE2D;
    input_desc.Texture2D.MipSlice = 0;
    input_desc.Texture2D.ArraySlice = 0;
    ComPtr<ID3D11VideoProcessorInputView> input_view;
    HRESULT hr = session->video_device->CreateVideoProcessorInputView(
        source, session->processor_enumerator.Get(), &input_desc, &input_view);
    if (FAILED(hr)) {
        return hr;
    }
    D3D11_VIDEO_PROCESSOR_OUTPUT_VIEW_DESC output_desc{};
    output_desc.ViewDimension = D3D11_VPOV_DIMENSION_TEXTURE2D;
    output_desc.Texture2D.MipSlice = 0;
    ComPtr<ID3D11VideoProcessorOutputView> output_view;
    hr = session->video_device->CreateVideoProcessorOutputView(
        target, session->processor_enumerator.Get(), &output_desc, &output_view);
    if (FAILED(hr)) {
        return hr;
    }
    RECT rect{0, 0, static_cast<LONG>(session->config.width), static_cast<LONG>(session->config.height)};
    session->video_context->VideoProcessorSetStreamSourceRect(session->processor.Get(), 0, TRUE, &rect);
    session->video_context->VideoProcessorSetStreamDestRect(session->processor.Get(), 0, TRUE, &rect);
    session->video_context->VideoProcessorSetOutputTargetRect(session->processor.Get(), TRUE, &rect);
    session->video_context->VideoProcessorSetStreamFrameFormat(
        session->processor.Get(), 0, D3D11_VIDEO_FRAME_FORMAT_PROGRESSIVE);
    D3D11_VIDEO_PROCESSOR_STREAM stream{};
    stream.Enable = TRUE;
    stream.pInputSurface = input_view.Get();
    return session->video_context->VideoProcessorBlt(
        session->processor.Get(), output_view.Get(), 0, 1, &stream);
}

zv_gpu_status initialize_session(zv_gpu_session* session) {
    HRESULT hr = select_output_and_device(session->config, &session->output, &session->device, &session->context);
    if (FAILED(hr)) {
        return status_from_hresult(hr);
    }
    DXGI_OUTDUPL_DESC duplication_desc{};
    hr = session->output->DuplicateOutput(session->device.Get(), &session->duplication);
    if (FAILED(hr)) {
        return status_from_hresult(hr);
    }
    hr = create_video_processor(session);
    if (FAILED(hr)) {
        return status_from_hresult(hr);
    }
    try {
        session->encoder = std::make_unique<NvEncoderD3D11>(
            session->device.Get(), session->config.width, session->config.height, NV_ENC_BUFFER_FORMAT_NV12, 0);
        NV_ENC_INITIALIZE_PARAMS initialize_params{NV_ENC_INITIALIZE_PARAMS_VER};
        NV_ENC_CONFIG encode_config{NV_ENC_CONFIG_VER};
        initialize_params.encodeConfig = &encode_config;
        session->encoder->CreateDefaultEncoderParams(
            &initialize_params, NV_ENC_CODEC_H264_GUID, NV_ENC_PRESET_LOW_LATENCY_HP_GUID);
        initialize_params.frameRateNum = session->config.fps;
        initialize_params.frameRateDen = 1;
        initialize_params.enablePTD = 1;
        encode_config.gopLength = session->config.fps * 2;
        encode_config.frameIntervalP = 1;
        encode_config.rcParams.rateControlMode = NV_ENC_PARAMS_RC_CBR;
        encode_config.rcParams.averageBitRate = session->config.bitrate_bps;
        encode_config.rcParams.maxBitRate = session->config.bitrate_bps;
        encode_config.encodeCodecConfig.h264Config.idrPeriod = encode_config.gopLength;
        encode_config.encodeCodecConfig.h264Config.repeatSPSPPS = 1;
        session->encoder->CreateEncoder(&initialize_params);
    } catch (...) {
        return ZV_GPU_ENCODER_ERROR;
    }
    return ZV_GPU_OK;
}

}  // namespace

uint32_t zv_gpu_bridge_abi_version(void) {
    return kAbiVersion;
}

zv_gpu_status zv_gpu_bridge_probe(zv_gpu_capabilities* capabilities) {
    if (!capabilities) {
        return ZV_GPU_INVALID_ARGUMENT;
    }
    *capabilities = {};
    capabilities->abi_version = kAbiVersion;
    HMODULE nvenc = LoadLibraryW(L"nvEncodeAPI64.dll");
    if (!nvenc) {
        set_detail(capabilities, "nvEncodeAPI64.dll_not_found");
        return ZV_GPU_UNAVAILABLE;
    }
    FreeLibrary(nvenc);
    capabilities->available = 1;
    capabilities->supports_dxgi = 1;
    capabilities->supports_wgc = 0;  // WGC surface interop is the next native source unit.
    capabilities->supports_nvenc = 1;
    capabilities->max_width = 16384;
    capabilities->max_height = 16384;
    set_detail(capabilities, "dxgi_d3d11_nvenc_available");
    return ZV_GPU_OK;
}

zv_gpu_status zv_gpu_session_create(const zv_gpu_session_config* config, zv_gpu_session** out_session) {
    if (!config || !out_session || config->abi_version != kAbiVersion || config->width < 2 || config->height < 2 ||
        config->fps == 0 || config->bitrate_bps == 0 || config->capture_backend == ZV_GPU_CAPTURE_WGC) {
        return ZV_GPU_INVALID_ARGUMENT;
    }
    auto session = std::make_unique<zv_gpu_session>();
    session->config = *config;
    const zv_gpu_status status = initialize_session(session.get());
    if (status != ZV_GPU_OK) {
        return status;
    }
    *out_session = session.release();
    return ZV_GPU_OK;
}

zv_gpu_status zv_gpu_session_capture_encode(
    zv_gpu_session* session,
    uint32_t force_keyframe,
    zv_gpu_packet_callback on_packet,
    void* user_data,
    zv_gpu_frame_stats* out_stats) {
    if (!session || !on_packet || !out_stats) {
        return ZV_GPU_INVALID_ARGUMENT;
    }
    *out_stats = {};
    out_stats->abi_version = kAbiVersion;
    const auto capture_start = std::chrono::steady_clock::now();
    DXGI_OUTDUPL_FRAME_INFO frame_info{};
    ComPtr<IDXGIResource> resource;
    HRESULT hr = session->duplication->AcquireNextFrame(0, &frame_info, &resource);
    if (hr == DXGI_ERROR_WAIT_TIMEOUT) {
        return ZV_GPU_OK;
    }
    if (FAILED(hr)) {
        return status_from_hresult(hr);
    }
    bool frame_acquired = true;
    out_stats->capture_ms = elapsed_ms(capture_start);
    ComPtr<ID3D11Texture2D> captured_texture;
    hr = resource.As(&captured_texture);
    if (FAILED(hr)) {
        session->duplication->ReleaseFrame();
        frame_acquired = false;
        return status_from_hresult(hr);
    }
    const auto convert_start = std::chrono::steady_clock::now();
    try {
        const NvEncInputFrame* input = session->encoder->GetNextInputFrame();
        hr = convert_bgra_to_nv12(session, captured_texture.Get(), reinterpret_cast<ID3D11Texture2D*>(input->inputPtr));
        session->duplication->ReleaseFrame();
        frame_acquired = false;
        if (FAILED(hr)) {
            return status_from_hresult(hr);
        }
        out_stats->convert_ms = elapsed_ms(convert_start);
        const auto encode_start = std::chrono::steady_clock::now();
        std::vector<std::vector<uint8_t>> encoded_packets;
        NV_ENC_PIC_PARAMS picture_params{NV_ENC_PIC_PARAMS_VER};
        if (force_keyframe) {
            picture_params.encodePicFlags = NV_ENC_PIC_FLAG_FORCEIDR;
        }
        session->encoder->EncodeFrame(encoded_packets, force_keyframe ? &picture_params : nullptr);
        out_stats->encode_ms = elapsed_ms(encode_start);
        for (const auto& bytes : encoded_packets) {
            if (bytes.empty()) {
                continue;
            }
            const zv_gpu_packet packet{
                bytes.data(), static_cast<uint32_t>(bytes.size()), session->config.width, session->config.height,
                session->frame_index, contains_idr(bytes) ? 1u : 0u};
            on_packet(&packet, user_data);
            ++out_stats->packets;
            out_stats->keyframe |= packet.keyframe;
        }
        ++session->frame_index;
        return ZV_GPU_OK;
    } catch (...) {
        if (frame_acquired) {
            session->duplication->ReleaseFrame();
        }
        return ZV_GPU_ENCODER_ERROR;
    }
}

zv_gpu_status zv_gpu_session_reset(zv_gpu_session* session) {
    if (!session) {
        return ZV_GPU_INVALID_ARGUMENT;
    }
    session->encoder.reset();
    session->processor.Reset();
    session->processor_enumerator.Reset();
    session->video_context.Reset();
    session->video_device.Reset();
    session->duplication.Reset();
    session->output.Reset();
    session->context.Reset();
    session->device.Reset();
    return initialize_session(session);
}

void zv_gpu_session_destroy(zv_gpu_session* session) {
    if (!session) {
        return;
    }
    session->encoder.reset();
    delete session;
}
