"""Optional host for the signed D3D11/NVENC GPU media bridge DLL.

The DLL owns all GPU objects.  This module deliberately exposes only encoded
Annex-B packets, so a D3D11 texture cannot leak into PIL, NumPy, or PyAV.
"""

from __future__ import annotations

import ctypes
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable


ABI_VERSION = 1
_DLL_NAME = "zview_gpu_media_bridge.dll"
_STATUS_OK = 0


class NativeGpuMediaBridgeError(RuntimeError):
    """The optional bridge is unavailable or rejected a frame."""


class _Capabilities(ctypes.Structure):
    _fields_ = [
        ("abi_version", ctypes.c_uint32),
        ("available", ctypes.c_uint32),
        ("supports_dxgi", ctypes.c_uint32),
        ("supports_wgc", ctypes.c_uint32),
        ("supports_nvenc", ctypes.c_uint32),
        ("max_width", ctypes.c_uint32),
        ("max_height", ctypes.c_uint32),
        ("detail", ctypes.c_char * 256),
    ]


class _SessionConfig(ctypes.Structure):
    _fields_ = [
        ("abi_version", ctypes.c_uint32),
        ("width", ctypes.c_uint32),
        ("height", ctypes.c_uint32),
        ("fps", ctypes.c_uint32),
        ("bitrate_bps", ctypes.c_uint32),
        ("capture_backend", ctypes.c_uint32),
        ("adapter_luid_low", ctypes.c_uint32),
        ("adapter_luid_high", ctypes.c_int32),
        ("monitor_index", ctypes.c_uint32),
    ]


class _Packet(ctypes.Structure):
    _fields_ = [
        ("data", ctypes.POINTER(ctypes.c_uint8)),
        ("size", ctypes.c_uint32),
        ("width", ctypes.c_uint32),
        ("height", ctypes.c_uint32),
        ("pts_100ns", ctypes.c_uint64),
        ("keyframe", ctypes.c_uint32),
    ]


class _FrameStats(ctypes.Structure):
    _fields_ = [
        ("abi_version", ctypes.c_uint32),
        ("capture_ms", ctypes.c_double),
        ("convert_ms", ctypes.c_double),
        ("encode_ms", ctypes.c_double),
        ("packets", ctypes.c_uint32),
        ("keyframe", ctypes.c_uint32),
    ]


_PacketCallback = ctypes.CFUNCTYPE(None, ctypes.POINTER(_Packet), ctypes.c_void_p)

_BACKEND_IDS = {"auto": 0, "dxgi": 1, "wgc": 2}


@dataclass(frozen=True)
class NativeGpuBridgeCapabilities:
    available: bool
    supports_dxgi: bool
    supports_wgc: bool
    supports_nvenc: bool
    max_width: int
    max_height: int
    detail: str


@dataclass(frozen=True)
class NativeGpuFrame:
    packets: list[dict]
    width: int
    height: int
    capture_ms: float
    convert_ms: float
    encode_ms: float


def _decode_detail(value: bytes) -> str:
    return bytes(value).split(b"\0", 1)[0].decode("utf-8", errors="replace")


def bridge_library_candidates() -> tuple[Path, ...]:
    """Return only trusted package locations, plus an explicit absolute override."""
    candidates: list[Path] = []
    override = os.environ.get("ZVIEW_GPU_MEDIA_BRIDGE_PATH", "").strip()
    if override:
        candidate = Path(override)
        if candidate.is_absolute():
            candidates.append(candidate)

    module_root = Path(__file__).resolve().parents[1]
    candidates.extend(
        (
            module_root / "native" / "gpu_media_bridge" / "bin" / _DLL_NAME,
            module_root / _DLL_NAME,
            Path(sys.executable).resolve().parent / _DLL_NAME,
        )
    )
    unique: list[Path] = []
    for candidate in candidates:
        if candidate not in unique:
            unique.append(candidate)
    return tuple(unique)


class NativeGpuMediaBridge:
    """A single native capture-and-encode session bound to the bridge ABI."""

    def __init__(self, library, library_path: Path, session) -> None:
        self._library = library
        self.library_path = library_path
        self._session = session
        self._closed = False

    @classmethod
    def open(
        cls,
        *,
        width: int,
        height: int,
        fps: int,
        bitrate_bps: int,
        capture_backend: str,
        monitor_index: int = 0,
        library_loader: Callable[[str], object] | None = None,
    ) -> "NativeGpuMediaBridge":
        backend = str(capture_backend or "auto").strip().lower()
        backend_id = _BACKEND_IDS.get(backend)
        if backend_id is None:
            raise NativeGpuMediaBridgeError(f"unsupported_capture_backend:{backend}")
        library_path = next((path for path in bridge_library_candidates() if path.is_file()), None)
        if library_path is None:
            raise NativeGpuMediaBridgeError("bridge_dll_not_found")
        loader = library_loader or getattr(ctypes, "WinDLL", None)
        if loader is None:
            raise NativeGpuMediaBridgeError("bridge_requires_windows")
        try:
            library = loader(str(library_path))
        except OSError as exc:
            raise NativeGpuMediaBridgeError(f"bridge_load_failed:{exc}") from exc
        cls._bind_abi(library)
        abi_version = int(library.zv_gpu_bridge_abi_version())
        if abi_version != ABI_VERSION:
            raise NativeGpuMediaBridgeError(f"bridge_abi_mismatch:{abi_version}")

        capabilities = _Capabilities()
        status = int(library.zv_gpu_bridge_probe(ctypes.byref(capabilities)))
        if status != _STATUS_OK or not capabilities.available or not capabilities.supports_nvenc:
            detail = _decode_detail(capabilities.detail)
            raise NativeGpuMediaBridgeError(f"bridge_probe_failed:{status}:{detail}")
        if backend == "dxgi" and not capabilities.supports_dxgi:
            raise NativeGpuMediaBridgeError("bridge_dxgi_unavailable")
        if backend == "wgc" and not capabilities.supports_wgc:
            raise NativeGpuMediaBridgeError("bridge_wgc_unavailable")

        config = _SessionConfig(
            ABI_VERSION,
            max(2, int(width)),
            max(2, int(height)),
            max(1, int(fps)),
            max(1, int(bitrate_bps)),
            backend_id,
            0,
            0,
            max(0, int(monitor_index)),
        )
        session = ctypes.c_void_p()
        status = int(library.zv_gpu_session_create(ctypes.byref(config), ctypes.byref(session)))
        if status != _STATUS_OK or not session.value:
            raise NativeGpuMediaBridgeError(f"bridge_session_create_failed:{status}")
        return cls(library, library_path, session)

    @staticmethod
    def _bind_abi(library) -> None:
        library.zv_gpu_bridge_abi_version.argtypes = []
        library.zv_gpu_bridge_abi_version.restype = ctypes.c_uint32
        library.zv_gpu_bridge_probe.argtypes = [ctypes.POINTER(_Capabilities)]
        library.zv_gpu_bridge_probe.restype = ctypes.c_uint32
        library.zv_gpu_session_create.argtypes = [ctypes.POINTER(_SessionConfig), ctypes.POINTER(ctypes.c_void_p)]
        library.zv_gpu_session_create.restype = ctypes.c_uint32
        library.zv_gpu_session_capture_encode.argtypes = [
            ctypes.c_void_p,
            ctypes.c_uint32,
            _PacketCallback,
            ctypes.c_void_p,
            ctypes.POINTER(_FrameStats),
        ]
        library.zv_gpu_session_capture_encode.restype = ctypes.c_uint32
        library.zv_gpu_session_reset.argtypes = [ctypes.c_void_p]
        library.zv_gpu_session_reset.restype = ctypes.c_uint32
        library.zv_gpu_session_destroy.argtypes = [ctypes.c_void_p]
        library.zv_gpu_session_destroy.restype = None

    def capture_encode(self, *, force_keyframe: bool = False) -> NativeGpuFrame:
        if self._closed or not self._session:
            raise NativeGpuMediaBridgeError("bridge_session_closed")
        packets: list[dict] = []
        width = 0
        height = 0

        @_PacketCallback
        def on_packet(packet_ptr, _user_data) -> None:
            nonlocal width, height
            if not packet_ptr:
                return
            packet = packet_ptr.contents
            if not packet.data or not packet.size:
                return
            width = int(packet.width) or width
            height = int(packet.height) or height
            packets.append({
                "data": ctypes.string_at(packet.data, int(packet.size)),
                "keyframe": bool(packet.keyframe),
            })

        stats = _FrameStats(ABI_VERSION, 0.0, 0.0, 0.0, 0, 0)
        status = int(
            self._library.zv_gpu_session_capture_encode(
                self._session,
                1 if force_keyframe else 0,
                on_packet,
                None,
                ctypes.byref(stats),
            )
        )
        if status != _STATUS_OK:
            raise NativeGpuMediaBridgeError(f"bridge_capture_encode_failed:{status}")
        if not packets:
            return NativeGpuFrame([], width, height, float(stats.capture_ms), float(stats.convert_ms), float(stats.encode_ms))
        if not width or not height:
            raise NativeGpuMediaBridgeError("bridge_packet_missing_dimensions")
        return NativeGpuFrame(
            packets,
            width,
            height,
            float(stats.capture_ms),
            float(stats.convert_ms),
            float(stats.encode_ms),
        )

    def reset(self) -> None:
        if self._closed or not self._session:
            raise NativeGpuMediaBridgeError("bridge_session_closed")
        status = int(self._library.zv_gpu_session_reset(self._session))
        if status != _STATUS_OK:
            raise NativeGpuMediaBridgeError(f"bridge_reset_failed:{status}")

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        session, self._session = self._session, None
        if session:
            try:
                self._library.zv_gpu_session_destroy(session)
            except Exception:
                pass
