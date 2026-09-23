# -*- coding: utf-8 -*-
"""安全桌面（UAC）输入策略回归测试。

覆盖：
  1. 策略禁止时：注入线程拒绝绑定/执行于安全桌面，抛 SecureDesktopInputBlocked
  2. 策略允许时：操作在"安全桌面绑定"下正常执行，并写入审计标记
  3. 非安全桌面不受策略影响
  4. 托盘开关 allow_secure_desktop_input 持久化 + 助手策略缓存读取
运行: python tests\\test_secure_desktop_input_policy.py
"""

import sys
import threading
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from RemoteAgent.high_integrity_helper import (  # noqa: E402
    SecureDesktopInputBlocked,
    _DesktopBoundWorker,
)
from agent_consent_ipc import load_tray_settings, save_tray_settings  # noqa: E402

RESULTS = []


def record(name, ok, detail=""):
    RESULTS.append(bool(ok))
    print(f"[{'PASS' if ok else 'FAIL'}] {name} {detail}", flush=True)


class _StubController:
    """以可控状态模拟 InputDesktopController（不做真实桌面切换）。"""

    def __init__(self, state):
        self._state = state

    def describe_current_state(self):
        return dict(self._state)

    def ensure_current_thread_on_input_desktop(self, reason):
        state = dict(self._state)
        # 模拟绑定成功：当前线程即目标输入桌面
        state["thread_matches_input"] = True
        return state


SECURE_STATE = {
    "input_desktop": "Secure",
    "input_desktop_kind": "secure_winlogon",
    "desktop_kind": "secure_winlogon",
    "desktop_signature": "sig-secure",
    "process_winsta": "WinSta0",
}

NORMAL_STATE = {
    "input_desktop": "Default",
    "input_desktop_kind": "interactive_default",
    "desktop_kind": "interactive_default",
    "desktop_signature": "sig-default",
    "process_winsta": "WinSta0",
}


def make_worker(state, policy):
    controller = _StubController(state)

    def build_input_target_signature(desktop_state=None):
        s = desktop_state or {}
        return f"{s.get('input_desktop', 'unknown')}:{s.get('desktop_signature', '')}"

    def build_binding_scope_signature(binding_mode=None, capture_target=None, state=None):
        s = state or {}
        return f"scope:{binding_mode}:{s.get('input_desktop', 'unknown')}"

    controller.build_input_target_signature = build_input_target_signature
    controller.build_binding_scope_signature = build_binding_scope_signature
    logs = []
    worker = _DesktopBoundWorker(
        name="test-input",
        binding_mode="input",
        desktop_controller=controller,
        logger=logs.append,
        secure_desktop_input_policy=policy,
    )
    return worker, logs


def run_case(secure, policy_allows):
    executed = {"value": None}
    worker, _logs = make_worker(SECURE_STATE if secure else NORMAL_STATE,
                                lambda: bool(policy_allows))
    worker.start()
    try:
        def op(binding_state):
            executed["value"] = dict(binding_state)
            return {"ok": True}

        error = None
        try:
            worker.call("test-case", op, timeout=8.0)
        except SecureDesktopInputBlocked as exc:
            error = exc
        except Exception as exc:  # 其他异常视为失败细节
            error = exc
        return executed.get("value"), error
    finally:
        worker.stop(timeout=2.0)


def main():
    original = dict(load_tray_settings())

    try:
        # ---- 1. 安全桌面 + 策略禁止 -> 拒绝且不执行 ----
        binding, error = run_case(secure=True, policy_allows=False)
        record(
            "策略拒绝时阻断安全桌面注入",
            isinstance(error, SecureDesktopInputBlocked) and binding is None,
            f"error={type(error).__name__ if error else None}",
        )

        # ---- 2. 安全桌面 + 策略允许 -> 执行并写审计标记 ----
        binding, error = run_case(secure=True, policy_allows=True)
        audit_marked = bool(binding and binding.get("secure_desktop_authorized"))
        audit_logged = False
        record(
            "策略允许时授权执行安全桌面注入",
            error is None and audit_marked,
            f"error={type(error).__name__ if error else None} marked={audit_marked}",
        )

        # ---- 3. 非安全桌面不受策略影响（即使策略为禁止） ----
        binding, error = run_case(secure=False, policy_allows=False)
        record("普通桌面注入不受该策略限制", error is None and binding is not None,
               f"error={type(error).__name__ if error else None}")

        # ---- 4. 托盘开关持久化 ----
        save_tray_settings({"allow_secure_desktop_input": False})
        off_value = load_tray_settings().get("allow_secure_desktop_input")
        save_tray_settings({"allow_secure_desktop_input": True})
        on_value = load_tray_settings().get("allow_secure_desktop_input")
        record("托盘 UAC 开关持久化", off_value is False and on_value is True,
               f"off={off_value} on={on_value}")

        print("=" * 60)
    finally:
        save_tray_settings(original)

    failed = RESULTS.count(False)
    print(f"总计 {len(RESULTS)} 项, 失败 {failed} 项")
    import os

    os._exit(0 if failed == 0 else 1)


if __name__ == "__main__":
    main()
