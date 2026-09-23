/*
 * Z-View GPU media bridge ABI.
 *
 * The bridge owns the D3D11 device, capture surface, NV12 conversion, and
 * NVENC session.  Python must only receive Annex-B H.264 packets through the
 * packet callback; it must never receive a D3D11 texture or map it to CPU.
 */
#ifndef ZVIEW_GPU_MEDIA_BRIDGE_H
#define ZVIEW_GPU_MEDIA_BRIDGE_H

#include <stdint.h>

#if defined(_WIN32)
#define ZV_GPU_API __declspec(dllexport)
#else
#define ZV_GPU_API
#endif

#ifdef __cplusplus
extern "C" {
#endif

#define ZV_GPU_MEDIA_BRIDGE_ABI_VERSION 1u

typedef struct zv_gpu_session zv_gpu_session;

typedef enum zv_gpu_capture_backend {
    ZV_GPU_CAPTURE_AUTO = 0,
    ZV_GPU_CAPTURE_DXGI = 1,
    ZV_GPU_CAPTURE_WGC = 2
} zv_gpu_capture_backend;

typedef enum zv_gpu_status {
    ZV_GPU_OK = 0,
    ZV_GPU_UNAVAILABLE = 1,
    ZV_GPU_INVALID_ARGUMENT = 2,
    ZV_GPU_DEVICE_LOST = 3,
    ZV_GPU_CAPTURE_ACCESS_LOST = 4,
    ZV_GPU_ENCODER_ERROR = 5,
    ZV_GPU_INTERNAL_ERROR = 6
} zv_gpu_status;

typedef struct zv_gpu_capabilities {
    uint32_t abi_version;
    uint32_t available;
    uint32_t supports_dxgi;
    uint32_t supports_wgc;
    uint32_t supports_nvenc;
    uint32_t max_width;
    uint32_t max_height;
    char detail[256];
} zv_gpu_capabilities;

typedef struct zv_gpu_session_config {
    uint32_t abi_version;
    uint32_t width;
    uint32_t height;
    uint32_t fps;
    uint32_t bitrate_bps;
    zv_gpu_capture_backend capture_backend;
    uint32_t adapter_luid_low;
    int32_t adapter_luid_high;
    uint32_t monitor_index;
} zv_gpu_session_config;

typedef struct zv_gpu_packet {
    const uint8_t *data;
    uint32_t size;
    uint32_t width;
    uint32_t height;
    uint64_t pts_100ns;
    uint32_t keyframe;
} zv_gpu_packet;

typedef void (*zv_gpu_packet_callback)(const zv_gpu_packet *packet, void *user_data);

typedef struct zv_gpu_frame_stats {
    uint32_t abi_version;
    double capture_ms;
    double convert_ms;
    double encode_ms;
    uint32_t packets;
    uint32_t keyframe;
} zv_gpu_frame_stats;

/* The returned ABI version must equal ZV_GPU_MEDIA_BRIDGE_ABI_VERSION. */
ZV_GPU_API uint32_t zv_gpu_bridge_abi_version(void);

/* Probe D3D11, the selected capture API, and NVENC without creating a stream. */
ZV_GPU_API zv_gpu_status zv_gpu_bridge_probe(zv_gpu_capabilities *out_capabilities);

/* Create a session.  The bridge owns all GPU objects until destroy is called. */
ZV_GPU_API zv_gpu_status zv_gpu_session_create(
    const zv_gpu_session_config *config,
    zv_gpu_session **out_session);

/* Capture exactly one current desktop frame and synchronously emit zero or more Annex-B packets. */
ZV_GPU_API zv_gpu_status zv_gpu_session_capture_encode(
    zv_gpu_session *session,
    uint32_t force_keyframe,
    zv_gpu_packet_callback on_packet,
    void *user_data,
    zv_gpu_frame_stats *out_stats);

/* Recreate duplication/WGC and NVENC resources after access loss or device loss. */
ZV_GPU_API zv_gpu_status zv_gpu_session_reset(zv_gpu_session *session);

ZV_GPU_API void zv_gpu_session_destroy(zv_gpu_session *session);

#ifdef __cplusplus
}
#endif

#endif
