from __future__ import annotations

import os
import ctypes
import json
import shutil
import subprocess
import time
from ctypes import wintypes
from pathlib import Path
from typing import Any, Callable

from Common.runtime_paths import get_app_dir, get_program_data_dir


class _DISPLAY_DEVICEW(ctypes.Structure):
    _fields_ = [
        ("cb", wintypes.DWORD),
        ("DeviceName", wintypes.WCHAR * 32),
        ("DeviceString", wintypes.WCHAR * 128),
        ("StateFlags", wintypes.DWORD),
        ("DeviceID", wintypes.WCHAR * 128),
        ("DeviceKey", wintypes.WCHAR * 128),
    ]


class VirtualDisplayProvider:
    DISPLAY_DEVICE_ATTACHED_TO_DESKTOP = 0x00000001
    DISPLAY_DEVICE_MIRRORING_DRIVER = 0x00000008
    DISPLAY_DEVICE_REMOTE = 0x04000000
    DISPLAY_DEVICE_DISCONNECT = 0x02000000
    SM_CMONITORS = 80

    def __init__(self, logger: Callable[[str], None] | None = None):
        self.logger = logger
        self._cached_status: dict[str, Any] | None = None
        self._cached_at = 0.0

    def get_status(self, *, force_refresh: bool = False) -> dict[str, Any]:
        # 环境变量开关：禁用虚拟显示器状态查询（避免VMware下反复PowerShell查询+修复死循环）
        if os.environ.get("ZVIEW_DISABLE_VIRTUAL_DISPLAY", "").strip() in ("1", "true", "True"):
            return {
                "provisioning_state": "attached",
                "attached_virtual_display": True,
                "virtual_display_attached": True,
                "physical_display_attached": True,
                "skipped_by_env": True,
                "available_tools": {},
                "hardware_ids": [],
            }
        if (
            not force_refresh
            and self._cached_status is not None
            and (time.time() - self._cached_at) < 5.0
        ):
            return dict(self._cached_status)

        status = self._collect_status()
        self._cached_status = dict(status)
        self._cached_at = time.time()
        return dict(status)

    def ensure_attached(self) -> dict[str, Any]:
        # 环境变量开关：禁用虚拟显示器修复（VMware/RDP环境下用现有显示器捕获，避免修复死循环）
        if os.environ.get("ZVIEW_DISABLE_VIRTUAL_DISPLAY", "").strip() in ("1", "true", "True"):
            return {
                "action": "ensure_attached",
                "changed": False,
                "provisioning_state": "attached",
                "attached_virtual_display": True,
                "skipped_by_env": True,
            }
        status = self.get_status(force_refresh=True)
        tools = status.get("available_tools") or {}
        if not tools.get("pnputil") and not tools.get("devcon"):
            status["action"] = "ensure_attached"
            status["changed"] = False
            status["error"] = "provisioning_tools_unavailable"
            return status

        changed = False
        notes = list(status.get("notes") or [])
        provisioning_state = str(status.get("provisioning_state") or "unknown")
        inf_path = status.get("driver_inf_path") or ""
        hardware_ids = self._normalize_string_list(status.get("hardware_ids") or [])
        install_method = str(status.get("preferred_install_method_resolved") or "auto")

        if provisioning_state == "attached":
            status["action"] = "ensure_attached"
            status["changed"] = False
            return status

        if provisioning_state in {
            "driver_package_ready_install_pending",
            "installed_missing_enablement",
            "installed_detached",
        }:
            if provisioning_state == "driver_package_ready_install_pending" and inf_path:
                changed = bool(
                    self._install_driver_package(
                        inf_path=inf_path,
                        hardware_ids=hardware_ids,
                        install_method=install_method,
                        tools=tools,
                        notes=notes,
                    )
                    or changed
                )

            self._scan_devices(tools=tools, notes=notes)

            devices = self._query_virtual_display_devices(
                manifest=status.get("driver_manifest") or {}
            )
            device = self._select_best_device(
                devices,
                manifest=status.get("driver_manifest") or {},
            )
            instance_id = str((device or {}).get("InstanceId") or "").strip()
            if instance_id:
                if str((device or {}).get("Status") or "").strip().lower() not in {"ok", "unknown"}:
                    changed = bool(
                        self._enable_device(
                            instance_id=instance_id,
                            tools=tools,
                            notes=notes,
                        )
                        or changed
                    )
                changed = bool(
                    self._restart_device(
                        instance_id=instance_id,
                        tools=tools,
                        notes=notes,
                        allow_failure=True,
                    )
                    or changed
                )
            else:
                if inf_path and hardware_ids and tools.get("devcon"):
                    changed = bool(
                        self._instantiate_virtual_display_device(
                            inf_path=inf_path,
                            hardware_ids=hardware_ids,
                            tools=tools,
                            notes=notes,
                        )
                        or changed
                    )
                    self._scan_devices(tools=tools, notes=notes)
                else:
                    notes.append("virtual_display_device_instance_not_found_after_install")

        refreshed = self.get_status(force_refresh=True)
        refreshed["action"] = "ensure_attached"
        refreshed["changed"] = bool(changed)
        refreshed["notes"] = self._merge_notes(refreshed.get("notes") or [], notes)
        return refreshed

    def repair(self) -> dict[str, Any]:
        if os.environ.get("ZVIEW_DISABLE_VIRTUAL_DISPLAY", "").strip() in ("1", "true", "True"):
            return {
                "action": "repair",
                "changed": False,
                "provisioning_state": "attached",
                "attached_virtual_display": True,
                "skipped_by_env": True,
            }
        status = self.get_status(force_refresh=True)
        tools = status.get("available_tools") or {}
        if not tools.get("pnputil") and not tools.get("devcon"):
            status["action"] = "repair"
            status["changed"] = False
            status["error"] = "provisioning_tools_unavailable"
            return status

        changed = False
        notes = list(status.get("notes") or [])
        devices = self._query_virtual_display_devices(
            manifest=status.get("driver_manifest") or {}
        )
        device = self._select_best_device(
            devices,
            manifest=status.get("driver_manifest") or {},
        )
        instance_id = str((device or {}).get("InstanceId") or "").strip()
        if instance_id:
            changed = bool(
                self._disable_device(
                    instance_id=instance_id,
                    tools=tools,
                    notes=notes,
                    allow_failure=True,
                )
                or changed
            )
            changed = bool(
                self._enable_device(
                    instance_id=instance_id,
                    tools=tools,
                    notes=notes,
                    allow_failure=True,
                )
                or changed
            )
            changed = bool(
                self._restart_device(
                    instance_id=instance_id,
                    tools=tools,
                    notes=notes,
                    allow_failure=True,
                )
                or changed
            )
        elif status.get("driver_inf_path") and status.get("hardware_ids") and tools.get("devcon"):
            changed = bool(
                self._instantiate_virtual_display_device(
                    inf_path=str(status.get("driver_inf_path") or ""),
                    hardware_ids=self._normalize_string_list(status.get("hardware_ids") or []),
                    tools=tools,
                    notes=notes,
                )
                or changed
            )

        ensure_status = self.ensure_attached()
        ensure_status["action"] = "repair"
        ensure_status["changed"] = bool(changed or ensure_status.get("changed"))
        ensure_status["notes"] = self._merge_notes(ensure_status.get("notes") or [], notes)
        return ensure_status

    def _collect_status(self) -> dict[str, Any]:
        package = self._discover_driver_package()
        tools = self._discover_tools(package)
        devices = self._query_virtual_display_devices(manifest=package["manifest"])
        device = self._select_best_device(devices, manifest=package["manifest"])
        display_inventory = self._query_display_inventory(
            manifest=package["manifest"],
            device=device,
        )

        notes: list[str] = []
        available = bool(
            tools.get("powershell")
            and (
                tools.get("pnputil")
                or (
                    tools.get("devcon")
                    and package["inf_path"]
                    and package["hardware_ids"]
                )
            )
        )
        can_provision = bool(available and package["has_complete_package"] and package["has_trusted_signature"])

        if not tools.get("pnputil"):
            notes.append("pnputil_unavailable")
        if not tools.get("powershell"):
            notes.append("powershell_unavailable")
        if package["manifest_loaded"]:
            notes.append("virtual_display_manifest_loaded")
        if package["devcon_path"]:
            notes.append("virtual_display_devcon_available")
        if package["hardware_ids"]:
            notes.append("virtual_display_manifest_hardware_ids_present")
        for item in package["manifest_notes"]:
            if item not in notes:
                notes.append(item)
        for item in display_inventory.get("notes") or []:
            if item not in notes:
                notes.append(item)

        attached_confirmed_by_inventory = bool(
            display_inventory.get("matched_virtual_attached_count", 0) > 0
        )
        attached_inferred_from_device = bool(
            device is not None and self._is_attached_like(device, manifest=package["manifest"])
        )
        attached_virtual_display = bool(
            attached_confirmed_by_inventory or attached_inferred_from_device
        )
        device_attached_confidence = "none"
        if attached_confirmed_by_inventory:
            device_attached_confidence = "confirmed_by_display_inventory"
            notes.append("virtual_display_attached_confirmed_by_display_inventory")
        elif attached_inferred_from_device:
            device_attached_confidence = "inferred_from_pnp_device"
            notes.append("virtual_display_attached_inferred_from_pnp_device")
        elif device is not None:
            device_attached_confidence = "device_present_not_attached"

        provisioning_state = "unknown"
        if not available:
            provisioning_state = "provisioning_tools_unavailable"
        elif not package["package_root_exists"]:
            provisioning_state = "driver_package_missing"
        elif not package["inf_path"]:
            provisioning_state = "driver_package_present_missing_inf"
        elif not package["has_complete_package"]:
            provisioning_state = "driver_package_incomplete"
        elif not package["has_trusted_signature"]:
            provisioning_state = "driver_package_unsigned_or_untrusted"
        elif device is None:
            provisioning_state = "driver_package_ready_install_pending"
        else:
            status_name = str(device.get("Status") or "").strip().lower()
            problem_code = str(device.get("ProblemCode") or "").strip()
            if status_name == "ok":
                provisioning_state = "attached" if attached_virtual_display else "installed_detached"
            elif problem_code:
                provisioning_state = "installed_missing_enablement"
            else:
                provisioning_state = "installed_detached"

        if device is not None:
            notes.append("virtual_display_device_detected")
        if package["has_complete_package"]:
            notes.append("virtual_display_driver_package_present")
        if package["has_trusted_signature"]:
            notes.append("virtual_display_driver_catalog_signature_valid")
        elif package["has_complete_package"]:
            notes.append("virtual_display_driver_catalog_signature_not_valid")

        status = {
            "provider": "virtual_display_provider",
            "can_provision_virtual_display": bool(can_provision),
            "provisioning_state": provisioning_state,
            "package_root": package["package_root"],
            "package_root_exists": bool(package["package_root_exists"]),
            "driver_manifest_path": package["manifest_path"],
            "driver_manifest_loaded": bool(package["manifest_loaded"]),
            "driver_manifest_error": package["manifest_error"],
            "driver_manifest": dict(package["manifest"]),
            "manifest_notes": list(package["manifest_notes"]),
            "driver_inf_path": package["inf_path"],
            "driver_catalog_path": package["cat_path"],
            "driver_binary_path": package["sys_path"],
            "devcon_executable_path": package["devcon_path"],
            "driver_package_complete": bool(package["has_complete_package"]),
            "driver_package_signed": bool(package["has_trusted_signature"]),
            "driver_catalog_signature_status": package["cat_signature_status"],
            "driver_binary_signature_status": package["sys_signature_status"],
            "preferred_install_method": package["preferred_install_method"],
            "preferred_install_method_resolved": self._resolve_install_method(
                package["preferred_install_method"],
                tools=tools,
                has_hardware_ids=bool(package["hardware_ids"]),
            ),
            "hardware_ids": list(package["hardware_ids"]),
            "friendly_name_keywords": list(package["friendly_name_keywords"]),
            "instance_id_keywords": list(package["instance_id_keywords"]),
            "attach_keywords": list(package["attach_keywords"]),
            "available_tools": tools,
            "installed_device_present": bool(device is not None),
            "attached_virtual_display": bool(provisioning_state == "attached"),
            "device_instance_id": str((device or {}).get("InstanceId") or ""),
            "device_name": str((device or {}).get("FriendlyName") or ""),
            "device_status": str((device or {}).get("Status") or ""),
            "device_problem_code": str((device or {}).get("ProblemCode") or ""),
            "device_class": str((device or {}).get("Class") or ""),
            "device_attached_to_desktop": bool(attached_confirmed_by_inventory),
            "device_attached_confidence": device_attached_confidence,
            "devices": devices,
            "display_inventory_provider": str(display_inventory.get("provider") or ""),
            "display_inventory_virtual_adapter_count": int(
                display_inventory.get("matched_virtual_adapter_count") or 0
            ),
            "display_inventory_virtual_attached_count": int(
                display_inventory.get("matched_virtual_attached_count") or 0
            ),
            "display_inventory_attached_display_count": int(
                display_inventory.get("attached_display_count") or 0
            ),
            "display_inventory_render_monitor_count": int(
                display_inventory.get("render_monitor_count") or 0
            ),
            "display_inventory_remote_adapter_present": bool(
                display_inventory.get("remote_adapter_present", False)
            ),
            "display_inventory_matched_virtual_adapters": list(
                display_inventory.get("matched_virtual_adapters") or []
            ),
            "notes": notes,
        }
        return status

    def _discover_driver_package(self) -> dict[str, Any]:
        roots = [
            get_app_dir() / "Drivers" / "VirtualDisplay",
            get_app_dir().parent / "Drivers" / "VirtualDisplay",
            get_program_data_dir() / "CMDB-Agent" / "Drivers" / "VirtualDisplay",
        ]

        resolved_root: Path | None = None
        resolved_manifest_path = ""
        manifest_loaded = False
        manifest_error = ""
        manifest: dict[str, Any] = {}
        manifest_notes: list[str] = []
        inf_path = ""
        cat_path = ""
        sys_path = ""
        devcon_path = ""
        has_complete_package = False
        has_trusted_signature = False
        cat_signature_status = "missing"
        sys_signature_status = "missing"
        preferred_install_method = "auto"
        hardware_ids: list[str] = []
        friendly_name_keywords: list[str] = []
        instance_id_keywords: list[str] = []
        attach_keywords: list[str] = []
        for root in roots:
            if not root.exists():
                continue
            resolved_root = root
            manifest_path = root / "driver_manifest.json"
            if manifest_path.exists():
                resolved_manifest_path = str(manifest_path)
                manifest_loaded, manifest_error, manifest = self._load_driver_manifest(manifest_path)
                manifest_notes = self._merge_notes(
                    manifest_notes,
                    ["virtual_display_manifest_parse_failed"] if manifest_error else [],
                )
            preferred_install_method = str(manifest.get("preferred_install_method") or "auto")
            hardware_ids = self._normalize_string_list(manifest.get("hardware_ids") or [])
            friendly_name_keywords = self._normalize_string_list(
                manifest.get("friendly_name_keywords") or []
            )
            instance_id_keywords = self._normalize_string_list(
                manifest.get("instance_id_keywords") or []
            )
            attach_keywords = self._normalize_string_list(manifest.get("attach_keywords") or [])

            inf = self._resolve_package_file(root, manifest.get("inf_relative_path"), "*.inf")
            cat = self._resolve_package_file(root, manifest.get("catalog_relative_path"), "*.cat")
            sys_file = self._resolve_package_file(root, manifest.get("binary_relative_path"), "*.sys")
            devcon = self._resolve_package_file(root, manifest.get("devcon_relative_path"), "devcon*.exe")
            inf_path = str(inf) if inf else ""
            cat_path = str(cat) if cat else ""
            sys_path = str(sys_file) if sys_file else ""
            devcon_path = str(devcon) if devcon else ""
            has_complete_package = bool(inf and cat and sys_file)
            cat_signature_status = self._get_authenticode_signature_status(cat) if cat else "missing"
            sys_signature_status = self._get_authenticode_signature_status(sys_file) if sys_file else "missing"
            has_trusted_signature = bool(cat_signature_status.lower() == "valid")
            if inf:
                break

        return {
            "package_root": str(resolved_root) if resolved_root else str(roots[0]),
            "package_root_exists": bool(resolved_root and resolved_root.exists()),
            "manifest_path": resolved_manifest_path,
            "manifest_loaded": bool(manifest_loaded),
            "manifest_error": manifest_error,
            "manifest": manifest,
            "manifest_notes": manifest_notes,
            "inf_path": inf_path,
            "cat_path": cat_path,
            "sys_path": sys_path,
            "devcon_path": devcon_path,
            "has_complete_package": bool(has_complete_package),
            "has_trusted_signature": bool(has_trusted_signature),
            "cat_signature_status": cat_signature_status,
            "sys_signature_status": sys_signature_status,
            "preferred_install_method": preferred_install_method,
            "hardware_ids": hardware_ids,
            "friendly_name_keywords": friendly_name_keywords,
            "instance_id_keywords": instance_id_keywords,
            "attach_keywords": attach_keywords,
        }

    def _discover_tools(self, package: dict[str, Any]) -> dict[str, Any]:
        devcon_path = str(package.get("devcon_path") or "").strip()
        powershell_path = str(shutil.which("powershell") or shutil.which("pwsh") or "").strip()
        return {
            "pnputil": bool(shutil.which("pnputil")),
            "powershell": bool(powershell_path),
            "powershell_path": powershell_path,
            "devcon": bool(devcon_path or shutil.which("devcon")),
            "devcon_path": devcon_path or str(shutil.which("devcon") or ""),
        }

    def _query_virtual_display_devices(
        self,
        *,
        manifest: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        powershell_path = str(
            shutil.which("powershell") or shutil.which("pwsh") or "powershell"
        ).strip()
        script = r"""
$ErrorActionPreference = 'Stop'
$devices = Get-PnpDevice -Class Display | ForEach-Object {
    $instanceId = ''
    $problemCode = ''
    try { $instanceId = $_.InstanceId } catch {}
    try { $problemCode = $_.ProblemCode } catch {}
    [PSCustomObject]@{
        FriendlyName = [string]$_.FriendlyName
        Class = [string]$_.Class
        Status = [string]$_.Status
        InstanceId = [string]$instanceId
        ProblemCode = [string]$problemCode
    }
}
$devices | ConvertTo-Json -Depth 4 -Compress
"""
        output = self._run_command(
            [powershell_path, "-NoProfile", "-Command", script],
            allow_failure=True,
        )
        if self.logger is not None:
            try:
                preview = (output or "")[:500]
                self.logger(f"virtual_display_query_output preview={preview!r}")
            except Exception:
                pass
        if not output:
            return []
        try:
            parsed = json.loads(output)
        except json.JSONDecodeError as exc:
            if self.logger is not None:
                self.logger(f"virtual_display_query_json_decode_error err={exc} output={output[:500]!r}")
            return []

        if isinstance(parsed, dict):
            rows = [parsed]
        elif isinstance(parsed, list):
            rows = parsed
        else:
            rows = []

        filtered: list[dict[str, Any]] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            if self._is_remote_display_adapter(row, manifest=manifest):
                continue
            if self._looks_like_virtual_display(row, manifest=manifest):
                filtered.append(row)
        return filtered

    def _query_display_inventory(
        self,
        *,
        manifest: dict[str, Any] | None = None,
        device: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        inventory = {
            "provider": "enum_display_devices",
            "render_monitor_count": 0,
            "attached_display_count": 0,
            "remote_adapter_present": False,
            "matched_virtual_adapter_count": 0,
            "matched_virtual_attached_count": 0,
            "matched_virtual_adapters": [],
            "notes": [],
        }
        try:
            user32 = ctypes.WinDLL("user32", use_last_error=True)
            user32.EnumDisplayDevicesW.argtypes = [
                wintypes.LPCWSTR,
                wintypes.DWORD,
                ctypes.POINTER(_DISPLAY_DEVICEW),
                wintypes.DWORD,
            ]
            user32.EnumDisplayDevicesW.restype = wintypes.BOOL
            user32.GetSystemMetrics.argtypes = [wintypes.INT]
            user32.GetSystemMetrics.restype = wintypes.INT
        except Exception as exc:
            inventory["notes"].append("display_inventory_probe_unavailable")
            if self.logger is not None:
                self.logger(f"virtual display inventory probe unavailable: {exc}")
            return inventory

        matched_virtual_adapters: list[dict[str, Any]] = []
        attached_display_count = 0
        remote_adapter_present = False
        adapter_index = 0

        while True:
            adapter = _DISPLAY_DEVICEW()
            adapter.cb = ctypes.sizeof(_DISPLAY_DEVICEW)
            if not user32.EnumDisplayDevicesW(None, adapter_index, ctypes.byref(adapter), 0):
                break

            flags = int(adapter.StateFlags or 0)
            row = {
                "DeviceName": str(adapter.DeviceName or "").strip(),
                "DeviceString": str(adapter.DeviceString or "").strip(),
                "DeviceID": str(adapter.DeviceID or "").strip(),
                "DeviceKey": str(adapter.DeviceKey or "").strip(),
                "attached": bool(flags & self.DISPLAY_DEVICE_ATTACHED_TO_DESKTOP),
                "mirroring": bool(flags & self.DISPLAY_DEVICE_MIRRORING_DRIVER),
                "remote": bool(flags & self.DISPLAY_DEVICE_REMOTE),
                "disconnected": bool(flags & self.DISPLAY_DEVICE_DISCONNECT),
            }
            row["remote"] = bool(
                row["remote"]
                or self._looks_like_remote_adapter_text(
                    row["DeviceName"],
                    row["DeviceString"],
                    row["DeviceID"],
                    row["DeviceKey"],
                    manifest=manifest,
                )
            )
            if row["remote"]:
                remote_adapter_present = True
            if row["attached"] and not row["mirroring"] and not row["disconnected"]:
                attached_display_count += 1
            if self._display_adapter_matches_virtual_device(row, manifest=manifest, device=device):
                matched_virtual_adapters.append(row)
            adapter_index += 1

        matched_virtual_attached_count = sum(
            1
            for item in matched_virtual_adapters
            if item.get("attached") and not item.get("mirroring") and not item.get("disconnected")
        )
        notes: list[str] = []
        if matched_virtual_attached_count > 0:
            notes.append("virtual_display_inventory_confirms_attached_adapter")
        elif matched_virtual_adapters:
            notes.append("virtual_display_inventory_detects_adapter_without_desktop_attachment")
        else:
            notes.append("virtual_display_inventory_did_not_match_adapter")

        inventory.update(
            {
                "render_monitor_count": int(user32.GetSystemMetrics(self.SM_CMONITORS) or 0),
                "attached_display_count": int(attached_display_count),
                "remote_adapter_present": bool(remote_adapter_present),
                "matched_virtual_adapter_count": len(matched_virtual_adapters),
                "matched_virtual_attached_count": int(matched_virtual_attached_count),
                "matched_virtual_adapters": matched_virtual_adapters,
                "notes": notes,
            }
        )
        return inventory

    def _select_best_device(
        self,
        devices: list[dict[str, Any]],
        *,
        manifest: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        if not devices:
            return None
        manifest = manifest or {}
        manifest_hwids = [
            str(h).strip().lower()
            for h in (manifest.get("hardware_ids") or [])
            if str(h).strip()
        ]

        def _rank_key(item: dict[str, Any]) -> tuple:
            instance_id = str(item.get("InstanceId") or "").strip().lower()
            friendly = str(item.get("FriendlyName") or "").strip().lower()
            status_ok = str(item.get("Status") or "").strip().lower() == "ok"
            attached_like = self._is_attached_like(item, manifest=manifest)
            hwid_exact = 0 if (manifest_hwids and instance_id in manifest_hwids) else 1
            return (
                hwid_exact,
                0 if status_ok else 1,
                0 if attached_like else 1,
                friendly,
                instance_id,
            )

        ranked = sorted(devices, key=_rank_key)
        if self.logger is not None:
            try:
                self.logger(
                    f"virtual_display_select_best hwids={manifest_hwids} ranked="
                    f"{[(d.get('FriendlyName'), d.get('InstanceId')) for d in ranked]}"
                )
            except Exception:
                pass
        return ranked[0]

    def _is_remote_display_adapter(
        self,
        row: dict[str, Any],
        *,
        manifest: dict[str, Any] | None = None,
    ) -> bool:
        text = " ".join(
            [
                str(row.get("FriendlyName") or ""),
                str(row.get("InstanceId") or ""),
            ]
        ).lower()
        markers = [
            "remote display adapter",
            "rdpidd",
            "remotedisplayenum",
        ]
        markers.extend(self._normalize_string_list((manifest or {}).get("remote_adapter_keywords") or []))
        return any(
            marker in text
            for marker in markers
        )

    def _looks_like_virtual_display(
        self,
        row: dict[str, Any],
        *,
        manifest: dict[str, Any] | None = None,
    ) -> bool:
        text = " ".join(
            [
                str(row.get("FriendlyName") or ""),
                str(row.get("InstanceId") or ""),
            ]
        ).lower()
        manifest = manifest or {}
        manifest_keywords = self._normalize_string_list(
            list(manifest.get("friendly_name_keywords") or [])
            + list(manifest.get("instance_id_keywords") or [])
            + list(manifest.get("hardware_ids") or [])
        )
        if manifest_keywords and any(marker in text for marker in manifest_keywords):
            return True
        return any(
            marker in text
            for marker in (
                "virtual",
                "indirect",
                "idd",
                "iddsampledriver",
                "displaylink",
                "dummy",
                "usb graphics",
                "msbdd",
            )
        )

    def _is_attached_like(
        self,
        row: dict[str, Any],
        *,
        manifest: dict[str, Any] | None = None,
    ) -> bool:
        text = " ".join(
            [
                str(row.get("FriendlyName") or ""),
                str(row.get("InstanceId") or ""),
            ]
        ).lower()
        markers = self._normalize_string_list((manifest or {}).get("attach_keywords") or [])
        if not markers:
            markers = ["attached", "active"]
        return any(marker in text for marker in markers)

    def _display_adapter_matches_virtual_device(
        self,
        row: dict[str, Any],
        *,
        manifest: dict[str, Any] | None = None,
        device: dict[str, Any] | None = None,
    ) -> bool:
        if self._is_remote_display_adapter(
            {
                "FriendlyName": row.get("DeviceString"),
                "InstanceId": row.get("DeviceID"),
            },
            manifest=manifest,
        ):
            return False

        manifest = manifest or {}
        device = device or {}
        haystack = " ".join(
            [
                str(row.get("DeviceName") or ""),
                str(row.get("DeviceString") or ""),
                str(row.get("DeviceID") or ""),
                str(row.get("DeviceKey") or ""),
            ]
        ).lower()
        direct_tokens = self._normalize_string_list(
            [
                device.get("FriendlyName"),
                device.get("InstanceId"),
                *list(manifest.get("friendly_name_keywords") or []),
                *list(manifest.get("instance_id_keywords") or []),
                *list(manifest.get("hardware_ids") or []),
            ]
        )
        if direct_tokens and any(token in haystack for token in direct_tokens):
            return True
        return self._looks_like_virtual_display(
            {
                "FriendlyName": row.get("DeviceString"),
                "InstanceId": row.get("DeviceID"),
            },
            manifest=manifest,
        )

    def _looks_like_remote_adapter_text(
        self,
        *parts: str,
        manifest: dict[str, Any] | None = None,
    ) -> bool:
        text = " ".join(str(part or "") for part in parts).lower()
        markers = [
            "remote display adapter",
            "rdpidd",
            "remotedisplayenum",
            "remote desktop",
            "terminal server",
            "ms_rdp",
        ]
        markers.extend(
            self._normalize_string_list((manifest or {}).get("remote_adapter_keywords") or [])
        )
        return any(marker in text for marker in markers)

    def _run_pnputil(
        self,
        arguments: list[str],
        notes: list[str],
        *,
        allow_failure: bool = False,
    ) -> None:
        command = ["pnputil", *arguments]
        output = self._run_command(command, allow_failure=allow_failure)
        if output:
            notes.append(f"pnputil:{' '.join(arguments)}")

    def _run_devcon(
        self,
        executable: str,
        arguments: list[str],
        notes: list[str],
        *,
        allow_failure: bool = False,
    ) -> None:
        if not executable:
            return
        output = self._run_command([executable, *arguments], allow_failure=allow_failure)
        if output:
            notes.append(f"devcon:{' '.join(arguments)}")

    def _run_command(self, command: list[str], *, allow_failure: bool = False) -> str:
        try:
            result = subprocess.run(
                command,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="ignore",
                timeout=30,
                check=not allow_failure,
            )
        except Exception as exc:
            if self.logger is not None:
                self.logger(
                    f"virtual display command failed: command={' '.join(command)} error={exc}"
                )
            if allow_failure:
                return ""
            raise

        output = "\n".join(
            part.strip() for part in (result.stdout, result.stderr) if str(part or "").strip()
        ).strip()
        if self.logger is not None:
            self.logger(
                f"virtual display command: command={' '.join(command)} rc={result.returncode}"
            )
        return output

    def _get_authenticode_signature_status(self, path: Path | None) -> str:
        if not path:
            return "missing"
        powershell_path = str(shutil.which("powershell") or shutil.which("pwsh") or "").strip()
        if not powershell_path:
            return "powershell_unavailable"
        script = (
            "$sig = Get-AuthenticodeSignature -LiteralPath "
            + json.dumps(str(path))
            + "; [string]$sig.Status"
        )
        output = self._run_command(
            [powershell_path, "-NoProfile", "-Command", script],
            allow_failure=True,
        )
        return str(output or "unknown").splitlines()[-1].strip() or "unknown"

    def _load_driver_manifest(self, manifest_path: Path) -> tuple[bool, str, dict[str, Any]]:
        try:
            raw = json.loads(manifest_path.read_text(encoding="utf-8"))
        except Exception as exc:
            return False, str(exc), {}

        if not isinstance(raw, dict):
            return False, "manifest_must_be_json_object", {}

        normalized = {
            "preferred_install_method": str(raw.get("preferred_install_method") or "auto").strip().lower(),
            "hardware_ids": self._normalize_string_list(raw.get("hardware_ids") or []),
            "friendly_name_keywords": self._normalize_string_list(
                raw.get("friendly_name_keywords") or []
            ),
            "instance_id_keywords": self._normalize_string_list(
                raw.get("instance_id_keywords") or []
            ),
            "attach_keywords": self._normalize_string_list(raw.get("attach_keywords") or []),
            "remote_adapter_keywords": self._normalize_string_list(
                raw.get("remote_adapter_keywords") or []
            ),
            "inf_relative_path": str(raw.get("inf_relative_path") or "").strip(),
            "catalog_relative_path": str(raw.get("catalog_relative_path") or "").strip(),
            "binary_relative_path": str(raw.get("binary_relative_path") or "").strip(),
            "devcon_relative_path": str(raw.get("devcon_relative_path") or "").strip(),
        }
        if normalized["preferred_install_method"] not in {
            "auto",
            "pnputil",
            "devcon_install",
            "devcon_update",
        }:
            normalized["preferred_install_method"] = "auto"
        return True, "", normalized

    def _resolve_package_file(
        self,
        root: Path,
        relative_path: Any,
        fallback_glob: str,
    ) -> Path | None:
        relative_text = str(relative_path or "").strip()
        if relative_text:
            candidate = (root / relative_text).resolve()
            try:
                candidate.relative_to(root.resolve())
            except ValueError:
                candidate = Path(relative_text)
            if candidate.exists() and candidate.is_file():
                return candidate
        return next(root.rglob(fallback_glob), None)

    def _resolve_install_method(
        self,
        preferred_method: str,
        *,
        tools: dict[str, Any],
        has_hardware_ids: bool,
    ) -> str:
        method = str(preferred_method or "auto").strip().lower()
        if method == "pnputil":
            return "pnputil"
        if method in {"devcon_install", "devcon_update"} and tools.get("devcon") and has_hardware_ids:
            return method
        if tools.get("pnputil"):
            return "pnputil"
        if tools.get("devcon") and has_hardware_ids:
            return "devcon_install"
        return "unavailable"

    def _install_driver_package(
        self,
        *,
        inf_path: str,
        hardware_ids: list[str],
        install_method: str,
        tools: dict[str, Any],
        notes: list[str],
    ) -> bool:
        changed = False
        normalized_method = str(install_method or "auto")
        if normalized_method == "devcon_install" and tools.get("devcon") and hardware_ids:
            changed = bool(
                self._instantiate_virtual_display_device(
                    inf_path=inf_path,
                    hardware_ids=hardware_ids,
                    tools=tools,
                    notes=notes,
                )
                or changed
            )
        elif normalized_method == "devcon_update" and tools.get("devcon") and hardware_ids:
            changed = bool(
                self._update_virtual_display_driver(
                    inf_path=inf_path,
                    hardware_ids=hardware_ids,
                    tools=tools,
                    notes=notes,
                )
                or changed
            )
        if tools.get("pnputil"):
            self._run_pnputil(["/add-driver", str(inf_path), "/install"], notes)
            changed = True
        elif not changed and tools.get("devcon") and hardware_ids:
            changed = bool(
                self._instantiate_virtual_display_device(
                    inf_path=inf_path,
                    hardware_ids=hardware_ids,
                    tools=tools,
                    notes=notes,
                )
                or changed
            )
        return changed

    def _instantiate_virtual_display_device(
        self,
        *,
        inf_path: str,
        hardware_ids: list[str],
        tools: dict[str, Any],
        notes: list[str],
    ) -> bool:
        devcon_path = str(tools.get("devcon_path") or "").strip()
        if not devcon_path or not inf_path or not hardware_ids:
            return False
        changed = False
        for hardware_id in hardware_ids:
            if not hardware_id:
                continue
            self._run_devcon(
                devcon_path,
                ["install", str(inf_path), hardware_id],
                notes,
                allow_failure=True,
            )
            changed = True
        return changed

    def _update_virtual_display_driver(
        self,
        *,
        inf_path: str,
        hardware_ids: list[str],
        tools: dict[str, Any],
        notes: list[str],
    ) -> bool:
        devcon_path = str(tools.get("devcon_path") or "").strip()
        if not devcon_path or not inf_path or not hardware_ids:
            return False
        changed = False
        for hardware_id in hardware_ids:
            if not hardware_id:
                continue
            self._run_devcon(
                devcon_path,
                ["update", str(inf_path), hardware_id],
                notes,
                allow_failure=True,
            )
            changed = True
        return changed

    def _scan_devices(self, *, tools: dict[str, Any], notes: list[str]) -> bool:
        if tools.get("pnputil"):
            self._run_pnputil(["/scan-devices"], notes, allow_failure=True)
            return True
        devcon_path = str(tools.get("devcon_path") or "").strip()
        if devcon_path:
            self._run_devcon(devcon_path, ["rescan"], notes, allow_failure=True)
            return True
        return False

    def _disable_device(
        self,
        *,
        instance_id: str,
        tools: dict[str, Any],
        notes: list[str],
        allow_failure: bool = False,
    ) -> bool:
        if tools.get("pnputil"):
            self._run_pnputil(["/disable-device", instance_id], notes, allow_failure=allow_failure)
            return True
        devcon_path = str(tools.get("devcon_path") or "").strip()
        if devcon_path:
            self._run_devcon(devcon_path, ["disable", instance_id], notes, allow_failure=allow_failure)
            return True
        return False

    def _enable_device(
        self,
        *,
        instance_id: str,
        tools: dict[str, Any],
        notes: list[str],
        allow_failure: bool = False,
    ) -> bool:
        if tools.get("pnputil"):
            self._run_pnputil(["/enable-device", instance_id], notes, allow_failure=allow_failure)
            return True
        devcon_path = str(tools.get("devcon_path") or "").strip()
        if devcon_path:
            self._run_devcon(devcon_path, ["enable", instance_id], notes, allow_failure=allow_failure)
            return True
        return False

    def _restart_device(
        self,
        *,
        instance_id: str,
        tools: dict[str, Any],
        notes: list[str],
        allow_failure: bool = False,
    ) -> bool:
        if tools.get("pnputil"):
            self._run_pnputil(["/restart-device", instance_id], notes, allow_failure=allow_failure)
            return True
        devcon_path = str(tools.get("devcon_path") or "").strip()
        if devcon_path:
            self._run_devcon(devcon_path, ["restart", instance_id], notes, allow_failure=allow_failure)
            return True
        return False

    def _normalize_string_list(self, raw: Any) -> list[str]:
        if raw is None:
            return []
        if isinstance(raw, str):
            items = [raw]
        elif isinstance(raw, (list, tuple, set)):
            items = list(raw)
        else:
            items = [raw]

        normalized: list[str] = []
        for item in items:
            text = str(item or "").strip().lower()
            if text and text not in normalized:
                normalized.append(text)
        return normalized

    def _merge_notes(self, left: list[str], right: list[str]) -> list[str]:
        merged: list[str] = []
        for item in [*left, *right]:
            text = str(item or "").strip()
            if text and text not in merged:
                merged.append(text)
        return merged
