"""
Windows 输入桌面切换辅助模块。
"""

from __future__ import annotations

import contextlib
import ctypes
from ctypes import wintypes
import threading
import time

from console_utils import enable_utf8_stdio, safe_console_print

enable_utf8_stdio()
print = safe_console_print


class InputDesktopController:
    DESKTOP_READOBJECTS = 0x0001
    DESKTOP_CREATEWINDOW = 0x0002
    DESKTOP_ENUMERATE = 0x0040
    DESKTOP_WRITEOBJECTS = 0x0080
    DESKTOP_SWITCHDESKTOP = 0x0100
    UOI_NAME = 2
    DESKTOP_ACCESS = (
        DESKTOP_READOBJECTS
        | DESKTOP_CREATEWINDOW
        | DESKTOP_ENUMERATE
        | DESKTOP_WRITEOBJECTS
        | DESKTOP_SWITCHDESKTOP
    )

    def __init__(self, component: str):
        self.component = component
        self.user32 = ctypes.WinDLL("user32", use_last_error=True)
        self.kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        self._log_lock = threading.Lock()
        self._desktop_handle_lock = threading.Lock()
        self._binding_state_lock = threading.Lock()
        self._last_log_key: tuple | None = None
        self._managed_desktop_handles: dict[str, dict[str, object]] = {}
        self._thread_bound_desktop_names: dict[int, str] = {}
        self._desktop_context_generation = 0
        self._last_binding_snapshot: dict[str, str | bool | int] = {}
        self._last_inventory_signature = ""
        self._last_inventory_trace = ""
        self._last_window_station_name = ""
        self._setup_windows_apis()

    def _setup_windows_apis(self):
        self._enum_desktops_proc = ctypes.WINFUNCTYPE(
            wintypes.BOOL,
            wintypes.LPWSTR,
            wintypes.LPARAM,
        )
        self.user32.OpenInputDesktop.argtypes = [
            wintypes.DWORD,
            wintypes.BOOL,
            wintypes.DWORD,
        ]
        self.user32.OpenInputDesktop.restype = wintypes.HANDLE
        self.user32.OpenDesktopW.argtypes = [
            wintypes.LPCWSTR,
            wintypes.DWORD,
            wintypes.BOOL,
            wintypes.DWORD,
        ]
        self.user32.OpenDesktopW.restype = wintypes.HANDLE
        self.user32.CloseDesktop.argtypes = [wintypes.HANDLE]
        self.user32.CloseDesktop.restype = wintypes.BOOL
        self.user32.GetThreadDesktop.argtypes = [wintypes.DWORD]
        self.user32.GetThreadDesktop.restype = wintypes.HANDLE
        self.user32.SetThreadDesktop.argtypes = [wintypes.HANDLE]
        self.user32.SetThreadDesktop.restype = wintypes.BOOL
        self.user32.GetUserObjectInformationW.argtypes = [
            wintypes.HANDLE,
            ctypes.c_int,
            wintypes.LPVOID,
            wintypes.DWORD,
            ctypes.POINTER(wintypes.DWORD),
        ]
        self.user32.GetUserObjectInformationW.restype = wintypes.BOOL
        self.user32.GetProcessWindowStation.argtypes = []
        self.user32.GetProcessWindowStation.restype = wintypes.HANDLE
        self.user32.EnumDesktopsW.argtypes = [
            wintypes.HANDLE,
            self._enum_desktops_proc,
            wintypes.LPARAM,
        ]
        self.user32.EnumDesktopsW.restype = wintypes.BOOL
        self.user32.GetSystemMetrics.argtypes = [wintypes.INT]
        self.user32.GetSystemMetrics.restype = wintypes.INT
        self.kernel32.GetCurrentThreadId.restype = wintypes.DWORD
        self.kernel32.GetCurrentProcessId.restype = wintypes.DWORD
        self.kernel32.ProcessIdToSessionId.argtypes = [wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)]
        self.kernel32.ProcessIdToSessionId.restype = wintypes.BOOL
        self.kernel32.WTSGetActiveConsoleSessionId.restype = wintypes.DWORD

    def has_input_desktop(self) -> bool:
        desktop = None
        try:
            desktop = self.user32.OpenInputDesktop(0, False, self.DESKTOP_ACCESS)
            return bool(desktop)
        except Exception:
            return False
        finally:
            if desktop:
                with contextlib.suppress(Exception):
                    self.user32.CloseDesktop(desktop)

    def close(self):
        with self._desktop_handle_lock:
            cached_handles = list(self._managed_desktop_handles.items())
            self._managed_desktop_handles.clear()
            self._thread_bound_desktop_names.clear()
        for _, cached_entry in cached_handles:
            desktop_handle = self._extract_cached_desktop_handle(cached_entry)
            if desktop_handle:
                with contextlib.suppress(Exception):
                    self.user32.CloseDesktop(desktop_handle)

    def __del__(self):
        with contextlib.suppress(Exception):
            self.close()

    def describe_current_state(self) -> dict[str, str | bool]:
        thread_id = self.kernel32.GetCurrentThreadId()
        thread_desktop = self.user32.GetThreadDesktop(thread_id)
        process_winsta = self.user32.GetProcessWindowStation()
        input_desktop = None
        try:
            input_desktop = self.user32.OpenInputDesktop(0, False, self.DESKTOP_ACCESS)
            state = {
                "thread_id": str(int(thread_id)),
                "thread_desktop": self._get_desktop_name(thread_desktop) or "unknown",
                "thread_desktop_handle": self._format_handle(thread_desktop),
                "input_desktop": self._get_desktop_name(input_desktop) or "unknown",
                "input_desktop_handle": self._format_handle(input_desktop),
                "process_winsta": self._get_desktop_name(process_winsta) or "unknown",
                "process_winsta_handle": self._format_handle(process_winsta),
            }
            return self._finalize_state(state, thread_desktop, input_desktop)
        except Exception:
            state = {
                "thread_id": str(int(thread_id)),
                "thread_desktop": self._get_desktop_name(thread_desktop) or "unknown",
                "thread_desktop_handle": self._format_handle(thread_desktop),
                "input_desktop": "unavailable",
                "input_desktop_handle": "0x0",
                "process_winsta": self._get_desktop_name(process_winsta) or "unknown",
                "process_winsta_handle": self._format_handle(process_winsta),
            }
            return self._finalize_state(state, thread_desktop, None)
        finally:
            if input_desktop:
                with contextlib.suppress(Exception):
                    self.user32.CloseDesktop(input_desktop)

    def describe_binding_state(self) -> dict[str, str | bool | int]:
        state = dict(self.describe_current_state())
        with self._binding_state_lock:
            snapshot = dict(self._last_binding_snapshot)
        if snapshot:
            state["desktop_context_generation"] = int(snapshot.get("desktop_context_generation") or 0)
            state["last_binding_reason"] = str(snapshot.get("desktop_binding_reason") or "")
            state["last_binding_mode"] = str(snapshot.get("binding_mode") or "")
            state["last_binding_status"] = str(snapshot.get("status") or "")
            state["last_binding_desktop"] = str(snapshot.get("capture_desktop") or snapshot.get("thread_desktop") or "")
            state["last_binding_desktop_kind"] = str(
                snapshot.get("capture_desktop_kind") or snapshot.get("thread_desktop_kind") or ""
            )
            state["last_rebind_reason"] = str(snapshot.get("desktop_rebind_reason") or "")
            state["last_rebind_attempted"] = bool(snapshot.get("desktop_rebind_attempted"))
            state["last_rebind_succeeded"] = bool(snapshot.get("desktop_rebind_succeeded"))
            state["last_binding_changed_thread_desktop"] = bool(snapshot.get("desktop_binding_changed"))
            state["last_binding_selected_source"] = str(
                snapshot.get("capture_desktop_source") or snapshot.get("selected_desktop_source") or ""
            )
            state["last_binding_recorded_at"] = int(snapshot.get("desktop_binding_recorded_at") or 0)
        cache_snapshot = self._get_cached_desktop_handle_snapshot()
        state["desktop_handle_cache_size"] = len(cache_snapshot)
        state["desktop_handle_cache_names"] = ",".join(cache_snapshot)
        return state

    def build_desktop_signature(
        self,
        state: dict[str, str | bool] | None = None,
        *,
        binding_mode: str | None = None,
    ) -> str:
        snapshot = dict(state or self.describe_current_state())
        normalized_binding_mode = str(binding_mode or snapshot.get("binding_mode") or "input").strip().lower()
        if normalized_binding_mode == "capture":
            target_signature = str(
                snapshot.get("capture_target_signature")
                or self.build_capture_target_signature(state=snapshot)
            )
            return "|".join(
                [
                    "mode=capture",
                    f"target={target_signature}",
                    f"thread={self._normalize_name(str(snapshot.get('thread_desktop') or 'unknown'))}",
                    f"capture={self._normalize_name(str(snapshot.get('capture_desktop') or 'unknown'))}",
                    f"input={self._normalize_name(str(snapshot.get('input_desktop') or 'unknown'))}",
                    f"match={1 if snapshot.get('capture_thread_matches_target') else 0}",
                ]
            )

        target_signature = self.build_input_target_signature(snapshot)
        return "|".join(
            [
                "mode=input",
                f"target={target_signature}",
                f"thread={self._normalize_name(str(snapshot.get('thread_desktop') or 'unknown'))}",
                f"input={self._normalize_name(str(snapshot.get('input_desktop') or 'unknown'))}",
                f"match={1 if snapshot.get('thread_matches_input') else 0}",
            ]
        )

    def build_input_target_signature(self, state: dict[str, str | bool] | None = None) -> str:
        snapshot = dict(state or self.describe_current_state())
        desktop_name = str(snapshot.get("input_desktop") or snapshot.get("thread_desktop") or "unknown")
        desktop_kind = str(
            snapshot.get("input_desktop_kind")
            or snapshot.get("desktop_kind")
            or self._classify_desktop_name(desktop_name)
        )
        return "|".join(
            [
                f"session={snapshot.get('session_id', 'unknown')}",
                f"console={snapshot.get('active_console_session_id', 'unknown')}",
                f"remote={1 if snapshot.get('is_remote_session') else 0}",
                f"winsta={self._normalize_name(str(snapshot.get('process_winsta') or 'unknown'))}",
                f"input={self._normalize_name(desktop_name)}",
                f"kind={self._normalize_name(desktop_kind or 'unknown')}",
                f"virtual={snapshot.get('virtual_screen_origin', '0,0')}:{snapshot.get('virtual_screen_size', '0x0')}",
            ]
        )

    def build_capture_target_signature(
        self,
        capture_target: dict[str, str | bool | int] | None = None,
        state: dict[str, str | bool] | None = None,
    ) -> str:
        snapshot = dict(state or self.describe_current_state())
        target = dict(capture_target or {})
        target_session_id = target.get("session_id", snapshot.get("session_id", "unknown"))
        target_station = str(target.get("station_name") or snapshot.get("process_winsta") or "unknown")
        target_state = str(target.get("state") or "unknown")
        is_remote_target = self._coerce_bool(target.get("is_remote_session"), bool(snapshot.get("is_remote_session")))
        is_console_target = self._coerce_bool(
            target.get("is_console_session"),
            str(target_session_id) == str(snapshot.get("active_console_session_id")),
        )
        desktop_candidates = ",".join(
            self._normalize_name(name)
            for name in self._build_capture_desktop_candidates(
                capture_target=target,
                state=snapshot,
            )
        )
        preferred_desktops = ",".join(
            self._normalize_name(name)
            for name in self._normalize_capture_desktop_names(
                target.get("preferred_capture_desktops")
                if target
                else snapshot.get("preferred_capture_desktops")
            )
        )
        preferred_kind = self._normalize_name(
            str(
                target.get("preferred_capture_desktop_kind")
                or snapshot.get("preferred_capture_desktop_kind")
                or ""
            )
        )
        policy_signature = self._normalize_name(
            str(
                target.get("capture_binding_policy_signature")
                or snapshot.get("capture_binding_policy_signature")
                or ""
            )
        )
        authoritative_binding = self._coerce_bool(
            target.get("authoritative_capture_binding"),
            self._coerce_bool(snapshot.get("authoritative_capture_binding")),
        )
        binding_generation = self._coerce_int(
            target.get("capture_binding_generation"),
            self._coerce_int(snapshot.get("capture_binding_generation"), 0),
        )
        return "|".join(
            [
                f"session={target_session_id}",
                f"console={snapshot.get('active_console_session_id', 'unknown')}",
                f"remote={1 if is_remote_target else 0}",
                f"console_target={1 if is_console_target else 0}",
                f"station={self._normalize_name(target_station or 'unknown')}",
                f"state={self._normalize_name(target_state or 'unknown')}",
                f"candidates={desktop_candidates or 'default'}",
                f"preferred={preferred_desktops or 'none'}",
                f"preferred_kind={preferred_kind or 'unknown'}",
                f"authoritative={1 if authoritative_binding else 0}",
                f"generation={binding_generation}",
                f"policy={policy_signature or 'none'}",
                f"virtual={snapshot.get('virtual_screen_origin', '0,0')}:{snapshot.get('virtual_screen_size', '0x0')}",
            ]
        )

    def build_capture_binding_identity(
        self,
        capture_target: dict[str, str | bool | int] | None = None,
        state: dict[str, str | bool] | None = None,
    ) -> str:
        snapshot = dict(state or self.describe_current_state())
        capture_desktop_name = str(snapshot.get("capture_desktop") or snapshot.get("thread_desktop") or "unknown")
        capture_desktop_kind = str(
            snapshot.get("capture_desktop_kind")
            or snapshot.get("desktop_kind")
            or self._classify_desktop_name(capture_desktop_name)
        )
        capture_source = str(
            snapshot.get("capture_desktop_source")
            or snapshot.get("selected_desktop_source")
            or "unknown"
        )
        target_signature = str(
            snapshot.get("capture_target_signature")
            or self.build_capture_target_signature(capture_target, state=snapshot)
        )
        return "|".join(
            [
                f"target={target_signature or 'unknown'}",
                f"desktop={self._normalize_name(capture_desktop_name) or 'unknown'}",
                f"kind={self._normalize_name(capture_desktop_kind) or 'unknown'}",
                f"source={self._normalize_name(capture_source) or 'unknown'}",
                f"allowed={1 if snapshot.get('selected_desktop_allowed_by_policy', True) else 0}",
                f"preferred_kind_match={1 if snapshot.get('selected_desktop_matches_preferred_kind', True) else 0}",
            ]
        )

    def build_binding_scope_signature(
        self,
        *,
        binding_mode: str | None = None,
        capture_target: dict[str, str | bool | int] | None = None,
        state: dict[str, str | bool] | None = None,
    ) -> str:
        snapshot = dict(state or self.describe_current_state())
        normalized_binding_mode = str(binding_mode or snapshot.get("binding_mode") or "input").strip().lower()
        if normalized_binding_mode == "capture":
            target_signature = str(
                snapshot.get("capture_target_signature")
                or self.build_capture_target_signature(capture_target, state=snapshot)
            )
        else:
            target_signature = str(
                snapshot.get("input_target_signature")
                or self.build_input_target_signature(snapshot)
            )
        return "|".join(
            [
                f"mode={normalized_binding_mode or 'input'}",
                f"session={snapshot.get('session_id', 'unknown')}",
                f"console={snapshot.get('active_console_session_id', 'unknown')}",
                f"remote={1 if snapshot.get('is_remote_session') else 0}",
                f"winsta={self._normalize_name(str(snapshot.get('process_winsta') or 'unknown'))}",
                f"inventory={self._normalize_name(str(snapshot.get('desktop_inventory_signature') or 'empty'))}",
                f"target={target_signature or 'unknown'}",
            ]
        )

    def describe_transition_state(self) -> dict[str, str | bool | int | list[str]]:
        state = dict(self.describe_current_state())
        binding_state = dict(self.describe_binding_state())
        previous_signature = str(binding_state.get("desktop_context_transition_signature") or "").strip()
        current_signature = str(state.get("desktop_context_transition_signature") or "").strip()
        recovery_reasons: list[str] = []

        if str(state.get("input_desktop") or "").strip().lower() == "unavailable":
            recovery_reasons.append("input_desktop_unavailable")
        if bool(binding_state.get("desktop_handle_open_failed")):
            recovery_reasons.append("desktop_handle_open_failed")
        if bool(binding_state.get("desktop_handle_stale_cached")):
            recovery_reasons.append("desktop_handle_stale_cached")
        if not bool(state.get("selected_desktop_allowed_by_policy", True)):
            recovery_reasons.append("selected_desktop_blocked_by_policy")
        if not bool(state.get("selected_desktop_matches_preferred_kind", True)):
            recovery_reasons.append("selected_desktop_kind_mismatch")
        if str(state.get("binding_mode") or "input").strip().lower() == "capture":
            if not bool(state.get("capture_thread_matches_target", True)):
                recovery_reasons.append("capture_thread_not_bound_to_target")
        elif not bool(state.get("thread_matches_input", True)):
            recovery_reasons.append("input_thread_not_bound")

        transition_reasons: list[str] = []
        if bool(state.get("window_station_changed")):
            transition_reasons.append("window_station_changed")
        if bool(state.get("desktop_inventory_changed")):
            transition_reasons.append("desktop_inventory_changed")
        if previous_signature and current_signature and previous_signature != current_signature:
            transition_reasons.append("desktop_context_signature_changed")

        return {
            "requires_recovery": bool(recovery_reasons),
            "recovery_reasons": recovery_reasons,
            "transition_detected": bool(transition_reasons),
            "transition_reasons": transition_reasons,
            "current_signature": current_signature,
            "previous_signature": previous_signature,
            "binding_mode": str(state.get("binding_mode") or "input"),
            "status": str(state.get("status") or binding_state.get("status") or "unknown"),
            "selected_desktop_name": str(state.get("selected_desktop_name") or state.get("capture_desktop") or ""),
            "selected_desktop_kind": str(state.get("selected_desktop_kind") or state.get("capture_desktop_kind") or ""),
            "selected_desktop_source": str(
                state.get("selected_desktop_source")
                or state.get("capture_desktop_source")
                or binding_state.get("last_binding_selected_source")
                or ""
            ),
        }

    def ensure_current_thread_on_input_desktop(self, reason: str) -> dict[str, str | bool]:
        thread_id = self.kernel32.GetCurrentThreadId()
        current_desktop = self.user32.GetThreadDesktop(thread_id)
        current_name = self._get_desktop_name(current_desktop) or "unknown"
        current_handle = self._format_handle(current_desktop)
        input_desktop = None
        input_name = current_name
        input_handle = current_handle
        switched = False
        status = "already_bound"
        rebind_required = False
        rebind_attempted = False
        rebind_succeeded = False
        handle_details: dict[str, str | bool | int] = {}

        try:
            input_desktop = self.user32.OpenInputDesktop(0, False, self.DESKTOP_ACCESS)
            if not input_desktop:
                error_code = ctypes.get_last_error()
                inferred_input_name, inferred_input_source, inferred_input_inferred = (
                    self._select_rebindable_input_desktop_name(
                        thread_desktop_name=current_name,
                        thread_id=int(thread_id),
                        desktop_inventory=self._list_window_station_desktops(),
                    )
                )
                input_name = inferred_input_name or "unavailable"
                input_handle = "0x0"

                if self._normalize_name(input_name) not in {"", "unknown", "unavailable"}:
                    if self._normalize_name(current_name) == self._normalize_name(input_name):
                        status = f"input_desktop_inferred_current_thread error={error_code}"
                        self._remember_thread_binding(thread_id, input_name)
                        self._log_switch(
                            reason,
                            thread_id,
                            current_name,
                            current_handle,
                            input_name,
                            input_handle,
                            status,
                        )
                        return self._finalize_state(
                            {
                                "thread_id": str(int(thread_id)),
                                "thread_desktop": current_name,
                                "thread_desktop_handle": current_handle,
                                "input_desktop": input_name,
                                "input_desktop_handle": input_handle,
                                "input_desktop_source": inferred_input_source,
                                "input_desktop_inferred": inferred_input_inferred,
                                "input_desktop_open_failed": True,
                                "input_desktop_open_error": f"open_failed:{error_code}",
                                "switched": False,
                                "status": status,
                                "selected_desktop_source": inferred_input_source,
                                "desktop_rebind_required": False,
                                "desktop_rebind_attempted": False,
                                "desktop_rebind_succeeded": False,
                                "desktop_rebind_reason": "input_desktop_handle_unavailable_inferred_current_thread",
                            },
                            current_desktop,
                            None,
                            binding_mode="input",
                            binding_reason=reason,
                            record_binding_snapshot=True,
                        )

                    rebind_required = True
                    rebind_attempted = True
                    bind_handle, handle_details = self._acquire_cached_desktop_handle(input_name)
                    if bind_handle and self.user32.SetThreadDesktop(bind_handle):
                        switched = True
                        rebind_succeeded = True
                        status = "bound_to_inferred_input_desktop"
                        self._remember_thread_binding(thread_id, input_name)
                    else:
                        rebound_error = ctypes.get_last_error()
                        if bind_handle:
                            self._invalidate_cached_desktop_handle(
                                input_name,
                                reason=f"set_thread_desktop_failed:{rebound_error}",
                            )
                        status = f"switch_failed error={rebound_error}"
                    self._log_switch(
                        reason,
                        thread_id,
                        current_name,
                        current_handle,
                        input_name,
                        input_handle,
                        status,
                    )
                    bound_desktop = self.user32.GetThreadDesktop(thread_id)
                    return self._finalize_state(
                        {
                            "thread_id": str(int(thread_id)),
                            "thread_desktop": self._get_desktop_name(bound_desktop) or current_name,
                            "thread_desktop_handle": self._format_handle(bound_desktop),
                            "input_desktop": input_name,
                            "input_desktop_handle": input_handle,
                            "input_desktop_source": inferred_input_source,
                            "input_desktop_inferred": inferred_input_inferred,
                            "input_desktop_open_failed": True,
                            "input_desktop_open_error": f"open_failed:{error_code}",
                            "switched": switched,
                            "status": status,
                            "selected_desktop_source": inferred_input_source,
                            "desktop_rebind_required": rebind_required,
                            "desktop_rebind_attempted": rebind_attempted,
                            "desktop_rebind_succeeded": rebind_succeeded,
                            "desktop_rebind_reason": "input_desktop_handle_unavailable_inferred",
                            **handle_details,
                        },
                        bound_desktop,
                        None,
                        binding_mode="input",
                        binding_reason=reason,
                        record_binding_snapshot=True,
                    )

                status = f"open_failed error={error_code}"
                self._log_switch(
                    reason,
                    thread_id,
                    current_name,
                    current_handle,
                    "unavailable",
                    "0x0",
                    status,
                )
                return self._finalize_state(
                    {
                        "thread_id": str(int(thread_id)),
                        "thread_desktop": current_name,
                        "thread_desktop_handle": current_handle,
                        "input_desktop": "unavailable",
                        "input_desktop_handle": "0x0",
                        "input_desktop_source": inferred_input_source or "unavailable",
                        "input_desktop_inferred": inferred_input_inferred,
                        "input_desktop_open_failed": True,
                        "input_desktop_open_error": f"open_failed:{error_code}",
                        "switched": False,
                        "status": status,
                        "selected_desktop_source": "input_desktop",
                        "desktop_rebind_required": False,
                        "desktop_rebind_attempted": False,
                        "desktop_rebind_succeeded": False,
                        "desktop_rebind_reason": "input_desktop_unavailable",
                    },
                    current_desktop,
                    None,
                    binding_mode="input",
                    binding_reason=reason,
                    record_binding_snapshot=True,
                )

            input_name = self._get_desktop_name(input_desktop) or "unknown"
            input_handle = self._format_handle(input_desktop)
            if not self._same_handle(current_desktop, input_desktop):
                rebind_required = True
                rebind_attempted = True
                bind_handle, handle_details = self._acquire_cached_desktop_handle(input_name)
                if bind_handle and self.user32.SetThreadDesktop(bind_handle):
                    switched = True
                    rebind_succeeded = True
                    status = "bound_to_input_desktop"
                    self._remember_thread_binding(thread_id, input_name)
                else:
                    error_code = ctypes.get_last_error()
                    if bind_handle:
                        self._invalidate_cached_desktop_handle(
                            input_name,
                            reason=f"set_thread_desktop_failed:{error_code}",
                        )
                    status = f"switch_failed error={error_code}"
                self._log_switch(
                    reason,
                    thread_id,
                    current_name,
                    current_handle,
                    input_name,
                    input_handle,
                    status,
                )
            bound_desktop = self.user32.GetThreadDesktop(thread_id)
            return self._finalize_state(
                {
                "thread_id": str(int(thread_id)),
                "thread_desktop": self._get_desktop_name(bound_desktop) or input_name,
                "thread_desktop_handle": self._format_handle(bound_desktop),
                "input_desktop": input_name,
                "input_desktop_handle": input_handle,
                "input_desktop_source": "open_input_desktop",
                "input_desktop_inferred": False,
                "switched": switched,
                "status": status,
                "selected_desktop_source": "input_desktop",
                "desktop_rebind_required": rebind_required,
                "desktop_rebind_attempted": rebind_attempted,
                "desktop_rebind_succeeded": rebind_succeeded,
                "desktop_rebind_reason": "input_desktop_mismatch" if rebind_required else "already_on_input_desktop",
                **handle_details,
                },
                bound_desktop,
                input_desktop,
                binding_mode="input",
                binding_reason=reason,
                record_binding_snapshot=True,
            )
        except Exception as exc:
            status = f"exception={exc}"
            self._log_switch(
                reason,
                thread_id,
                current_name,
                current_handle,
                input_name,
                input_handle,
                status,
            )
            return self._finalize_state(
                {
                "thread_id": str(int(thread_id)),
                "thread_desktop": current_name,
                "thread_desktop_handle": current_handle,
                "input_desktop": input_name,
                "input_desktop_handle": input_handle,
                "input_desktop_source": "open_input_desktop" if input_desktop else "unavailable",
                "input_desktop_inferred": False,
                "switched": False,
                "status": status,
                "selected_desktop_source": "input_desktop",
                "desktop_rebind_required": rebind_required,
                "desktop_rebind_attempted": rebind_attempted,
                "desktop_rebind_succeeded": rebind_succeeded,
                "desktop_rebind_reason": "input_desktop_exception",
                **handle_details,
                },
                current_desktop,
                input_desktop,
                binding_mode="input",
                binding_reason=reason,
                record_binding_snapshot=True,
            )
        finally:
            if input_desktop:
                with contextlib.suppress(Exception):
                    self.user32.CloseDesktop(input_desktop)

    def ensure_current_thread_for_capture(
        self,
        reason: str,
        capture_target: dict[str, str | bool | int] | None = None,
    ) -> dict[str, str | bool]:
        thread_id = self.kernel32.GetCurrentThreadId()
        current_desktop = self.user32.GetThreadDesktop(thread_id)
        current_name = self._get_desktop_name(current_desktop) or "unknown"
        current_handle = self._format_handle(current_desktop)
        input_desktop = None
        input_name = "unavailable"
        input_handle = "0x0"
        target = dict(capture_target or {})
        candidate_names: list[str] = []
        candidate_entries: list[dict[str, str | bool | int]] = []
        selected_desktop = current_desktop
        selected_name = current_name
        selected_handle = current_handle
        status = "already_bound"
        switched = False
        last_error = ""
        rebind_required = False
        rebind_attempted = False
        rebind_succeeded = False
        selected_candidate_index = -1
        selected_candidate_source = "thread_desktop"
        selected_candidate_kind = self._classify_desktop_name(current_name)
        selected_candidate_name = current_name
        handle_details: dict[str, str | bool | int] = {}

        try:
            input_desktop = self.user32.OpenInputDesktop(0, False, self.DESKTOP_ACCESS)
            if input_desktop:
                input_name = self._get_desktop_name(input_desktop) or "unknown"
                input_handle = self._format_handle(input_desktop)

            seed_state = self._finalize_state(
                {
                    "thread_id": str(int(thread_id)),
                    "thread_desktop": current_name,
                    "thread_desktop_handle": current_handle,
                    "input_desktop": input_name,
                    "input_desktop_handle": input_handle,
                    "status": "capture_probe",
                },
                current_desktop,
                input_desktop,
                binding_mode="capture",
                capture_target=target,
                target_desktop=current_desktop,
                target_desktop_name=current_name,
                record_binding_snapshot=False,
            )
            candidate_names = self._build_capture_desktop_candidates(
                capture_target=target,
                state=seed_state,
            )
            candidate_entries = self._build_capture_desktop_candidate_entries(
                capture_target=target,
                state=seed_state,
            )

            for candidate_index, candidate_entry in enumerate(candidate_entries):
                candidate_name = str(candidate_entry.get("name") or "")
                candidate_display_name = candidate_name
                try:
                    if self._normalize_name(candidate_name) == self._normalize_name(input_name) and input_desktop:
                        candidate_display_name = input_name
                    candidate_handle, handle_details = self._acquire_cached_desktop_handle(
                        candidate_display_name or candidate_name
                    )
                    if candidate_handle:
                        candidate_display_name = self._get_desktop_name(candidate_handle) or candidate_display_name

                    if not candidate_handle:
                        last_error = f"open_failed:{candidate_name}:{ctypes.get_last_error()}"
                        continue

                    if self._same_handle(current_desktop, candidate_handle):
                        selected_desktop = current_desktop
                        selected_name = current_name
                        selected_handle = current_handle
                        status = "already_bound"
                        selected_candidate_index = candidate_index
                        selected_candidate_source = str(candidate_entry.get("source") or "unknown")
                        selected_candidate_kind = str(
                            candidate_entry.get("kind") or self._classify_desktop_name(candidate_display_name)
                        )
                        selected_candidate_name = candidate_display_name
                        break

                    rebind_required = True
                    rebind_attempted = True
                    if self.user32.SetThreadDesktop(candidate_handle):
                        switched = True
                        rebind_succeeded = True
                        self._remember_thread_binding(thread_id, candidate_display_name)
                        selected_desktop = self.user32.GetThreadDesktop(thread_id)
                        selected_name = self._get_desktop_name(selected_desktop) or candidate_display_name
                        selected_handle = self._format_handle(selected_desktop)
                        status = "bound_to_capture_desktop"
                        selected_candidate_index = candidate_index
                        selected_candidate_source = str(candidate_entry.get("source") or "unknown")
                        selected_candidate_kind = str(
                            candidate_entry.get("kind") or self._classify_desktop_name(selected_name)
                        )
                        selected_candidate_name = selected_name
                        self._log_switch(
                            reason,
                            thread_id,
                            current_name,
                            current_handle,
                            selected_name,
                            selected_handle,
                            status,
                        )
                        break

                    error_code = ctypes.get_last_error()
                    self._invalidate_cached_desktop_handle(
                        candidate_display_name or candidate_name,
                        reason=f"set_thread_desktop_failed:{error_code}",
                    )
                    last_error = f"switch_failed:{candidate_name}:{error_code}"
                except Exception:
                    self._invalidate_cached_desktop_handle(
                        candidate_display_name or candidate_name,
                        reason="candidate_handle_exception",
                    )
                    raise

            else:
                status = last_error or "no_capture_desktop_bound"
                self._log_switch(
                    reason,
                    thread_id,
                    current_name,
                    current_handle,
                    selected_name,
                    selected_handle,
                    status,
                )

            bound_desktop = self.user32.GetThreadDesktop(thread_id)
            if bound_desktop:
                selected_desktop = bound_desktop
                selected_name = self._get_desktop_name(bound_desktop) or selected_name
                selected_handle = self._format_handle(bound_desktop)

            return self._finalize_state(
                {
                    "thread_id": str(int(thread_id)),
                    "thread_desktop": selected_name,
                    "thread_desktop_handle": selected_handle,
                    "input_desktop": input_name,
                    "input_desktop_handle": input_handle,
                    "switched": switched,
                    "status": status,
                    "selected_desktop_source": selected_candidate_source,
                    "capture_desktop_source": selected_candidate_source,
                    "capture_selected_candidate_name": selected_candidate_name,
                    "capture_selected_candidate_index": selected_candidate_index,
                    "capture_selected_candidate_source": selected_candidate_source,
                    "capture_selected_candidate_kind": selected_candidate_kind,
                    "capture_candidate_trace": self._serialize_capture_candidate_trace(candidate_entries),
                    "desktop_rebind_required": rebind_required,
                    "desktop_rebind_attempted": rebind_attempted,
                    "desktop_rebind_succeeded": rebind_succeeded,
                    "desktop_rebind_reason": (
                        "capture_desktop_mismatch" if rebind_required else "already_on_capture_desktop"
                    ),
                    **handle_details,
                },
                selected_desktop,
                input_desktop,
                binding_mode="capture",
                capture_target=target,
                target_desktop=selected_desktop,
                target_desktop_name=selected_name,
                capture_candidates=candidate_names,
                binding_reason=reason,
                record_binding_snapshot=True,
            )
        except Exception as exc:
            status = f"exception={exc}"
            self._log_switch(
                reason,
                thread_id,
                current_name,
                current_handle,
                selected_name,
                selected_handle,
                status,
            )
            return self._finalize_state(
                {
                    "thread_id": str(int(thread_id)),
                    "thread_desktop": current_name,
                    "thread_desktop_handle": current_handle,
                    "input_desktop": input_name,
                    "input_desktop_handle": input_handle,
                    "switched": False,
                    "status": status,
                    "selected_desktop_source": selected_candidate_source,
                    "capture_desktop_source": selected_candidate_source,
                    "capture_selected_candidate_name": selected_candidate_name,
                    "capture_selected_candidate_index": selected_candidate_index,
                    "capture_selected_candidate_source": selected_candidate_source,
                    "capture_selected_candidate_kind": selected_candidate_kind,
                    "capture_candidate_trace": self._serialize_capture_candidate_trace(candidate_entries),
                    "desktop_rebind_required": rebind_required,
                    "desktop_rebind_attempted": rebind_attempted,
                    "desktop_rebind_succeeded": rebind_succeeded,
                    "desktop_rebind_reason": "capture_binding_exception",
                    **handle_details,
                },
                current_desktop,
                input_desktop,
                binding_mode="capture",
                capture_target=target,
                target_desktop=current_desktop,
                target_desktop_name=current_name,
                capture_candidates=candidate_names,
                binding_reason=reason,
                record_binding_snapshot=True,
            )
        finally:
            if input_desktop:
                with contextlib.suppress(Exception):
                    self.user32.CloseDesktop(input_desktop)

    @contextlib.contextmanager
    def activate_input_desktop(self, reason: str):
        thread_id = self.kernel32.GetCurrentThreadId()
        original_desktop = self.user32.GetThreadDesktop(thread_id)
        original_name = self._get_desktop_name(original_desktop) or "unknown"
        original_handle = self._format_handle(original_desktop)
        input_desktop = None
        input_name = original_name
        input_handle = original_handle
        switched = False

        try:
            input_desktop = self.user32.OpenInputDesktop(0, False, self.DESKTOP_ACCESS)
            if input_desktop:
                input_name = self._get_desktop_name(input_desktop) or "unknown"
                input_handle = self._format_handle(input_desktop)
                if not self._same_handle(original_desktop, input_desktop):
                    bind_handle, _handle_details = self._acquire_cached_desktop_handle(input_name)
                    if bind_handle and self.user32.SetThreadDesktop(bind_handle):
                        switched = True
                        self._remember_thread_binding(thread_id, input_name)
                        self._log_switch(
                            reason,
                            thread_id,
                            original_name,
                            original_handle,
                            input_name,
                            input_handle,
                            "switched",
                        )
                    else:
                        error_code = ctypes.get_last_error()
                        if bind_handle:
                            self._invalidate_cached_desktop_handle(
                                input_name,
                                reason=f"set_thread_desktop_failed:{error_code}",
                            )
                        self._log_switch(
                            reason,
                            thread_id,
                            original_name,
                            original_handle,
                            input_name,
                            input_handle,
                            f"switch_failed error={error_code}",
                        )
            else:
                error_code = ctypes.get_last_error()
                inferred_input_name, _inferred_source, _inferred_flag = self._select_rebindable_input_desktop_name(
                    thread_desktop_name=original_name,
                    thread_id=int(thread_id),
                    desktop_inventory=self._list_window_station_desktops(),
                )
                if self._normalize_name(inferred_input_name) not in {"", "unknown", "unavailable"}:
                    input_name = inferred_input_name
                    input_handle = "0x0"
                    if self._normalize_name(original_name) != self._normalize_name(input_name):
                        bind_handle, _handle_details = self._acquire_cached_desktop_handle(input_name)
                        if bind_handle and self.user32.SetThreadDesktop(bind_handle):
                            switched = True
                            self._remember_thread_binding(thread_id, input_name)
                            self._log_switch(
                                reason,
                                thread_id,
                                original_name,
                                original_handle,
                                input_name,
                                input_handle,
                                "inferred_switched",
                            )
                        else:
                            rebound_error = ctypes.get_last_error()
                            if bind_handle:
                                self._invalidate_cached_desktop_handle(
                                    input_name,
                                    reason=f"set_thread_desktop_failed:{rebound_error}",
                                )
                            self._log_switch(
                                reason,
                                thread_id,
                                original_name,
                                original_handle,
                                input_name,
                                input_handle,
                                f"inferred_switch_failed error={rebound_error}",
                            )
                    else:
                        self._remember_thread_binding(thread_id, input_name)
                        self._log_switch(
                            reason,
                            thread_id,
                            original_name,
                            original_handle,
                            input_name,
                            input_handle,
                            f"input_desktop_inferred_current_thread error={error_code}",
                        )
                else:
                    self._log_switch(
                        reason,
                        thread_id,
                        original_name,
                        original_handle,
                        "unavailable",
                        "0x0",
                        f"open_failed error={error_code}",
                    )
        except Exception as exc:
            self._log_switch(
                reason,
                thread_id,
                original_name,
                original_handle,
                input_name,
                input_handle,
                f"exception={exc}",
            )

        try:
            yield
        finally:
            if switched and original_desktop:
                try:
                    if self.user32.SetThreadDesktop(original_desktop):
                        self._remember_thread_binding(thread_id, original_name)
                        self._log_switch(
                            reason,
                            thread_id,
                            input_name,
                            input_handle,
                            original_name,
                            original_handle,
                            "restored",
                        )
                    else:
                        error_code = ctypes.get_last_error()
                        self._log_switch(
                            reason,
                            thread_id,
                            input_name,
                            input_handle,
                            original_name,
                            original_handle,
                            f"restore_failed error={error_code}",
                        )
                except Exception as exc:
                    self._log_switch(
                        reason,
                        thread_id,
                        input_name,
                        input_handle,
                        original_name,
                        original_handle,
                        f"restore_exception={exc}",
                    )

            if input_desktop:
                with contextlib.suppress(Exception):
                    self.user32.CloseDesktop(input_desktop)

    def _get_desktop_name(self, desktop_handle) -> str:
        if not desktop_handle:
            return ""

        needed = wintypes.DWORD(0)
        self.user32.GetUserObjectInformationW(
            desktop_handle,
            self.UOI_NAME,
            None,
            0,
            ctypes.byref(needed),
        )
        if needed.value <= 2:
            return ""

        buffer = ctypes.create_unicode_buffer(max(1, needed.value // ctypes.sizeof(wintypes.WCHAR)))
        if not self.user32.GetUserObjectInformationW(
            desktop_handle,
            self.UOI_NAME,
            buffer,
            needed.value,
            ctypes.byref(needed),
        ):
            return ""
        return str(buffer.value or "").strip()

    def _log_switch(
        self,
        reason: str,
        thread_id: int,
        source_desktop: str,
        source_handle: str,
        target_desktop: str,
        target_handle: str,
        outcome: str,
    ):
        log_key = (
            reason,
            int(thread_id),
            self._normalize_name(source_desktop),
            str(source_handle or ""),
            self._normalize_name(target_desktop),
            str(target_handle or ""),
            outcome,
        )
        with self._log_lock:
            if log_key == self._last_log_key:
                return
            self._last_log_key = log_key

        print(
            f"[DesktopContext][{self.component}] {reason}: "
            f"thread={int(thread_id)} "
            f"{source_desktop or 'unknown'}[{source_handle}] -> "
            f"{target_desktop or 'unknown'}[{target_handle}] "
            f"status={outcome}"
        )

    def _normalize_name(self, desktop_name: str) -> str:
        return str(desktop_name or "").strip().lower()

    def _finalize_state(
        self,
        state: dict[str, str | bool],
        thread_desktop,
        input_desktop,
        *,
        binding_mode: str = "input",
        capture_target: dict[str, str | bool | int] | None = None,
        target_desktop=None,
        target_desktop_name: str = "",
        capture_candidates: list[str] | None = None,
        binding_reason: str = "",
        record_binding_snapshot: bool = False,
    ) -> dict[str, str | bool]:
        session_id = self._get_current_process_session_id()
        console_session_id = self._get_active_console_session_id()
        process_winsta = self.user32.GetProcessWindowStation()
        process_winsta_name = str(
            state.get("process_winsta")
            or self._get_desktop_name(process_winsta)
            or "unknown"
        )
        virtual_x = self.user32.GetSystemMetrics(76)
        virtual_y = self.user32.GetSystemMetrics(77)
        screen_width = self.user32.GetSystemMetrics(78)
        screen_height = self.user32.GetSystemMetrics(79)
        normalized_binding_mode = str(binding_mode or "input").strip().lower()
        thread_desktop_name = str(state.get("thread_desktop") or self._get_desktop_name(thread_desktop) or "unknown")
        thread_desktop_kind = self._classify_desktop_name(thread_desktop_name)
        target_desktop_name = str(
            target_desktop_name
            or self._get_desktop_name(target_desktop)
            or state.get("thread_desktop")
            or input_desktop_name
            or "unknown"
        )
        target_desktop_kind = self._classify_desktop_name(target_desktop_name)
        thread_id = self._coerce_int(
            state.get("thread_id"),
            int(self.kernel32.GetCurrentThreadId()),
        )
        desktop_inventory = self._list_window_station_desktops()
        remembered_thread_desktop = self._get_remembered_thread_binding(thread_id)
        requested_input_desktop_name = str(
            state.get("input_desktop")
            or self._get_desktop_name(input_desktop)
            or "unavailable"
        )
        explicit_input_desktop_source = str(state.get("input_desktop_source") or "").strip()
        if (
            explicit_input_desktop_source
            and self._normalize_name(requested_input_desktop_name) not in {"", "unknown", "unavailable"}
        ):
            resolved_input_desktop_name = self._resolve_window_station_desktop_name(
                requested_input_desktop_name,
                desktop_inventory=desktop_inventory,
            )
            input_desktop_name = str(resolved_input_desktop_name or requested_input_desktop_name)
            input_desktop_source = explicit_input_desktop_source
            input_desktop_inferred = self._coerce_bool(state.get("input_desktop_inferred"))
        else:
            input_desktop_name, input_desktop_source, input_desktop_inferred = self._infer_input_desktop_name(
                input_desktop_name=requested_input_desktop_name,
                thread_desktop_name=thread_desktop_name,
                thread_id=thread_id,
                desktop_inventory=desktop_inventory,
            )
        input_desktop_kind = self._classify_desktop_name(input_desktop_name)
        state["session_id"] = str(session_id) if session_id is not None else "unknown"
        state["active_console_session_id"] = (
            str(console_session_id) if console_session_id is not None else "unknown"
        )
        state["binding_mode"] = normalized_binding_mode
        state["is_remote_session"] = bool(self.user32.GetSystemMetrics(0x1000))
        state["process_winsta"] = process_winsta_name
        state["process_winsta_handle"] = str(
            state.get("process_winsta_handle")
            or self._format_handle(process_winsta)
        )
        state["thread_matches_input"] = bool(
            (input_desktop and self._same_handle(thread_desktop, input_desktop))
            or (
                not input_desktop
                and self._normalize_name(thread_desktop_name)
                and self._normalize_name(thread_desktop_name) == self._normalize_name(input_desktop_name)
                and self._normalize_name(input_desktop_name) not in {"unknown", "unavailable"}
            )
        )
        state["thread_matches_target"] = bool(
            (target_desktop and self._same_handle(thread_desktop, target_desktop))
            or (
                not target_desktop
                and self._normalize_name(thread_desktop_name)
                and self._normalize_name(thread_desktop_name) == self._normalize_name(target_desktop_name)
                and self._normalize_name(target_desktop_name) not in {"unknown", "unavailable"}
            )
        )
        state["virtual_screen_origin"] = f"{int(virtual_x)},{int(virtual_y)}"
        state["virtual_screen_size"] = f"{int(screen_width)}x{int(screen_height)}"
        state["thread_desktop"] = thread_desktop_name
        state["thread_desktop_kind"] = thread_desktop_kind
        state["input_desktop"] = input_desktop_name
        state["input_desktop_kind"] = input_desktop_kind
        state["input_desktop_source"] = input_desktop_source
        state["input_desktop_inferred"] = bool(input_desktop_inferred)
        state["capture_desktop"] = target_desktop_name
        state["capture_desktop_kind"] = target_desktop_kind
        state["capture_desktop_source"] = str(
            state.get("capture_desktop_source")
            or state.get("selected_desktop_source")
            or ("input_desktop" if normalized_binding_mode == "input" else "")
        )
        state["capture_thread_matches_target"] = bool(state["thread_matches_target"])
        state["capture_desktop_candidates"] = ",".join(capture_candidates or [])
        state["capture_candidate_trace"] = str(state.get("capture_candidate_trace") or "")
        state["desktop_kind"] = target_desktop_kind if normalized_binding_mode == "capture" else input_desktop_kind
        target = dict(capture_target or {})
        preferred_capture_desktops = self._normalize_capture_desktop_names(
            target.get("preferred_capture_desktops")
            if target
            else state.get("preferred_capture_desktops")
        )
        preferred_capture_desktop_kind = str(
            target.get("preferred_capture_desktop_kind")
            or state.get("preferred_capture_desktop_kind")
            or ""
        ).strip()
        desktop_transition_reason = str(
            target.get("desktop_transition_reason")
            or state.get("desktop_transition_reason")
            or ""
        ).strip()
        authoritative_capture_binding = self._coerce_bool(
            target.get("authoritative_capture_binding"),
            self._coerce_bool(state.get("authoritative_capture_binding")),
        )
        allow_secure_desktop = self._coerce_bool(
            target.get("allow_secure_desktop"),
            self._coerce_bool(state.get("allow_secure_desktop")),
        )
        allow_screensaver_desktop = self._coerce_bool(
            target.get("allow_screensaver_desktop"),
            self._coerce_bool(state.get("allow_screensaver_desktop")),
        )
        capture_binding_generation = self._coerce_int(
            target.get("capture_binding_generation"),
            self._coerce_int(state.get("capture_binding_generation"), 0),
        )
        capture_binding_policy_signature = str(
            target.get("capture_binding_policy_signature")
            or state.get("capture_binding_policy_signature")
            or ""
        ).strip()
        state["preferred_capture_desktops"] = list(preferred_capture_desktops)
        state["preferred_capture_desktop_kind"] = preferred_capture_desktop_kind
        state["desktop_transition_reason"] = desktop_transition_reason
        state["authoritative_capture_binding"] = authoritative_capture_binding
        state["allow_secure_desktop"] = allow_secure_desktop
        state["allow_screensaver_desktop"] = allow_screensaver_desktop
        state["capture_binding_generation"] = capture_binding_generation
        state["capture_binding_policy_signature"] = capture_binding_policy_signature
        state["capture_target_session_id"] = str(target.get("session_id") or state["session_id"])
        state["capture_target_station_name"] = str(target.get("station_name") or "")
        state["capture_target_state"] = str(target.get("state") or "")
        state["capture_target_is_remote_session"] = self._coerce_bool(
            target.get("is_remote_session"),
            bool(state.get("is_remote_session")),
        )
        state["capture_target_is_console_session"] = self._coerce_bool(
            target.get("is_console_session"),
            str(target.get("session_id") or state["session_id"]) == str(state["active_console_session_id"]),
        )
        state["selected_desktop_allowed_by_policy"] = self._is_capture_desktop_allowed_by_policy(
            target_desktop_name,
            preferred_capture_desktops,
            allow_secure_desktop=allow_secure_desktop,
            allow_screensaver_desktop=allow_screensaver_desktop,
            authoritative_capture_binding=authoritative_capture_binding,
        )
        state["selected_desktop_matches_preferred_kind"] = self._desktop_matches_preferred_kind(
            desktop_name=target_desktop_name,
            desktop_kind=target_desktop_kind,
            preferred_kind=preferred_capture_desktop_kind,
            preferred_capture_desktops=preferred_capture_desktops,
            authoritative_capture_binding=authoritative_capture_binding,
        )
        state["selected_desktop_name"] = target_desktop_name
        state["selected_desktop_kind"] = target_desktop_kind
        state["desktop_binding_changed"] = bool(state.get("switched"))
        state["desktop_rebind_required"] = bool(state.get("desktop_rebind_required"))
        state["desktop_rebind_attempted"] = bool(state.get("desktop_rebind_attempted"))
        state["desktop_rebind_succeeded"] = bool(state.get("desktop_rebind_succeeded"))
        state["desktop_binding_reason"] = str(state.get("desktop_binding_reason") or binding_reason or "")
        state["desktop_handle_cache_hit"] = bool(state.get("desktop_handle_cache_hit"))
        state["desktop_handle_cache_reused"] = bool(state.get("desktop_handle_cache_reused"))
        state["desktop_handle_reopened"] = bool(state.get("desktop_handle_reopened"))
        state["desktop_handle_open_failed"] = bool(state.get("desktop_handle_open_failed"))
        state["desktop_handle_stale_cached"] = bool(state.get("desktop_handle_stale_cached"))
        state["input_target_signature"] = self.build_input_target_signature(state)
        state["capture_target_signature"] = self.build_capture_target_signature(capture_target, state=state)
        state["desktop_signature"] = self.build_desktop_signature(
            state,
            binding_mode=normalized_binding_mode,
        )
        inventory_details = self._refresh_desktop_inventory_context(
            process_winsta_name=process_winsta_name,
            desktop_inventory=desktop_inventory,
        )
        state["available_desktops"] = list(desktop_inventory)
        state["available_desktop_trace"] = self._serialize_desktop_inventory(desktop_inventory)
        state["desktop_inventory_signature"] = str(inventory_details.get("desktop_inventory_signature") or "")
        state["desktop_inventory_previous_signature"] = str(
            inventory_details.get("desktop_inventory_previous_signature") or ""
        )
        state["desktop_inventory_changed"] = bool(inventory_details.get("desktop_inventory_changed"))
        state["desktop_inventory_change_reason"] = str(
            inventory_details.get("desktop_inventory_change_reason") or ""
        )
        state["window_station_changed"] = bool(inventory_details.get("window_station_changed"))
        state["window_station_previous_name"] = str(inventory_details.get("window_station_previous_name") or "")
        state["desktop_inventory_invalidated_cache_names"] = str(
            inventory_details.get("desktop_inventory_invalidated_cache_names") or ""
        )
        state["desktop_inventory_invalidated_cache_count"] = int(
            inventory_details.get("desktop_inventory_invalidated_cache_count") or 0
        )
        state["thread_last_bound_desktop"] = remembered_thread_desktop
        state["thread_last_bound_matches_current"] = (
            self._normalize_name(str(state.get("thread_last_bound_desktop") or ""))
            == self._normalize_name(thread_desktop_name)
            if str(state.get("thread_last_bound_desktop") or "").strip()
            else False
        )
        state["desktop_binding_scope_signature"] = self.build_binding_scope_signature(
            binding_mode=normalized_binding_mode,
            capture_target=target if normalized_binding_mode == "capture" else None,
            state=state,
        )
        current_handle_scope_signature = self._build_handle_scope_signature(
            process_winsta_name=process_winsta_name,
            desktop_inventory_signature=str(state.get("desktop_inventory_signature") or "empty"),
        )
        state["desktop_handle_scope_signature"] = str(
            state.get("desktop_handle_scope_signature")
            or current_handle_scope_signature
            or ""
        )
        state["desktop_handle_current_scope_signature"] = current_handle_scope_signature
        state["desktop_handle_scope_drift"] = bool(state.get("desktop_handle_scope_drift"))
        state["capture_binding_identity"] = self.build_capture_binding_identity(target, state=state)
        state["desktop_context_transition_signature"] = self._build_desktop_context_transition_signature(state)
        cache_snapshot = self._get_cached_desktop_handle_snapshot()
        state["desktop_handle_cache_size"] = len(cache_snapshot)
        state["desktop_handle_cache_names"] = ",".join(cache_snapshot)
        if record_binding_snapshot:
            return self._record_binding_snapshot(state)
        return state

    def _record_binding_snapshot(self, state: dict[str, str | bool | int]) -> dict[str, str | bool | int]:
        snapshot = dict(state)
        with self._binding_state_lock:
            self._desktop_context_generation += 1
            snapshot["desktop_context_generation"] = int(self._desktop_context_generation)
            snapshot["desktop_binding_recorded_at"] = int(time.time() * 1000)
            self._last_binding_snapshot = dict(snapshot)
        return snapshot

    def _build_capture_desktop_candidate_entries(
        self,
        *,
        capture_target: dict[str, str | bool | int] | None = None,
        state: dict[str, str | bool] | None = None,
    ) -> list[dict[str, str | bool | int]]:
        snapshot = dict(state or self.describe_current_state())
        target = dict(capture_target or {})
        target_state = str(target.get("state") or "").strip().lower()
        target_is_console = self._coerce_bool(
            target.get("is_console_session"),
            str(target.get("session_id") or snapshot.get("session_id"))
            == str(snapshot.get("active_console_session_id")),
        )
        target_is_remote = self._coerce_bool(
            target.get("is_remote_session"),
            bool(snapshot.get("is_remote_session")),
        )
        authoritative_capture_binding = self._coerce_bool(
            target.get("authoritative_capture_binding"),
            self._coerce_bool(snapshot.get("authoritative_capture_binding")),
        )
        allow_secure_desktop = self._coerce_bool(
            target.get("allow_secure_desktop"),
            self._coerce_bool(snapshot.get("allow_secure_desktop")),
        )
        allow_screensaver_desktop = self._coerce_bool(
            target.get("allow_screensaver_desktop"),
            self._coerce_bool(snapshot.get("allow_screensaver_desktop")),
        )
        preferred_desktops = self._normalize_capture_desktop_names(
            target.get("preferred_capture_desktops")
            if target
            else snapshot.get("preferred_capture_desktops")
        )
        preferred_desktop_set = {
            self._normalize_name(item)
            for item in preferred_desktops
            if self._normalize_name(item)
        }
        desktop_inventory = self._list_window_station_desktops()

        candidates: list[dict[str, str | bool | int]] = []
        seen_names: set[str] = set()

        def add(name: str | None, source: str) -> None:
            requested_name = str(name or "").strip()
            resolved_name = self._resolve_window_station_desktop_name(
                requested_name,
                desktop_inventory=desktop_inventory,
            )
            candidate_name = resolved_name or requested_name
            normalized_name = self._normalize_name(candidate_name)
            if not normalized_name or normalized_name in {"unknown", "unavailable"}:
                return
            if desktop_inventory and not self._desktop_name_in_inventory(candidate_name, desktop_inventory):
                return
            if not self._capture_desktop_allowed_by_gates(
                candidate_name,
                allow_secure_desktop=allow_secure_desktop,
                allow_screensaver_desktop=allow_screensaver_desktop,
            ):
                return
            allowed_by_policy = self._is_capture_desktop_allowed_by_policy(
                candidate_name,
                preferred_desktops,
                allow_secure_desktop=allow_secure_desktop,
                allow_screensaver_desktop=allow_screensaver_desktop,
                authoritative_capture_binding=authoritative_capture_binding,
            )
            if authoritative_capture_binding and not allowed_by_policy:
                return
            if normalized_name in seen_names:
                return
            seen_names.add(normalized_name)
            candidates.append(
                {
                    "name": candidate_name,
                    "kind": self._classify_desktop_name(candidate_name),
                    "source": source,
                    "preferred": normalized_name in preferred_desktop_set,
                    "allowed_by_policy": allowed_by_policy,
                }
            )

        if authoritative_capture_binding and preferred_desktops:
            for name in preferred_desktops:
                add(name, "policy_preferred")

        add(str(snapshot.get("input_desktop") or ""), "input_desktop")
        add(str(snapshot.get("thread_desktop") or ""), "thread_desktop")

        fallback_source = "state_fallback"
        if target_state == "disconnected":
            fallback_source = "disconnected_fallback"
            for name in ("Disconnect", "Default", "Screen-Saver", "Winlogon"):
                add(name, fallback_source)
        elif target_is_console:
            fallback_source = "console_fallback"
            for name in ("Default", "Screen-Saver", "Winlogon", "Disconnect"):
                add(name, fallback_source)
        elif target_is_remote:
            fallback_source = "remote_fallback"
            for name in ("Default", "Screen-Saver", "Disconnect", "Winlogon"):
                add(name, fallback_source)
        else:
            for name in ("Default", "Screen-Saver", "Winlogon", "Disconnect"):
                add(name, fallback_source)

        return candidates

    def _build_capture_desktop_candidates(
        self,
        *,
        capture_target: dict[str, str | bool | int] | None = None,
        state: dict[str, str | bool] | None = None,
    ) -> list[str]:
        entries = self._build_capture_desktop_candidate_entries(
            capture_target=capture_target,
            state=state,
        )
        return [str(entry.get("name") or "").strip() for entry in entries if str(entry.get("name") or "").strip()]

    def _serialize_capture_candidate_trace(
        self,
        candidate_entries: list[dict[str, str | bool | int]] | None,
    ) -> str:
        if not candidate_entries:
            return ""
        serialized_entries: list[str] = []
        for index, entry in enumerate(candidate_entries):
            serialized_entries.append(
                ":".join(
                    [
                        str(index),
                        self._normalize_name(str(entry.get("name") or "unknown")),
                        self._normalize_name(str(entry.get("kind") or "unknown")),
                        self._normalize_name(str(entry.get("source") or "unknown")),
                        "1" if bool(entry.get("allowed_by_policy")) else "0",
                    ]
                )
            )
        return ",".join(serialized_entries)

    def _normalize_capture_desktop_names(self, value) -> list[str]:
        if value in (None, ""):
            return []
        if isinstance(value, (list, tuple, set)):
            raw_items = list(value)
        else:
            raw_items = str(value).split(",")

        normalized_items: list[str] = []
        seen_names: set[str] = set()
        for item in raw_items:
            name = str(item or "").strip()
            normalized_name = self._normalize_name(name)
            if not normalized_name or normalized_name in {"unknown", "unavailable"}:
                continue
            if normalized_name in seen_names:
                continue
            seen_names.add(normalized_name)
            normalized_items.append(name)
        return normalized_items

    def _capture_desktop_allowed_by_gates(
        self,
        desktop_name: str,
        *,
        allow_secure_desktop: bool,
        allow_screensaver_desktop: bool,
    ) -> bool:
        desktop_kind = self._classify_desktop_name(desktop_name)
        if desktop_kind == "secure_winlogon":
            return bool(allow_secure_desktop)
        if desktop_kind == "screensaver":
            return bool(allow_screensaver_desktop)
        return True

    def _is_capture_desktop_allowed_by_policy(
        self,
        desktop_name: str,
        preferred_capture_desktops: list[str] | None,
        *,
        allow_secure_desktop: bool,
        allow_screensaver_desktop: bool,
        authoritative_capture_binding: bool,
    ) -> bool:
        normalized_name = self._normalize_name(desktop_name)
        if not normalized_name:
            return False
        if not self._capture_desktop_allowed_by_gates(
            normalized_name,
            allow_secure_desktop=allow_secure_desktop,
            allow_screensaver_desktop=allow_screensaver_desktop,
        ):
            return False
        if not authoritative_capture_binding:
            return True
        preferred_names = {
            self._normalize_name(item)
            for item in self._normalize_capture_desktop_names(preferred_capture_desktops)
        }
        if not preferred_names:
            return True
        return normalized_name in preferred_names

    def _desktop_matches_preferred_kind(
        self,
        *,
        desktop_name: str,
        desktop_kind: str,
        preferred_kind: str,
        preferred_capture_desktops: list[str] | None = None,
        authoritative_capture_binding: bool = False,
    ) -> bool:
        normalized_preferred_kind = self._normalize_name(preferred_kind)
        normalized_desktop_name = self._normalize_name(desktop_name)
        if authoritative_capture_binding and normalized_desktop_name:
            preferred_names = {
                self._normalize_name(item)
                for item in self._normalize_capture_desktop_names(preferred_capture_desktops)
            }
            if normalized_desktop_name in preferred_names:
                return True
        if not normalized_preferred_kind:
            return True
        observed_kind = self._normalize_name(desktop_kind) or self._classify_desktop_name(desktop_name)
        return observed_kind == normalized_preferred_kind

    def _coerce_int(self, value, default: int = 0) -> int:
        try:
            return int(value)
        except Exception:
            return int(default)

    def _coerce_bool(self, value, default: bool = False) -> bool:
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

    def _classify_desktop_name(self, desktop_name: str) -> str:
        normalized_name = self._normalize_name(desktop_name)
        terminal_name = normalized_name.rsplit("\\", 1)[-1] if normalized_name else ""
        if terminal_name in {"default", "default desktop"}:
            return "interactive_default"
        if terminal_name in {"winlogon", "secure"}:
            return "secure_winlogon"
        if terminal_name in {"disconnect", "disconnected"}:
            return "disconnected_shell"
        if terminal_name in {"screen-saver", "screensaver", "screen saver"}:
            return "screensaver"
        if not normalized_name or normalized_name == "unavailable":
            return "unavailable"
        return terminal_name or normalized_name

    def _get_current_process_session_id(self) -> int | None:
        current_pid = self.kernel32.GetCurrentProcessId()
        session_id = wintypes.DWORD()
        if self.kernel32.ProcessIdToSessionId(current_pid, ctypes.byref(session_id)):
            return int(session_id.value)
        return None

    def _get_active_console_session_id(self) -> int | None:
        try:
            session_id = int(self.kernel32.WTSGetActiveConsoleSessionId())
        except Exception:
            return None
        if session_id in (-1, 0xFFFFFFFF):
            return None
        return session_id

    def _same_handle(self, left_handle, right_handle) -> bool:
        return self._get_handle_value(left_handle) == self._get_handle_value(right_handle)

    def _build_handle_scope_signature(
        self,
        *,
        process_winsta_name: str = "",
        desktop_inventory_signature: str = "",
    ) -> str:
        session_id = self._get_current_process_session_id()
        console_session_id = self._get_active_console_session_id()
        current_station_name = str(
            process_winsta_name
            or self._get_desktop_name(self.user32.GetProcessWindowStation())
            or "unknown"
        ).strip()
        inventory_signature = str(desktop_inventory_signature or "empty").strip() or "empty"
        return "|".join(
            [
                f"session={session_id if session_id is not None else 'unknown'}",
                f"console={console_session_id if console_session_id is not None else 'unknown'}",
                f"remote={1 if self.user32.GetSystemMetrics(0x1000) else 0}",
                f"winsta={self._normalize_name(current_station_name or 'unknown')}",
                f"inventory={self._normalize_name(inventory_signature or 'empty')}",
            ]
        )

    def _make_desktop_handle_record(
        self,
        desktop_handle,
        *,
        desktop_name: str,
        scope_signature: str,
    ) -> dict[str, object]:
        return {
            "handle": desktop_handle,
            "desktop_name": str(desktop_name or "").strip(),
            "scope_signature": str(scope_signature or "").strip(),
            "opened_at": int(time.time() * 1000),
        }

    def _extract_cached_desktop_handle(self, cached_entry):
        if isinstance(cached_entry, dict):
            return cached_entry.get("handle")
        return cached_entry

    def _extract_cached_desktop_scope_signature(self, cached_entry) -> str:
        if isinstance(cached_entry, dict):
            return str(cached_entry.get("scope_signature") or "").strip()
        return ""

    def _extract_cached_desktop_name(self, cached_entry, default_name: str = "") -> str:
        if isinstance(cached_entry, dict):
            return str(cached_entry.get("desktop_name") or default_name or "").strip()
        return str(default_name or "").strip()

    def _acquire_cached_desktop_handle(self, desktop_name: str):
        desktop_inventory = self._list_window_station_desktops()
        inventory_details = self._refresh_desktop_inventory_context(desktop_inventory=desktop_inventory)
        resolved_name = self._resolve_window_station_desktop_name(
            desktop_name,
            desktop_inventory=desktop_inventory,
        )
        effective_name = str(resolved_name or desktop_name or "").strip()
        normalized_name = self._normalize_name(effective_name)
        current_scope_signature = self._build_handle_scope_signature(
            process_winsta_name=str(inventory_details.get("window_station_name") or ""),
            desktop_inventory_signature=str(inventory_details.get("desktop_inventory_signature") or ""),
        )
        handle_details: dict[str, str | bool | int] = {
            "desktop_handle_target_name": effective_name,
            "desktop_handle_cache_hit": False,
            "desktop_handle_cache_reused": False,
            "desktop_handle_reopened": False,
            "desktop_handle_open_failed": False,
            "desktop_handle_stale_cached": False,
            "desktop_handle_open_error": "",
            "desktop_handle_cached_scope_signature": "",
            "desktop_handle_scope_signature": current_scope_signature,
            "desktop_handle_scope_drift": False,
            "desktop_inventory_signature": str(inventory_details.get("desktop_inventory_signature") or ""),
            "desktop_inventory_changed": bool(inventory_details.get("desktop_inventory_changed")),
            "desktop_inventory_change_reason": str(
                inventory_details.get("desktop_inventory_change_reason") or ""
            ),
        }
        if not normalized_name or normalized_name in {"unknown", "unavailable"}:
            handle_details["desktop_handle_open_failed"] = True
            handle_details["desktop_handle_open_error"] = "invalid_desktop_name"
            return None, handle_details
        if desktop_inventory and not self._desktop_name_in_inventory(effective_name, desktop_inventory):
            handle_details["desktop_handle_open_failed"] = True
            handle_details["desktop_handle_open_error"] = "desktop_not_present_in_window_station"
            return None, handle_details

        with self._desktop_handle_lock:
            cached_entry = self._managed_desktop_handles.get(normalized_name)
        cached_handle = self._extract_cached_desktop_handle(cached_entry)
        cached_scope_signature = self._extract_cached_desktop_scope_signature(cached_entry)
        if cached_scope_signature:
            handle_details["desktop_handle_cached_scope_signature"] = cached_scope_signature
            if cached_scope_signature != current_scope_signature:
                handle_details["desktop_handle_scope_drift"] = True
        had_cached_handle = bool(cached_handle)
        if cached_handle:
            handle_details["desktop_handle_cache_hit"] = True
        if cached_handle and cached_scope_signature and cached_scope_signature != current_scope_signature:
            handle_details["desktop_handle_stale_cached"] = True
            self._invalidate_cached_desktop_handle(
                effective_name,
                reason=(
                    "handle_scope_drift:"
                    f"{cached_scope_signature or 'unknown'}->{current_scope_signature or 'unknown'}"
                ),
            )
            cached_handle = None
        if cached_handle and self._is_desktop_handle_valid(cached_handle, expected_name=effective_name):
            handle_details["desktop_handle_cache_reused"] = True
            return cached_handle, handle_details

        if cached_handle:
            handle_details["desktop_handle_stale_cached"] = True
            self._invalidate_cached_desktop_handle(effective_name, reason="stale_cached_handle")

        desktop_handle = self.user32.OpenDesktopW(
            effective_name,
            0,
            False,
            self.DESKTOP_ACCESS,
        )
        if not desktop_handle:
            handle_details["desktop_handle_open_failed"] = True
            handle_details["desktop_handle_open_error"] = f"open_failed:{ctypes.get_last_error()}"
            return None, handle_details
        if not self._is_desktop_handle_valid(desktop_handle, expected_name=effective_name):
            with contextlib.suppress(Exception):
                self.user32.CloseDesktop(desktop_handle)
            handle_details["desktop_handle_open_failed"] = True
            handle_details["desktop_handle_open_error"] = "opened_handle_name_mismatch"
            return None, handle_details

        with self._desktop_handle_lock:
            existing_entry = self._managed_desktop_handles.get(normalized_name)
            existing_handle = self._extract_cached_desktop_handle(existing_entry)
            existing_scope_signature = self._extract_cached_desktop_scope_signature(existing_entry)
            if existing_handle and self._is_desktop_handle_valid(existing_handle, expected_name=effective_name):
                if existing_scope_signature and existing_scope_signature != current_scope_signature:
                    self._managed_desktop_handles.pop(normalized_name, None)
                    with contextlib.suppress(Exception):
                        self.user32.CloseDesktop(existing_handle)
                else:
                    with contextlib.suppress(Exception):
                        self.user32.CloseDesktop(desktop_handle)
                    handle_details["desktop_handle_cache_hit"] = True
                    handle_details["desktop_handle_cache_reused"] = True
                    handle_details["desktop_handle_cached_scope_signature"] = existing_scope_signature
                    handle_details["desktop_handle_scope_signature"] = current_scope_signature
                    handle_details["desktop_handle_scope_drift"] = bool(
                        existing_scope_signature and existing_scope_signature != current_scope_signature
                    )
                    return existing_handle, handle_details
            self._managed_desktop_handles[normalized_name] = self._make_desktop_handle_record(
                desktop_handle,
                desktop_name=effective_name,
                scope_signature=current_scope_signature,
            )
        handle_details["desktop_handle_reopened"] = had_cached_handle
        return desktop_handle, handle_details

    def _list_window_station_desktops(self) -> list[str]:
        process_winsta = self.user32.GetProcessWindowStation()
        if not process_winsta:
            return []

        desktop_names: list[str] = []

        @self._enum_desktops_proc
        def _enum_desktop_callback(desktop_name, _lparam):
            normalized_name = str(desktop_name or "").strip()
            if normalized_name:
                desktop_names.append(normalized_name)
            return True

        if not self.user32.EnumDesktopsW(process_winsta, _enum_desktop_callback, 0):
            return []
        return self._normalize_desktop_inventory(desktop_names)

    def _normalize_desktop_inventory(self, desktop_names: list[str] | tuple[str, ...]) -> list[str]:
        ordered_names: list[str] = []
        seen_names: set[str] = set()
        for name in desktop_names:
            normalized_name = str(name or "").strip()
            normalized_key = self._normalize_name(normalized_name)
            if not normalized_key or normalized_key in seen_names:
                continue
            seen_names.add(normalized_key)
            ordered_names.append(normalized_name)
        return ordered_names

    def _serialize_desktop_inventory(self, desktop_inventory: list[str]) -> str:
        if not desktop_inventory:
            return ""
        return ",".join(
            f"{self._normalize_name(name)}:{self._classify_desktop_name(name)}"
            for name in desktop_inventory
        )

    def _compute_desktop_inventory_signature(self, desktop_inventory: list[str]) -> str:
        if not desktop_inventory:
            return "empty"
        return "|".join(self._normalize_name(name) for name in desktop_inventory)

    def _refresh_desktop_inventory_context(
        self,
        *,
        process_winsta_name: str = "",
        desktop_inventory: list[str] | None = None,
    ) -> dict[str, str | bool | int]:
        inventory = self._normalize_desktop_inventory(desktop_inventory or self._list_window_station_desktops())
        inventory_trace = self._serialize_desktop_inventory(inventory)
        inventory_signature = self._compute_desktop_inventory_signature(inventory)
        current_station_name = str(
            process_winsta_name
            or self._get_desktop_name(self.user32.GetProcessWindowStation())
            or "unknown"
        ).strip()
        previous_signature = ""
        previous_trace = ""
        previous_station_name = ""
        inventory_changed = False
        window_station_changed = False
        change_reason = ""

        with self._binding_state_lock:
            previous_signature = str(self._last_inventory_signature or "")
            previous_trace = str(self._last_inventory_trace or "")
            previous_station_name = str(self._last_window_station_name or "")
            normalized_previous_station = self._normalize_name(previous_station_name)
            normalized_current_station = self._normalize_name(current_station_name)
            if normalized_previous_station and normalized_current_station:
                window_station_changed = normalized_previous_station != normalized_current_station
            inventory_changed = bool(previous_signature) and previous_signature != inventory_signature
            if not previous_signature:
                change_reason = "inventory_initialized"
            elif window_station_changed:
                change_reason = "window_station_changed"
            elif inventory_changed:
                change_reason = "desktop_inventory_changed"
            self._last_inventory_signature = inventory_signature
            self._last_inventory_trace = inventory_trace
            self._last_window_station_name = current_station_name

        invalidated_names: list[str] = []
        if window_station_changed:
            invalidated_names = self._invalidate_cached_desktop_handles(
                reason="window_station_changed",
            )
        elif inventory_changed:
            invalidated_names = self._invalidate_cached_desktop_handles_not_in_inventory(
                inventory,
                reason="desktop_inventory_changed",
            )

        return {
            "desktop_inventory_signature": inventory_signature,
            "desktop_inventory_previous_signature": previous_signature,
            "desktop_inventory_previous_trace": previous_trace,
            "desktop_inventory_changed": inventory_changed,
            "desktop_inventory_change_reason": change_reason,
            "window_station_name": current_station_name,
            "window_station_previous_name": previous_station_name,
            "window_station_changed": window_station_changed,
            "desktop_inventory_invalidated_cache_names": ",".join(invalidated_names),
            "desktop_inventory_invalidated_cache_count": len(invalidated_names),
        }

    def _desktop_name_aliases(self, desktop_name: str) -> list[str]:
        normalized_name = self._normalize_name(desktop_name)
        if not normalized_name:
            return []

        alias_candidates = [normalized_name]
        if "\\" in normalized_name:
            alias_candidates.append(normalized_name.rsplit("\\", 1)[-1])
        if normalized_name in {"screensaver", "screen saver"}:
            alias_candidates.append("screen-saver")
        if normalized_name == "screen-saver":
            alias_candidates.extend(["screensaver", "screen saver"])
        if normalized_name == "secure":
            alias_candidates.append("winlogon")
        if normalized_name == "disconnected":
            alias_candidates.append("disconnect")

        ordered_aliases: list[str] = []
        seen_aliases: set[str] = set()
        for alias in alias_candidates:
            normalized_alias = self._normalize_name(alias)
            if not normalized_alias or normalized_alias in seen_aliases:
                continue
            seen_aliases.add(normalized_alias)
            ordered_aliases.append(normalized_alias)
        return ordered_aliases

    def _resolve_window_station_desktop_name(
        self,
        desktop_name: str,
        *,
        desktop_inventory: list[str] | None = None,
    ) -> str:
        requested_name = str(desktop_name or "").strip()
        if not requested_name:
            return ""

        inventory = self._normalize_desktop_inventory(desktop_inventory or self._list_window_station_desktops())
        if not inventory:
            if "\\" in requested_name:
                return requested_name.rsplit("\\", 1)[-1]
            return requested_name

        inventory_lookup = {
            self._normalize_name(name): name
            for name in inventory
        }
        for alias in self._desktop_name_aliases(requested_name):
            direct_match = inventory_lookup.get(alias)
            if direct_match:
                return direct_match

        for alias in self._desktop_name_aliases(requested_name):
            suffix = f"\\{alias}"
            for inventory_name in inventory:
                normalized_inventory_name = self._normalize_name(inventory_name)
                if normalized_inventory_name.endswith(suffix):
                    return inventory_name

        fallback_name = requested_name.rsplit("\\", 1)[-1] if "\\" in requested_name else requested_name
        fallback_lookup = inventory_lookup.get(self._normalize_name(fallback_name))
        if fallback_lookup:
            return fallback_lookup
        return ""

    def _infer_input_desktop_name(
        self,
        *,
        input_desktop_name: str,
        thread_desktop_name: str,
        thread_id: int,
        desktop_inventory: list[str] | None = None,
    ) -> tuple[str, str, bool]:
        requested_input = str(input_desktop_name or "").strip()
        normalized_input = self._normalize_name(requested_input)
        inventory = self._normalize_desktop_inventory(desktop_inventory or self._list_window_station_desktops())
        if normalized_input and normalized_input not in {"unknown", "unavailable"}:
            resolved_input = self._resolve_window_station_desktop_name(
                requested_input,
                desktop_inventory=inventory,
            )
            return str(resolved_input or requested_input), "open_input_desktop", False

        remembered_desktop = self._get_remembered_thread_binding(thread_id)
        candidate_sources = [
            (thread_desktop_name, "thread_desktop_inferred"),
            (remembered_desktop, "remembered_thread_binding_inferred"),
        ]
        if len(inventory) == 1:
            candidate_sources.append((inventory[0], "inventory_singleton_inferred"))

        for candidate_name, source in candidate_sources:
            resolved_name = self._resolve_window_station_desktop_name(
                str(candidate_name or "").strip(),
                desktop_inventory=inventory,
            )
            if resolved_name:
                return resolved_name, source, True

        return requested_input or "unavailable", "unavailable", False

    def _select_rebindable_input_desktop_name(
        self,
        *,
        thread_desktop_name: str,
        thread_id: int,
        desktop_inventory: list[str] | None = None,
    ) -> tuple[str, str, bool]:
        inventory = self._normalize_desktop_inventory(desktop_inventory or self._list_window_station_desktops())
        remembered_desktop = self._resolve_window_station_desktop_name(
            self._get_remembered_thread_binding(thread_id),
            desktop_inventory=inventory,
        )
        if remembered_desktop:
            return remembered_desktop, "remembered_thread_binding_inferred", True

        return self._infer_input_desktop_name(
            input_desktop_name="unavailable",
            thread_desktop_name=thread_desktop_name,
            thread_id=thread_id,
            desktop_inventory=inventory,
        )

    def _desktop_name_in_inventory(self, desktop_name: str, desktop_inventory: list[str] | None) -> bool:
        if not desktop_inventory:
            return False
        resolved_name = self._resolve_window_station_desktop_name(
            desktop_name,
            desktop_inventory=desktop_inventory,
        )
        normalized_name = self._normalize_name(resolved_name or desktop_name)
        return normalized_name in {
            self._normalize_name(name)
            for name in self._normalize_desktop_inventory(desktop_inventory)
        }

    def _invalidate_cached_desktop_handle(self, desktop_name: str, *, reason: str = "") -> None:
        normalized_name = self._normalize_name(desktop_name)
        if not normalized_name:
            return
        with self._desktop_handle_lock:
            cached_entry = self._managed_desktop_handles.pop(normalized_name, None)
        cached_handle = self._extract_cached_desktop_handle(cached_entry)
        if cached_handle:
            with contextlib.suppress(Exception):
                self.user32.CloseDesktop(cached_handle)
            print(
                f"[DesktopContext][{self.component}] invalidate cached desktop handle: "
                f"desktop={desktop_name or 'unknown'} reason={reason or 'unknown'}"
            )

    def _invalidate_cached_desktop_handles(self, *, reason: str = "") -> list[str]:
        with self._desktop_handle_lock:
            cached_handles = list(self._managed_desktop_handles.items())
            self._managed_desktop_handles.clear()
        invalidated_names: list[str] = []
        for normalized_name, cached_entry in cached_handles:
            cached_handle = self._extract_cached_desktop_handle(cached_entry)
            invalidated_names.append(normalized_name)
            if cached_handle:
                with contextlib.suppress(Exception):
                    self.user32.CloseDesktop(cached_handle)
        if invalidated_names:
            print(
                f"[DesktopContext][{self.component}] invalidate cached desktop handles: "
                f"count={len(invalidated_names)} reason={reason or 'unknown'} names={','.join(invalidated_names)}"
            )
        return invalidated_names

    def _invalidate_cached_desktop_handles_not_in_inventory(
        self,
        desktop_inventory: list[str],
        *,
        reason: str = "",
    ) -> list[str]:
        inventory_lookup = {
            self._normalize_name(name)
            for name in self._normalize_desktop_inventory(desktop_inventory)
        }
        with self._desktop_handle_lock:
            stale_names = [
                name
                for name in list(self._managed_desktop_handles.keys())
                if name not in inventory_lookup
            ]
        for stale_name in stale_names:
            self._invalidate_cached_desktop_handle(stale_name, reason=reason)
        return stale_names

    def _is_desktop_handle_valid(self, desktop_handle, *, expected_name: str = "") -> bool:
        if not desktop_handle:
            return False
        observed_name = self._get_desktop_name(desktop_handle)
        if not observed_name:
            return False
        if expected_name and self._normalize_name(observed_name) != self._normalize_name(expected_name):
            return False
        return True

    def _remember_thread_binding(self, thread_id: int, desktop_name: str) -> None:
        normalized_name = self._normalize_name(desktop_name)
        if not normalized_name:
            return
        with self._desktop_handle_lock:
            self._thread_bound_desktop_names[int(thread_id)] = normalized_name

    def _get_remembered_thread_binding(self, thread_id: int) -> str:
        with self._desktop_handle_lock:
            return str(self._thread_bound_desktop_names.get(int(thread_id)) or "")

    def _get_cached_desktop_handle_snapshot(self) -> list[str]:
        with self._desktop_handle_lock:
            return sorted(self._managed_desktop_handles.keys())

    def _build_desktop_context_transition_signature(self, state: dict[str, str | bool | int]) -> str:
        return "|".join(
            [
                f"winsta={self._normalize_name(str(state.get('process_winsta') or 'unknown'))}",
                f"inventory={self._normalize_name(str(state.get('desktop_inventory_signature') or 'empty'))}",
                f"thread={self._normalize_name(str(state.get('thread_desktop') or 'unknown'))}",
                f"input={self._normalize_name(str(state.get('input_desktop') or 'unknown'))}",
                f"capture={self._normalize_name(str(state.get('capture_desktop') or 'unknown'))}",
                f"mode={self._normalize_name(str(state.get('binding_mode') or 'input'))}",
                f"target={self._normalize_name(str(state.get('capture_target_signature') or state.get('input_target_signature') or 'unknown'))}",
            ]
        )

    def _format_handle(self, handle) -> str:
        return hex(self._get_handle_value(handle))

    def _get_handle_value(self, handle) -> int:
        try:
            return int(ctypes.cast(handle, ctypes.c_void_p).value or 0)
        except Exception:
            return 0
