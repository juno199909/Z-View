"""
Z-View Agent 终端安全管理执行器与采集器
真实执行 Windows 防火墙/USB/进程/文件保护/行为监控操作
依赖: SYSTEM 权限（Agent 以 LocalSystem 服务运行）
"""

import os
import json
import time
import hashlib
import subprocess
import threading
import ctypes
from pathlib import Path
from typing import Any, Dict, List, Optional

import psutil

try:
    import winreg
except ImportError:
    winreg = None


def _run(cmd: str, timeout: int = 30) -> Dict[str, Any]:
    """执行命令并返回结构化结果"""
    try:
        r = subprocess.run(
            cmd, shell=True, capture_output=True, text=True,
            errors="replace", timeout=timeout, check=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        return {
            "success": r.returncode == 0,
            "stdout": (r.stdout or "").strip()[:8000],
            "stderr": (r.stderr or "").strip()[:4000],
            "returncode": r.returncode,
        }
    except subprocess.TimeoutExpired:
        return {"success": False, "stdout": "", "stderr": f"timeout after {timeout}s", "returncode": None}
    except Exception as exc:
        return {"success": False, "stdout": "", "stderr": str(exc), "returncode": None}


# ============================================================
# 防火墙管理 (netsh advfirewall)
# ============================================================

def get_firewall_status() -> Dict[str, Any]:
    """查询防火墙启用状态"""
    r = _run("netsh advfirewall show allprofiles state", timeout=15)
    profiles = {}
    current = None
    for line in (r.get("stdout") or "").splitlines():
        line = line.strip()
        if line.startswith("配置文件 配置"):
            current = line.split()[-1] if line.split() else None
        elif "ON" in line.upper() or "OFF" in line.upper() or "开" in line or "关" in line:
            if current:
                profiles[current] = "enabled" if ("ON" in line.upper() or "开" in line) else "disabled"
    return {
        "success": r["success"],
        "profiles": profiles,
        "any_enabled": any(v == "enabled" for v in profiles.values()),
        "raw": r.get("stdout", "")[:500],
    }


def enable_firewall(enable: bool = True) -> Dict[str, Any]:
    action = "on" if enable else "off"
    r = _run(f"netsh advfirewall set allprofiles state {action}", timeout=15)
    return {"success": r["success"], "action": action, "message": r.get("stderr") or r.get("stdout")}


def apply_firewall_rules(rules: List[Dict[str, Any]]) -> Dict[str, Any]:
    """应用防火墙规则列表，返回逐条结果"""
    results = []
    success_count = 0
    for rule in rules:
        name = rule.get("name") or f"zv-rule-{int(time.time())}"
        direction = "in" if rule.get("direction", "in") == "in" else "out"
        action = "allow" if rule.get("action", "allow") == "allow" else "block"
        protocol = rule.get("protocol", "any")
        local_port = rule.get("local_port")
        remote_ip = rule.get("remote_ip")

        parts = [
            "netsh advfirewall firewall add rule",
            f'name="{name}"',
            f"dir={direction}",
            f"action={action}",
        ]
        if protocol and protocol != "any":
            parts.append(f"protocol={protocol}")
        if local_port:
            parts.append(f"localport={local_port}")
        if remote_ip:
            parts.append(f"remoteip={remote_ip}")

        cmd = " ".join(parts)
        r = _run(cmd, timeout=15)
        entry = {"name": name, "direction": direction, "action": action, "success": r["success"]}
        if not r["success"]:
            entry["error"] = r.get("stderr") or r.get("stdout")
        else:
            success_count += 1
        results.append(entry)
    return {
        "success": success_count == len(rules),
        "applied": success_count,
        "failed": len(rules) - success_count,
        "details": results,
    }


def delete_firewall_rule(name: str) -> Dict[str, Any]:
    r = _run(f'netsh advfirewall firewall delete rule name="{name}"', timeout=15)
    return {"success": r["success"], "name": name, "message": r.get("stdout")}


def list_firewall_rules(direction: str = "in") -> Dict[str, Any]:
    """列出防火墙规则（用 PowerShell Get-NetFirewallRule 跨语言环境稳定）"""
    dir_filter = "Inbound" if direction == "in" else "Outbound"
    ps_script = (
        "Get-NetFirewallRule -Direction " + dir_filter + " | "
        "Select-Object DisplayName,Direction,Action,Enabled | "
        "Select-Object -First 80 | ConvertTo-Json -Compress"
    )
    r = _run('powershell -NoProfile -Command "' + ps_script + '"', timeout=40)
    rules = []
    raw = (r.get("stdout") or "").strip()
    if raw:
        try:
            data = json.loads(raw)
            if isinstance(data, dict):
                data = [data]
            if isinstance(data, list):
                for item in data:
                    rules.append({
                        "rule_name": str(item.get("DisplayName") or "")[:255],
                        "direction": str(item.get("Direction") or "")[:20],
                        "action": str(item.get("Action") or "")[:20],
                        "enabled": str(item.get("Enabled") or "")[:20],
                    })
        except (json.JSONDecodeError, TypeError):
            pass
    return {"success": r["success"], "rules": rules[:200], "count": len(rules)}


# ============================================================
# USB 管控 (注册表 + WMI 设备枚举)
# ============================================================

USBSTOR_REG_PATH = r"SYSTEM\CurrentControlSet\Services\USBSTOR"


def get_usb_storage_policy() -> Dict[str, Any]:
    """查询USB存储设备禁用状态 (注册表 USBSTOR Start 键)"""
    if not winreg:
        return {"success": False, "error": "winreg unavailable"}
    try:
        key = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, USBSTOR_REG_PATH, 0, winreg.KEY_READ)
        start_val, _ = winreg.QueryValueEx(key, "Start")
        winreg.CloseKey(key)
        # 3=允许(默认启动), 4=禁用
        return {"success": True, "usb_storage_blocked": start_val == 4, "start_value": start_val}
    except FileNotFoundError:
        return {"success": True, "usb_storage_blocked": False, "start_value": 3, "note": "key not found, default allowed"}
    except Exception as exc:
        return {"success": False, "error": str(exc)}


def set_usb_storage_policy(block: bool = True) -> Dict[str, Any]:
    """禁用/启用USB存储设备 (修改注册表 USBSTOR Start 键，需重启或重新插拔生效)"""
    if not winreg:
        return {"success": False, "error": "winreg unavailable"}
    target = 4 if block else 3
    try:
        key = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, USBSTOR_REG_PATH, 0, winreg.KEY_SET_VALUE)
        winreg.SetValueEx(key, "Start", 0, winreg.REG_DWORD, target)
        winreg.CloseKey(key)
        return {"success": True, "usb_storage_blocked": block, "start_value": target}
    except FileNotFoundError:
        return {"success": False, "error": "USBSTOR service key not found"}
    except Exception as exc:
        return {"success": False, "error": str(exc)}


def enumerate_usb_devices() -> Dict[str, Any]:
    """枚举当前USB设备（WMI Win32_PnPEntity）"""
    try:
        import pythoncom
        import win32com.client
        pythoncom.CoInitialize()
        try:
            wmi = win32com.client.Dispatch("WbemScripting.SWbemLocator")
            svc = wmi.ConnectServer(".", r"root\cimv2")
            devices = []
            for d in svc.ExecQuery("SELECT * FROM Win32_PnPEntity WHERE PnPClass='USB' OR PnPClass='USBStore' OR PnPClass='HID' OR PnPClass='MTP' OR PnPClass='Image' OR PnPClass='Net'"):
                device_id = str(d.DeviceID or "")
                vid_pid = ""
                for part in device_id.split("\\"):
                    if part.startswith("VID_") and "PID_" in part:
                        vid_pid = part
                        break
                devices.append({
                    "device_id": device_id[:255],
                    "vid_pid": vid_pid[:20],
                    "friendly_name": str(d.Name or "")[:255],
                    "device_class": str(d.PNPClass or d.PnPClass or "")[:50],
                    "manufacturer": str(d.Manufacturer or "")[:255],
                    "status": str(d.Status or "")[:50],
                })
            return {"success": True, "devices": devices, "count": len(devices)}
        finally:
            pythoncom.CoUninitialize()
    except Exception as exc:
        return {"success": False, "devices": [], "error": str(exc)}


# ============================================================
# 进程管控 (psutil + taskkill)
# ============================================================

def list_processes() -> Dict[str, Any]:
    """列出当前进程（前100）"""
    procs = []
    for p in psutil.process_iter(["pid", "name", "username", "exe", "cmdline"]):
        try:
            info = p.info
            procs.append({
                "pid": info.get("pid"),
                "name": (info.get("name") or "")[:255],
                "user": (info.get("username") or "")[:100],
                "path": (info.get("exe") or "")[:500],
                "cmd": " ".join(info.get("cmdline") or [])[:500],
            })
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    return {"success": True, "processes": procs[:100], "count": len(procs)}


def kill_process(pid: Optional[int] = None, name: Optional[str] = None) -> Dict[str, Any]:
    """结束进程（按 pid 或 name）"""
    killed = []
    failed = []
    if pid:
        try:
            p = psutil.Process(int(pid))
            p_name = p.name()
            p.kill()
            p.wait(timeout=5)
            killed.append({"pid": pid, "name": p_name})
        except Exception as exc:
            failed.append({"pid": pid, "error": str(exc)})
    if name:
        for p in psutil.process_iter(["pid", "name"]):
            try:
                if p.info["name"] and p.info["name"].lower() == name.lower():
                    p.kill()
                    killed.append({"pid": p.info["pid"], "name": name})
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
    return {
        "success": len(failed) == 0 and len(killed) > 0,
        "killed": killed,
        "failed": failed,
    }


def scan_processes_against_blacklist(blacklist: List[str]) -> Dict[str, Any]:
    """扫描进程是否命中黑名单"""
    hits = []
    bl_lower = [b.lower().strip() for b in blacklist if b.strip()]
    for p in psutil.process_iter(["pid", "name", "exe"]):
        try:
            info = p.info
            pname = (info.get("name") or "").lower()
            pexe = (info.get("exe") or "").lower()
            for bl in bl_lower:
                if bl in pname or bl in pexe:
                    hits.append({"pid": info["pid"], "name": info.get("name"), "matched": bl})
                    break
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    return {"success": True, "hits": hits, "hit_count": len(hits)}


# ============================================================
# 文件保护 (哈希基线 + 异常检测)
# ============================================================

def build_file_baseline(dir_path: str) -> Dict[str, Any]:
    """建立目录文件哈希基线"""
    d = Path(dir_path)
    if not d.exists() or not d.is_dir():
        return {"success": False, "error": f"Directory not found: {dir_path}"}
    baselines = []
    count = 0
    for f in d.rglob("*"):
        if f.is_file() and count < 2000:
            try:
                h = hashlib.md5()
                with open(f, "rb") as fp:
                    for chunk in iter(lambda: fp.read(65536), b""):
                        h.update(chunk)
                baselines.append({
                    "file_path": str(f.relative_to(d)),
                    "file_size": f.stat().st_size,
                    "md5": h.hexdigest(),
                })
                count += 1
            except (PermissionError, OSError):
                continue
    return {"success": True, "dir": dir_path, "files": baselines, "count": count}


def check_file_anomalies(dir_path: str, old_baselines: Dict[str, str]) -> Dict[str, Any]:
    """对比基线检测文件异常"""
    d = Path(dir_path)
    anomalies = []
    if not d.exists():
        return {"success": False, "error": f"Directory not found: {dir_path}"}
    current = {}
    for f in d.rglob("*"):
        if f.is_file():
            try:
                h = hashlib.md5()
                with open(f, "rb") as fp:
                    for chunk in iter(lambda: fp.read(65536), b""):
                        h.update(chunk)
                rel = str(f.relative_to(d))
                current[rel] = h.hexdigest()
            except (PermissionError, OSError):
                continue
    # 检测修改/新增
    for rel, md5 in current.items():
        if rel in old_baselines:
            if old_baselines[rel] != md5:
                anomalies.append({"file_path": rel, "anomaly_type": "modified", "old_md5": old_baselines[rel], "new_md5": md5})
        else:
            anomalies.append({"file_path": rel, "anomaly_type": "created", "new_md5": md5})
    # 检测删除
    for rel in old_baselines:
        if rel not in current:
            anomalies.append({"file_path": rel, "anomaly_type": "deleted"})
    # 批量变更检测
    if len(anomalies) > 50:
        anomalies.append({"anomaly_type": "mass_change", "count": len(anomalies), "warning": "Possible ransomware activity"})
    return {"success": True, "dir": dir_path, "anomalies": anomalies, "anomaly_count": len(anomalies)}


# ============================================================
# 行为监控采集
# ============================================================

def collect_startup_items() -> Dict[str, Any]:
    """采集启动项（注册表 Run 键）"""
    if not winreg:
        return {"success": False, "error": "winreg unavailable"}
    items = []
    run_keys = [
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Run", "HKLM"),
        (winreg.HKEY_CURRENT_USER, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Run", "HKCU"),
    ]
    for hive, path, label in run_keys:
        try:
            key = winreg.OpenKey(hive, path, 0, winreg.KEY_READ)
            i = 0
            while True:
                try:
                    name, value, _ = winreg.EnumValue(key, i)
                    items.append({"source": label, "name": name, "value": str(value)[:500]})
                    i += 1
                except OSError:
                    break
            winreg.CloseKey(key)
        except FileNotFoundError:
            continue
        except Exception:
            continue
    return {"success": True, "startup_items": items, "count": len(items)}


def collect_services() -> Dict[str, Any]:
    """采集服务列表"""
    r = _run("sc query state= all", timeout=15)
    services = []
    current = {}
    for line in (r.get("stdout") or "").splitlines():
        line = line.strip()
        if line.startswith("SERVICE_NAME:"):
            if current:
                services.append(current)
            current = {"name": line.split(":", 1)[1].strip()}
        elif line.startswith("DISPLAY_NAME:"):
            current["display"] = line.split(":", 1)[1].strip()[:255]
        elif line.startswith("STATE:"):
            current["state"] = line.split(":", 1)[1].strip()[:50]
    if current:
        services.append(current)
    return {"success": r["success"], "services": services[:200], "count": len(services)}


def collect_network_connections() -> Dict[str, Any]:
    """采集网络连接"""
    conns = []
    try:
        for c in psutil.net_connections(kind="inet"):
            if c.status and c.laddr and c.raddr:
                conns.append({
                    "local": f"{c.laddr.ip}:{c.laddr.port}",
                    "remote": f"{c.raddr.ip}:{c.raddr.port}",
                    "status": c.status,
                    "pid": c.pid,
                })
    except (psutil.AccessDenied, psutil.NoSuchProcess):
        pass
    return {"success": True, "connections": conns[:200], "count": len(conns)}


# ============================================================
# 终端隔离 (防火墙阻断，保留控制端口)
# ============================================================

def isolate_host(control_port: int = 9001) -> Dict[str, Any]:
    """隔离终端：阻断所有入站连接，但保留控制端口"""
    results = []
    # 阻断所有入站
    r1 = _run('netsh advfirewall firewall add rule name="zv-isolate-block-in" dir=in action=block protocol=any', timeout=15)
    results.append({"rule": "block-all-inbound", "success": r1["success"]})
    # 允许控制端口入站（覆盖阻断规则，需更高优先级——放最后更具体）
    r2 = _run(f'netsh advfirewall firewall add rule name="zv-isolate-allow-control" dir=in action=allow protocol=TCP localport={control_port}', timeout=15)
    results.append({"rule": "allow-control-port", "success": r2["success"]})
    # 允许 RDP 用于远控恢复（可选）
    r3 = _run('netsh advfirewall firewall add rule name="zv-isolate-allow-rdp" dir=in action=allow protocol=TCP localport=3389', timeout=15)
    results.append({"rule": "allow-rdp", "success": r3["success"]})
    return {
        "success": all(r["success"] for r in results),
        "control_port": control_port,
        "rules": results,
        "note": "Host isolated. Inbound blocked except control port and RDP.",
    }


def unisolate_host() -> Dict[str, Any]:
    """解除隔离：删除隔离规则"""
    r1 = _run('netsh advfirewall firewall delete rule name="zv-isolate-block-in"', timeout=15)
    r2 = _run('netsh advfirewall firewall delete rule name="zv-isolate-allow-control"', timeout=15)
    r3 = _run('netsh advfirewall firewall delete rule name="zv-isolate-allow-rdp"', timeout=15)
    return {"success": True, "removed": [r1["success"], r2["success"], r3["success"]]}


# ============================================================
# 安全命令分发器
# ============================================================

def execute_security_command(command_type: str, params: Dict[str, Any]) -> Dict[str, Any]:
    """统一安全命令分发"""
    handlers = {
        "firewall_status": lambda p: get_firewall_status(),
        "firewall_enable": lambda p: enable_firewall(p.get("enable", True)),
        "firewall_apply": lambda p: apply_firewall_rules(p.get("rules", [])),
        "firewall_delete_rule": lambda p: delete_firewall_rule(p.get("name", "")),
        "firewall_list_rules": lambda p: list_firewall_rules(p.get("direction", "in")),
        "usb_status": lambda p: get_usb_storage_policy(),
        "usb_block": lambda p: set_usb_storage_policy(block=True),
        "usb_allow": lambda p: set_usb_storage_policy(block=False),
        "usb_enumerate": lambda p: enumerate_usb_devices(),
        "process_list": lambda p: list_processes(),
        "kill_process": lambda p: kill_process(p.get("pid"), p.get("name")),
        "process_scan_blacklist": lambda p: scan_processes_against_blacklist(p.get("blacklist", [])),
        "file_baseline": lambda p: build_file_baseline(p.get("dir_path", "")),
        "file_check": lambda p: check_file_anomalies(p.get("dir_path", ""), p.get("baselines", {})),
        "collect_startup": lambda p: collect_startup_items(),
        "collect_services": lambda p: collect_services(),
        "collect_network": lambda p: collect_network_connections(),
        "isolate_host": lambda p: isolate_host(p.get("control_port", 9001)),
        "unisolate_host": lambda p: unisolate_host(),
        "security_scan": lambda p: _full_security_scan(),
    }
    handler = handlers.get(command_type)
    if not handler:
        return {"success": False, "error": f"Unknown security command: {command_type}"}
    try:
        return handler(params)
    except Exception as exc:
        return {"success": False, "error": f"{type(exc).__name__}: {exc}"}


def _full_security_scan() -> Dict[str, Any]:
    """完整安全扫描（组合采集）"""
    return {
        "success": True,
        "firewall": get_firewall_status(),
        "usb": get_usb_storage_policy(),
        "startup": collect_startup_items(),
        "network": collect_network_connections(),
        "process_count": len(list_processes().get("processes", [])),
        "scan_time": time.strftime("%Y-%m-%d %H:%M:%S"),
    }