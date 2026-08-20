"""
Z-View Agent Core Module - 备份恢复版
包含：资产采集、心跳上报、软件管理、远程桌面服务端
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import os
import platform
import socket
import subprocess
import sys
import threading
import time
import traceback
import uuid
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Optional, Dict
from urllib.parse import urljoin, urlparse

import psutil
import requests

from config_utils import ensure_env_loaded, get_env
from console_utils import enable_utf8_stdio, safe_console_print

enable_utf8_stdio()
print = safe_console_print
ensure_env_loaded()

# =============================================================================
# 配置
# =============================================================================

_DEFAULT_CONFIG = {
    "server_url": get_env("ZVIEW_SERVER_URL", "http://127.0.0.1:8080") or "http://127.0.0.1:8080",
    "token": get_env("ZVIEW_AGENT_TOKEN", "") or "",
    "intervals": {
        "heartbeat": 30,
        "system_status": 30,
        "software": 30,
        "hardware": 86400,
    },
    "remote_desktop": {
        "require_consent": True,
        "consent_timeout_seconds": 30,
        "allow_if_no_user": False,
    },
    "control_port": int(get_env("ZVIEW_AGENT_CONTROL_PORT", "9001") or "9001"),
    "log_level": "INFO",
}

_DEFAULT_SOFTWARE_CONFIG = {
    "server_url": get_env("ZVIEW_SOFTWARE_SERVER_URL", "http://127.0.0.1:8081") or "http://127.0.0.1:8081",
    "token": get_env("ZVIEW_AGENT_TOKEN", "") or "",
    "intervals": {
        "policy_sync": 300,
        "task_poll": 30,
    },
    "download_dir": get_env("ZVIEW_DOWNLOAD_DIR", r"C:\CMDB-Agent\Downloads") or r"C:\CMDB-Agent\Downloads",
    "max_retries": 3,
}


def _load_config_from_file(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _merge_config(default: dict, override: dict) -> dict:
    result = dict(default)
    for key, value in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = _merge_config(result[key], value)
        else:
            result[key] = value
    return result


_CONFIG_CANDIDATES = (
    Path(__file__).resolve().parent / "config.local.json",
    Path(__file__).resolve().parent / "config.json",
)

if getattr(sys, "frozen", False):
    # 打包版配置解析优先级（V1.6.0 程序/数据分离）：
    # 1) ProgramData\CMDB-Agent\config\config.local.json —— 安装器写入，versions 切换不受影响
    # 2) exe 旁 config.local.json —— 旧版单文件布局兼容
    # 3) 构建时打入的 config.json
    _program_data_config = (
        Path(os.environ.get("ProgramData", ".")) / "CMDB-Agent" / "config" / "config.local.json"
    )
    _exe_dir = Path(sys.executable).resolve().parent
    _CONFIG_CANDIDATES = (
        _program_data_config,
        _exe_dir / "config.local.json",
    ) + _CONFIG_CANDIDATES


def _load_user_config() -> dict:
    for candidate in _CONFIG_CANDIDATES:
        data = _load_config_from_file(candidate)
        if data:
            return data
    return {}


def _apply_env_overrides(config: dict, software_config: dict) -> tuple[dict, dict]:
    merged_config = _merge_config(config, {})
    merged_software_config = _merge_config(software_config, {})

    agent_token = get_env("ZVIEW_AGENT_TOKEN")
    if agent_token:
        merged_config["token"] = agent_token
        merged_software_config["token"] = agent_token

    server_url = get_env("ZVIEW_SERVER_URL")
    if server_url:
        merged_config["server_url"] = server_url

    control_port = get_env("ZVIEW_AGENT_CONTROL_PORT")
    if control_port:
        try:
            merged_config["control_port"] = int(control_port)
        except ValueError:
            pass

    software_server_url = get_env("ZVIEW_SOFTWARE_SERVER_URL")
    if software_server_url:
        merged_software_config["server_url"] = software_server_url
    elif server_url:
        merged_software_config["server_url"] = server_url.replace(":8080", ":8081")

    download_dir = get_env("ZVIEW_DOWNLOAD_DIR")
    if download_dir:
        merged_software_config["download_dir"] = download_dir

    return merged_config, merged_software_config


_user_config = _load_user_config()

CONFIG = _merge_config(_DEFAULT_CONFIG, _user_config)
SOFTWARE_CONFIG = _merge_config(_DEFAULT_SOFTWARE_CONFIG, _user_config)

# Agent 版本号（发布时由 build_agent.ps1 或手动 bump；升级机制以此判定新版本）
AGENT_VERSION = "1.9.0"

# 一机一密（P0-01）：设备凭据缓存（agent_id + device_secret）
_AGENT_CREDENTIALS_CACHE_PATH = (
    Path(os.environ.get("ProgramData", ".")) / "CMDB-Agent" / "runtime" / "agent-credentials.json"
)


def _load_device_credentials() -> dict:
    """加载设备凭据：config.json 字段优先，其次 runtime 缓存文件"""
    creds = {
        "agent_id": CONFIG.get("agent_id"),
        "device_secret": CONFIG.get("device_secret"),
    }
    if creds.get("agent_id") and creds.get("device_secret"):
        return creds
    try:
        if _AGENT_CREDENTIALS_CACHE_PATH.exists():
            data = json.loads(_AGENT_CREDENTIALS_CACHE_PATH.read_text(encoding="utf-8"))
            creds["agent_id"] = creds.get("agent_id") or data.get("agent_id")
            creds["device_secret"] = creds.get("device_secret") or data.get("device_secret")
    except Exception:
        pass
    return creds


def _save_device_credentials(agent_id: int, device_secret: str) -> None:
    """持久化设备凭据到 runtime 目录（仅服务进程本地）"""
    try:
        _AGENT_CREDENTIALS_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        _AGENT_CREDENTIALS_CACHE_PATH.write_text(
            json.dumps({"agent_id": agent_id, "device_secret": device_secret}, ensure_ascii=False),
            encoding="utf-8",
        )
    except Exception as exc:
        print(f"[Auth] 设备凭据保存失败: {exc}")


def _device_credentials() -> dict:
    if getattr(_device_credentials, "_cache", None) is None:
        _device_credentials._cache = _load_device_credentials()  # type: ignore[attr-defined]
    return _device_credentials._cache  # type: ignore[attr-defined]

_UPGRADE_STATE = {"in_progress": False}

# 统一 server_url 为顶层配置
if "server_url" in _user_config:
    SOFTWARE_CONFIG["server_url"] = _user_config["server_url"].replace(":8080", ":8081")

CONFIG, SOFTWARE_CONFIG = _apply_env_overrides(CONFIG, SOFTWARE_CONFIG)

# =============================================================================
# 全局状态
# =============================================================================

_AGENT_STATE: dict[str, Any] = {
    "asset_id": None,
    "hostname": socket.gethostname(),
    "main_ip": None,
    "main_mac": None,
    "running": False,
    "threads": {},
}

_LOCK = threading.Lock()


def _agent_headers() -> dict:
    # 一机一密：已注册设备凭据则用 zv1 token，否则退回全局 token
    creds = _device_credentials()
    agent_id = creds.get("agent_id")
    device_secret = creds.get("device_secret")
    if agent_id and device_secret:
        authorization = f"Bearer zv1:{int(agent_id)}:{device_secret}"
    else:
        authorization = f"Bearer {CONFIG.get('token', '')}"
    return {
        "Content-Type": "application/json",
        "Authorization": authorization,
    }


# =============================================================================
# P0-02：Agent → 平台 TLS（自动协商 + CA 校验）
# 平台 8443 端口提供 TLS 监听（证书 SAN 含平台 IP）；Agent 先试 https://host:8443，
# 成功后缓存 24h；失败回落 http://host:8080（10 分钟内不重试）。
# CA 校验：runtime/ca-bundle.pem（心跳响应下发）或安装目录 ca-bundle.pem。
# =============================================================================

_AGENT_ERROR_LOG_PATH = (
    Path(os.environ.get("ProgramData", ".")) / "CMDB-Agent" / "logs" / "agent-error.log"
)
_AGENT_TLS_CACHE_PATH = (
    Path(os.environ.get("ProgramData", ".")) / "CMDB-Agent" / "runtime" / "agent-tls.json"
)


def _ca_bundle_path() -> Optional[str]:
    candidates = []
    env_bundle = get_env("ZVIEW_AGENT_CA_BUNDLE")
    if env_bundle:
        candidates.append(env_bundle)
    try:
        if getattr(sys, "frozen", False):
            candidates.append(str(Path(sys.executable).parent / "ca-bundle.pem"))
    except Exception:
        pass
    candidates.append(str(_AGENT_TLS_CACHE_PATH.parent / "ca-bundle.pem"))
    for candidate in candidates:
        if candidate and os.path.exists(candidate):
            return candidate
    return None


def _log_agent_error(message: str) -> None:
    """P1-03/P0-06：Agent 内部失败详情落盘（stdout 在服务模式不可见）。"""
    try:
        _AGENT_ERROR_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        import datetime as _dt
        with open(_AGENT_ERROR_LOG_PATH, "a", encoding="utf-8") as fh:
            fh.write(f"[{_dt.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {message}\n")
    except Exception:
        pass


def clear_device_credentials() -> None:
    """清除本地设备凭据缓存（zv1 401 时回退全局 token）。"""
    try:
        if _AGENT_TLS_CACHE_PATH.parent.exists():
            cred = _AGENT_TLS_CACHE_PATH.parent / "agent-credentials.json"
            if cred.exists():
                cred.unlink()
    except Exception:
        pass
    try:
        _device_credentials._cache = None  # type: ignore[attr-defined]
    except Exception:
        pass


def _agent_requests_verify():
    bundle = _ca_bundle_path()
    if bundle:
        return bundle
    return False  # 证书未分发时退化为不校验（明文风险与旧版一致）


def _platform_base() -> str:
    """平台基地址：TLS 自动协商。"""
    base = str(CONFIG.get("server_url") or "").rstrip("/")
    if not base.startswith("http://"):
        return base
    parsed = urlparse(base)
    tls_base = f"https://{parsed.hostname}:8443"

    cached = {}
    try:
        if _AGENT_TLS_CACHE_PATH.exists():
            cached = json.loads(_AGENT_TLS_CACHE_PATH.read_text(encoding="utf-8"))
    except Exception:
        cached = {}
    now = time.time()
    if cached.get("tls_ok") and now - float(cached.get("ts", 0)) < 86400:
        return tls_base
    if cached.get("ts") and not cached.get("tls_ok") and now - float(cached.get("ts", 0)) < 600:
        return base

    verify = _agent_requests_verify()
    if not verify:
        print("[TLS] 警告：ca-bundle 未分发，TLS 探测将以不校验模式进行")
    try:
        requests.get(urljoin(tls_base, "/"), headers=_agent_headers(), timeout=5, verify=verify)
        ok = True
    except Exception:
        ok = False
    try:
        _AGENT_TLS_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        _AGENT_TLS_CACHE_PATH.write_text(json.dumps({"tls_ok": ok, "ts": now}), encoding="utf-8")
    except Exception:
        pass
    if ok:
        print(f"[TLS] 平台 TLS 通道已启用: {tls_base} (verify={'ca-bundle' if verify else 'OFF'})")
        return tls_base
    print("[TLS] 平台 TLS 探测失败，继续使用 http")
    return base


def _software_headers() -> dict:
    return {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {SOFTWARE_CONFIG.get('token', '')}",
    }


# =============================================================================
# 网络工具 - 智能网卡选择
# =============================================================================

def _is_virtual_nic(name: str) -> bool:
    virtual_keywords = [
        "vmware", "virtualbox", "hyper-v", "vbox", "virtual", "vpn",
        "tap", "tun", "loopback", "pseudo", "wan miniport",
    ]
    lower = name.lower()
    return any(kw in lower for kw in virtual_keywords)


def _get_default_gateway() -> str | None:
    try:
        gws = psutil.net_if_addrs()
        for name, addrs in gws.items():
            for addr in addrs:
                if addr.family == socket.AF_INET:
                    pass
        # Gateway is optional telemetry. Avoid invoking netsh every heartbeat:
        # it creates a visible console host on some interactive Windows hosts.
    except Exception:
        pass
    return None


def _ip_in_same_subnet(ip: str, gateway: str) -> bool:
    try:
        ip_parts = ip.split(".")
        gw_parts = gateway.split(".")
        return ip_parts[:3] == gw_parts[:3]
    except Exception:
        return False


def get_primary_network_info() -> tuple[str | None, str | None]:
    """返回 (ip_address, mac_address)，智能排除虚拟网卡。"""
    try:
        default_gw = _get_default_gateway()
        interfaces = psutil.net_if_addrs()
        stats = psutil.net_if_stats()

        candidates = []
        for name, addrs in interfaces.items():
            if _is_virtual_nic(name):
                continue
            if name not in stats or not stats[name].isup:
                continue

            ipv4 = None
            mac = None
            for addr in addrs:
                if addr.family == socket.AF_INET and addr.address != "127.0.0.1":
                    ipv4 = addr.address
                elif addr.family == psutil.AF_LINK:
                    mac = addr.address

            if ipv4:
                # 优先匹配网关同网段
                score = 0
                if default_gw and _ip_in_same_subnet(ipv4, default_gw):
                    score += 100
                # 优先有流量的网卡
                if name in stats:
                    score += stats[name].speed or 0
                candidates.append((score, ipv4, mac or ""))

        if candidates:
            candidates.sort(key=lambda x: x[0], reverse=True)
            return candidates[0][1], candidates[0][2]
    except Exception as exc:
        print(f"[Network] 网卡选择失败: {exc}")

    # 兜底：任意非127 IPv4
    try:
        hostname = socket.gethostname()
        ip = socket.getaddrinfo(hostname, None, socket.AF_INET)[0][4][0]
        if ip and ip != "127.0.0.1":
            return ip, ""
    except Exception:
        pass

    return None, None


# =============================================================================
# 硬件信息采集
# =============================================================================

def _wmi_query(wql: str) -> list[dict]:
    """执行 WMI 查询，返回字典列表。"""
    if os.name != "nt":
        return []
    try:
        import win32com.client
        wmi = win32com.client.Dispatch("WbemScripting.SWbemLocator")
        svc = wmi.ConnectServer(".", "root\\cimv2")
        results = []
        for item in svc.ExecQuery(wql):
            row = {}
            for prop in item.Properties_:
                try:
                    val = prop.Value
                    row[prop.Name] = val
                except Exception:
                    row[prop.Name] = None
            results.append(row)
        return results
    except Exception as exc:
        print(f"[WMI] 查询失败 {wql[:60]}: {exc}")
        return []


def collect_hardware_info() -> dict:
    info = {
        "os_info": platform.platform(),
        "os_version": platform.version(),
        "architecture": platform.machine(),
        "hostname": socket.gethostname(),
        "manufacturer": "",
        "model": "",
        "serial_number": "",
        "cpu_info": "",
        "memory_total_mb": 0,
        "disk_info": [],
    }

    # CPU
    try:
        info["cpu_info"] = platform.processor() or ""
        if not info["cpu_info"]:
            cpu = psutil.cpu_freq()
            info["cpu_info"] = f"{psutil.cpu_count(logical=False)}C/{psutil.cpu_count(logical=True)}T"
    except Exception:
        pass

    # 内存
    try:
        mem = psutil.virtual_memory()
        info["memory_total_mb"] = round(mem.total / (1024 * 1024), 2)
    except Exception:
        pass

    # 磁盘
    try:
        for part in psutil.disk_partitions(all=False):
            try:
                usage = psutil.disk_usage(part.mountpoint)
                info["disk_info"].append({
                    "device": part.device,
                    "mountpoint": part.mountpoint,
                    "fstype": part.fstype,
                    "total_mb": round(usage.total / (1024 * 1024), 2),
                    "used_mb": round(usage.used / (1024 * 1024), 2),
                    "free_mb": round(usage.free / (1024 * 1024), 2),
                    "percent": usage.percent,
                })
            except Exception:
                pass
    except Exception:
        pass

    # WMI 详细信息
    if os.name == "nt":
        try:
            cs = _wmi_query("SELECT Manufacturer, Model, SerialNumber FROM Win32_ComputerSystem")
            if cs:
                info["manufacturer"] = str(cs[0].get("Manufacturer") or "")
                info["model"] = str(cs[0].get("Model") or "")
            bios = _wmi_query("SELECT SerialNumber FROM Win32_BIOS")
            if bios:
                info["serial_number"] = str(bios[0].get("SerialNumber") or "")
        except Exception:
            pass

    return info


def collect_system_status() -> dict:
    status = {
        "cpu_percent": 0.0,
        "memory_percent": 0.0,
        "memory_used_mb": 0.0,
        "memory_total_mb": 0.0,
        "disk_percent": 0.0,
        "timestamp": datetime.now().isoformat(),
    }
    try:
        status["cpu_percent"] = psutil.cpu_percent(interval=1)
    except Exception:
        pass
    try:
        mem = psutil.virtual_memory()
        status["memory_percent"] = mem.percent
        status["memory_used_mb"] = round(mem.used / (1024 * 1024), 2)
        status["memory_total_mb"] = round(mem.total / (1024 * 1024), 2)
    except Exception:
        pass
    try:
        # 取系统盘使用率
        for part in psutil.disk_partitions(all=False):
            if os.name == "nt" and part.device.lower().startswith("c:"):
                usage = psutil.disk_usage(part.mountpoint)
                status["disk_percent"] = usage.percent
                break
    except Exception:
        pass
    return status


# =============================================================================
# 软件清单采集
# =============================================================================

def _collect_software_from_registry() -> list[dict]:
    software = []
    if os.name != "nt":
        return software

    registry_paths = [
        (r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall", False),
        (r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall", False),
    ]

    try:
        import winreg
        for path, _ in registry_paths:
            try:
                with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, path) as key:
                    for i in range(winreg.QueryInfoKey(key)[0]):
                        try:
                            subkey_name = winreg.EnumKey(key, i)
                            with winreg.OpenKey(key, subkey_name) as subkey:
                                def _read(name: str) -> str:
                                    try:
                                        val, _ = winreg.QueryValueEx(subkey, name)
                                        return str(val) if val else ""
                                    except Exception:
                                        return ""

                                name = _read("DisplayName")
                                if not name:
                                    continue

                                version = _read("DisplayVersion")
                                publisher = _read("Publisher")
                                install_date = _read("InstallDate")
                                install_location = _read("InstallLocation")
                                size_kb = _read("EstimatedSize")
                                try:
                                    size_mb = round(int(size_kb) / 1024, 2) if size_kb else None
                                except Exception:
                                    size_mb = None

                                software.append({
                                    "name": name,
                                    "version": version,
                                    "publisher": publisher,
                                    "install_date": install_date,
                                    "install_location": install_location,
                                    "size_mb": size_mb,
                                    "source": "registry",
                                })
                        except Exception:
                            pass
            except Exception:
                pass
    except Exception as exc:
        print(f"[Software] 注册表读取失败: {exc}")

    return software


def collect_software_list() -> list[dict]:
    return _collect_software_from_registry()


# =============================================================================
# 平台策略同步（心跳自动下发，管理台可配置）
# =============================================================================

_AGENT_POLICIES_CACHE_PATH = (
    Path(os.environ.get("ProgramData", ".")) / "CMDB-Agent" / "runtime" / "agent-policies.json"
)


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


def _apply_agent_policies(policies: Any, persist: bool = True) -> dict | None:
    """将平台下发的策略合并进 CONFIG 并热更新消费方。

    返回实际生效的增量；无有效变更时返回 None。
    """
    if not isinstance(policies, dict) or not policies:
        return None

    applied: dict = {}

    intervals_in = policies.get("intervals")
    if isinstance(intervals_in, dict):
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

    remote_in = policies.get("remote_desktop")
    if isinstance(remote_in, dict):
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

    if persist and applied:
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

    return applied or None


def _load_cached_agent_policies() -> None:
    """进程启动或远控会话创建前，从本地缓存恢复最近一次平台策略。"""
    try:
        if not _AGENT_POLICIES_CACHE_PATH.exists():
            return
        data = json.loads(_AGENT_POLICIES_CACHE_PATH.read_text(encoding="utf-8"))
        _apply_agent_policies(data, persist=False)
    except Exception:
        pass


def _current_interval(name: str, default: int) -> int:
    try:
        value = int(CONFIG.get("intervals", {}).get(name, default))
    except (TypeError, ValueError):
        return default
    return max(5, min(value, 604800))


# =============================================================================
# 资产注册 / 获取 asset_id
# =============================================================================

def get_asset_id_from_server() -> int | None:
    ip, mac = get_primary_network_info()
    _AGENT_STATE["main_ip"] = ip
    _AGENT_STATE["main_mac"] = mac

    hardware = collect_hardware_info()
    payload = {
        "hostname": _AGENT_STATE["hostname"],
        "ip_address": ip,
        "mac_address": mac,
        "os_info": hardware.get("os_info", ""),
        "manufacturer": hardware.get("manufacturer", ""),
        "model": hardware.get("model", ""),
        "serial_number": hardware.get("serial_number", ""),
        "cpu_info": hardware.get("cpu_info", ""),
        "memory_total_mb": hardware.get("memory_total_mb", 0),
        "status": "online",
        "agent_version": AGENT_VERSION,
        "agent_install_status": "installed",
    }

    try:
        url = urljoin(_platform_base(), "/api/v1/agent/heartbeat")
        resp = requests.post(url, json=payload, headers=_agent_headers(), timeout=30, verify=_agent_requests_verify())
        data = resp.json()
        if resp.status_code in (200, 201) and isinstance(data, dict):
            asset_id = data.get("asset_id") or data.get("id")
            if asset_id:
                _AGENT_STATE["asset_id"] = int(asset_id)
                print(f"[Agent] Asset ID: {_AGENT_STATE['asset_id']}")
                return _AGENT_STATE["asset_id"]
        # P1-03：非 200 详情落盘 + 401 凭据失效自动回退全局 token 重试
        detail = f"register HTTP {resp.status_code}: {resp.text[:200]}"
        _log_agent_error(detail)
        if resp.status_code == 401 and _device_credentials().get("device_secret"):
            print("[Auth] zv1 credential rejected (401); clearing and retrying with global token")
            clear_device_credentials()
            resp = requests.post(url, json=payload, headers=_agent_headers(), timeout=30, verify=_agent_requests_verify())
            data = resp.json()
            if resp.status_code in (200, 201) and isinstance(data, dict):
                asset_id = data.get("asset_id") or data.get("id")
                # P1-03/1.7.3：全局重试时平台会重发设备凭据（allow_rotate），保存以恢复 zv1 通道
                credential_info = data.get("agent_credential")
                if isinstance(credential_info, dict) and credential_info.get("agent_id") and credential_info.get("device_secret"):
                    try:
                        _save_device_credentials(
                            int(credential_info["agent_id"]),
                            str(credential_info["device_secret"]),
                        )
                        _device_credentials._cache = {  # type: ignore[attr-defined]
                            "agent_id": int(credential_info["agent_id"]),
                            "device_secret": str(credential_info["device_secret"]),
                        }
                        print(f"[Auth] credential re-issued and enabled (agent_id={credential_info['agent_id']})")
                    except Exception as exc:
                        print(f"[Auth] credential re-save failed: {exc}")
                if asset_id:
                    _AGENT_STATE["asset_id"] = int(asset_id)
                    print(f"[Agent] Asset ID (global retry): {_AGENT_STATE['asset_id']}")
                    return _AGENT_STATE["asset_id"]
            _log_agent_error(f"global token retry HTTP {resp.status_code}")
    except Exception as exc:
        detail = f"register exception {type(exc).__name__}: {exc}"
        print(f"[Agent] 获取 asset_id 失败: {exc}")
        _log_agent_error(detail + "\n" + traceback.format_exc()[-800:])

    return None


# =============================================================================
# 心跳上报线程
# =============================================================================



# ============================================================
# Agent 自动升级（R13）+ 升级状态机（P0-06）
# 状态：CHECK → DOWNLOAD → VERIFY_HASH → VERIFY_SIGNATURE → BACKUP
#       → INSTALL(bat) → START → HEALTH_CHECK → COMMIT
# 任一步失败 → ROLLBACK（bat 内自动执行并写结果标记）→ FAILED
# 状态持久化：ProgramData\CMDB-Agent\runtime\upgrade-state.json（worker 侧）
#             upgrade-bat-result.txt（bat 终态标记，新进程启动时消费）
# ============================================================

_UPGRADE_STATE_PATH = (
    Path(os.environ.get("ProgramData", ".")) / "CMDB-Agent" / "runtime" / "upgrade-state.json"
)
_UPGRADE_BAT_RESULT_PATH = (
    Path(os.environ.get("ProgramData", ".")) / "CMDB-Agent" / "runtime" / "upgrade-bat-result.txt"
)


def _upgrade_state_write(stage: str, **extra: Any) -> None:
    """持久化升级状态机当前阶段。"""
    try:
        _UPGRADE_STATE["stage"] = stage
        state = {}
        if _UPGRADE_STATE_PATH.exists():
            try:
                state = json.loads(_UPGRADE_STATE_PATH.read_text(encoding="utf-8"))
            except Exception:
                state = {}
        state.update({"stage": stage, "updated_at": time.strftime("%Y-%m-%d %H:%M:%S")})
        # V1.7.0：服务端事务 ID 一经写入随状态文件贯穿全部阶段（updater/安装器 merge 继承）
        if _UPGRADE_STATE.get("server_upgrade_id"):
            state.setdefault("server_upgrade_id", _UPGRADE_STATE["server_upgrade_id"])
        state.update(extra)
        _UPGRADE_STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
        _UPGRADE_STATE_PATH.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as exc:
        print(f"[Upgrade] state write failed: {exc}")


# V1.5 升级风暴修复：失败台账（指数退避）+ 跨重启升级锁。
# 2026-09-09 事故：下载失败 → 内存 in_progress 复位 → 每 30s 重试 → 1.5h 内
# 泄漏 44GB 临时文件撑满 C 盘。台账跨重启计数，失败达到阈值后指数退避，
# 从机制上禁止"无限重试"。
_UPGRADE_LEDGER_PATH = (
    Path(os.environ.get("ProgramData", ".")) / "CMDB-Agent" / "runtime" / "upgrade-attempts.json"
)
_UPGRADE_LOCK_PATH = (
    Path(os.environ.get("ProgramData", ".")) / "CMDB-Agent" / "runtime" / "upgrade.lock"
)
_UPGRADE_MAX_ATTEMPTS = 5
_UPGRADE_BACKOFF_BASE_SECONDS = 1800        # 第 5 次失败起退避 30min
_UPGRADE_BACKOFF_MAX_SECONDS = 86400        # 退避上限 24h
_UPGRADE_LOCK_STALE_SECONDS = 1800          # 锁 30min 未释放视为崩溃残留，可接管


def _upgrade_ledger_read() -> dict:
    try:
        data = json.loads(_UPGRADE_LEDGER_PATH.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _upgrade_ledger_write(ledger: dict) -> None:
    try:
        _UPGRADE_LEDGER_PATH.parent.mkdir(parents=True, exist_ok=True)
        _UPGRADE_LEDGER_PATH.write_text(json.dumps(ledger, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as exc:
        print(f"[Upgrade] ledger write failed: {exc}")


def _record_upgrade_failure(target_version: str, reason: str) -> float:
    """记录一次失败尝试（24h 滚动窗口），返回下次允许重试的时间戳（0 = 立即）。"""
    ledger = _upgrade_ledger_read()
    entry = ledger.get(target_version) or {}
    attempts = int(entry.get("attempts") or 0)
    window_started_at = float(entry.get("window_started_at") or 0)
    if time.time() - window_started_at > 86400:
        attempts = 0
        window_started_at = time.time()
    attempts += 1
    entry.update({
        "attempts": attempts,
        "window_started_at": window_started_at,
        "last_error": str(reason)[:300],
        "last_attempt_at": time.time(),
    })
    next_retry_at = 0.0
    if attempts >= _UPGRADE_MAX_ATTEMPTS:
        backoff = min(
            _UPGRADE_BACKOFF_BASE_SECONDS * (2 ** (attempts - _UPGRADE_MAX_ATTEMPTS)),
            _UPGRADE_BACKOFF_MAX_SECONDS,
        )
        next_retry_at = time.time() + backoff
        entry["next_retry_at"] = next_retry_at
        entry["backoff_seconds"] = backoff
        print(f"[Upgrade] target {target_version} failed {attempts} times; "
              f"backoff {int(backoff)}s")
    ledger[target_version] = entry
    _upgrade_ledger_write(ledger)
    return next_retry_at


def _clear_upgrade_failure(target_version: str) -> None:
    """升级成功（COMMIT）后清除该版本的失败台账。"""
    ledger = _upgrade_ledger_read()
    if target_version in ledger:
        ledger.pop(target_version, None)
        _upgrade_ledger_write(ledger)


def _upgrade_backoff_remaining(target_version: str) -> float:
    entry = _upgrade_ledger_read().get(target_version) or {}
    return max(0.0, float(entry.get("next_retry_at") or 0) - time.time())


def _acquire_upgrade_lock(upgrade_id: str) -> bool:
    """跨重启升级锁（EXCL 创建）；存在但超过陈旧阈值视为崩溃残留，允许接管。"""
    try:
        if _UPGRADE_LOCK_PATH.exists():
            payload = {}
            try:
                payload = json.loads(_UPGRADE_LOCK_PATH.read_text(encoding="utf-8"))
            except Exception:
                payload = {}
            if time.time() - float(payload.get("started_at") or 0) < _UPGRADE_LOCK_STALE_SECONDS:
                return False
            print("[Upgrade] stale upgrade lock detected; taking over")
            _UPGRADE_LOCK_PATH.unlink(missing_ok=True)
        _UPGRADE_LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(str(_UPGRADE_LOCK_PATH), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(json.dumps({"pid": os.getpid(), "upgrade_id": upgrade_id,
                                 "started_at": time.time()}))
        return True
    except FileExistsError:
        return False
    except Exception as exc:
        print(f"[Upgrade] lock acquire error: {exc}")
        return False


def _release_upgrade_lock() -> None:
    try:
        _UPGRADE_LOCK_PATH.unlink(missing_ok=True)
    except Exception:
        pass


def _verify_authenticode_signature(file_path: str) -> tuple[bool, str, str]:
    """升级前 Authenticode 校验（P0-05/P0-06 联动）。

    实现：PowerShell Get-AuthenticodeSignature（本机实测可靠；ctypes 直连
    WinVerifyTrust 在部分环境返回 0x57 参数错误，故弃用）。每次升级仅调用一次。

    返回 (trusted, reason, availability)：
    - valid（Valid）→ 继续升级
    - invalid（NotSigned / HashMismatch，篡改证据）→ 终止升级
    - unavailable（NotTrusted 证书未铺 / UnknownError 环境异常）→ 告警继续
    """
    try:
        literal = str(file_path).replace("'", "''")
        script = (
            "$s = Get-AuthenticodeSignature -LiteralPath '" + literal + "'; "
            "Write-Output $s.Status; "
            "if ($s.SignerCertificate) { Write-Output $s.SignerCertificate.Subject }"
        )
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        proc = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
            capture_output=True, text=True, timeout=90, creationflags=flags,
        )
        lines = [line.strip() for line in (proc.stdout or "").splitlines() if line.strip()]
        status = lines[0] if lines else "UnknownError"
        subject = lines[1] if len(lines) > 1 else ""
        if status == "Valid":
            return True, f"signature valid: {subject}", "valid"
        if status in ("NotSigned", "HashMismatch"):
            return False, status, "invalid"
        return False, f"status={status}", "unavailable"
    except Exception as exc:
        return False, f"verify unavailable: {exc}", "unavailable"


def _migrate_to_onedir(new_version: str, expected_sha256: str, upgrade_id: str, from_version: str) -> None:
    """V1.6.0 存量终端一次性迁移：旧单文件布局 → onedir versions/current 新布局。

    下载 onedir-zip → SHA256 → 解包 temp → 校验内层 exe 签名 →
    分离进程运行新布局安装器（--install --migrate-from/--migrate-to），
    安装器负责停旧服务、更新 binPath 到 current\\Z-View.exe 并重启。
    """
    import hashlib
    import shutil
    import tempfile
    import zipfile

    log_tag = "[Upgrade]"
    platform_base = _platform_base().rstrip("/")
    url = f"{platform_base}/api/v1/agent/upgrade/download?version={new_version}"
    extract_root = Path(tempfile.gettempdir()) / f"zv-migrate-{new_version}"
    try:
        _upgrade_state_write("DOWNLOAD", upgrade_id=upgrade_id, to_version=new_version)
        zip_path = extract_root.parent / f"Z-View-{new_version}-onedir.zip"
        headers = _agent_headers()
        resp = requests.get(url, headers=headers, timeout=900, stream=True, verify=_agent_requests_verify())
        if resp.status_code != 200:
            raise RuntimeError(f"download http {resp.status_code}")
        with open(zip_path, "wb") as fh:
            for chunk in resp.iter_content(1024 * 256):
                fh.write(chunk)

        _upgrade_state_write("VERIFYING", upgrade_id=upgrade_id, to_version=new_version)
        digest = hashlib.sha256(zip_path.read_bytes()).hexdigest()
        if digest.lower() != expected_sha256.lower():
            raise RuntimeError("sha256 mismatch")
        if extract_root.exists():
            shutil.rmtree(extract_root, ignore_errors=True)
        extract_root.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(zip_path) as zf:
            zf.extractall(extract_root)
        new_exe = extract_root / "Z-View.exe"
        if not new_exe.exists():
            for sub in extract_root.iterdir():
                if sub.is_dir() and (sub / "Z-View.exe").exists():
                    extract_root = sub
                    new_exe = sub / "Z-View.exe"
                    break
        if not new_exe.exists():
            raise RuntimeError("package missing Z-View.exe")

        trusted, reason, availability = _verify_authenticode_signature(str(new_exe))
        if availability == "invalid":
            raise RuntimeError(f"signature invalid: {reason}")
        if availability == "unavailable":
            print(f"{log_tag} 签名校验基础设施不可用（{reason}），继续迁移")

        _upgrade_state_write("MIGRATING", upgrade_id=upgrade_id,
                             from_version=from_version, to_version=new_version)
        flags = subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP
        subprocess.Popen([
            str(new_exe), "--install", "--quiet",
            "--server-url", platform_base,
            "--migrate-from", from_version,
            "--migrate-to", new_version,
        ], creationflags=flags, close_fds=True, cwd=str(extract_root))
        print(f"{log_tag} migration installer launched; service will switch to current\\Z-View.exe")
    except Exception as exc:
        print(f"{log_tag} migration failed: {exc}")
        _record_upgrade_failure(new_version, f"migration: {exc}")
        _upgrade_state_write("FAILED", failure_reason=f"migration: {exc}")


def perform_self_upgrade(new_version: str, expected_sha256: str) -> None:
    """升级状态机（P0-06）：

    CHECK → DOWNLOAD → VERIFY_HASH → VERIFY_SIGNATURE → BACKUP → INSTALL(bat)。
    bat 负责 INSTALL 之后的部分：START → HEALTH_CHECK → COMMIT / ROLLBACK / FAILED，
    终态写入 upgrade-bat-result.txt，由升级后的新进程启动时消费。
    失败自动回滚；服务起不来时尝试自愈重建服务注册（360 事件教训）。
    """
    import hashlib
    import subprocess
    import tempfile

    log_tag = "[Upgrade]"
    from_version = AGENT_VERSION
    upgrade_id = f"{from_version}->{new_version}@{time.strftime('%Y%m%d%H%M%S')}"
    _UPGRADE_STATE["upgrade_id"] = upgrade_id
    _UPGRADE_STATE["to_version"] = new_version
    try:
        _UPGRADE_STATE["in_progress"] = True
        _upgrade_state_write("CHECK", upgrade_id=upgrade_id,
                             from_version=from_version, to_version=new_version)
        print(f"{log_tag} [{upgrade_id}] 开始升级")

        # V1.6.0 新布局（versions + current junction）：委托独立 ZViewUpdater 执行
        # 下载/校验/staging/切换/健康检查/回滚，Agent 不再自己替换自己。
        package_type = str(_UPGRADE_STATE.get("package_type") or "")
        try:
            import cmdb_agent_layout as _layout
        except Exception:
            _layout = None
        if _layout is not None and _layout.is_v2_layout():
            updater_exe = _layout.UPDATER_DIR / "ZViewUpdater.exe"
            if not updater_exe.exists():
                _upgrade_state_write("FAILED", failure_reason="updater missing for v2 layout")
                _UPGRADE_STATE["in_progress"] = False
                return
            task = {
                "server_url": _platform_base(),
                "token": str(CONFIG.get("token") or ""),
                "target_version": new_version,
                "sha256": expected_sha256,
                "from_version": from_version,
                "upgrade_id": upgrade_id,
            }
            _layout.UPGRADE_TASK_FILE.parent.mkdir(parents=True, exist_ok=True)
            _layout.UPGRADE_TASK_FILE.write_text(
                json.dumps(task, ensure_ascii=False, indent=2), encoding="utf-8")
            flags = subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP
            subprocess.Popen([str(updater_exe)], creationflags=flags, close_fds=True)
            print(f"{log_tag} delegated to ZViewUpdater; state tracked in upgrade-state.json")
            _UPGRADE_STATE["in_progress"] = False
            return

        # V1.6.0 迁移：旧单文件布局收到 onedir-zip 包 → 一次性迁移到新布局
        if _layout is not None and package_type == "onedir-zip":
            _migrate_to_onedir(new_version, expected_sha256, upgrade_id, from_version)
            _UPGRADE_STATE["in_progress"] = False
            return

        if not _acquire_upgrade_lock(upgrade_id):
            print(f"{log_tag} 另一个升级持有锁，跳过本次触发")
            _UPGRADE_STATE["in_progress"] = False
            return

        base = _platform_base().rstrip("/")
        headers = _agent_headers()
        url = f"{base}/api/v1/agent/upgrade/download?version={new_version}"

        # DOWNLOAD
        _upgrade_state_write("DOWNLOAD", upgrade_id=upgrade_id)
        resp = requests.get(url, headers=headers, timeout=300, stream=True, verify=_agent_requests_verify())
        if resp.status_code != 200:
            print(f"{log_tag} 下载失败 HTTP {resp.status_code}")
            _record_upgrade_failure(new_version, f"download http {resp.status_code}")
            _release_upgrade_lock()
            _upgrade_state_write("FAILED", failure_reason=f"download http {resp.status_code}")
            _UPGRADE_STATE["in_progress"] = False
            return
        new_exe = os.path.join(tempfile.gettempdir(), f"Z-View-{new_version}.exe")
        with open(new_exe, "wb") as fh:
            for chunk in resp.iter_content(1024 * 256):
                fh.write(chunk)

        # VERIFY_HASH
        _upgrade_state_write("VERIFY_HASH", upgrade_id=upgrade_id)
        digest = hashlib.sha256(open(new_exe, "rb").read()).hexdigest()
        if digest.lower() != expected_sha256.lower():
            print(f"{log_tag} SHA256 校验失败: {digest} != {expected_sha256}")
            os.remove(new_exe)
            _record_upgrade_failure(new_version, "sha256 mismatch")
            _release_upgrade_lock()
            _upgrade_state_write("FAILED", failure_reason="sha256 mismatch")
            _UPGRADE_STATE["in_progress"] = False
            return

        # VERIFY_SIGNATURE：确证签名无效 → 终止；基础设施不可用 → 告警继续
        _upgrade_state_write("VERIFY_SIGNATURE", upgrade_id=upgrade_id)
        trusted, reason, availability = _verify_authenticode_signature(new_exe)
        if availability == "invalid":
            print(f"{log_tag} 签名校验失败，终止升级: {reason}")
            os.remove(new_exe)
            _record_upgrade_failure(new_version, f"signature invalid: {reason}")
            _release_upgrade_lock()
            _upgrade_state_write("FAILED", failure_reason=f"signature invalid: {reason}")
            _UPGRADE_STATE["in_progress"] = False
            return
        if availability == "unavailable":
            print(f"{log_tag} 警告：签名校验基础设施不可用（{reason}），继续升级")

        # BACKUP
        _upgrade_state_write("BACKUP", upgrade_id=upgrade_id)
        target_exe = os.path.abspath(sys.argv[0]) if sys.argv and sys.argv[0] else r"C:\Program Files\CMDB-Agent\Z-View.exe"
        backup_exe = target_exe + ".upgrade-old"
        try:
            if os.path.exists(target_exe):
                if os.path.exists(backup_exe):
                    os.remove(backup_exe)
                import shutil
                shutil.copy2(target_exe, backup_exe)
        except Exception as exc:
            print(f"{log_tag} 备份失败（继续）: {exc}")

        # INSTALL： bat 负责 net stop → 替换 → net start → 健康检查 → COMMIT/ROLLBACK
        _upgrade_state_write("INSTALL", upgrade_id=upgrade_id)
        service_name = "CMDB-Agent"
        service_bin = f'"{target_exe}" --service-host'
        bat_path = os.path.join(tempfile.gettempdir(), "zv-agent-upgrade.bat")
        marker = str(_UPGRADE_BAT_RESULT_PATH)
        with open(bat_path, "w", encoding="gbk", errors="replace") as fh:
            fh.write("@echo off\r\n")
            fh.write("timeout /t 3 /nobreak >nul\r\n")
            fh.write(f"net stop {service_name} >nul 2>&1\r\n")
            fh.write("timeout /t 2 /nobreak >nul\r\n")
            fh.write("taskkill /F /IM Z-View.exe >nul 2>&1\r\n")
            fh.write("timeout /t 2 /nobreak >nul\r\n")
            fh.write(f'copy /Y "{new_exe}" "{target_exe}" >nul 2>&1\r\n')
            fh.write("if errorlevel 1 goto rollback\r\n")
            fh.write(f"net start {service_name} >nul 2>&1\r\n")
            # HEALTH_CHECK：轮询服务 RUNNING 最多 60s
            fh.write("set /a ZV_TRIES=0\r\n")
            fh.write(":waitstart\r\n")
            fh.write(f'sc query {service_name} | find "RUNNING" >nul 2>&1\r\n')
            fh.write("if not errorlevel 1 goto commit\r\n")
            fh.write("set /a ZV_TRIES+=1\r\n")
            fh.write("if %ZV_TRIES% geq 12 goto starthealthfail\r\n")
            fh.write("timeout /t 5 /nobreak >nul\r\n")
            fh.write("goto waitstart\r\n")
            fh.write(":commit\r\n")
            fh.write(f'echo COMMIT> "{marker}"\r\n')
            fh.write(f'del "{_UPGRADE_LOCK_PATH}" >nul 2>&1\r\n')
            fh.write(f'del "{new_exe}" >nul 2>&1\r\n')
            fh.write('del "%~f0" >nul 2>&1\r\n')
            fh.write("exit /b 0\r\n")
            # ROLLBACK：先回滚旧版，再救服务
            fh.write(":rollback\r\n")
            fh.write(f'echo ROLLBACK_OK> "{marker}"\r\n')
            fh.write(f'del "{_UPGRADE_LOCK_PATH}" >nul 2>&1\r\n')
            fh.write(f'copy /Y "{backup_exe}" "{target_exe}" >nul 2>&1\r\n')
            fh.write(f"net start {service_name} >nul 2>&1\r\n")
            fh.write("set /a ZV_TRIES2=0\r\n")
            fh.write(":waitstart2\r\n")
            fh.write(f'sc query {service_name} | find "RUNNING" >nul 2>&1\r\n')
            fh.write("if not errorlevel 1 goto done\r\n")
            fh.write("set /a ZV_TRIES2+=1\r\n")
            fh.write("if %ZV_TRIES2% geq 6 goto serviceheal\r\n")
            fh.write("timeout /t 5 /nobreak >nul\r\n")
            fh.write("goto waitstart2\r\n")
            # 自愈：服务注册被破坏时重建（360 事件教训）
            fh.write(":serviceheal\r\n")
            fh.write(f'sc delete {service_name} >nul 2>&1\r\n')
            fh.write(f'sc create {service_name} binPath= "{service_bin}" start= auto obj= LocalSystem >nul 2>&1\r\n')
            fh.write(f"net start {service_name} >nul 2>&1\r\n")
            fh.write(":done\r\n")
            fh.write('del "%~f0" >nul 2>&1\r\n')
            fh.write("exit /b 1\r\n")

        flags = subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS
        subprocess.Popen(
            ["cmd", "/c", bat_path],
            creationflags=flags,
            close_fds=True,
        )
        print(f"{log_tag} 升级脚本已启动，本进程即将退出，等待服务自动恢复为新版本 {new_version}")
        time.sleep(1.5)
        os._exit(0)
    except Exception as exc:
        print(f"{log_tag} 升级失败: {exc}")
        _record_upgrade_failure(new_version, str(exc))
        _release_upgrade_lock()
        _upgrade_state_write("FAILED", failure_reason=str(exc))
        _UPGRADE_STATE["in_progress"] = False


def _consume_upgrade_result_after_start() -> None:
    """升级后新进程启动时消费 bat 终态标记（P0-06 健康检查闭环）。"""
    try:
        if not _UPGRADE_BAT_RESULT_PATH.exists():
            return
        marker = _UPGRADE_BAT_RESULT_PATH.read_text(encoding="utf-8", errors="replace").strip().splitlines()
        result = marker[0].strip() if marker else ""
        state = {}
        if _UPGRADE_STATE_PATH.exists():
            try:
                state = json.loads(_UPGRADE_STATE_PATH.read_text(encoding="utf-8"))
            except Exception:
                state = {}
        upgrade_id = state.get("upgrade_id", "unknown")
        to_version = str(state.get("to_version") or "")
        if result == "COMMIT":
            print(f"[Upgrade] 升级成功 (COMMIT): {upgrade_id}")
            state["stage"] = "COMMIT"
            _UPGRADE_STATE_PATH.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
            if to_version:
                _clear_upgrade_failure(to_version)
        elif result.startswith("ROLLBACK"):
            print(f"[Upgrade] 升级失败已回滚 ({result}): {upgrade_id}")
            state["stage"] = "ROLLBACK"
            state["failure_reason"] = "service health check failed after install"
            _UPGRADE_STATE_PATH.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
            if to_version:
                _record_upgrade_failure(to_version, "rollback after install")
        else:
            print(f"[Upgrade] 升级终态未知 ({result or 'empty'}): {upgrade_id}")
        _release_upgrade_lock()
        _UPGRADE_BAT_RESULT_PATH.unlink(missing_ok=True)
    except Exception as exc:
        print(f"[Upgrade] consume upgrade result failed: {exc}")


def _get_last_upgrade_state() -> Optional[Dict[str, Any]]:
    """心跳上报用：读取最近一次升级状态（存在且为终态时）。"""
    try:
        if _UPGRADE_STATE_PATH.exists():
            state = json.loads(_UPGRADE_STATE_PATH.read_text(encoding="utf-8"))
            if state.get("stage") in ("COMMIT", "ROLLBACK", "FAILED"):
                return state
    except Exception:
        pass
    return None



def _check_heartbeat_self_heal(consecutive_failures: int) -> None:
    """P0-06：心跳连续失败达到阈值时退出进程，由服务重启 worker（自愈）。"""
    if consecutive_failures >= 10:
        print(f"[SelfHeal] heartbeat failed {consecutive_failures} consecutive times; exiting for worker restart")
        os._exit(1)


def _heartbeat_loop():
    consecutive_failures = 0  # P0-06：连续失败自愈计数
    while _AGENT_STATE["running"]:
        try:
            asset_id = _AGENT_STATE.get("asset_id")
            if not asset_id:
                # 尝试重新注册
                asset_id = get_asset_id_from_server()
                if not asset_id:
                    consecutive_failures += 1
                    _check_heartbeat_self_heal(consecutive_failures)
                    time.sleep(_current_interval("heartbeat", 30))
                    continue

            ip, mac = get_primary_network_info()
            status = collect_system_status()
            payload = {
                "asset_id": asset_id,
                "hostname": _AGENT_STATE["hostname"],
                "ip_address": ip,
                "mac_address": mac,
                "cpu_usage": status.get("cpu_percent", 0),
                "memory_usage": status.get("memory_percent", 0),
                "disk_usage": status.get("disk_percent", 0),
                "process_count": len(psutil.pids()),
                "logged_users": os.getlogin() if hasattr(os, "getlogin") else "",
                "status": "online",
                "agent_version": AGENT_VERSION,
            }
            # P0-06 + V1.7.0：升级状态随心跳上报 —— 进行中报实时阶段，空闲报最近终态
            if _UPGRADE_STATE.get("in_progress"):
                payload["agent_upgrade_state"] = {
                    "stage": _UPGRADE_STATE.get("stage") or "RUNNING",
                    "server_upgrade_id": _UPGRADE_STATE.get("server_upgrade_id"),
                    "upgrade_id": _UPGRADE_STATE.get("upgrade_id"),
                    "from_version": AGENT_VERSION,
                    "to_version": _UPGRADE_STATE.get("to_version"),
                }
            else:
                last_upgrade_state = _get_last_upgrade_state()
                if last_upgrade_state:
                    payload["agent_upgrade_state"] = last_upgrade_state

            url = urljoin(_platform_base(), "/api/v1/agent/heartbeat")
            resp = requests.post(url, json=payload, headers=_agent_headers(), timeout=30, verify=_agent_requests_verify())
            if resp.status_code in (200, 201):
                applied = None
                try:
                    body = resp.json()
                except Exception:
                    body = None
                if isinstance(body, dict):
                    applied = _apply_agent_policies(body.get("policies"))
                    # 一机一密（P0-01）：保存平台签发的设备凭据，后续请求改用 zv1 token
                    credential_info = body.get("agent_credential")
                    if isinstance(credential_info, dict) and credential_info.get("agent_id") and credential_info.get("device_secret"):
                        try:
                            _save_device_credentials(
                                int(credential_info["agent_id"]),
                                str(credential_info["device_secret"]),
                            )
                            _device_credentials._cache = {  # type: ignore[attr-defined]
                                "agent_id": int(credential_info["agent_id"]),
                                "device_secret": str(credential_info["device_secret"]),
                            }
                            print(f"[Auth] 设备凭据已签发并启用 (agent_id={credential_info['agent_id']})")
                        except Exception as exc:
                            print(f"[Auth] 设备凭据启用失败: {exc}")
                    # P0-02：保存平台证书公钥（ca-bundle），TLS 校验用
                    ca_pem = body.get("agent_ca_bundle_pem")
                    if isinstance(ca_pem, str) and "BEGIN CERTIFICATE" in ca_pem:
                        try:
                            ca_path = _AGENT_TLS_CACHE_PATH.parent / "ca-bundle.pem"
                            ca_path.parent.mkdir(parents=True, exist_ok=True)
                            # V1.5 加固：原子写。2026-09-09 事故：非原子写被截断成 0 字节，
                            # TLS 心跳拿空 bundle 校验必然 SSLError(136)，且刷新依赖成功心跳，
                            # 形成死锁，终端掉线 19 小时。
                            ca_tmp = ca_path.with_suffix(".pem.tmp")
                            ca_tmp.write_text(ca_pem, encoding="utf-8")
                            os.replace(str(ca_tmp), str(ca_path))
                        except Exception as exc:
                            print(f"[TLS] ca-bundle 保存失败: {exc}")
                    # 自动升级（R13）：平台在心跳响应中携带 upgrade 指令
                    upgrade_info = body.get("upgrade")
                    if isinstance(upgrade_info, dict) and upgrade_info.get("version") and upgrade_info.get("sha256"):
                        _UPGRADE_STATE["package_type"] = str(upgrade_info.get("package_type") or "exe")
                        # V1.7.0：携带服务端事务 ID，贯穿状态文件与终态上报
                        _UPGRADE_STATE["server_upgrade_id"] = str(upgrade_info.get("upgrade_id") or "")
                        # V1.5 重试限制：目标版本处于退避期内则静默跳过（每小时最多提示一次）
                        backoff_remaining = _upgrade_backoff_remaining(str(upgrade_info["version"]))
                        if backoff_remaining > 0:
                            last_note = float(_UPGRADE_STATE.get("backoff_note_at") or 0)
                            if time.time() - last_note > 3600:
                                _UPGRADE_STATE["backoff_note_at"] = time.time()
                                print(f"[Upgrade] target {upgrade_info['version']} backoff {int(backoff_remaining)}s remaining; skip")
                        elif not _UPGRADE_STATE.get("in_progress"):
                            _UPGRADE_STATE["in_progress"] = True
                            try:
                                threading.Thread(
                                    target=perform_self_upgrade,
                                    args=(upgrade_info["version"], upgrade_info["sha256"]),
                                    daemon=True,
                                    name="agent-self-upgrade",
                                ).start()
                            except Exception as exc:
                                print(f"[Upgrade] trigger failed: {exc}")
                                _UPGRADE_STATE["in_progress"] = False
                consecutive_failures = 0
                print(f"[Heartbeat] OK (asset_id={asset_id})")
                # V1.6.0：心跳成功即写入健康标记，ZViewUpdater 健康检查据此判定 COMMIT
                try:
                    import cmdb_agent_layout as _layout_health
                    _layout_health.write_upgrade_health(AGENT_VERSION, True)
                except Exception:
                    pass
                if applied:
                    print(f"[Heartbeat] 平台策略已更新: {applied}")
                if last_upgrade_state and last_upgrade_state.get("stage") in ("COMMIT", "ROLLBACK", "FAILED"):
                    # 上报成功后标记 REPORTED，避免每轮重复上报
                    try:
                        last_upgrade_state["stage"] = "REPORTED"
                        _UPGRADE_STATE_PATH.write_text(
                            json.dumps(last_upgrade_state, ensure_ascii=False, indent=2), encoding="utf-8"
                        )
                    except Exception:
                        pass
            else:
                consecutive_failures += 1
                print(f"[Heartbeat] HTTP {resp.status_code}: {resp.text[:200]}")
        except Exception as exc:
            consecutive_failures += 1
            print(f"[Heartbeat] 错误: {exc}")

        # P0-06：连续失败自愈（10 次 ≈ 5 分钟）—— 退出由服务重启 worker
        if consecutive_failures >= 10:
            print(f"[SelfHeal] heartbeat failed {consecutive_failures} consecutive times; exiting for worker restart")
            os._exit(1)

        time.sleep(_current_interval("heartbeat", 30))


def _system_status_loop():
    """Compatibility no-op: heartbeat already carries the live metrics."""
    return


def _hardware_report_loop():
    """终端接入后立即上报一次完整硬件信息，之后按平台策略周期上报。

    复用 trigger_immediate_report 的心跳通道（服务端心跳接口会更新
    os/cpu/memory 等静态字段）；上报成功才计入周期，失败时每 60 秒重试。
    """
    last_run = 0.0
    while _AGENT_STATE["running"]:
        now = time.time()
        if now - last_run < _current_interval("hardware", 86400):
            time.sleep(60)
            continue

        result = trigger_immediate_report()
        if result.get("success"):
            print(f"[Hardware] 硬件信息上报成功 (asset_id={result.get('asset_id')})")
            last_run = now
        else:
            print(f"[Hardware] 硬件信息上报失败: {result.get('error')}")

        time.sleep(60)


_SOFTWARE_FULL_SYNC_INTERVAL = 6 * 3600  # 无变化时也定期全量上报，兜底数据一致性
_SOFTWARE_REPORT_STATE = {"hash": None, "last_full_sync": 0.0}


def _software_payload_hash(software: list) -> str:
    """对清单做顺序无关哈希，用于变更检测（不变则跳过上报）。"""
    normalized = sorted(
        (
            str(i.get("name") or ""),
            str(i.get("version") or ""),
            str(i.get("publisher") or i.get("vendor") or ""),
            str(i.get("install_date") or ""),
        )
        for i in software
        if i.get("name")
    )
    return hashlib.sha256(json.dumps(normalized, ensure_ascii=False).encode("utf-8")).hexdigest()


def _software_report_loop():
    while _AGENT_STATE["running"]:
        try:
            asset_id = _AGENT_STATE.get("asset_id")
            if not asset_id:
                time.sleep(_current_interval("software", 120))
                continue

            software = collect_software_list()
            payload_hash = _software_payload_hash(software)
            now = time.time()
            unchanged = payload_hash == _SOFTWARE_REPORT_STATE["hash"]
            full_sync_due = now - _SOFTWARE_REPORT_STATE["last_full_sync"] >= _SOFTWARE_FULL_SYNC_INTERVAL
            if unchanged and not full_sync_due:
                # 清单无变化：跳过上报（省 17KB+/次的传输与服务端整表重写）
                time.sleep(_current_interval("software", 120))
                continue

            ip, mac = get_primary_network_info()
            payload = {
                "asset_id": asset_id,
                "hostname": _AGENT_STATE["hostname"],
                "ip_address": ip,
                "mac_address": mac,
                "status": "online",
                "report_type": "software",
                "agent_version": AGENT_VERSION,
                "software_hash": payload_hash,
                "software_list": [
                    {
                        "name": item.get("name"),
                        "version": item.get("version"),
                        "vendor": item.get("publisher") or item.get("vendor"),
                        "install_date": item.get("install_date"),
                        "size": item.get("size_mb") or item.get("size"),
                    }
                    for item in software
                    if item.get("name")
                ],
            }

            url = urljoin(_platform_base(), "/api/v1/agent/heartbeat")
            resp = requests.post(url, json=payload, headers=_agent_headers(), timeout=60, verify=_agent_requests_verify())
            if resp.status_code in (200, 201):
                _SOFTWARE_REPORT_STATE["hash"] = payload_hash
                _SOFTWARE_REPORT_STATE["last_full_sync"] = now
                print(f"[Software] 上报 {len(software)} 个软件 ({'full-sync' if unchanged else 'changed'})")
            else:
                print(f"[Software] HTTP {resp.status_code}")
        except Exception as exc:
            print(f"[Software] 错误: {exc}")

        time.sleep(_current_interval("software", 120))


def _network_report_loop():
    """网络状态/流量/质量采集上报（复用心跳通道，独立低频线程，10s 默认）。

    采集→计算→上报分离：NetworkCollector 只产出快照，本循环仅负责上报；
    连续失败静默降级（打印前 3 次），不影响心跳与远控。
    """
    try:
        import network_monitor
    except Exception as exc:
        print(f"[Network] network_monitor 不可用，网络采集禁用: {exc}")
        return

    interval = _current_interval("network", 10)
    collector = network_monitor.NetworkCollector(interval=interval)
    consecutive_failures = 0

    while _AGENT_STATE["running"]:
        try:
            asset_id = _AGENT_STATE.get("asset_id")
            if not asset_id:
                time.sleep(5)
                continue

            snapshot = collector.snapshot()
            if snapshot:
                ip, mac = get_primary_network_info()
                payload = {
                    "asset_id": asset_id,
                    "hostname": _AGENT_STATE["hostname"],
                    "ip_address": ip,
                    "mac_address": mac,
                    "status": "online",
                    "report_type": "network",
                    "agent_version": AGENT_VERSION,
                    "network": network_monitor.snapshot_to_payload(snapshot),
                }
                url = urljoin(_platform_base(), "/api/v1/agent/heartbeat")
                resp = requests.post(
                    url, json=payload, headers=_agent_headers(), timeout=20, verify=_agent_requests_verify()
                )
                if resp.status_code in (200, 201):
                    consecutive_failures = 0
                else:
                    consecutive_failures += 1
                    if consecutive_failures <= 3:
                        print(f"[Network] HTTP {resp.status_code}: {resp.text[:120]}")
        except Exception as exc:
            consecutive_failures += 1
            if consecutive_failures <= 3:
                print(f"[Network] 采集上报错误: {exc}")

        time.sleep(_current_interval("network", 10))


# =============================================================================
# 软件管理 - 策略同步 + 任务轮询
# =============================================================================

class SoftwareManager:
    def __init__(self, asset_id: int):
        self.asset_id = asset_id
        self.running = False
        self.thread: threading.Thread | None = None
        self.download_dir = Path(SOFTWARE_CONFIG.get("download_dir", r"C:\CMDB-Agent\Downloads"))
        self.download_dir.mkdir(parents=True, exist_ok=True)

    def start(self):
        if self.running:
            return
        self.running = True
        self.thread = threading.Thread(target=self._loop, name="software-manager", daemon=True)
        self.thread.start()
        print(f"[SoftwareManager] 启动 (asset_id={self.asset_id})")

    def stop(self):
        self.running = False

    def _loop(self):
        policy_interval = SOFTWARE_CONFIG.get("intervals", {}).get("policy_sync", 300)
        task_interval = SOFTWARE_CONFIG.get("intervals", {}).get("task_poll", 30)
        last_policy_sync = 0.0

        while self.running:
            now = time.time()

            # 策略同步
            if now - last_policy_sync >= policy_interval:
                try:
                    self._sync_policies()
                    last_policy_sync = now
                except Exception as exc:
                    print(f"[SoftwareManager] 策略同步失败: {exc}")

            # 任务轮询
            try:
                self._poll_and_execute_tasks()
            except Exception as exc:
                print(f"[SoftwareManager] 任务轮询失败: {exc}")

            time.sleep(task_interval)

    def _sync_policies(self):
        url = urljoin(SOFTWARE_CONFIG["server_url"], "/api/v1/software/agent/policies")
        payload = {"asset_id": self.asset_id}
        resp = requests.post(url, json=payload, headers=_software_headers(), timeout=30)
        if resp.status_code == 200:
            print("[SoftwareManager] 策略同步成功")
        else:
            print(f"[SoftwareManager] 策略同步 HTTP {resp.status_code}")

    def _poll_and_execute_tasks(self):
        url = urljoin(SOFTWARE_CONFIG["server_url"], "/api/v1/software/agent/tasks/poll")
        payload = {"asset_id": self.asset_id}
        resp = requests.post(url, json=payload, headers=_software_headers(), timeout=30)
        if resp.status_code != 200:
            return

        data = resp.json()
        tasks = data.get("tasks", []) if isinstance(data, dict) else []
        if not tasks:
            return

        for task in tasks:
            try:
                self._execute_task(task)
            except Exception as exc:
                print(f"[SoftwareManager] 任务执行失败: {exc}")
                self._report_task_result(task.get("result_id") or task.get("id"), "failed", str(exc))

    def _execute_task(self, task: dict):
        # 后端当前下发 task_id/result_id/package_info；保留旧字段兼容历史接口。
        result_id = task.get("result_id") or task.get("id")
        task_id = task.get("task_id") or task.get("id")
        task_type = task.get("task_type") or task.get("type") or "install"
        package_info = task.get("package_info") or {}
        package_id = package_info.get("id") or task.get("package_id")

        print(
            f"[SoftwareManager] 执行任务: task_id={task_id} result_id={result_id} "
            f"type={task_type} package={package_id}"
        )

        if not result_id:
            print("[SoftwareManager] 任务缺少 result_id，无法回传执行结果")
            return

        if task_type in ("install", "upgrade") and package_id:
            self._do_install(result_id, task_id, int(package_id), task, package_info)
        elif task_type == "uninstall" and package_id:
            self._do_uninstall(result_id, task_id, int(package_id), task, package_info)
        else:
            self._report_task_result(result_id, "failed", f"unsupported_task_type={task_type}")

    def _do_install(self, result_id, task_id, package_id: int, task: dict, package_info: dict):
        # 下载软件包
        download_url = urljoin(
            SOFTWARE_CONFIG["server_url"],
            f"/api/v1/software/agent/packages/{package_id}/download?asset_id={self.asset_id}",
        )
        file_name = self._safe_file_name(package_info.get("file_name") or f"package_{package_id}.bin")
        file_path = self.download_dir / file_name
        expected_hash = str(package_info.get("file_hash") or "").strip()

        max_retries = SOFTWARE_CONFIG.get("max_retries", 3)
        for attempt in range(max_retries):
            try:
                self._report_task_result(
                    result_id,
                    "downloading",
                    f"开始下载软件包 task_id={task_id} package_id={package_id}",
                    progress=0,
                    download_progress=0,
                )
                self._download_file(download_url, file_path, result_id, expected_hash)
                break
            except Exception as exc:
                print(f"[SoftwareManager] 下载失败 (attempt {attempt + 1}): {exc}")
                if attempt == max_retries - 1:
                    raise
                time.sleep(2 ** attempt)

        # 执行安装
        install_cmd = package_info.get("install_command") or task.get("install_command")
        install_cmd = self._build_package_command(install_cmd, file_path)
        print(f"[SoftwareManager] 执行安装: {install_cmd}")
        self._report_task_result(
            result_id,
            "installing",
            f"开始执行安装命令 task_id={task_id}",
            progress=80,
            download_progress=100,
            install_progress=0,
        )
        result = subprocess.run(
            install_cmd, shell=True, capture_output=True, text=True,
            errors="replace", timeout=int(task.get("timeout") or 600), check=False,
        )

        if result.returncode == 0:
            self._report_task_result(
                result_id,
                "success",
                "install_success",
                progress=100,
                download_progress=100,
                install_progress=100,
                stdout_log=self._truncate_log(result.stdout or "install_success"),
                stderr_log=self._truncate_log(result.stderr),
            )
        else:
            self._report_task_result(
                result_id, "failed",
                f"exit_code={result.returncode} stderr={result.stderr[:500]}",
                progress=100,
                download_progress=100,
                install_progress=100,
                stdout_log=self._truncate_log(result.stdout),
                stderr_log=self._truncate_log(result.stderr),
                error_message=f"安装命令退出码={result.returncode}",
            )

    def _do_uninstall(self, result_id, task_id, package_id: int, task: dict, package_info: dict):
        uninstall_cmd = package_info.get("uninstall_command") or task.get("uninstall_command")
        if not uninstall_cmd:
            self._report_task_result(result_id, "failed", f"no_uninstall_command package_id={package_id}")
            return

        print(f"[SoftwareManager] 执行卸载: {uninstall_cmd}")
        self._report_task_result(
            result_id,
            "installing",
            f"开始执行卸载命令 task_id={task_id}",
            progress=10,
            install_progress=10,
        )
        result = subprocess.run(
            uninstall_cmd, shell=True, capture_output=True, text=True,
            errors="replace", timeout=int(task.get("timeout") or 300), check=False,
        )

        if result.returncode == 0:
            self._report_task_result(
                result_id,
                "success",
                "uninstall_success",
                progress=100,
                install_progress=100,
                stdout_log=self._truncate_log(result.stdout or "uninstall_success"),
                stderr_log=self._truncate_log(result.stderr),
            )
        else:
            self._report_task_result(
                result_id, "failed",
                f"exit_code={result.returncode} stderr={result.stderr[:500]}",
                progress=100,
                install_progress=100,
                stdout_log=self._truncate_log(result.stdout),
                stderr_log=self._truncate_log(result.stderr),
                error_message=f"卸载命令退出码={result.returncode}",
            )

    def _download_file(self, url: str, file_path: Path, result_id: int | None = None, expected_hash: str = ""):
        headers = _software_headers()
        resume_pos = 0
        if file_path.exists():
            resume_pos = file_path.stat().st_size
            headers["Range"] = f"bytes={resume_pos}-"

        mode = "ab" if resume_pos > 0 else "wb"
        with requests.get(url, headers=headers, stream=True, timeout=120) as resp:
            if resp.status_code == 416 and file_path.exists():
                # 本地断点文件长度异常时删除后重新下载，避免无限 416。
                file_path.unlink()
                return self._download_file(url, file_path, result_id, expected_hash)
            resp.raise_for_status()
            if resume_pos > 0 and resp.status_code == 200:
                # 服务端未按 Range 返回 206 时必须重写文件，否则会追加出损坏包。
                resume_pos = 0
                mode = "wb"
            total = int(resp.headers.get("Content-Length", 0)) + resume_pos
            downloaded = resume_pos
            expected_hash = expected_hash or str(resp.headers.get("X-File-Hash") or "").strip()
            last_reported_progress = -1
            with open(file_path, mode) as f:
                for chunk in resp.iter_content(chunk_size=1024 * 1024):
                    if chunk:
                        f.write(chunk)
                        downloaded += len(chunk)
                        if total > 0 and result_id:
                            progress = int((downloaded / total) * 100)
                            if progress >= 100 or progress - last_reported_progress >= 10:
                                self._report_task_result(
                                    result_id,
                                    "downloading",
                                    f"下载进度 {progress}%",
                                    progress=min(progress, 80),
                                    download_progress=min(progress, 100),
                                )
                                last_reported_progress = progress
                            print(f"[SoftwareManager] 下载进度: {progress}%")

        # SHA256 校验
        sha256 = hashlib.sha256()
        with open(file_path, "rb") as f:
            for chunk in iter(lambda: f.read(4096), b""):
                sha256.update(chunk)
        actual_hash = sha256.hexdigest()
        if expected_hash and actual_hash.lower() != expected_hash.lower():
            raise RuntimeError(f"软件包 SHA256 校验失败 expected={expected_hash} actual={actual_hash}")
        print(f"[SoftwareManager] 下载完成 SHA256={actual_hash}")

    @staticmethod
    def _safe_file_name(file_name: str) -> str:
        # 只保留文件名，防止服务端数据异常导致写到下载目录之外。
        cleaned = Path(str(file_name)).name.strip()
        return cleaned or "package.bin"

    @staticmethod
    def _build_package_command(command: str | None, file_path: Path) -> str:
        # 安装命令支持显式占位符；没有命令时直接执行下载的软件包。
        quoted_path = f'"{file_path}"'
        if not command:
            return quoted_path
        install_command = str(command)
        for placeholder in ("{file_path}", "{package_path}", "%FILE_PATH%", "%PACKAGE_PATH%"):
            install_command = install_command.replace(f'"{placeholder}"', quoted_path)
            install_command = install_command.replace(f"'{placeholder}'", quoted_path)
            install_command = install_command.replace(placeholder, quoted_path)
        return install_command

    @staticmethod
    def _truncate_log(text: str | None, limit: int = 20000) -> str:
        # 限制单次回传日志长度，避免数据库字段和接口请求过大。
        if not text:
            return ""
        value = str(text)
        if len(value) <= limit:
            return value
        return value[-limit:]

    def _report_task_result(
        self,
        result_id: int | None,
        status: str,
        message: str,
        progress: int | None = None,
        download_progress: int | None = None,
        install_progress: int | None = None,
        stdout_log: str | None = None,
        stderr_log: str | None = None,
        error_message: str | None = None,
    ):
        if result_id is None:
            return
        try:
            if status == "completed":
                status = "success"
            url = urljoin(SOFTWARE_CONFIG["server_url"], f"/api/v1/software/agent/task-results/{result_id}")
            payload = {
                "asset_id": self.asset_id,
                "status": status,
                "completed_at": datetime.now().isoformat(),
            }
            if progress is not None:
                payload["progress"] = max(0, min(100, int(progress)))
            if download_progress is not None:
                payload["download_progress"] = max(0, min(100, int(download_progress)))
            if install_progress is not None:
                payload["install_progress"] = max(0, min(100, int(install_progress)))
            if status in {"failed", "timeout"}:
                payload["error_message"] = self._truncate_log(error_message or message, 1000)
            else:
                payload["stdout_log"] = self._truncate_log(stdout_log or message)
            if stdout_log is not None and "stdout_log" not in payload:
                payload["stdout_log"] = self._truncate_log(stdout_log)
            if stderr_log is not None:
                payload["stderr_log"] = self._truncate_log(stderr_log)

            resp = requests.put(url, json=payload, headers=_software_headers(), timeout=30)
            if resp.status_code >= 400:
                print(f"[SoftwareManager] 结果上报 HTTP {resp.status_code}: {resp.text[:500]}")
        except Exception as exc:
            print(f"[SoftwareManager] 结果上报失败: {exc}")


# =============================================================================
# 远程桌面服务端
# =============================================================================

def _truncate_control_output(value: str | None, limit: int = 20000) -> str:
    text = str(value or "")
    if len(text) <= limit:
        return text
    return text[:limit] + "\n...[truncated]"


# =============================================================================
# 高权限命令白名单（P0-10）
# 结构化协议：{"zview_cmd": {"op": "restart|shutdown|script|raw", ...}}
# - restart/shutdown：delay 强制钳位 0~3600
# - script：base64 编码 PowerShell，长度上限 128KB
# - raw：自由命令，默认允许（ZVIEW_AGENT_ALLOW_RAW_COMMAND=0 可关闭），逐条告警日志
# - 兼容：旧平台裸 {"command": "..."} 按 raw 处理（同 raw 开关）
# =============================================================================

_RAW_COMMAND_ENV_NAME = "ZVIEW_AGENT_ALLOW_RAW_COMMAND"
_SCRIPT_B64_MAX_LEN = 128 * 1024


def _raw_command_allowed() -> bool:
    return str(get_env(_RAW_COMMAND_ENV_NAME, "1") or "1").strip().lower() not in ("0", "false", "no", "off")


def _clamp_timeout(value: Any, default: int = 60) -> int:
    try:
        timeout = int(value)
    except (TypeError, ValueError):
        timeout = default
    return max(1, min(timeout, 300))


def _dispatch_zview_cmd(zv: dict, operator: str) -> dict:
    op = str(zv.get("op") or "").strip().lower()

    if op in ("restart", "shutdown"):
        try:
            delay = int(zv.get("delay", 0) or 0)
        except (TypeError, ValueError):
            delay = 0
        delay = max(0, min(delay, 3600))
        flag = "/r" if op == "restart" else "/s"
        command = f"shutdown {flag} /t {delay} /f"
        print(f"[Command] whitelist op={op} delay={delay} operator={operator}")
        return execute_control_command(command, 15)

    if op == "script":
        encoded = str(zv.get("encoded") or "").strip()
        if not encoded or len(encoded) > _SCRIPT_B64_MAX_LEN:
            return {"success": False, "error": "script encoded payload missing or too large",
                    "stdout": "", "stderr": "", "returncode": None}
        try:
            base64.b64decode(encoded, validate=True)
        except Exception:
            return {"success": False, "error": "script encoded payload is not valid base64",
                    "stdout": "", "stderr": "", "returncode": None}
        command = f"powershell -NoProfile -ExecutionPolicy Bypass -EncodedCommand {encoded}"
        print(f"[Command] whitelist op=script len={len(encoded)} operator={operator}")
        return execute_control_command(command, _clamp_timeout(zv.get("timeout_seconds", 120), 120))

    if op == "raw":
        command = str(zv.get("command") or "").strip()
        if not command:
            return {"success": False, "error": "Command is required",
                    "stdout": "", "stderr": "", "returncode": None}
        if not _raw_command_allowed():
            print(f"[Command] raw command DENIED by ZVIEW_AGENT_ALLOW_RAW_COMMAND=0 operator={operator}")
            return {"success": False, "error": "Raw command execution is disabled on this agent",
                    "stdout": "", "stderr": "", "returncode": None}
        print(f"[Command] raw command executed (audit): operator={operator} command={command[:200]}")
        return execute_control_command(command, _clamp_timeout(zv.get("timeout_seconds", 60), 60))

    return {"success": False, "error": f"unknown zview_cmd op: {op}",
            "stdout": "", "stderr": "", "returncode": None}


def handle_control_command_payload(payload: dict) -> dict:
    """9001 /api/v1/command 统一入口：结构化白名单优先，裸 command 兼容。"""
    operator = str(payload.get("operator") or payload.get("requester") or "platform")
    zv = payload.get("zview_cmd")
    if isinstance(zv, dict):
        return _dispatch_zview_cmd(zv, operator)
    # 兼容旧平台：裸 command 字段按 raw 处理（同一开关管控）
    legacy_command = str(payload.get("command") or "").strip()
    if legacy_command:
        return _dispatch_zview_cmd({"op": "raw", "command": legacy_command,
                                    "timeout_seconds": payload.get("timeout_seconds", 60)}, operator)
    return {"success": False, "error": "command payload required (zview_cmd or command)",
            "stdout": "", "stderr": "", "returncode": None}


def execute_control_command(command: str, timeout_seconds: int = 60) -> dict:
    normalized_command = str(command or "").strip()
    if not normalized_command:
        return {
            "success": False,
            "error": "Command is required",
            "stdout": "",
            "stderr": "",
            "returncode": None,
        }

    timeout_seconds = max(1, min(int(timeout_seconds or 60), 300))
    try:
        result = subprocess.run(
            normalized_command,
            shell=True,
            capture_output=True,
            text=True,
            errors="replace",
            timeout=timeout_seconds,
            check=False,
        )
        return {
            "success": result.returncode == 0,
            "stdout": _truncate_control_output(result.stdout),
            "stderr": _truncate_control_output(result.stderr),
            "returncode": result.returncode,
            "error": "" if result.returncode == 0 else f"Command exited with code {result.returncode}",
        }
    except subprocess.TimeoutExpired as exc:
        return {
            "success": False,
            "stdout": _truncate_control_output(exc.stdout),
            "stderr": _truncate_control_output(exc.stderr),
            "returncode": None,
            "error": f"Command timed out after {timeout_seconds} seconds",
        }
    except Exception as exc:
        return {
            "success": False,
            "stdout": "",
            "stderr": "",
            "returncode": None,
            "error": f"Command execution failed: {type(exc).__name__}: {exc}",
        }


def trigger_immediate_report() -> dict:
    asset_id = get_asset_id_from_server()
    if not asset_id:
        return {
            "success": False,
            "error": "Unable to register asset with platform",
        }

    ip_address, mac_address = get_primary_network_info()
    hardware = collect_hardware_info()
    system_status = collect_system_status()
    disk_total = sum(
        float(item.get("total_mb") or 0)
        for item in hardware.get("disk_info") or []
    )
    payload = {
        "asset_id": asset_id,
        "hostname": _AGENT_STATE["hostname"],
        "ip_address": ip_address,
        "mac_address": mac_address,
        "status": "online",
        "report_type": "triggered_report",
        "agent_version": AGENT_VERSION,
        "os_type": hardware.get("os_info") or platform.platform(),
        "os_version": hardware.get("os_version") or platform.version(),
        "cpu_cores": psutil.cpu_count(logical=False) or psutil.cpu_count() or 0,
        "memory_total": system_status.get("memory_total_mb") or hardware.get("memory_total_mb") or 0,
        "disk_total": round(disk_total),
        "serial_number": hardware.get("serial_number") or "",
        "manufacturer": hardware.get("manufacturer") or "",
        "model": hardware.get("model") or "",
    }

    try:
        response = requests.post(
            urljoin(_platform_base(), "/api/v1/agent/heartbeat"),
            json=payload,
            headers=_agent_headers(),
            timeout=30,
            verify=_agent_requests_verify(),
        )
        response.raise_for_status()
        response_body = response.json() if response.content else {}
    except Exception as exc:
        return {
            "success": False,
            "asset_id": asset_id,
            "error": f"Immediate report failed: {type(exc).__name__}: {exc}",
        }

    return {
        "success": True,
        "asset_id": asset_id,
        "message": "Immediate report completed",
        "result": response_body,
    }


class AgentControlRequestHandler(BaseHTTPRequestHandler):
    server_version = "ZViewAgentControl/1.0"
    max_request_body_bytes = 1024 * 1024

    def log_message(self, format, *args):
        print(f"[AgentControl] {format % args}")

    def _send_json(self, status_code: int, payload: dict):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status_code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(body)

    def _is_authorized(self) -> bool:
        expected_token = str(CONFIG.get("token") or "").strip()
        if not expected_token:
            return False
        authorization = str(self.headers.get("Authorization") or "").strip()
        provided_token = authorization[7:].strip() if authorization.lower().startswith("bearer ") else ""
        return bool(provided_token) and hmac.compare_digest(provided_token, expected_token)

    def _read_json_body(self) -> dict:
        raw_length = self.headers.get("Content-Length") or "0"
        try:
            content_length = int(raw_length)
        except ValueError as exc:
            raise ValueError("Invalid Content-Length") from exc
        if content_length < 0 or content_length > self.max_request_body_bytes:
            raise ValueError("Request body is too large")
        raw_body = self.rfile.read(content_length) if content_length else b"{}"
        if not raw_body:
            return {}
        value = json.loads(raw_body.decode("utf-8"))
        if not isinstance(value, dict):
            raise ValueError("JSON body must be an object")
        return value

    def do_POST(self):
        if not self._is_authorized():
            self._send_json(401, {"success": False, "error": "Unauthorized"})
            return

        try:
            payload = self._read_json_body()
        except (UnicodeDecodeError, ValueError, json.JSONDecodeError) as exc:
            self._send_json(400, {"success": False, "error": str(exc)})
            return

        if self.path == "/api/v1/command":
            result = handle_control_command_payload(payload)
            self._send_json(200 if result["success"] else 400, result)
            return

        if self.path == "/api/v1/security-command":
            try:
                from security_manager import execute_security_command
                command_type = payload.get("command_type") or ""
                params = payload.get("params") or {}
                result = execute_security_command(command_type, params)
                self._send_json(200 if result.get("success") else 400, result)
            except Exception as exc:
                self._send_json(500, {"success": False, "error": str(exc)})
            return

        if self.path == "/api/v1/trigger-report":
            result = trigger_immediate_report()
            self._send_json(200 if result["success"] else 502, result)
            return

        self._send_json(404, {"success": False, "error": "Unknown control endpoint"})


class AgentControlServer:
    def __init__(self, host: str | None = None, port: int | None = None):
        host = host or get_env("ZVIEW_BIND_HOST", "0.0.0.0") or "0.0.0.0"  # P0-2: 可配置监听地址
        self.host = host
        self.port = int(port or CONFIG.get("control_port") or 9001)
        self.server: ThreadingHTTPServer | None = None
        self.thread: threading.Thread | None = None

    def start(self):
        if self.thread and self.thread.is_alive():
            return
        self.server = ThreadingHTTPServer((self.host, self.port), AgentControlRequestHandler)
        self.server.daemon_threads = True
        self.thread = threading.Thread(target=self.server.serve_forever, name="agent-control", daemon=True)
        self.thread.start()
        print(f"[AgentControl] Listening on http://{self.host}:{self.port}")

    def stop(self):
        if self.server:
            self.server.shutdown()
            self.server.server_close()
            self.server = None
        if self.thread:
            self.thread.join(timeout=2)
            self.thread = None


class StarletteWebSocketAdapter:
    """Adapt the native websockets connection to the engine's Starlette API."""

    def __init__(self, connection):
        self.connection = connection
        self.remote_address = connection.remote_address

    async def receive_text(self) -> str:
        message = await self.connection.recv()
        if isinstance(message, bytes):
            return message.decode("utf-8")
        return str(message)

    async def send_json(self, payload: dict):
        await self.connection.send(json.dumps(payload, ensure_ascii=False))

    async def send_bytes(self, data: bytes):
        await self.connection.send(data)

    async def close(self, code: int = 1000, reason: str = ""):
        await self.connection.close(code=code, reason=reason)


class RemoteDesktopServer:
    def __init__(self, host: str | None = None, port: int = 9000):
        host = host or get_env("ZVIEW_BIND_HOST", "0.0.0.0") or "0.0.0.0"  # P0-2: 可配置监听地址
        self.host = host
        self.port = port
        self.running = False
        self.server = None
        self.thread: threading.Thread | None = None

    @staticmethod
    def _log(message: str, exc: BaseException | None = None) -> None:
        lines = [f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] [RemoteDesktopServer] {message}"]
        if exc is not None:
            lines.append(traceback.format_exception(type(exc), exc, exc.__traceback__))
        try:
            log_dir = Path(os.environ.get("ProgramData", ".")) / "CMDB-Agent" / "logs"
            log_dir.mkdir(parents=True, exist_ok=True)
            with open(log_dir / "remote-desktop-server.log", "a", encoding="utf-8") as fh:
                fh.write("\n".join(lines) + "\n")
        except Exception:
            pass

    def start(self):
        if self.running:
            return
        self.running = True
        self.thread = threading.Thread(target=self.serve_blocking, name="rdp-server", daemon=True)
        self.thread.start()
        print(f"[RemoteDesktop] 服务端启动: ws://{self.host}:{self.port}/remote-desktop")

    def serve_blocking(self):
        """Run the websocket server loop in the calling thread until stopped."""
        if self.running:
            return
        self.running = True
        try:
            asyncio.set_event_loop(asyncio.new_event_loop())
            loop = asyncio.get_event_loop()
            loop.run_until_complete(self._serve())
        except Exception as exc:
            self._log("服务线程异常退出", exc)
            print(f"[RemoteDesktop] 服务端错误: {exc}")
        finally:
            self.running = False

    def stop(self):
        self.running = False
        if self.server:
            try:
                self.server.close()
            except Exception:
                pass

    def _run_server(self):
        self.serve_blocking()

    async def _serve(self):
        try:
            import websockets
        except ImportError:
            self._log("websockets 未安装，远程桌面不可用")
            print("[RemoteDesktop] websockets 未安装，远程桌面不可用")
            return

        self._log(f"websockets {getattr(websockets, '__version__', 'unknown')} 导入成功，准备监听 ws://{self.host}:{self.port}")

        try:
            apply_agent_firewall_whitelist(
                CONFIG.get("server_url", ""), logger=lambda m: self._log(m)
            )
        except Exception:
            pass

        async def handler(websocket, path=None):
            self._log(f"客户端连接: {websocket.remote_address}")
            try:
                session = self._create_session(StarletteWebSocketAdapter(websocket))
                await session.start()
            except Exception as exc:
                import traceback
                self._log(f"会话错误: {type(exc).__name__}: {exc}")
                self._log("traceback: " + traceback.format_exc()[-800:])
            finally:
                self._log(f"客户端断开: {websocket.remote_address}")

        try:
            self.server = await websockets.serve(
                handler, self.host, self.port,
                ping_interval=20, ping_timeout=10,
            )
            self._log(f"监听成功 ws://{self.host}:{self.port}，等待连接")
            await self.server.wait_closed()
            self._log("server.wait_closed 返回，服务端正常关闭")
        except Exception as exc:
            self._log("serve 失败", exc)
            print(f"[RemoteDesktop] serve 错误: {exc}")

    def _create_session(self, websocket):
        _load_cached_agent_policies()
        session_id = f"session_{int(time.time() * 1000)}_{uuid.uuid4().hex[:8]}"
        try:
            # 优先使用 v2 引擎
            from remote_desktop_engine_v2 import RemoteDesktopSession as V2Session
            return V2Session(websocket, session_id)
        except Exception as exc:
            print(f"[RemoteDesktop] v2 引擎加载失败 ({exc})，回退到 v1")
            from remote_desktop_engine import RemoteDesktopSession as V1Session
            return V1Session(websocket, session_id)


# =============================================================================
# 公开接口（入口文件依赖这些符号）
# =============================================================================

_server_instance: RemoteDesktopServer | None = None
_control_server_instance: AgentControlServer | None = None
_software_manager_instance: SoftwareManager | None = None


def start_cmdb_reporter():
    """启动资产采集和心跳上报线程。"""
    _AGENT_STATE["running"] = True
    # P0-06：升级后新进程启动时消费 bat 终态标记（COMMIT/ROLLBACK）
    _consume_upgrade_result_after_start()

    threads = [
        ("heartbeat", _heartbeat_loop),
        ("system_status", _system_status_loop),
        ("hardware_report", _hardware_report_loop),
        ("software_report", _software_report_loop),
        ("network_report", _network_report_loop),
    ]

    for name, target in threads:
        t = threading.Thread(target=target, name=f"agent-{name}", daemon=True)
        t.start()
        _AGENT_STATE["threads"][name] = t
        print(f"[Agent] 线程启动: {name}")


def start_software_management(asset_id: int):
    """启动软件管理（策略同步 + 任务轮询）。"""
    global _software_manager_instance
    if _software_manager_instance is not None:
        return
    _software_manager_instance = SoftwareManager(asset_id)
    _software_manager_instance.start()


_security_policy_sync_instance: "SecurityPolicySync | None" = None


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


def start_security_policy_sync(asset_id: int):
    """启动安全策略自动轮询同步。"""
    global _security_policy_sync_instance
    if _security_policy_sync_instance is not None:
        return
    _security_policy_sync_instance = SecurityPolicySync(asset_id)
    _security_policy_sync_instance.start()


def start_remote_desktop_server(wait: bool = False):
    """启动远程桌面 WebSocket 服务端。

    wait=False 时在后台线程运行并立即返回；
    wait=True 时在调用线程内阻塞运行，直到服务端关闭。
    """
    global _server_instance
    if _server_instance is not None:
        return
    _server_instance = RemoteDesktopServer(port=9000)  # host 经 ZVIEW_BIND_HOST 可配置
    if wait:
        _server_instance.serve_blocking()
    else:
        _server_instance.start()


def start_agent_control_server():
    """Start the authenticated HTTP control plane for commands and reports."""
    global _control_server_instance
    if _control_server_instance is not None:
        return
    _control_server_instance = AgentControlServer(
        host="0.0.0.0",
        port=int(CONFIG.get("control_port") or 9001),
    )
    _control_server_instance.start()


# 模块加载时恢复最近一次平台下发的策略（重启后立即生效，无需等待下一轮心跳）
_load_cached_agent_policies()
