# -*- coding: utf-8 -*-
"""网络/硬件/系统状态采集（V1.8.2 迁入自 cmdb_agent_core.py）。"""
from __future__ import annotations

import os
import platform
import socket
from datetime import datetime

import psutil

from console_utils import safe_console_print

print = safe_console_print


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

