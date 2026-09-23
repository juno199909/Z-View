from remote_desktop_engine_v2 import RemoteDesktopSession


def test_native_bridge_profile_uses_virtual_screen_dimensions():
    session = object.__new__(RemoteDesktopSession)
    session.capture_backend_preference = "dxgi"
    session._h264_scale_override = 1.0
    session._h264_scale = 1.0
    session._h264_crf_override = 17
    session._h264_qos_level = 0
    session.fps = 60
    session.screen_info = {
        "virtual_width": 1920,
        "virtual_height": 1080,
        "primary_width": 1920,
        "primary_height": 1080,
    }

    profile = session._native_gpu_bridge_profile()

    assert profile is not None
    width, height, fps, bitrate_bps, backend = profile
    assert (width, height, fps, backend) == (1920, 1080, 60, "dxgi")
    assert bitrate_bps > 0
