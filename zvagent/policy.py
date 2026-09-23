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
import requests
import threading
import time
from urllib.parse import urljoin
from config_utils import get_env
from zvagent.auth import _agent_requests_verify, _platform_base

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
    if "allow_shell" in remote_in and remote_in.get("allow_shell") is not None:
        value = bool(remote_in.get("allow_shell"))
        current_remote["allow_shell"] = value
        clean["allow_shell"] = value
    if (
        "shell_timeout_seconds" in remote_in
        and remote_in.get("shell_timeout_seconds") is not None
    ):
        try:
            value = max(5, min(600, int(remote_in.get("shell_timeout_seconds"))))
        except (TypeError, ValueError):
            value = None
        if value is not None:
            current_remote["shell_timeout_seconds"] = value
            clean["shell_timeout_seconds"] = value
    if clean:
        applied["remote_desktop"] = clean
        try:
            from remote_desktop_engine_v2 import CONSENT_MANAGER

            CONSENT_MANAGER.configure(current_remote)
        except Exception:
            pass
        try:
            from remote_desktop_engine_v2 import REMOTE_SHELL_SETTINGS

            REMOTE_SHELL_SETTINGS.configure(current_remote)
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


class SecurityPolicySync:
    """安全策略自动轮询同步：定时从平台拉取绑定的安全策略并应用，回传执行结果。"""

    _LOG_PATH = Path(os.environ.get("ProgramData", ".")) / "CMDB-Agent" / "logs" / "security-sync.log"

    def __init__(self, asset_id: int):
        self.asset_id = asset_id
        self.running = False
        self.thread: threading.Thread | None = None

    def _log(self, msg: str):
        line = f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {msg}"
        print(f"[SecurityPolicySync] {msg}")
        try:
            self._LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
            with open(self._LOG_PATH, "a", encoding="utf-8") as f:
                f.write(line + "\n")
        except Exception:
            pass

    def start(self):
        if self.running:
            return
        self.running = True
        self.thread = threading.Thread(target=self._loop, name="security-policy-sync", daemon=True)
        self.thread.start()
        self._log(f"启动 (asset_id={self.asset_id})")

    def stop(self):
        self.running = False

    def _loop(self):
        interval = int(CONFIG.get("intervals", {}).get("security_policy_sync", 300) or 300)
        interval = max(60, min(interval, 3600))
        self._log(f"轮询间隔={interval}s")
        while self.running:
            try:
                self._sync_and_apply()
            except Exception as exc:
                self._log(f"同步失败: {exc}")
            time.sleep(interval)

    def _sync_and_apply(self):
        from security_manager import execute_security_command
        token = CONFIG.get("token") or ""
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        url = urljoin(_platform_base(), f"/api/v1/agent/security-policies?asset_id={self.asset_id}")
        self._log(f"拉取策略: {url}")
        resp = requests.get(url, headers=headers, timeout=30, verify=_agent_requests_verify())
        if resp.status_code != 200:
            self._log(f"拉取策略 HTTP {resp.status_code}")
            return
        data = resp.json()
        policies = data.get("policies") or []
        self._log(f"拉取到 {len(policies)} 条安全策略")
        if not policies:
            return
        for p in policies:
            try:
                self._apply_one(p, execute_security_command)
            except Exception as exc:
                self._log(f"应用策略 {p.get('id')} 失败: {exc}")

    def _apply_one(self, policy: dict, executor):
        ptype = policy.get("policy_type")
        config = policy.get("config") or {}
        policy_id = policy.get("id")
        scope_type = policy.get("scope_type", "asset")
        applied = 0
        failed = 0
        error_detail = None
        try:
            if ptype == "firewall":
                rules = config.get("rules") or []
                res = executor("firewall_apply", {"rules": rules})
                applied = res.get("applied", 0)
                failed = res.get("failed", 0)
                if not res.get("success"):
                    error_detail = res.get("error") or json.dumps(res.get("details", []))[:500]
            elif ptype == "usb":
                action = config.get("action", "block")
                cmd = "usb_block" if action == "block" else "usb_allow"
                res = executor(cmd, {})
                if not res.get("success"):
                    failed = 1
                    error_detail = res.get("error", "usb apply failed")
                else:
                    applied = 1
            else:
                error_detail = f"unknown policy_type: {ptype}"
                failed = 1
        except Exception as exc:
            failed = 1
            error_detail = str(exc)

        self._report_result(policy_id, scope_type, applied, failed, error_detail)

    def _report_result(self, policy_id, scope_type, applied, failed, error_detail):
        token = CONFIG.get("token") or ""
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        url = urljoin(_platform_base(), "/api/v1/agent/security-policy-result")
        status = "success" if failed == 0 and applied > 0 else ("partial" if applied > 0 else "failed")
        payload = {
            "policy_id": policy_id,
            "asset_id": self.asset_id,
            "scope_type": scope_type,
            "status": status,
            "applied_rules": applied,
            "failed_rules": failed,
            "error_detail": error_detail,
        }
        try:
            requests.post(url, json=payload, headers=headers, timeout=15, verify=_agent_requests_verify())
        except Exception as exc:
            print(f"[SecurityPolicySync] 回传结果失败: {exc}")

def apply_agent_firewall_whitelist(server_url: str, logger=None):
    """R2 防火墙白名单（可选，ZVIEW_FIREWALL_WHITELIST=1 启用）：
    仅允许平台主机与本机访问 Agent 9000/9001 端口。
    幂等：规则名固定，先删后建。失败不影响服务启动。
    """
    import subprocess
    import ipaddress
    from urllib.parse import urlparse
    try:
        if get_env("ZVIEW_FIREWALL_WHITELIST", "").strip() not in ("1", "true", "True"):
            return
        parsed = urlparse(server_url)
        platform_host = parsed.hostname or ""
        try:
            platform_ip = ipaddress.ip_address(platform_host).exploded
        except ValueError:
            import socket
            platform_ip = socket.gethostbyname(platform_host)
        allow_remoteip = f"{platform_ip},127.0.0.1"
        try:
            lan_net = ipaddress.ip_network(f"{platform_ip}/24", strict=False)
            if lan_net.prefixlen < 32:
                allow_remoteip += f",{lan_net.network_address}/{lan_net.prefixlen}"
        except ValueError:
            pass
        # 额外放行网段（跨网段观看端场景）：ZVIEW_EXTRA_FIREWALL_ALLOW="172.17.40.0/24,10.0.0.5"
        extra_allow = get_env("ZVIEW_EXTRA_FIREWALL_ALLOW", "").strip()
        if extra_allow:
            allow_remoteip += f",{extra_allow}"
            if logger:
                logger(f"agent firewall extra allow: {extra_allow}")
        RULE_ALLOW = "zv-agent-allow-platform"
        RULE_BLOCK = "zv-agent-block-others"
        for name in (RULE_ALLOW, RULE_BLOCK):
            subprocess.run(
                ["netsh", "advfirewall", "firewall", "delete", "rule", f"name={name}"],
                capture_output=True, timeout=15, check=False,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        subprocess.run(
            ["netsh", "advfirewall", "firewall", "add", "rule", f"name={RULE_ALLOW}",
             "dir=in", "action=allow", "protocol=TCP",
             "localport=9000,9001", f"remoteip={allow_remoteip}"],
            capture_output=True, timeout=15, check=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        subprocess.run(
            ["netsh", "advfirewall", "firewall", "add", "rule", f"name={RULE_BLOCK}",
             "dir=in", "action=block", "protocol=TCP", "localport=9000,9001"],
            capture_output=True, timeout=15, check=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        if logger:
            logger(f"agent firewall whitelist applied: allow={platform_ip},127.0.0.1 -> 9000,9001")
    except Exception as exc:
        if logger:
            logger(f"agent firewall whitelist failed (ignored): {exc}")


_security_policy_sync_instance: SecurityPolicySync | None = None


def start_security_policy_sync(asset_id: int):
    """启动安全策略自动轮询同步。"""
    global _security_policy_sync_instance
    if _security_policy_sync_instance is not None:
        return
    _security_policy_sync_instance = SecurityPolicySync(asset_id)
    _security_policy_sync_instance.start()

