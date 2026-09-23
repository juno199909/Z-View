# GPU media bridge

This component is the required native boundary for the 60 FPS path.  It is not
implemented by PyAV or dxcam: both currently return CPU-resident frames before
H.264 encoding.

## Required pipeline

1. Capture one `ID3D11Texture2D` through Desktop Duplication (DXGI) or a
   Windows Graphics Capture `IDirect3DSurface` (WGC).
2. Keep the capture texture on the same D3D11 adapter selected for NVENC.
3. Convert RGB/BGRA to an NV12 `ID3D11Texture2D` with a D3D11 video processor
   or compute shader.  Do not call `Map`, `GetData`, `Image.fromarray`, or
   `numpy.asarray` in this path.
4. Register the NV12 texture with NVENC as a D3D11 input resource and encode
   with low-latency settings: no B frames, async depth one, IDR on request.
5. Copy the resulting Annex-B bytes only after NVENC completes, and synchronously
   publish them through the callback declared in
   `include/zview_gpu_media_bridge.h`.

The Python session remains responsible for frame ordering, reliable keyframe
delivery, QUIC datagrams, browser decoding, authorization, and the existing
MSS/PyAV fallback.  The native bridge must not open a network connection or
accept control input.

## Current implementation

The checked-in CMake target implements the DXGI half of this design now:
Desktop Duplication obtains the current `ID3D11Texture2D`, the D3D11 video
processor writes NV12 directly into the texture registered with NVENC, and the
bridge publishes Annex-B through the ABI callback. It has no CPU `Map` or image
library dependency. WGC surface interop is intentionally still reported as
unsupported by the DLL until its `IDirect3DSurface` conversion source unit is
implemented and run on a physical NVIDIA test machine.

Build after installing Visual Studio Build Tools, the Windows SDK, and cloning
NVIDIA's `video-sdk-samples` repository:

```powershell
cmake -S native/gpu_media_bridge -B D:/zview-gpu-build -G "Visual Studio 17 2022" -A x64 `
  -DZVIEW_NVENC_SDK_DIR=D:/path/to/video-sdk-samples
cmake --build D:/zview-gpu-build --config Release
```

The generated DLL is placed in `native/gpu_media_bridge/bin/` and must be
Authenticode-signed before an Agent package includes it.

## ABI and lifecycle

`zview_gpu_media_bridge.h` is the versioned C ABI.  The host must call
`zv_gpu_bridge_probe` before starting a session and use the bridge only when
all of the following are true:

- ABI version equals `ZV_GPU_MEDIA_BRIDGE_ABI_VERSION`.
- `available`, `supports_nvenc`, and the requested capture backend are true.
- The selected desktop adapter and NVENC adapter are the same LUID.

`zv_gpu_session_capture_encode` is synchronous.  Packet memory remains valid
only for the duration of the callback.  A `DEVICE_LOST` or
`CAPTURE_ACCESS_LOST` result must trigger one reset attempt, request an IDR,
and then fall back to the Python WGC/DXGI/MSS route if it persists.

## Build prerequisites

Build this DLL on a Windows GPU build machine, not in the Python packaging
environment.  It requires:

- Visual Studio Build Tools with the Desktop C++ workload and a Windows 10/11 SDK.
- NVIDIA Video Codec SDK headers (`nvEncodeAPI.h`) compatible with the target
  driver.  `nvEncodeAPI64.dll` is provided by the NVIDIA display driver at run
  time and must be loaded dynamically.
- D3D11, DXGI, Windows Graphics Capture, and D3DCompiler libraries from the
  Windows SDK.

The DLL and every staged dependency must be Authenticode-signed before it is
included in an Agent release.  The host must never enable this route merely
because a DLL file exists; probe success is the activation condition.

## Acceptance gate

On Juno at native resolution, WGC and DXGI each need a 60-second QUIC run at
60 FPS with: received FPS at least 55, no incomplete frames, no gateway media
drops, p95 control RTT no worse than the PyAV baseline, and native bridge
conversion time below 3 ms.  The resulting `pipeline_stats` must identify the
bridge and report capture, GPU conversion, and NVENC timings separately.
