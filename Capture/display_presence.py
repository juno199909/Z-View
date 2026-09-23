from __future__ import annotations

import ctypes
import os
import threading
import time
from dataclasses import asdict, dataclass, field
from ctypes import wintypes
from typing import Any

from Common.models import SessionDescriptor


class _DISPLAY_DEVICEW(ctypes.Structure):
    _fields_ = [
        ("cb", wintypes.DWORD),
        ("DeviceName", wintypes.WCHAR * 32),
        ("DeviceString", wintypes.WCHAR * 128),
        ("StateFlags", wintypes.DWORD),
        ("DeviceID", wintypes.WCHAR * 128),
        ("DeviceKey", wintypes.WCHAR * 128),
    ]


@dataclass(slots=True)
class DisplayPresenceAssessment:
    substrate_class: str
    continuity_mode: str
    persistent: bool
    best_effort_only: bool
    display_surface_available: bool
    physical_display_attached: bool
    virtual_display_attached: bool
    remote_display_surface: bool
    secure_desktop_surface: bool
    disconnected_surface: bool
    render_monitor_count: int
    attached_display_count: int
    requires_virtual_display_for_full_continuity: bool
    rank_hint: int
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class DisplayPresenceProbe:
    DISPLAY_DEVICE_ATTACHED_TO_DESKTOP = 0x00000001
    DISPLAY_DEVICE_MIRRORING_DRIVER = 0x00000008
    DISPLAY_DEVICE_REMOTE = 0x04000000
    DISPLAY_DEVICE_DISCONNECT = 0x02000000
    SM_CMONITORS = 80
    DEFAULT_VIRTUAL_MARKERS = (
        "virtual",
        "indirect",
        "idd",
        "iddsampledriver",
        "displaylink",
        "usb graphics",
        "dummy",
        "msbdd",
    )
    DEFAULT_REMOTE_MARKERS = (
        "rdp",
        "remote display",
        "remote display adapter",
        "terminal server",
        "remote desktop",
        "ms_rdp",
        "rdpidd",
        "remotedisplayenum",
    )

    def __init__(self, cache_ttl_seconds: float = 2.0):
        self.cache_ttl_seconds = max(0.5, float(cache_ttl_seconds or 0.5))
        self.user32 = ctypes.WinDLL("user32", use_last_error=True)
        self._inventory_lock = threading.Lock()
        self._inventory_cache: dict[str, Any] | None = None
        self._inventory_cache_at = 0.0
        self._virtual_display_hints_lock = threading.Lock()
        self._virtual_display_hints = self._empty_virtual_display_hints()
        self._setup_windows_apis()

    def _setup_windows_apis(self) -> None:
        self.user32.EnumDisplayDevicesW.argtypes = [
            wintypes.LPCWSTR,
            wintypes.DWORD,
            ctypes.POINTER(_DISPLAY_DEVICEW),
            wintypes.DWORD,
        ]
        self.user32.EnumDisplayDevicesW.restype = wintypes.BOOL
        self.user32.GetSystemMetrics.argtypes = [wintypes.INT]
        self.user32.GetSystemMetrics.restype = wintypes.INT

    def get_display_inventory(self, *, force_refresh: bool = False) -> dict[str, Any]:
        now = time.time()
        with self._inventory_lock:
            if (
                not force_refresh
                and self._inventory_cache is not None
                and (now - self._inventory_cache_at) < self.cache_ttl_seconds
            ):
                return dict(self._inventory_cache)

            inventory = self._probe_display_inventory()
            # ZVIEW_DISABLE_VIRTUAL_DISPLAY=1：信任现有物理显示器可捕获。
            # 服务进程(Session 0)枚举 DISPLAY_DEVICE_ACTIVE 对 console 显示器可能为
            # False（会话相关视图），导致全链路误判 headless 并阻塞捕获。
            import os as _os
            if _os.environ.get("ZVIEW_DISABLE_VIRTUAL_DISPLAY", "").strip() in ("1", "true", "True"):
                if not inventory.get("physical_display_attached"):
                    inventory["physical_display_attached"] = True
                    inventory["skipped_by_env"] = True
                    if not int(inventory.get("attached_display_count") or 0):
                        inventory["attached_display_count"] = 1
            self._inventory_cache = dict(inventory)
            self._inventory_cache_at = now
            return dict(inventory)

    def update_virtual_display_hints(self, status: dict[str, Any] | None) -> bool:
        normalized = self._normalize_virtual_display_hints(status)
        with self._virtual_display_hints_lock:
            if normalized == self._virtual_display_hints:
                return False
            self._virtual_display_hints = normalized
        with self._inventory_lock:
            self._inventory_cache = None
            self._inventory_cache_at = 0.0
        return True

    def get_virtual_display_hints(self) -> dict[str, Any]:
        with self._virtual_display_hints_lock:
            return {
                "provider": str(self._virtual_display_hints.get("provider") or ""),
                "provisioning_state": str(
                    self._virtual_display_hints.get("provisioning_state") or ""
                ),
                "attached_virtual_display": bool(
                    self._virtual_display_hints.get("attached_virtual_display", False)
                ),
                "friendly_name_keywords": list(
                    self._virtual_display_hints.get("friendly_name_keywords") or []
                ),
                "instance_id_keywords": list(
                    self._virtual_display_hints.get("instance_id_keywords") or []
                ),
                "hardware_ids": list(self._virtual_display_hints.get("hardware_ids") or []),
                "attach_keywords": list(self._virtual_display_hints.get("attach_keywords") or []),
                "remote_adapter_keywords": list(
                    self._virtual_display_hints.get("remote_adapter_keywords") or []
                ),
                "device_name": str(self._virtual_display_hints.get("device_name") or ""),
                "device_instance_id": str(
                    self._virtual_display_hints.get("device_instance_id") or ""
                ),
            }

    def assess(
        self,
        descriptor: SessionDescriptor | None = None,
        *,
        desktop_state: dict[str, Any] | None = None,
        inventory: dict[str, Any] | None = None,
    ) -> DisplayPresenceAssessment:
        inventory = dict(inventory or self.get_display_inventory())
        state = dict(desktop_state or {})
        if descriptor is None and not state:
            return DisplayPresenceAssessment(
                substrate_class="none",
                continuity_mode="none",
                persistent=False,
                best_effort_only=True,
                display_surface_available=False,
                physical_display_attached=bool(inventory.get("physical_display_attached")),
                virtual_display_attached=bool(inventory.get("virtual_display_attached")),
                remote_display_surface=False,
                secure_desktop_surface=False,
                disconnected_surface=False,
                render_monitor_count=int(inventory.get("render_monitor_count") or 0),
                attached_display_count=int(inventory.get("attached_display_count") or 0),
                requires_virtual_display_for_full_continuity=not bool(
                    inventory.get("physical_display_attached") or inventory.get("virtual_display_attached")
                ),
                rank_hint=99,
                notes=list(inventory.get("notes") or []),
            )
        desktop_kind = str(
            state.get("capture_desktop_kind")
            or state.get("desktop_kind")
            or ""
        ).strip().lower()
        target_state = str(
            state.get("capture_target_state")
            or getattr(descriptor, "state", "")
            or ""
        ).strip().lower()
        is_remote_session = self._coerce_bool(
            state.get("capture_target_is_remote_session"),
            bool(getattr(descriptor, "is_remote_session", False)),
        )
        # VMware/RDP环境禁用虚拟显示器开关：影响RDP session persistent判定 + Console不假装有虚拟显示面
        _vd_disabled = os.environ.get("ZVIEW_DISABLE_VIRTUAL_DISPLAY", "").strip() in ("1", "true", "True")
        _rdp_rank_override = None
        is_console_session = self._coerce_bool(
            state.get("capture_target_is_console_session"),
            bool(getattr(descriptor, "is_console_session", False)),
        )
        is_disconnected = (
            target_state == "disconnected"
            or bool(getattr(descriptor, "is_disconnected", False))
            or desktop_kind == "disconnected_shell"
        )
        is_secure_desktop = desktop_kind.startswith("secure_") or desktop_kind == "winlogon"

        physical_display_attached = bool(inventory.get("physical_display_attached"))
        virtual_display_attached = bool(inventory.get("virtual_display_attached"))
        attached_display_count = int(inventory.get("attached_display_count") or 0)
        render_monitor_count = int(inventory.get("render_monitor_count") or 0)
        display_surface_available = attached_display_count > 0 or render_monitor_count > 0

        notes = list(inventory.get("notes") or [])
        if is_secure_desktop:
            notes.append("secure_desktop_target_detected")
        if is_disconnected:
            notes.append("capture_target_marked_disconnected")
        if is_remote_session:
            notes.append("capture_target_is_remote_session")
        if is_console_session:
            notes.append("capture_target_is_console_session")

        requires_virtual_display = False
        if is_disconnected:
            substrate_class = "disconnected_surface"
            continuity_mode = "best_effort_disconnected"
            persistent = False
            best_effort_only = True
            remote_display_surface = False
            secure_desktop_surface = False
            display_surface_available = False
        elif is_remote_session:
            substrate_class = "remote_session_surface"
            continuity_mode = "best_effort_remote"
            # RDP会话在禁用虚拟显示器环境下视为持久（VMware/RDP环境唯一可抓桌面）
            persistent = bool(_vd_disabled)
            best_effort_only = not _vd_disabled
            remote_display_surface = True
            secure_desktop_surface = False
            requires_virtual_display = False if _vd_disabled else (not (physical_display_attached or virtual_display_attached))
            # RDP persistent 时给更好的 rank（比 console_headless=4 更优先）
            _rdp_rank_override = 0 if _vd_disabled else None
        elif is_console_session and is_secure_desktop and (physical_display_attached or virtual_display_attached):
            substrate_class = "secure_console_surface"
            continuity_mode = "secure_console_persistent"
            persistent = True
            best_effort_only = False
            remote_display_surface = False
            secure_desktop_surface = True
        elif is_console_session and physical_display_attached:
            substrate_class = "physical_console_surface"
            continuity_mode = "console_persistent"
            persistent = True
            best_effort_only = False
            remote_display_surface = False
            secure_desktop_surface = False
        elif is_console_session and virtual_display_attached and not _vd_disabled:
            substrate_class = "virtual_display_surface"
            continuity_mode = "virtual_console_persistent"
            persistent = True
            best_effort_only = False
            remote_display_surface = False
            secure_desktop_surface = False
        elif is_console_session:
            substrate_class = "console_headless_surface"
            continuity_mode = "best_effort_console_headless"
            persistent = False
            best_effort_only = True
            remote_display_surface = False
            secure_desktop_surface = False
            requires_virtual_display = True
        elif display_surface_available and not is_remote_session:
            substrate_class = "interactive_display_surface"
            continuity_mode = "interactive_persistent"
            persistent = True
            best_effort_only = False
            remote_display_surface = False
            secure_desktop_surface = is_secure_desktop
        else:
            substrate_class = "unknown_best_effort"
            continuity_mode = "best_effort_unknown"
            persistent = False
            best_effort_only = True
            remote_display_surface = False
            secure_desktop_surface = is_secure_desktop
            if not virtual_display_attached:
                requires_virtual_display = True

        if requires_virtual_display:
            notes.append("virtual_display_recommended_for_full_continuity")
        if virtual_display_attached:
            notes.append("virtual_display_surface_detected")
        if physical_display_attached:
            notes.append("physical_display_surface_detected")
        if inventory.get("remote_adapter_present"):
            notes.append("remote_display_adapter_detected")

        rank_map = {
            "physical_console_surface": 0,
            "virtual_display_surface": 1,
            "secure_console_surface": 2,
            "interactive_display_surface": 3,
            "console_headless_surface": 4,
            "remote_session_surface": 5,
            "disconnected_surface": 6,
            "unknown_best_effort": 7,
        }

        return DisplayPresenceAssessment(
            substrate_class=substrate_class,
            continuity_mode=continuity_mode,
            persistent=bool(persistent),
            best_effort_only=bool(best_effort_only),
            display_surface_available=bool(display_surface_available),
            physical_display_attached=bool(physical_display_attached),
            virtual_display_attached=bool(virtual_display_attached),
            remote_display_surface=bool(remote_display_surface),
            secure_desktop_surface=bool(secure_desktop_surface),
            disconnected_surface=bool(is_disconnected),
            render_monitor_count=render_monitor_count,
            attached_display_count=attached_display_count,
            requires_virtual_display_for_full_continuity=bool(requires_virtual_display),
            rank_hint=(_rdp_rank_override if _rdp_rank_override is not None else rank_map.get(substrate_class, 99)),
            notes=notes,
        )

    def _probe_display_inventory(self) -> dict[str, Any]:
        hints = self.get_virtual_display_hints()
        adapters: list[dict[str, Any]] = []
        physical_display_attached = False
        virtual_display_attached = False
        remote_adapter_present = False
        attached_display_count = 0
        notes: list[str] = []

        adapter_index = 0
        while True:
            adapter = _DISPLAY_DEVICEW()
            adapter.cb = ctypes.sizeof(_DISPLAY_DEVICEW)
            if not self.user32.EnumDisplayDevicesW(None, adapter_index, ctypes.byref(adapter), 0):
                break

            flags = int(adapter.StateFlags or 0)
            device_name = str(adapter.DeviceName or "").strip()
            device_string = str(adapter.DeviceString or "").strip()
            device_id = str(adapter.DeviceID or "").strip()
            device_key = str(adapter.DeviceKey or "").strip()
            attached = bool(flags & self.DISPLAY_DEVICE_ATTACHED_TO_DESKTOP)
            mirroring = bool(flags & self.DISPLAY_DEVICE_MIRRORING_DRIVER)
            remote = bool(flags & self.DISPLAY_DEVICE_REMOTE) or self._looks_remote(
                device_name,
                device_string,
                device_id,
                device_key,
                hints=hints,
            )
            disconnected = bool(flags & self.DISPLAY_DEVICE_DISCONNECT)
            virtual = self._looks_virtual(
                device_name,
                device_string,
                device_id,
                device_key,
                hints=hints,
            )

            if remote:
                remote_adapter_present = True
            if attached and not mirroring and not disconnected:
                attached_display_count += 1
                if virtual:
                    virtual_display_attached = True
                elif not remote:
                    physical_display_attached = True

            adapters.append(
                {
                    "device_name": device_name,
                    "device_string": device_string,
                    "attached": attached,
                    "mirroring": mirroring,
                    "remote": remote,
                    "disconnected": disconnected,
                    "virtual": virtual,
                }
            )
            adapter_index += 1

        render_monitor_count = int(self.user32.GetSystemMetrics(self.SM_CMONITORS) or 0)
        if attached_display_count <= 0 and render_monitor_count > 0:
            notes.append("system_reports_render_monitors_without_attached_desktop_adapters")
        if hints.get("attached_virtual_display"):
            notes.append("service_reports_virtual_display_attached")
        if any(hints.get(key) for key in ("friendly_name_keywords", "instance_id_keywords", "hardware_ids")):
            notes.append("service_virtual_display_hints_loaded")
        if not physical_display_attached and not virtual_display_attached:
            notes.append("no_persistent_display_surface_detected")

        return {
            "physical_display_attached": bool(physical_display_attached),
            "virtual_display_attached": bool(virtual_display_attached),
            "remote_adapter_present": bool(remote_adapter_present),
            "attached_display_count": int(attached_display_count),
            "render_monitor_count": int(render_monitor_count),
            "virtual_display_provider": str(hints.get("provider") or ""),
            "virtual_display_provisioning_state": str(hints.get("provisioning_state") or ""),
            "service_virtual_display_attached_hint": bool(
                hints.get("attached_virtual_display", False)
            ),
            "adapters": adapters,
            "notes": notes,
        }

    def _looks_virtual(self, *parts: str, hints: dict[str, Any] | None = None) -> bool:
        haystack = " ".join(str(part or "") for part in parts).lower()
        if "remote display adapter" in haystack:
            return False
        virtual_markers = list(self.DEFAULT_VIRTUAL_MARKERS)
        hints = hints or {}
        virtual_markers.extend(hints.get("friendly_name_keywords") or [])
        virtual_markers.extend(hints.get("instance_id_keywords") or [])
        virtual_markers.extend(hints.get("hardware_ids") or [])
        device_name = str(hints.get("device_name") or "").strip().lower()
        device_instance_id = str(hints.get("device_instance_id") or "").strip().lower()
        if device_name and device_name in haystack:
            return True
        if device_instance_id and device_instance_id in haystack:
            return True
        if any(marker in haystack for marker in virtual_markers):
            return True
        return False

    def _looks_remote(self, *parts: str, hints: dict[str, Any] | None = None) -> bool:
        haystack = " ".join(str(part or "") for part in parts).lower()
        remote_markers = list(self.DEFAULT_REMOTE_MARKERS)
        remote_markers.extend((hints or {}).get("remote_adapter_keywords") or [])
        return any(marker in haystack for marker in remote_markers)

    def _empty_virtual_display_hints(self) -> dict[str, Any]:
        return {
            "provider": "",
            "provisioning_state": "",
            "attached_virtual_display": False,
            "friendly_name_keywords": [],
            "instance_id_keywords": [],
            "hardware_ids": [],
            "attach_keywords": [],
            "remote_adapter_keywords": [],
            "device_name": "",
            "device_instance_id": "",
        }

    def _normalize_virtual_display_hints(self, status: dict[str, Any] | None) -> dict[str, Any]:
        if not isinstance(status, dict):
            return self._empty_virtual_display_hints()
        manifest = status.get("driver_manifest") or {}
        if not isinstance(manifest, dict):
            manifest = {}
        return {
            "provider": str(status.get("provider") or ""),
            "provisioning_state": str(status.get("provisioning_state") or ""),
            "attached_virtual_display": bool(status.get("attached_virtual_display", False)),
            "friendly_name_keywords": self._normalize_string_list(
                status.get("friendly_name_keywords")
                or manifest.get("friendly_name_keywords")
                or []
            ),
            "instance_id_keywords": self._normalize_string_list(
                status.get("instance_id_keywords")
                or manifest.get("instance_id_keywords")
                or []
            ),
            "hardware_ids": self._normalize_string_list(
                status.get("hardware_ids") or manifest.get("hardware_ids") or []
            ),
            "attach_keywords": self._normalize_string_list(
                status.get("attach_keywords") or manifest.get("attach_keywords") or []
            ),
            "remote_adapter_keywords": self._normalize_string_list(
                status.get("remote_adapter_keywords")
                or manifest.get("remote_adapter_keywords")
                or []
            ),
            "device_name": str(status.get("device_name") or "").strip(),
            "device_instance_id": str(status.get("device_instance_id") or "").strip(),
        }

    def _normalize_string_list(self, values: Any) -> list[str]:
        if values in (None, ""):
            return []
        if isinstance(values, (str, bytes)):
            values = [values]
        normalized: list[str] = []
        for item in values:
            text = str(item or "").strip().lower()
            if text and text not in normalized:
                normalized.append(text)
        return normalized

    def _coerce_bool(self, value: Any, default: bool = False) -> bool:
        if isinstance(value, bool):
            return value
        if value in (None, ""):
            return bool(default)
        if isinstance(value, (int, float)):
            return bool(value)
        normalized = str(value).strip().lower()
        if normalized in {"1", "true", "yes", "y", "on"}:
            return True
        if normalized in {"0", "false", "no", "n", "off"}:
            return False
        return bool(default)
