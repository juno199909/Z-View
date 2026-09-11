# -*- coding: utf-8 -*-
"""Agent 策略应用（V1.8.3 正式化：handler 注册表模式）。

心跳响应中的 policies 按 key 分发给注册的 handler（intervals / remote_desktop），
新增策略类型只需注册新 handler，应用逻辑与分发循环解耦。
应用结果持久化到 runtime\agent-policies.json（重启恢复）。
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Callable

from console_utils import safe_console_print
from zvagent.config import CONFIG

print = safe_console_print


_AGENT_POLICIES_CACHE_PATH = (
    Path(os.environ.get("ProgramData", ".")) / "CMDB-Agent" / "runtime" / "agent-policies.json"
)

# 策略 handler 注册表：policy key -> handler(policies, applied)
_POLICY_HANDLERS: dict[str, Callable[[dict, dict], None]] = {}


def policy_handler(key: str) -> Callable:
    """注册策略 handler 装饰器。"""
    def deco(fn: Callable[[dict, dict], None]) -> Callable[[dict, dict], None]:
        _POLICY_HANDLERS[key] = fn
        return fn
    return deco


def _set_prompt_on_secure_desktop(disable: bool) -> str:
    """设置 UAC 提示是否免安全桌面。

    PromptOnSecureDesktop=1 时 UAC 弹窗位于独立安全桌面，远控抓屏/输入均不可见；
    置 0 后 UAC 弹窗显示在当前桌面，远程会话可直接查看与操作。
    需要 SYSTEM/管理员权限；无权限或非 Windows 时返回 "skipped"。
    返回: "changed" / "unchanged" / "skipped"。
    """
    if os.name != "nt":
        return "skipped"
    try:
        import winreg

        key_path = r"SOFTWARE\Microsoft\Windows\CurrentVersion\Policies\System"
        desired = 0 if disable else 1
        with winreg.OpenKey(
            winreg.HKEY_LOCAL_MACHINE, key_path, 0, winreg.KEY_READ | winreg.KEY_WRITE
        ) as key:
            try:
                current, _value_type = winreg.QueryValueEx(key, "PromptOnSecureDesktop")
            except FileNotFoundError:
                current = 1
            current = int(current or 1)
            if current == desired:
                return "unchanged"
            winreg.SetValueEx(key, "PromptOnSecureDesktop", 0, winreg.REG_DWORD, desired)
        print(f"[Policy] PromptOnSecureDesktop -> {desired} (UAC secure desktop {'disabled' if disable else 'enabled'})")
        return "changed"
    except PermissionError:
        print("[Policy] 无法修改 PromptOnSecureDesktop：需要 SYSTEM/管理员权限")
        return "skipped"
    except Exception as exc:
        print(f"[Policy] 设置 PromptOnSecureDesktop 失败: {exc}")
        return "skipped"


@policy_handler("intervals")
def _handle_intervals(policies: dict, applied: dict) -> None:
    intervals_in = policies.get("intervals")
    if not isinstance(intervals_in, dict):
        return
    current_intervals = CONFIG.setdefault("intervals", {})
    for key in ("heartbeat", "software", "hardware"):
        if key not in intervals_in:
            continue
        raw = intervals_in.get(key)
        if isinstance(raw, bool):
            continue
        try:
            value = int(raw)
        except (TypeError, ValueError):
            continue
        value = max(5, min(value, 604800))
        current_intervals[key] = value
        applied[key] = value


@policy_handler("remote_desktop")
def _handle_remote_desktop(policies: dict, applied: dict) -> None:
    remote_in = policies.get("remote_desktop")
    if not isinstance(remote_in, dict):
        return
    current_remote = CONFIG.setdefault("remote_desktop", {})
    clean: dict = {}
    if "require_consent" in remote_in and remote_in.get("require_consent") is not None:
        value = bool(remote_in.get("require_consent"))
        current_remote["require_consent"] = value
        clean["require_consent"] = value
    if (
        "consent_timeout_seconds" in remote_in
        and remote_in.get("consent_timeout_seconds") is not None
    ):
        try:
            value = max(5, int(remote_in.get("consent_timeout_seconds")))
        except (TypeError, ValueError):
            value = None
        if value is not None:
            current_remote["consent_timeout_seconds"] = value
            clean["consent_timeout_seconds"] = value
    if "allow_if_no_user" in remote_in and remote_in.get("allow_if_no_user") is not None:
        value = bool(remote_in.get("allow_if_no_user"))
        current_remote["allow_if_no_user"] = value
        clean["allow_if_no_user"] = value
    if (
        "disable_uac_secure_desktop" in remote_in
        and remote_in.get("disable_uac_secure_desktop") is not None
    ):
        value = bool(remote_in.get("disable_uac_secure_desktop"))
        current_remote["disable_uac_secure_desktop"] = value
        clean["disable_uac_secure_desktop"] = value
        _set_prompt_on_secure_desktop(value)
    if clean:
        applied["remote_desktop"] = clean
        try:
            from remote_desktop_engine_v2 import CONSENT_MANAGER

            CONSENT_MANAGER.configure(current_remote)
        except Exception:
            pass


def _persist_applied(applied: dict) -> None:
    try:
        _AGENT_POLICIES_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        merged = {}
        try:
            merged = json.loads(_AGENT_POLICIES_CACHE_PATH.read_text(encoding="utf-8"))
            if not isinstance(merged, dict):
                merged = {}
        except Exception:
            merged = {}
        stored_intervals = merged.setdefault("intervals", {})
        for key in ("heartbeat", "software", "hardware"):
            if key in applied:
                stored_intervals[key] = applied[key]
        if isinstance(applied.get("remote_desktop"), dict):
            merged["remote_desktop"] = {
                **(merged.get("remote_desktop") or {}),
                **applied["remote_desktop"],
            }
        _AGENT_POLICIES_CACHE_PATH.write_text(
            json.dumps(merged, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    except Exception:
        pass


def apply_agent_policies(policies: Any, persist: bool = True) -> dict | None:
    """将平台下发的策略分发给注册的 handlers 并热更新消费方。

    返回实际生效的增量；无有效变更时返回 None。
    """
    if not isinstance(policies, dict) or not policies:
        return None

    applied: dict = {}
    for key, handler in _POLICY_HANDLERS.items():
        if key not in policies:
            continue
        try:
            handler(policies, applied)
        except Exception as exc:
            print(f"[Policy] handler {key} failed: {exc}")

    if persist and applied:
        _persist_applied(applied)

    return applied or None


# 兼容别名（V1.8.2 前的存量引用）
_apply_agent_policies = apply_agent_policies


def _load_cached_agent_policies() -> None:
    """进程启动或远控会话创建前，从本地缓存恢复最近一次平台策略。"""
    try:
        if not _AGENT_POLICIES_CACHE_PATH.exists():
            return
        data = json.loads(_AGENT_POLICIES_CACHE_PATH.read_text(encoding="utf-8"))
        apply_agent_policies(data, persist=False)
    except Exception:
        pass


def _current_interval(name: str, default: int) -> int:
    try:
        value = int(CONFIG.get("intervals", {}).get(name, default))
    except (TypeError, ValueError):
        return default
    return max(5, min(value, 604800))
