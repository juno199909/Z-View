from types import SimpleNamespace
import sys

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

sys.path.insert(0, r"D:\IT2026\IT2026\IT2026\IT2026")

from remote_desktop_api import CreateSessionRequest, _require_remote_desktop_access, _require_session_scope
from zvagent.config import CONFIG
from zvagent.remote import RemoteDesktopServer


def _request_with_user(user):
    return SimpleNamespace(state=SimpleNamespace(auth_user=user))


def test_remote_desktop_access_requires_explicit_control_permission():
    with pytest.raises(HTTPException, match="remote_desktop:control"):
        _require_remote_desktop_access(_request_with_user({"role": "viewer"}))

    user = _require_remote_desktop_access(_request_with_user({"role": "operator"}))
    assert user["role"] == "operator"


def test_remote_desktop_scope_rejects_unassigned_asset_group():
    with pytest.raises(HTTPException, match="资产分组范围"):
        _require_session_scope({"role": "operator", "scoped_group_ids": [10]}, 20)

    _require_session_scope({"role": "operator", "scoped_group_ids": [10]}, 10)


@pytest.mark.parametrize(
    "payload",
    [
        {"asset_id": 1, "fps_limit": 61},
        {"asset_id": 1, "fps_limit": 0},
        {"asset_id": 1, "max_duration_sec": 59},
        {"asset_id": 1, "max_duration_sec": 7201},
    ],
)
def test_remote_session_limits_are_bounded(payload):
    with pytest.raises(ValidationError):
        CreateSessionRequest(**payload)


def test_agent_remote_desktop_requires_matching_bearer_token(monkeypatch):
    monkeypatch.setitem(CONFIG, "token", "agent-test-token")
    server = RemoteDesktopServer()

    assert server._is_authorized(SimpleNamespace(request_headers={"Authorization": "Bearer agent-test-token"}))
    assert not server._is_authorized(SimpleNamespace(request_headers={"Authorization": "Bearer wrong-token"}))
    assert not server._is_authorized(SimpleNamespace(request_headers={}))
