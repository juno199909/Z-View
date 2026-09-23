# -*- coding: utf-8 -*-
"""WTS 同意弹窗响应值映射回归测试。

背景：本机（RDP 会话）上 WTSSendMessageW 等待超时后返回 TRUE 且 response=0，
而非 IDTIMEOUT(32000)，旧代码将其报为 unknown_response:0。修复后 response=0
必须与超时同等处理。

运行: python tests\\test_consent_response_mapping.py
"""

import ctypes
import sys
import types
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from remote_desktop_engine_v2 import RemoteAccessConsentManager  # noqa: E402


class _StubWts:
    """以预设 response 值模拟 WTSSendMessageW 的返回行为。"""

    def __init__(self, reply):
        self.reply = reply
        self.calls = []

    def WTSSendMessageW(self, handle, session, title, title_len, message, message_len,
                        style, timeout, response_ptr, wait):
        self.calls.append({"session": session, "timeout": timeout, "wait": bool(wait)})
        ctypes.memmove(response_ptr, ctypes.byref(ctypes.wintypes.DWORD(self.reply)),
                       ctypes.sizeof(ctypes.wintypes.DWORD))
        return 1


def make_manager(reply):
    manager = RemoteAccessConsentManager()
    manager._wts_available = True
    manager.helper_enabled = False
    manager.timeout_seconds = 30
    manager._wtsapi32 = _StubWts(reply)
    manager._resolve_target_session_id = lambda: 2
    return manager


RESULTS = []


def record(name, ok, detail=""):
    RESULTS.append(bool(ok))
    print(f"[{'PASS' if ok else 'FAIL'}] {name} {detail}")


def run():
    # 1. 本机实际观测形态：TRUE + response=0 -> timeout（回归主用例）
    approved, reason = make_manager(0)._show_consent_dialog({})
    record("response=0 映射为 timeout", (approved, reason) == (False, "timeout"), f"reason={reason}")

    # 2. 标准 Windows 超时：IDTIMEOUT(32000) -> timeout
    approved, reason = make_manager(32000)._show_consent_dialog({})
    record("IDTIMEOUT 映射为 timeout", (approved, reason) == (False, "timeout"), f"reason={reason}")

    # 3. 用户点击「是」：IDYES(6) -> accepted
    approved, reason = make_manager(6)._show_consent_dialog({})
    record("IDYES 映射为 accepted", approved is True and reason == "accepted", f"reason={reason}")

    # 4. 用户点击「否」：IDNO(7) -> rejected
    approved, reason = make_manager(7)._show_consent_dialog({})
    record("IDNO 映射为 rejected", (approved, reason) == (False, "rejected"), f"reason={reason}")

    # 5. 其余未知值保持显式 unknown_response 报告，不吞异常
    approved, reason = make_manager(42)._show_consent_dialog({})
    record("未知值仍报 unknown_response", (approved, reason) == (False, "unknown_response:42"), f"reason={reason}")

    failed = RESULTS.count(False)
    print("=" * 60)
    print(f"总计 {len(RESULTS)} 项, 失败 {failed} 项")
    return failed == 0


if __name__ == "__main__":
    ok = run()
    sys.exit(0 if ok else 1)
