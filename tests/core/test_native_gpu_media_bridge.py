import ctypes
from pathlib import Path

import pytest

from Codec.native_gpu_media_bridge import (
    ABI_VERSION,
    NativeGpuMediaBridge,
    NativeGpuMediaBridgeError,
    bridge_library_candidates,
)
from Codec import native_gpu_media_bridge as bridge_module


class _FakeFunction:
    def __init__(self, callback):
        self.callback = callback
        self.argtypes = None
        self.restype = None

    def __call__(self, *args):
        return self.callback(*args)


class _FakeBridgeLibrary:
    def __init__(self):
        self.destroyed = []
        self.force_keyframe = None
        self.zv_gpu_bridge_abi_version = _FakeFunction(lambda: ABI_VERSION)
        self.zv_gpu_bridge_probe = _FakeFunction(self._probe)
        self.zv_gpu_session_create = _FakeFunction(self._create)
        self.zv_gpu_session_capture_encode = _FakeFunction(self._capture_encode)
        self.zv_gpu_session_reset = _FakeFunction(lambda _session: 0)
        self.zv_gpu_session_destroy = _FakeFunction(lambda session: self.destroyed.append(session.value))

    @staticmethod
    def _probe(capabilities):
        value = ctypes.cast(capabilities, ctypes.POINTER(bridge_module._Capabilities)).contents
        value.abi_version = ABI_VERSION
        value.available = 1
        value.supports_dxgi = 1
        value.supports_wgc = 1
        value.supports_nvenc = 1
        value.max_width = 3840
        value.max_height = 2160
        value.detail = b"fake"
        return 0

    @staticmethod
    def _create(config, out_session):
        value = ctypes.cast(config, ctypes.POINTER(bridge_module._SessionConfig)).contents
        assert value.width == 1920 and value.height == 1080 and value.fps == 60
        ctypes.cast(out_session, ctypes.POINTER(ctypes.c_void_p)).contents.value = 7
        return 0

    def _capture_encode(self, _session, force_keyframe, callback, _user_data, stats):
        self.force_keyframe = force_keyframe
        payload = (ctypes.c_uint8 * 5)(0, 0, 0, 1, 0x65)
        packet = bridge_module._Packet(payload, 5, 1920, 1080, 1000, 1)
        callback(ctypes.byref(packet), None)
        value = ctypes.cast(stats, ctypes.POINTER(bridge_module._FrameStats)).contents
        value.capture_ms = 1.5
        value.convert_ms = 0.7
        value.encode_ms = 0.8
        value.packets = 1
        value.keyframe = 1
        return 0


def test_bridge_candidates_are_absolute_and_do_not_use_cwd():
    candidates = bridge_library_candidates()

    assert candidates
    assert all(candidate.is_absolute() for candidate in candidates)
    assert all(candidate.name == "zview_gpu_media_bridge.dll" for candidate in candidates)


def test_bridge_rejects_unsupported_capture_backend_before_loading():
    with pytest.raises(NativeGpuMediaBridgeError, match="unsupported_capture_backend"):
        NativeGpuMediaBridge.open(
            width=1920,
            height=1080,
            fps=60,
            bitrate_bps=8_000_000,
            capture_backend="mss",
        )


def test_bridge_header_uses_same_abi_version():
    header = (
        Path(__file__).resolve().parents[2]
        / "native"
        / "gpu_media_bridge"
        / "include"
        / "zview_gpu_media_bridge.h"
    ).read_text(encoding="utf-8")

    assert f"ZV_GPU_MEDIA_BRIDGE_ABI_VERSION {ABI_VERSION}u" in header


def test_bridge_copies_annex_b_packet_from_callback(monkeypatch, tmp_path):
    dll_path = tmp_path / "zview_gpu_media_bridge.dll"
    dll_path.touch()
    monkeypatch.setenv("ZVIEW_GPU_MEDIA_BRIDGE_PATH", str(dll_path))
    fake_library = _FakeBridgeLibrary()
    bridge = NativeGpuMediaBridge.open(
        width=1920,
        height=1080,
        fps=60,
        bitrate_bps=8_000_000,
        capture_backend="wgc",
        library_loader=lambda _path: fake_library,
    )

    frame = bridge.capture_encode(force_keyframe=True)
    bridge.close()

    assert fake_library.force_keyframe == 1
    assert frame.packets == [{"data": b"\x00\x00\x00\x01\x65", "keyframe": True}]
    assert (frame.width, frame.height) == (1920, 1080)
    assert (frame.capture_ms, frame.convert_ms, frame.encode_ms) == (1.5, 0.7, 0.8)
    assert fake_library.destroyed == [7]
