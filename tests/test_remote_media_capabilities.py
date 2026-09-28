from Codec.h264_encoder import get_h264_capabilities
from remote_desktop_api import resolve_remote_fps_limit, resolve_remote_media_profile


def test_remote_media_capability_report_has_safe_fps_limit():
    capabilities = get_h264_capabilities()

    assert 4 <= capabilities["recommended_max_fps"] <= 60
    assert capabilities["hardware_encoder"] is False or capabilities["h264_available"] is True
    assert capabilities["recommended_max_width"] >= 1024
    assert capabilities["recommended_max_height"] >= 720
    assert capabilities["recommended_max_bitrate_bps"] >= 2_000_000
    assert capabilities["max_concurrent_sessions"] in (1, 2)


def test_unreported_agent_is_capped_to_stable_legacy_profile():
    fps, tier = resolve_remote_fps_limit(60, None)

    assert (fps, tier) == (30, "legacy_or_unreported")


def test_software_encoder_cannot_request_60_fps():
    fps, tier = resolve_remote_fps_limit(60, {
        "h264_available": True,
        "hardware_encoder": False,
        "recommended_max_fps": 60,
    })

    assert (fps, tier) == (30, "software_encoder")


def test_hardware_encoder_keeps_high_fps_profile():
    fps, tier = resolve_remote_fps_limit(60, {
        "h264_available": True,
        "hardware_encoder": True,
        "recommended_max_fps": 60,
    })

    assert (fps, tier) == (60, "hardware_encoder")


def test_media_profile_exposes_resolution_bitrate_and_session_limits():
    profile = resolve_remote_media_profile(60, {
        "hardware_encoder": True,
        "recommended_max_fps": 45,
        "recommended_max_width": 1600,
        "recommended_max_height": 900,
        "recommended_max_bitrate_bps": 8_000_000,
        "max_concurrent_sessions": 1,
        "performance_tier": "hardware_8g",
    })

    assert profile == {
        "tier": "hardware_8g",
        "effective_fps": 45,
        "max_width": 1600,
        "max_height": 900,
        "max_bitrate_bps": 8_000_000,
        "max_concurrent_sessions": 1,
    }
