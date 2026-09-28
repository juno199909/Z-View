from pathlib import Path
from types import SimpleNamespace

from zvplatform.routers import software_management
from webtransport_gateway import (
    AgentBridge,
    MAX_PENDING_BYTES,
    MAX_PENDING_FRAMES,
    MAX_PRE_STREAM_CONTROL_BYTES,
    MAX_PRE_STREAM_CONTROL_FRAMES,
)


def _bridge():
    bridge = AgentBridge.__new__(AgentBridge)
    bridge._pending_frames = []
    bridge._pre_stream_frames = []
    return bridge


def test_webtransport_pending_frames_have_count_and_byte_limits():
    bridge = _bridge()
    payload = b"x" * (MAX_PENDING_BYTES // MAX_PENDING_FRAMES)

    assert all(bridge._buffer_pending_frame(0, payload) for _ in range(MAX_PENDING_FRAMES))
    assert not bridge._buffer_pending_frame(0, b"x")


def test_webtransport_pre_stream_control_frames_have_count_and_byte_limits():
    bridge = _bridge()
    payload = b"x" * (MAX_PRE_STREAM_CONTROL_BYTES // MAX_PRE_STREAM_CONTROL_FRAMES)

    assert all(bridge._buffer_agent_frame(0, payload) for _ in range(MAX_PRE_STREAM_CONTROL_FRAMES))
    assert not bridge._buffer_agent_frame(0, b"x")


def test_legacy_package_index_requires_agent_auth(monkeypatch):
    calls = []
    request = SimpleNamespace(base_url="https://platform.example/")

    monkeypatch.setattr(software_management, "require_agent_request", lambda value: calls.append(value))
    monkeypatch.setattr(software_management, "get_packages", lambda **_: {
        "data": [{"id": 7, "display_name": "Example", "package_name": "example"}],
        "total": 1,
    })

    result = software_management.get_packages_legacy(request)

    assert calls == [request]
    assert result["data"][0]["download_url"].endswith("/api/v1/software/agent/packages/7/download")


def test_remote_message_limits_are_present_on_both_transport_paths():
    root = Path(__file__).resolve().parent.parent
    ws_proxy = (root / "zvplatform" / "routers" / "remote_desktop_ws.py").read_text(encoding="utf-8")
    wt_gateway = (root / "webtransport_gateway.py").read_text(encoding="utf-8")

    assert "max_size=MAX_REMOTE_MEDIA_MESSAGE_BYTES" in ws_proxy
    assert "max_size=MAX_REMOTE_MEDIA_MESSAGE_BYTES" in wt_gateway
