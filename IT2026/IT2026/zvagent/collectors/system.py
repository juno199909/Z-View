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
    """返回 (ip_address, mac_address)，智能排除虚拟网卡。

    P0 修复：注册路径要求 hostname/ip/mac 三字段非空——此前在部分物理机
    （Hyper-V vSwitch / 多 NIC / MAC 缺失）上会返回空值导致注册 400。
    修复策略：虚拟过滤无候选时回退全接口；MAC 兜底取任意非空 AF_LINK；
    最终兜底返回占位值（"0.0.0.0"/"unknown-mac"），不再返回空。
    """
    result = _select_primary_nic(include_virtual=False)
    if result and result[0]:
        ip, mac = result
        if not mac:
            mac = _first_available_mac()
        return ip, mac or "unknown-mac"
    # 回退 1：包含虚拟网卡（Hyper-V vSwitch 等场景物理流量走虚拟交换机）
    result = _select_primary_nic(include_virtual=True)
    if result and result[0]:
        ip, mac = result
        if not mac:
            mac = _first_available_mac()
        return ip, mac or "unknown-mac"
    # 回退 2：hostname 解析
    try:
        hostname = socket.gethostname()
        ip = socket.getaddrinfo(hostname, None, socket.AF_INET)[0][4][0]
        if ip and ip != "127.0.0.1":
            return ip, _first_available_mac() or "unknown-mac"
    except Exception:
        pass
    # 最终兜底：不返回空（注册 400 根因）
    return "0.0.0.0", "unknown-mac"


def _select_primary_nic(include_virtual: bool) -> tuple[str, str] | None:
    """从网卡列表中选择最优 (ip, mac)。"""
    try:
        interfaces = psutil.net_if_addrs()
        stats = psutil.net_if_stats()

        candidates = []
        for name, addrs in interfaces.items():
            if not include_virtual and _is_virtual_nic(name):
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
                # 优先有流量的网卡
                if name in stats:
                    score += stats[name].speed or 0
                candidates.append((score, ipv4, mac or ""))

        if candidates:
            candidates.sort(key=lambda x: x[0], reverse=True)
            return candidates[0][1], candidates[0][2]
    except Exception as exc:
        print(f"[Network] 网卡选择失败: {exc}")
    return None


def _first_available_mac() -> str | None:
    """从任意接口获取第一个非空 MAC（兜底）。"""
    try:
        for name, addrs in psutil.net_if_addrs().items():
            for addr in addrs:
                if addr.family == psutil.AF_LINK and addr.address:
                    return addr.address
    except Exception:
        pass
    return None


# =============================================================================
# 硬件信息采集
# =============================================================================

def _wmi_query(wql: str) -> list[dict]:
    """WMI query with per-call COM init (thread-safe for worker threads).

    Each call initializes COM on the current thread, because hardware
    collection runs inside daemon threads that do not have COM initialized.
    CoInitialize/CoUninitialize are reference-counted, so repeated calls
    from the same thread are safe.
    """
    if os.name != "nt":
        return []
    try:
        import pythoncom
        import win32com.client

        pythoncom.CoInitialize()
        try:
            svc = win32com.client.GetObject("winmgmts:")
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
            # Explicitly release COM objects before CoUninitialize to
            # avoid "Win32 exception occurred releasing IUnknown" warnings.
            del item
            del svc
            # Force Python to release COM wrappers before tearing down COM.
            import gc
            gc.collect()
            return results
        finally:
            pythoncom.CoUninitialize()
    except Exception as exc:
        print(f"[WMI] query failed {wql[:60]}: {exc}")
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

    # V1.9.34：GPU / 主板 / BIOS 采集（P1-①）
    info["gpu_name"] = ""
    info["gpu_memory_mb"] = 0
    info["motherboard"] = ""
    info["bios_vendor"] = ""
    info["bios_version"] = ""
    info["bios_date"] = ""
    if os.name == "nt":
        try:
            gpus = _wmi_query("SELECT Name, AdapterRAM FROM Win32_VideoController")
            if gpus:
                gpu_names = [str(g.get("Name") or "") for g in gpus if g.get("Name")]
                info["gpu_name"] = " / ".join(gpu_names) if gpu_names else ""
                max_mem = max((int(g.get("AdapterRAM") or 0) for g in gpus), default=0)
                info["gpu_memory_mb"] = round(max_mem / (1024 * 1024), 0) if max_mem else 0
        except Exception:
            pass
        try:
            baseboard = _wmi_query("SELECT Manufacturer, Product FROM Win32_BaseBoard")
            if baseboard:
                mb_mfr = str(baseboard[0].get("Manufacturer") or "")
                mb_prod = str(baseboard[0].get("Product") or "")
                info["motherboard"] = f"{mb_mfr} {mb_prod}".strip()
        except Exception:
            pass
        try:
            bios_info = _wmi_query("SELECT Manufacturer, SMBIOSBIOSVersion, ReleaseDate FROM Win32_BIOS")
            if bios_info:
                b = bios_info[0]
                info["bios_vendor"] = str(b.get("Manufacturer") or "")
                info["bios_version"] = str(b.get("SMBIOSBIOSVersion") or "")
                raw_date = str(b.get("ReleaseDate") or "")
                if len(raw_date) >= 8:
                    info["bios_date"] = f"{raw_date[:4]}-{raw_date[4:6]}-{raw_date[6:8]}"
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
        # 逐盘使用率（V1.9.21：此前只取 C: 的综合值，用户要求每盘单独显示百分比）
        disks = []
        for part in psutil.disk_partitions(all=False):
            try:
                usage = psutil.disk_usage(part.mountpoint)
                if usage.total <= 0:
                    continue  # 空光驱/虚拟挂载点
                disks.append({
                    "device": part.device,
                    "mountpoint": part.mountpoint,
                    "total_mb": round(usage.total / (1024 * 1024), 1),
                    "used_mb": round(usage.used / (1024 * 1024), 1),
                    "free_mb": round(usage.free / (1024 * 1024), 1),
                    "percent": usage.percent,
                })
                if os.name == "nt" and part.device.lower().startswith("c:") and not status["disk_percent"]:
                    status["disk_percent"] = usage.percent
            except Exception:
                continue
        status["disks"] = disks
    except Exception:
        pass
    return status


# =============================================================================
# 软件清单采集
# =============================================================================

