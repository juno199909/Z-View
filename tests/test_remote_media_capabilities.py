from Codec.h264_encoder import get_h264_capabilities
from remote_desktop_api import resolve_remote_fps_limit


def test_remote_media_capability_report_has_safe_fps_limit():
    capabilities = get_h264_capabilities()

    assert 4 <= capabilities["recommended_max_fps"] <= 60
    assert capabilities["hardware_encoder"] is False or capabilities["h264_available"] is True


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
