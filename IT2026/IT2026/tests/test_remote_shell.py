# -*- coding: utf-8 -*-
"""Remote Shell 基础功能回归测试（策略门控 + 协议处理）。

覆盖：
- RemoteShellSettings.configure：默认 fail-closed（allow_shell=False）、
  超时钳制（5..600）、非法输入不抛异常。
- RemoteDesktopSession.handle_shell_exec 策略关闭时回 shell_error(shell_disabled)。
- handle_shell_stop 空运行时回 shell_error(not_running)。
- zvagent policy handler：allow_shell / shell_timeout_seconds 下发生效。

运行: python -m pytest tests/test_remote_shell.py -q
"""

import asyncio
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from remote_desktop_engine_v2 import (  # noqa: E402
    REMOTE_SHELL_SETTINGS,
    RemoteDesktopSession,
    RemoteShellSettings,
)


class _StubWebSocket:
    """记录 send_json 调用的最小 WebSocket 桩。"""

    def __init__(self):
        self.sent = []

    async def send_json(self, payload):
        self.sent.append(payload)


def make_session():
    session = RemoteDesktopSession.__new__(RemoteDesktopSession)
    session.websocket = _StubWebSocket()
    session.session_id = "test-shell"
    session.running = False
    session.last_input_at = 0.0
    session.send_lock = asyncio.Lock()
    session._shell_executor = None
    import threading

    session._shell_lock = threading.Lock()
    session._shell_procs = {}
    session._shell_seq = 0
    return session


def test_settings_default_fail_closed():
    settings = RemoteShellSettings()
    assert settings.allow_shell is False
    assert settings.timeout_seconds == 60


def test_settings_configure_clamps_timeout():
    settings = RemoteShellSettings()
    settings.configure({"allow_shell": True, "shell_timeout_seconds": 99999})
    assert settings.allow_shell is True
    assert settings.timeout_seconds == 600
    settings.configure({"allow_shell": False, "shell_timeout_seconds": 0})
    assert settings.allow_shell is False
    assert settings.timeout_seconds == 5
    settings.configure({"shell_timeout_seconds": "not-a-number"})
    settings.configure(None)
    settings.configure("junk")
    # 非法输入不改变当前值
    assert settings.timeout_seconds == 5
    assert settings.allow_shell is False


def test_handle_shell_exec_disabled_by_policy():
    settings = RemoteShellSettings()
    session = make_session()
    original = REMOTE_SHELL_SETTINGS.allow_shell
    REMOTE_SHELL_SETTINGS.allow_shell = False
    try:
        asyncio.run(session.handle_shell_exec({"id": "s1", "command": "whoami"}))
    finally:
        REMOTE_SHELL_SETTINGS.allow_shell = original
    sent = session.websocket.sent
    assert len(sent) == 1
    assert sent[0]["type"] == "shell_error"
    assert sent[0]["code"] == "shell_disabled"


def test_handle_shell_exec_rejects_empty_and_oversize():
    settings = RemoteShellSettings()
    settings.allow_shell = True
    REMOTE_SHELL_SETTINGS.max_command_chars = settings.max_command_chars
    session = make_session()
    original = REMOTE_SHELL_SETTINGS.allow_shell
    REMOTE_SHELL_SETTINGS.allow_shell = True
    try:
        asyncio.run(session.handle_shell_exec({"id": "s1", "command": "   "}))
        asyncio.run(session.handle_shell_exec({"id": "s2", "command": "x" * 9000}))
    finally:
        REMOTE_SHELL_SETTINGS.allow_shell = original
    codes = [m.get("code") for m in session.websocket.sent]
    assert codes == ["empty_command", "command_too_long"]


def test_handle_shell_stop_when_idle():
    session = make_session()
    asyncio.run(session.handle_shell_stop({"id": "nope"}))
    assert len(session.websocket.sent) == 1
    assert session.websocket.sent[0]["code"] == "not_running"


def test_agent_policy_handler_applies_allow_shell(monkeypatch, tmp_path):
    zvagent_policy = pytest.importorskip("zvagent.policy")

    # 阻止策略 handler 触碰真实的同意管理器/运行时目录
    monkeypatch.setattr(
        zvagent_policy, "_set_prompt_on_secure_desktop", lambda value: None
    )
    monkeypatch.setattr(zvagent_policy, "_persist_applied", lambda applied: None)

    applied = {}
    zvagent_policy._handle_remote_desktop(
        {
            "remote_desktop": {
                "allow_shell": True,
                "shell_timeout_seconds": 120,
            }
        },
        applied,
    )
    assert applied["remote_desktop"]["allow_shell"] is True
    assert applied["remote_desktop"]["shell_timeout_seconds"] == 120
    config_remote = zvagent_policy.CONFIG.get("remote_desktop") or {}
    assert config_remote.get("allow_shell") is True
    assert config_remote.get("shell_timeout_seconds") == 120


def test_platform_policy_validation_accepts_shell_fields():
    service = pytest.importorskip("zvplatform.services.agent_policy_service")
    result, errors = service.normalize_agent_policies(
        {
            "remote_desktop": {
                "allow_shell": True,
                "shell_timeout_seconds": 300,
            }
        }
    )
    assert errors == []
    assert result["remote_desktop"]["allow_shell"] is True
    assert result["remote_desktop"]["shell_timeout_seconds"] == 300

    result, errors = service.normalize_agent_policies(
        {"remote_desktop": {"shell_timeout_seconds": 601}}
    )
    assert errors


pytestmark_integration = pytest.mark.skipif(
    __import__("os").name != "nt", reason="Windows PowerShell 集成冒烟"
)


async def _wait_for_type(session, msg_type, timeout_s):
    import time as _time

    deadline = _time.monotonic() + timeout_s
    while _time.monotonic() < deadline:
        if any(m.get("type") == msg_type for m in session.websocket.sent):
            return True
        # 必须让出事件循环：shell 线程通过 run_coroutine_threadsafe 回投消息
        await asyncio.sleep(0.1)
    return False


@pytest.mark.skipif(__import__("os").name != "nt", reason="Windows only")
def test_live_shell_exec_roundtrip_utf8(monkeypatch):
    """真实执行 PowerShell：验证消息分发 → Popen → 流式输出 → UTF-8 中文 → 终态。"""
    session = make_session()
    monkeypatch.setattr(REMOTE_SHELL_SETTINGS, "allow_shell", True)

    async def scenario():
        await session.handle_control({
            "type": "shell_exec",
            "id": "live-1",
            "command": "Write-Output ('你好-' + $env:USERNAME)",
        })
        assert await _wait_for_type(session, "shell_exit", 30)

    asyncio.run(scenario())
    types = [m.get("type") for m in session.websocket.sent]
    assert "shell_output" in types
    assert "shell_exit" in types
    stdout_text = "".join(
        m.get("data") or "" for m in session.websocket.sent if m.get("type") == "shell_output"
    )
    assert "你好-" in stdout_text  # UTF-8 链路（chcp 65001 + OutputEncoding + utf-8 解码）
    exit_msg = next(m for m in session.websocket.sent if m.get("type") == "shell_exit")
    assert exit_msg["exit_code"] == 0
    assert exit_msg["timed_out"] is False
    assert exit_msg.get("cwd")


@pytest.mark.skipif(__import__("os").name != "nt", reason="Windows only")
def test_live_shell_exec_timeout_kill(monkeypatch):
    """超时看门狗：长命令被 taskkill 终止并回 timed_out 终态。"""
    session = make_session()
    monkeypatch.setattr(REMOTE_SHELL_SETTINGS, "allow_shell", True)
    monkeypatch.setattr(REMOTE_SHELL_SETTINGS, "timeout_seconds", 5)

    async def scenario():
        await session.handle_control({
            "type": "shell_exec",
            "id": "live-2",
            "command": "Start-Sleep -Seconds 120",
        })
        assert await _wait_for_type(session, "shell_exit", 30)

    asyncio.run(scenario())
    exit_msg = next(m for m in session.websocket.sent if m.get("type") == "shell_exit")
    assert exit_msg["id"] == "live-2"
    assert exit_msg["timed_out"] is True
    assert exit_msg["exit_code"] != 0
    # 进程已清理
    assert not session._shell_procs
