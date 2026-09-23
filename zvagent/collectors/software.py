# -*- coding: utf-8 -*-
"""软件清单采集与变更哈希（V1.8.2 迁入自 cmdb_agent_core.py）。"""
from __future__ import annotations

import hashlib
import json
import os


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


