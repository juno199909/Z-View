"""
Z-View Agent Core Module - 备份恢复版
包含：资产采集、心跳上报、软件管理、远程桌面服务端
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import os
import platform
import queue
import re
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
from typing import Any
from urllib.parse import urljoin

import psutil
import requests

from config_utils import ensure_env_loaded, get_db_config, get_env
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
    return {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {CONFIG.get('token', '')}",
    }


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
        # 使用系统路由表获取默认网关
        if os.name == "nt":
            result = subprocess.run(
                ["netsh", "interface", "ip", "show", "config"],
                capture_output=True, text=True, encoding="utf-8", errors="ignore",
                timeout=10, check=False,
            )
            for line in result.stdout.splitlines():
                if "default gateway" in line.lower():
                    parts = line.split(":")
                    if len(parts) >= 2:
                        ip = parts[-1].strip()
                        if ip:
                            return ip
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
        "agent_version": "1.2.1",
        "agent_install_status": "installed",
    }

    try:
        url = urljoin(CONFIG["server_url"], "/api/v1/agent/heartbeat")
        resp = requests.post(url, json=payload, headers=_agent_headers(), timeout=30)
        data = resp.json()
        if resp.status_code in (200, 201) and isinstance(data, dict):
            asset_id = data.get("asset_id") or data.get("id")
            if asset_id:
                _AGENT_STATE["asset_id"] = int(asset_id)
                print(f"[Agent] Asset ID: {_AGENT_STATE['asset_id']}")
                return _AGENT_STATE["asset_id"]
    except Exception as exc:
        print(f"[Agent] 获取 asset_id 失败: {exc}")

    return None


# =============================================================================
# 心跳上报线程
# =============================================================================

def _heartbeat_loop():
    interval = CONFIG.get("intervals", {}).get("heartbeat", 30)
    while _AGENT_STATE["running"]:
        try:
            asset_id = _AGENT_STATE.get("asset_id")
            if not asset_id:
                # 尝试重新注册
                asset_id = get_asset_id_from_server()
                if not asset_id:
                    time.sleep(interval)
                    continue

            ip, mac = get_primary_network_info()
            status = collect_system_status()
            payload = {
                "asset_id": asset_id,
                "hostname": _AGENT_STATE["hostname"],
                "ip_address": ip,
                "mac_address": mac,
                "cpu_percent": status.get("cpu_percent", 0),
                "memory_percent": status.get("memory_percent", 0),
                "disk_percent": status.get("disk_percent", 0),
                "status": "online",
                "username": os.getlogin() if hasattr(os, "getlogin") else "",
            }

            url = urljoin(CONFIG["server_url"], "/api/v1/agent/heartbeat")
            resp = requests.post(url, json=payload, headers=_agent_headers(), timeout=30)
            if resp.status_code in (200, 201):
                print(f"[Heartbeat] OK (asset_id={asset_id})")
            else:
                print(f"[Heartbeat] HTTP {resp.status_code}: {resp.text[:200]}")
        except Exception as exc:
            print(f"[Heartbeat] 错误: {exc}")

        time.sleep(interval)


def _system_status_loop():
    interval = CONFIG.get("intervals", {}).get("system_status", 30)
    while _AGENT_STATE["running"]:
        try:
            asset_id = _AGENT_STATE.get("asset_id")
            if not asset_id:
                time.sleep(interval)
                continue

            status = collect_system_status()
            payload = {
                "asset_id": asset_id,
                "cpu_percent": status.get("cpu_percent", 0),
                "memory_percent": status.get("memory_percent", 0),
                "memory_used_mb": status.get("memory_used_mb", 0),
                "disk_percent": status.get("disk_percent", 0),
            }

            url = urljoin(CONFIG["server_url"], "/api/v1/assets/stats")
            resp = requests.post(url, json=payload, headers=_agent_headers(), timeout=30)
            if resp.status_code not in (200, 201):
                print(f"[SystemStatus] HTTP {resp.status_code}")
        except Exception as exc:
            print(f"[SystemStatus] 错误: {exc}")

        time.sleep(interval)


def _hardware_report_loop():
    interval = CONFIG.get("intervals", {}).get("hardware", 86400)
    last_run = 0.0
    while _AGENT_STATE["running"]:
        now = time.time()
        if now - last_run < interval:
            time.sleep(60)
            continue

        try:
            asset_id = _AGENT_STATE.get("asset_id")
            if not asset_id:
                time.sleep(60)
                continue

            hardware = collect_hardware_info()
            payload = {
                "asset_id": asset_id,
                **hardware,
            }

            url = urljoin(CONFIG["server_url"], "/api/v1/assets/detail")
            resp = requests.put(url, json=payload, headers=_agent_headers(), timeout=60)
            if resp.status_code in (200, 201):
                print("[Hardware] 硬件信息上报成功")
                last_run = now
            else:
                print(f"[Hardware] HTTP {resp.status_code}")
        except Exception as exc:
            print(f"[Hardware] 错误: {exc}")

        time.sleep(60)


def _software_report_loop():
    interval = CONFIG.get("intervals", {}).get("software", 30)
    while _AGENT_STATE["running"]:
        try:
            asset_id = _AGENT_STATE.get("asset_id")
            if not asset_id:
                time.sleep(interval)
                continue

            software = collect_software_list()
            payload = {
                "asset_id": asset_id,
                "software": software,
            }

            url = urljoin(CONFIG["server_url"], "/api/v1/assets/software")
            resp = requests.post(url, json=payload, headers=_agent_headers(), timeout=60)
            if resp.status_code in (200, 201):
                print(f"[Software] 上报 {len(software)} 个软件")
            else:
                print(f"[Software] HTTP {resp.status_code}")
        except Exception as exc:
            print(f"[Software] 错误: {exc}")

        time.sleep(interval)


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
        url = urljoin(SOFTWARE_CONFIG["server_url"], f"/api/v1/software/agent/policies")
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
            urljoin(CONFIG["server_url"], "/api/v1/agent/heartbeat"),
            json=payload,
            headers=_agent_headers(),
            timeout=30,
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
            result = execute_control_command(
                payload.get("command") or "",
                payload.get("timeout_seconds") or 60,
            )
            self._send_json(200 if result["success"] else 400, result)
            return

        if self.path == "/api/v1/trigger-report":
            result = trigger_immediate_report()
            self._send_json(200 if result["success"] else 502, result)
            return

        self._send_json(404, {"success": False, "error": "Unknown control endpoint"})


class AgentControlServer:
    def __init__(self, host: str = "0.0.0.0", port: int | None = None):
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


class RemoteDesktopServer:
    def __init__(self, host: str = "0.0.0.0", port: int = 9000):
        self.host = host
        self.port = port
        self.running = False
        self.server = None
        self.thread: threading.Thread | None = None

    def start(self):
        if self.running:
            return
        self.running = True
        self.thread = threading.Thread(target=self._run_server, name="rdp-server", daemon=True)
        self.thread.start()
        print(f"[RemoteDesktop] 服务端启动: ws://{self.host}:{self.port}/remote-desktop")

    def stop(self):
        self.running = False
        if self.server:
            try:
                self.server.close()
            except Exception:
                pass

    def _run_server(self):
        try:
            asyncio.set_event_loop(asyncio.new_event_loop())
            loop = asyncio.get_event_loop()
            loop.run_until_complete(self._serve())
        except Exception as exc:
            print(f"[RemoteDesktop] 服务端错误: {exc}")

    async def _serve(self):
        try:
            import websockets
        except ImportError:
            print("[RemoteDesktop] websockets 未安装，远程桌面不可用")
            return

        async def handler(websocket, path=None):
            print(f"[RemoteDesktop] 客户端连接: {websocket.remote_address}")
            try:
                session = self._create_session(websocket)
                await session.start()
            except Exception as exc:
                print(f"[RemoteDesktop] 会话错误: {exc}")
            finally:
                print(f"[RemoteDesktop] 客户端断开: {websocket.remote_address}")

        try:
            self.server = await websockets.serve(
                handler, self.host, self.port,
                ping_interval=20, ping_timeout=10,
            )
            await self.server.wait_closed()
        except Exception as exc:
            print(f"[RemoteDesktop] serve 错误: {exc}")

    def _create_session(self, websocket):
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

    threads = [
        ("heartbeat", _heartbeat_loop),
        ("system_status", _system_status_loop),
        ("hardware_report", _hardware_report_loop),
        ("software_report", _software_report_loop),
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


def start_remote_desktop_server():
    """启动远程桌面 WebSocket 服务端。"""
    global _server_instance
    if _server_instance is not None:
        return
    _server_instance = RemoteDesktopServer(host="0.0.0.0", port=9000)
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
