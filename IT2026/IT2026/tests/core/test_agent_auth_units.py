# -*- coding: utf-8 -*-
"""Agent 认证单元测试（无需运行中的服务器）：zv1 设备凭据协议 + 全局 token 轮换窗口"""
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

import auth_utils  # noqa: E402


def _reset(monkeypatch_env=None, verifier=None):
    auth_utils.set_agent_device_credential_verifier(verifier)
    os.environ.pop("ZVIEW_AGENT_TOKEN", None)
    os.environ.pop("ZVIEW_AGENT_TOKEN_PREVIOUS", None)
    os.environ.pop("ZVIEW_AGENT_TOKEN_FILE", None)
    os.environ.pop(auth_utils.LEGACY_AGENT_TOKEN_DISABLED_ENV_NAME, None)
    if monkeypatch_env:
        os.environ.update(monkeypatch_env)


def test_device_token_valid():
    _reset(verifier=lambda aid, sec: aid == 28 and sec == "good")
    auth = auth_utils.verify_agent_token("zv1:28:good")
    assert auth and auth["agent_auth_type"] == "device" and auth["agent_id"] == 28


def test_device_token_wrong_secret():
    _reset(verifier=lambda aid, sec: False)
    assert auth_utils.verify_agent_token("zv1:28:bad") is None


def test_device_token_malformed():
    _reset(verifier=lambda aid, sec: True)
    for bad in ("zv1:abc:sec", "zv1:28", "zv1:", "zv2:28:sec"):
        assert auth_utils.verify_agent_token(bad) is None


def test_global_token_current_and_previous():
    _reset(monkeypatch_env={"ZVIEW_AGENT_TOKEN": "new-token", "ZVIEW_AGENT_TOKEN_PREVIOUS": "old-token"})
    assert auth_utils.verify_agent_token("new-token")["token_source"] == "configured"
    assert auth_utils.verify_agent_token("old-token")["token_source"] == "previous"
    assert auth_utils.verify_agent_token("other") is None


def test_legacy_disabled():
    _reset(monkeypatch_env={auth_utils.LEGACY_AGENT_TOKEN_DISABLED_ENV_NAME: "1"})
    # 未配置 managed token 时 legacy 是唯一期望值，禁用后应拒绝
    assert auth_utils.verify_agent_token(auth_utils.LEGACY_AGENT_TOKEN) is None


def test_legacy_allowed_by_default():
    _reset()
    auth = auth_utils.verify_agent_token(auth_utils.LEGACY_AGENT_TOKEN)
    assert auth and auth["legacy_compat"] is True


def test_device_precedence_over_global():
    # zv1 前缀永远不与全局 token 比对
    _reset(monkeypatch_env={"ZVIEW_AGENT_TOKEN": "zv1:1:x"}, verifier=lambda aid, sec: False)
    assert auth_utils.verify_agent_token("zv1:1:x") is None
