from __future__ import annotations

import contextlib
import ctypes
import json
import os
import subprocess
import threading
import time
from dataclasses import dataclass
from ctypes import wintypes
from typing import Callable

from Capture.display_presence import DisplayPresenceAssessment, DisplayPresenceProbe
from Common.models import SessionDescriptor
from desktop_context import InputDesktopController
from RemoteService.display_substrate_manager import DisplaySubstrateManager


class _WTSSessionInfo(ctypes.Structure):
    _fields_ = [
        ("SessionId", wintypes.DWORD),
        ("pWinStationName", wintypes.LPWSTR),
        ("State", wintypes.DWORD),
    ]


WTS_CURRENT_SERVER_HANDLE = wintypes.HANDLE(0)
INVALID_SESSION_ID = 0xFFFFFFFF
WTS_ACTIVE = 0
WTS_CONNECTED = 1
WTS_CONNECTQUERY = 2
WTS_SHADOW = 3
WTS_DISCONNECTED = 4
WTS_IDLE = 5
WTS_LISTEN = 6
WTS_RESET = 7
WTS_DOWN = 8
WTS_INIT = 9
WTS_USERNAME = 5
WTS_DOMAIN_NAME = 7
_WTS_STATE_NAMES = {
    WTS_ACTIVE: "Active",
    WTS_CONNECTED: "Connected",
    WTS_CONNECTQUERY: "ConnectQuery",
    WTS_SHADOW: "Shadow",
    WTS_DISCONNECTED: "Disconnected",
    WTS_IDLE: "Idle",
    WTS_LISTEN: "Listen",
    WTS_RESET: "Reset",
    WTS_DOWN: "Down",
    WTS_INIT: "Init",
}

_NON_PERSISTENT_CAPTURE_SUBSTRATES = {
    "remote_session_surface",
    "disconnected_surface",
    "console_headless_surface",
    "unknown_best_effort",
    "display_surface_unavailable",
}

_NON_PERSISTENT_CAPTURE_BLOCKERS = {
    "non_persistent_capture_surface",
    "disconnected_capture_surface",
    "best_effort_capture_surface",
    "display_surface_unavailable",
    "remote_display_surface_without_persistent_substrate",
    "best_effort_capture_context",
}


@dataclass(slots=True)
class LegacySessionBridge:
    get_interactive_session_ids: Callable[[], list[int]]
    get_user_session_debug_label: Callable[[int], str]
    launch_user_session_agent_for_session: Callable[[int], bool]
    launch_high_integrity_helper_for_session: Callable[[int], dict]
    read_role_runtime_state: Callable[..., dict | None]
    has_recent_role_runtime_state: Callable[..., bool]
    is_role_mutex_active: Callable[..., bool]
    list_user_session_agent_pids: Callable[[int], list[int]]
    list_high_integrity_helper_pids: Callable[[int], list[int]]
    cleanup_duplicate_user_session_agent_processes: Callable[[int, int | None], list[int]]
    terminate_process: Callable[[int], bool]
    log_runtime_event: Callable[[str, str], None]


@dataclass(slots=True)
class _SessionRuntimeSnapshot:
    session_id: int
    label: str
    identity: str
    station_name: str
    state: str
    is_remote_session: bool
    is_active: bool
    is_connected: bool
    is_disconnected: bool
    is_console_session: bool

    def to_descriptor(self) -> SessionDescriptor:
        return SessionDescriptor(
            session_id=int(self.session_id),
            label=self.label,
            identity=self.identity,
            is_console_preferred=bool(self.is_console_session),
            station_name=self.station_name,
            state=self.state,
            is_remote_session=bool(self.is_remote_session),
            is_active=bool(self.is_active),
            is_connected=bool(self.is_connected),
            is_disconnected=bool(self.is_disconnected),
            is_console_session=bool(self.is_console_session),
        )


class SessionManager:
    def __init__(
        self,
        bridge: LegacySessionBridge,
        retry_seconds: int = 15,
        launch_cooldown_seconds: int = 20,
    ):
        self.bridge = bridge
        self.retry_seconds = retry_seconds
        self.launch_cooldown_seconds = launch_cooldown_seconds
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._desktop_controller = InputDesktopController("ServiceRuntime")
        self._display_presence_probe = DisplayPresenceProbe()
        self._display_substrate_manager = DisplaySubstrateManager(
            self._display_presence_probe,
            logger=self._log_virtual_display_event,
        )
        self._state_lock = threading.Lock()
        self._active_capture_session_id: int | None = None
        self._active_capture_backend = ""
        self._active_capture_desktop_signature = ""
        self._active_capture_desktop_kind = ""
        self._active_capture_host_descriptor_signature = ""
        self._capture_binding_generation = 0
        self._last_console_handoff_at = 0.0
        self._last_console_handoff_signature = ""
        self._last_console_handoff_result: dict[str, object] = {}
        self._last_persistent_substrate_recovery_at = 0.0
        self._last_persistent_substrate_recovery_signature = ""

    def _log_virtual_display_event(self, message: str) -> None:
        text = str(message or "").strip()
        if text:
            self.bridge.log_runtime_event("VirtualDisplay", text)

    def list_interactive_sessions(self) -> list[SessionDescriptor]:
        snapshots = self._list_session_runtime_snapshots()
        if snapshots:
            ordered_snapshots = sorted(snapshots, key=self._session_priority_key)
            return [item.to_descriptor() for item in ordered_snapshots]

        session_ids = self.bridge.get_interactive_session_ids()
        active_console_session_id = self._get_active_console_session_id()
        descriptors = [
            self._build_session_descriptor(
                session_id,
                console_preferred=(active_console_session_id is not None and session_id == active_console_session_id),
            )
            for session_id in session_ids
        ]
        return sorted(descriptors, key=self._descriptor_priority_key)

    def get_primary_remote_host_session(self) -> SessionDescriptor | None:
        descriptors = self.list_interactive_sessions()
        if not descriptors:
            return None
        return min(descriptors, key=self._descriptor_priority_key)

    def get_preferred_capture_host_session(self) -> SessionDescriptor | None:
        descriptors = self.list_interactive_sessions()
        return self._select_preferred_capture_descriptor(descriptors)

    def get_input_helper_host_session(self) -> SessionDescriptor | None:
        return self._select_input_helper_target_descriptor()

    def get_capture_helper_host_session(self) -> SessionDescriptor | None:
        return self._select_capture_helper_target_descriptor()

    def collect_runtime_status(self) -> list[dict]:
        primary_descriptor = self.get_primary_remote_host_session()
        primary_session_id = primary_descriptor.session_id if primary_descriptor is not None else None
        preferred_capture_descriptor = self.get_preferred_capture_host_session()
        preferred_capture_session_id = (
            preferred_capture_descriptor.session_id if preferred_capture_descriptor is not None else None
        )
        input_helper_descriptor = self.get_input_helper_host_session()
        input_helper_session_id = input_helper_descriptor.session_id if input_helper_descriptor is not None else None
        capture_helper_descriptor = self.get_capture_helper_host_session()
        capture_helper_session_id = (
            capture_helper_descriptor.session_id if capture_helper_descriptor is not None else None
        )
        desired_helper_session_ids = {
            int(session_id)
            for session_id in (input_helper_session_id, capture_helper_session_id)
            if session_id is not None
        }
        status_rows: list[dict] = []
        for descriptor in self.list_interactive_sessions():
            heartbeat_payload = self.bridge.read_role_runtime_state(
                "user-session-agent",
                session_bound=True,
                session_id=descriptor.session_id,
            ) or {}
            heartbeat_pid = int(heartbeat_payload.get("pid") or 0)
            session_pids = self.bridge.list_user_session_agent_pids(descriptor.session_id)
            helper_pids = self.bridge.list_high_integrity_helper_pids(descriptor.session_id)
            status_rows.append(
                {
                    "session_id": descriptor.session_id,
                    "label": descriptor.label,
                    "identity": descriptor.identity,
                    "station_name": descriptor.station_name,
                    "state": descriptor.state,
                    "is_primary_remote_host": descriptor.session_id == primary_session_id,
                    "is_preferred_capture_host": descriptor.session_id == preferred_capture_session_id,
                    "is_input_helper_host": descriptor.session_id == input_helper_session_id,
                    "is_capture_helper_host": descriptor.session_id == capture_helper_session_id,
                    "is_desired_helper_host": descriptor.session_id in desired_helper_session_ids,
                    "is_console_preferred": bool(descriptor.is_console_preferred),
                    "is_console_session": bool(descriptor.is_console_session),
                    "is_remote_session": bool(descriptor.is_remote_session),
                    "is_active": bool(descriptor.is_active),
                    "is_connected": bool(descriptor.is_connected),
                    "is_disconnected": bool(descriptor.is_disconnected),
                    "mutex_active": bool(
                        self.bridge.is_role_mutex_active(
                            "user-session-agent",
                            session_bound=True,
                            session_id=descriptor.session_id,
                        )
                    ),
                    "heartbeat_active": bool(
                        self.bridge.has_recent_role_runtime_state(
                            "user-session-agent",
                            session_bound=True,
                            session_id=descriptor.session_id,
                        )
                    ),
                    "heartbeat_pid": heartbeat_pid,
                    "session_pids": session_pids,
                    "helper_mutex_active": bool(
                        self.bridge.is_role_mutex_active(
                            "high-integrity-helper",
                            session_bound=True,
                            session_id=descriptor.session_id,
                        )
                    ),
                    "helper_heartbeat_active": bool(
                        self.bridge.has_recent_role_runtime_state(
                            "high-integrity-helper",
                            session_bound=True,
                            session_id=descriptor.session_id,
                        )
                    ),
                    "helper_heartbeat_pid": int(
                        (
                            self.bridge.read_role_runtime_state(
                                "high-integrity-helper",
                                session_bound=True,
                                session_id=descriptor.session_id,
                            )
                            or {}
                        ).get("pid")
                        or 0
                    ),
                    "helper_pids": helper_pids,
                }
            )
        return status_rows

    def get_console_session(self) -> SessionDescriptor | None:
        descriptors = self.list_interactive_sessions()
        for descriptor in descriptors:
            if descriptor.is_console_session:
                return descriptor
        raw_console_session_id = self._get_active_console_session_id()
        if raw_console_session_id is None:
            return None
        return self._build_session_descriptor(raw_console_session_id, console_preferred=True)

    def describe_session_topology(self) -> dict:
        primary_descriptor = self.get_primary_remote_host_session()
        console_descriptor = self.get_console_session()
        capture_descriptor = self._get_active_capture_descriptor()
        preferred_capture_descriptor = self.get_preferred_capture_host_session()
        input_helper_descriptor = self.get_input_helper_host_session()
        capture_helper_descriptor = self.get_capture_helper_host_session()
        interactive_sessions = self.list_interactive_sessions()
        capture_topology = self._build_capture_topology_diagnostics(
            interactive_sessions,
            primary_descriptor=primary_descriptor,
            preferred_capture_descriptor=preferred_capture_descriptor,
        )
        capture_continuity = self._build_capture_continuity_policy(
            interactive_sessions,
            primary_descriptor=primary_descriptor,
            preferred_capture_descriptor=preferred_capture_descriptor,
            active_capture_descriptor=capture_descriptor,
            input_helper_descriptor=input_helper_descriptor,
            capture_helper_descriptor=capture_helper_descriptor,
        )
        display_substrate = self._build_display_substrate_diagnostics(
            preferred_capture_descriptor=preferred_capture_descriptor,
            active_capture_descriptor=capture_descriptor,
        )
        remote_desktop_readiness = self._build_remote_desktop_readiness(
            interactive_sessions,
            primary_descriptor=primary_descriptor,
            console_descriptor=console_descriptor,
            preferred_capture_descriptor=preferred_capture_descriptor,
            active_capture_descriptor=capture_descriptor,
            input_helper_descriptor=input_helper_descriptor,
            capture_helper_descriptor=capture_helper_descriptor,
            capture_topology=capture_topology,
            capture_continuity=capture_continuity,
            display_substrate=display_substrate,
        )
        desired_helper_sessions = []
        seen_helper_sessions: set[int] = set()
        for descriptor in (input_helper_descriptor, capture_helper_descriptor):
            if descriptor is None or descriptor.session_id in seen_helper_sessions:
                continue
            seen_helper_sessions.add(descriptor.session_id)
            desired_helper_sessions.append(descriptor.to_dict())
        return {
            "primary_remote_host_session": (
                primary_descriptor.to_dict() if primary_descriptor is not None else None
            ),
            "console_session": console_descriptor.to_dict() if console_descriptor is not None else None,
            "preferred_capture_host_session": (
                preferred_capture_descriptor.to_dict() if preferred_capture_descriptor is not None else None
            ),
            "active_capture_session": (
                capture_descriptor.to_dict() if capture_descriptor is not None else None
            ),
            "input_helper_host_session": (
                input_helper_descriptor.to_dict() if input_helper_descriptor is not None else None
            ),
            "capture_helper_host_session": (
                capture_helper_descriptor.to_dict() if capture_helper_descriptor is not None else None
            ),
            "desired_helper_sessions": desired_helper_sessions,
            "capture_host_diverges_from_primary_remote_host": bool(
                primary_descriptor is not None
                and preferred_capture_descriptor is not None
                and primary_descriptor.session_id != preferred_capture_descriptor.session_id
            ),
            "helper_hosts_diverge": bool(
                input_helper_descriptor is not None
                and capture_helper_descriptor is not None
                and input_helper_descriptor.session_id != capture_helper_descriptor.session_id
            ),
            "capture_topology": capture_topology,
            "capture_continuity": capture_continuity,
            "display_substrate": display_substrate,
            "remote_desktop_readiness": remote_desktop_readiness,
            "topology_fingerprint": self._session_topology_fingerprint(interactive_sessions),
            "active_capture_host_descriptor_signature": self._get_active_capture_host_descriptor_signature(),
            "interactive_sessions": [item.to_dict() for item in interactive_sessions],
            "runtime_status": self.collect_runtime_status(),
        }

    def get_remote_desktop_readiness(self) -> dict:
        return self.describe_session_topology().get("remote_desktop_readiness") or {}

    def get_capability_notes(self) -> list[str]:
        diagnostics = self._build_capture_topology_diagnostics()
        continuity = self._build_capture_continuity_policy()
        display_substrate = self._build_display_substrate_diagnostics()
        readiness = self._build_remote_desktop_readiness(
            capture_topology=diagnostics,
            capture_continuity=continuity,
            display_substrate=display_substrate,
        )
        notes = [
            "capture_host_selection_service_managed",
            f"capture_host_persistence_class={diagnostics['preferred_capture_persistence_class']}",
            f"capture_host_substrate_class={diagnostics['preferred_capture_substrate_class']}",
            f"capture_host_strategy={diagnostics['capture_strategy']}",
            f"capture_continuity_mode={continuity['continuity_mode']}",
            f"display_substrate_state={display_substrate['provider_state']}",
            f"continuity_grade={readiness.get('continuity_grade') or 'best_effort_rdp_only'}",
        ]
        if diagnostics["has_persistent_capture_substrate"]:
            notes.append("persistent_capture_substrate_detected")
        else:
            notes.append("persistent_capture_substrate_not_detected")
        if continuity["best_effort_only"]:
            notes.append("capture_continuity_best_effort_only")
        else:
            notes.append("capture_continuity_persistent_host_available")
        if diagnostics["capture_prefers_primary_identity_console"]:
            notes.append("capture_host_prefers_primary_identity_console")
        if diagnostics["capture_host_on_transient_remote_surface"]:
            notes.append("capture_host_currently_uses_transient_remote_surface")
        if diagnostics.get("capture_host_on_non_persistent_surface"):
            notes.append("capture_host_currently_uses_non_persistent_surface")
        if self._get_tscon_path():
            notes.append("service_can_handoff_rdp_session_to_console")
            notes.append("capture_continuity_uses_official_session_handoff_when_possible")
        else:
            notes.append("service_console_handoff_binary_not_found")
        if diagnostics.get("requires_virtual_display_for_full_continuity"):
            notes.append("virtual_display_required_for_full_continuity")
        if display_substrate.get("continuity_blocked_by_missing_substrate"):
            notes.append("continuity_blocked_by_missing_persistent_surface")
        if display_substrate.get("can_provision_virtual_display"):
            notes.append("virtual_display_provisioning_supported")
        else:
            notes.append(
                "virtual_display_provisioning_state="
                + str(
                    display_substrate.get("virtual_display_provisioning_state")
                    or "not_supported_in_current_build"
                )
            )
        if diagnostics["console_session_available"]:
            notes.append("console_session_available")
        else:
            notes.append("console_session_unavailable")
        for note in continuity.get("notes") or []:
            if note not in notes:
                notes.append(str(note))
        for note in diagnostics.get("notes") or []:
            if note not in notes:
                notes.append(str(note))
        for blocker in readiness.get("continuity_blockers") or []:
            blocker_note = f"continuity_blocker={blocker}"
            if blocker_note not in notes:
                notes.append(blocker_note)
        return notes

    def _build_remote_desktop_readiness(
        self,
        descriptors: list[SessionDescriptor] | None = None,
        *,
        primary_descriptor: SessionDescriptor | None = None,
        console_descriptor: SessionDescriptor | None = None,
        preferred_capture_descriptor: SessionDescriptor | None = None,
        active_capture_descriptor: SessionDescriptor | None = None,
        input_helper_descriptor: SessionDescriptor | None = None,
        capture_helper_descriptor: SessionDescriptor | None = None,
        capture_topology: dict | None = None,
        capture_continuity: dict | None = None,
        display_substrate: dict | None = None,
    ) -> dict:
        interactive_descriptors = descriptors if descriptors is not None else self.list_interactive_sessions()
        primary = primary_descriptor or self._find_best_primary_remote_descriptor(interactive_descriptors)
        console = console_descriptor or self.get_console_session()
        preferred_capture = preferred_capture_descriptor or self._select_preferred_capture_descriptor(
            interactive_descriptors
        )
        active_capture = active_capture_descriptor or self._get_active_capture_descriptor()
        input_helper = input_helper_descriptor or self._select_input_helper_target_descriptor()
        capture_helper = capture_helper_descriptor or self._select_capture_helper_target_descriptor()
        topology = capture_topology or self._build_capture_topology_diagnostics(
            interactive_descriptors,
            primary_descriptor=primary,
            preferred_capture_descriptor=preferred_capture,
        )
        continuity = capture_continuity or self._build_capture_continuity_policy(
            interactive_descriptors,
            primary_descriptor=primary,
            preferred_capture_descriptor=preferred_capture,
            active_capture_descriptor=active_capture,
            input_helper_descriptor=input_helper,
            capture_helper_descriptor=capture_helper,
        )
        substrate = display_substrate or self._build_display_substrate_diagnostics(
            preferred_capture_descriptor=preferred_capture,
            active_capture_descriptor=active_capture,
        )

        blockers: list[str] = []
        requirements: list[str] = []
        notes: list[str] = []

        def add_unique(target: list[str], value: str) -> None:
            text = str(value or "").strip()
            if text and text not in target:
                target.append(text)

        if not interactive_descriptors:
            add_unique(blockers, "no_interactive_session")
            add_unique(requirements, "ensure_logged_in_user_or_persistent_unattended_desktop")

        if preferred_capture is None:
            add_unique(blockers, "no_preferred_capture_host")
            add_unique(requirements, "establish_interactive_capture_host")

        if bool(topology.get("capture_host_on_transient_remote_surface")):
            add_unique(blockers, "capture_host_currently_transient_remote_surface")
            add_unique(requirements, "migrate_capture_host_to_console_or_persistent_surface")
        if bool(topology.get("capture_host_on_non_persistent_surface")):
            add_unique(blockers, "capture_host_currently_non_persistent_surface")
            add_unique(requirements, "provide_persistent_display_substrate")

        if (
            not bool(topology.get("console_session_available"))
            and not bool(substrate.get("persistent_available"))
        ):
            add_unique(blockers, "no_console_or_persistent_capture_host")
            add_unique(requirements, "attach_physical_display_or_install_virtual_display")

        if bool(substrate.get("continuity_blocked_by_missing_substrate")):
            add_unique(blockers, "missing_persistent_display_substrate")
            add_unique(requirements, "attach_physical_display_or_install_virtual_display")

        provisioning_state = str(
            substrate.get("virtual_display_provisioning_state")
            or "not_supported_in_current_build"
        ).strip().lower()
        if provisioning_state in {
            "driver_package_missing",
            "driver_package_present_missing_inf",
            "driver_package_incomplete",
        }:
            add_unique(blockers, "virtual_display_driver_payload_missing")
            add_unique(requirements, "package_real_windows_supported_virtual_display_driver")
        elif provisioning_state == "driver_package_ready_install_pending":
            add_unique(blockers, "virtual_display_driver_not_installed")
            add_unique(requirements, "install_and_attach_virtual_display")
        elif provisioning_state == "installed_detached":
            add_unique(blockers, "virtual_display_present_but_not_attached")
            add_unique(requirements, "repair_or_attach_virtual_display")
        elif provisioning_state == "installed_missing_enablement":
            add_unique(blockers, "virtual_display_present_but_disabled")
            add_unique(requirements, "repair_or_enable_virtual_display")

        if bool(continuity.get("force_capture_host_migration")):
            add_unique(blockers, "capture_authority_migration_pending")
            add_unique(requirements, "allow_service_to_rebuild_capture_helper")

        if (
            preferred_capture is not None
            and bool(preferred_capture.is_remote_session)
            and not bool(substrate.get("persistent_available"))
        ):
            add_unique(blockers, "rdp_surface_is_only_available_capture_target")
            add_unique(requirements, "provide_non_rdp_persistent_display_substrate")

        if input_helper is None or capture_helper is None:
            add_unique(notes, "service_managed_helper_host_not_fully_ready")
        if input_helper is not None and capture_helper is not None:
            if input_helper.session_id == capture_helper.session_id:
                add_unique(notes, "input_and_capture_helpers_aligned")
            else:
                add_unique(notes, "input_and_capture_helpers_diverged")
        if console is not None and preferred_capture is not None:
            if console.session_id == preferred_capture.session_id:
                add_unique(notes, "preferred_capture_host_is_console_session")
            else:
                add_unique(notes, "preferred_capture_host_not_console_session")
        if substrate.get("physical_display_attached"):
            add_unique(notes, "physical_display_attached")
        if substrate.get("virtual_display_attached"):
            add_unique(notes, "virtual_display_attached")
        if substrate.get("remote_adapter_present"):
            add_unique(notes, "remote_display_adapter_present")
        for note in continuity.get("notes") or []:
            add_unique(notes, str(note))
        for note in topology.get("notes") or []:
            add_unique(notes, str(note))
        for note in substrate.get("notes") or []:
            add_unique(notes, str(note))

        if (
            not blockers
            and bool(substrate.get("persistent_ready_for_unattended"))
            and not bool(continuity.get("best_effort_only"))
            and not bool(continuity.get("preferred_capture_host_transient"))
            and not bool(continuity.get("active_capture_host_transient"))
        ):
            continuity_grade = "commercial_ready"
        elif bool(substrate.get("continuity_blocked_by_missing_substrate")):
            continuity_grade = "blocked_missing_substrate"
        elif bool(substrate.get("persistent_available")):
            continuity_grade = "persistent_but_limited"
        else:
            continuity_grade = "best_effort_rdp_only"

        return {
            "commercial_continuity_ready": continuity_grade == "commercial_ready",
            "continuity_grade": continuity_grade,
            "continuity_blockers": blockers,
            "continuity_requirements": requirements,
            "required_persistent_substrate": "physical_display_or_signed_virtual_display_idd",
            "target_matrix_verified": False,
            "target_matrix_unverified": [
                "mstsc_minimized",
                "rdp_disconnected",
                "locked_screen",
                "session_switch",
                "console_rdp_switch",
            ],
            "commercial_continuity_blocker": (
                blockers[0] if blockers else "target_matrix_not_verified"
            ),
            "preferred_capture_host_session_id": (
                int(preferred_capture.session_id) if preferred_capture is not None else None
            ),
            "active_capture_host_session_id": (
                int(active_capture.session_id) if active_capture is not None else None
            ),
            "capture_helper_host_session_id": (
                int(capture_helper.session_id) if capture_helper is not None else None
            ),
            "input_helper_host_session_id": (
                int(input_helper.session_id) if input_helper is not None else None
            ),
            "console_session_id": int(console.session_id) if console is not None else None,
            "primary_remote_host_session_id": int(primary.session_id) if primary is not None else None,
            "persistent_capture_substrate_detected": bool(
                continuity.get("persistent_capture_substrate_detected", False)
            ),
            "continuity_blocked_by_missing_substrate": bool(
                substrate.get("continuity_blocked_by_missing_substrate", False)
            ),
            "requires_virtual_display_for_full_continuity": bool(
                continuity.get("requires_virtual_display_for_full_continuity", True)
            ),
            "virtual_display_provisioning_state": provisioning_state or "not_supported_in_current_build",
            "display_substrate": dict(substrate),
            "capture_continuity": dict(continuity),
            "capture_topology": dict(topology),
            "notes": notes,
        }

    def get_virtual_display_status(self, *, force_refresh: bool = False) -> dict:
        return self._display_substrate_manager.get_virtual_display_status(force_refresh=force_refresh)

    def ensure_virtual_display(self) -> dict:
        return self._display_substrate_manager.ensure_virtual_display()

    def repair_virtual_display(self) -> dict:
        return self._display_substrate_manager.repair_virtual_display()

    def launch_user_session_agent(self, session_id: int) -> bool:
        return bool(self.bridge.launch_user_session_agent_for_session(int(session_id)))

    def ensure_user_session_agent(self, session_id: int | None = None) -> dict:
        descriptor = self._resolve_target_descriptor(session_id)
        if descriptor is None:
            raise RuntimeError("no interactive session available")

        runtime_rows = {
            int(row["session_id"]): row
            for row in self.collect_runtime_status()
        }
        runtime_row = runtime_rows.get(descriptor.session_id, {})
        already_active = bool(
            runtime_row.get("mutex_active")
            or runtime_row.get("heartbeat_active")
            or runtime_row.get("session_pids")
        )
        started = False
        if not already_active:
            started = self.launch_user_session_agent(descriptor.session_id)

        return {
            "session": descriptor.to_dict(),
            "already_active": already_active,
            "started": started,
            "runtime_status": runtime_row,
        }

    def restart_user_session_agent(self, session_id: int | None = None, wait_seconds: float = 8.0) -> dict:
        descriptor = self._resolve_target_descriptor(session_id)
        if descriptor is None:
            raise RuntimeError("no interactive session available")

        terminated_pids: list[int] = []
        for pid in self.bridge.list_user_session_agent_pids(descriptor.session_id):
            if self.bridge.terminate_process(int(pid)):
                terminated_pids.append(int(pid))

        deadline = time.time() + max(2.0, float(wait_seconds or 2.0))
        while time.time() < deadline:
            session_pids = self.bridge.list_user_session_agent_pids(descriptor.session_id)
            mutex_active = self.bridge.is_role_mutex_active(
                "user-session-agent",
                session_bound=True,
                session_id=descriptor.session_id,
            )
            if not session_pids and not mutex_active:
                break
            time.sleep(0.5)

        started = self.launch_user_session_agent(descriptor.session_id)
        return {
            "session": descriptor.to_dict(),
            "terminated_pids": terminated_pids,
            "started": started,
            "runtime_status": self._find_runtime_status(descriptor.session_id),
        }

    def ensure_high_integrity_helper(self, session_id: int | None = None) -> dict:
        descriptor = self._resolve_input_helper_target_descriptor(session_id)
        if descriptor is None:
            raise RuntimeError("no interactive session available")

        runtime_rows = {
            int(row["session_id"]): row
            for row in self.collect_runtime_status()
        }
        runtime_row = runtime_rows.get(descriptor.session_id, {})
        already_active = bool(
            runtime_row.get("helper_mutex_active")
            or runtime_row.get("helper_heartbeat_active")
            or runtime_row.get("helper_pids")
        )
        started = False
        launch_result: dict = {}
        if not already_active:
            launch_result = self.bridge.launch_high_integrity_helper_for_session(descriptor.session_id) or {}
            started = bool(launch_result.get("started"))

        return {
            "session": descriptor.to_dict(),
            "already_active": already_active,
            "started": started,
            "launch_result": launch_result,
            "runtime_status": self._find_runtime_status(descriptor.session_id),
        }

    def ensure_capture_helper(self, session_id: int | None = None) -> dict:
        descriptor = self._resolve_capture_helper_target_descriptor(session_id)
        if descriptor is None:
            raise RuntimeError("no interactive session available")

        runtime_rows = {
            int(row["session_id"]): row
            for row in self.collect_runtime_status()
        }
        runtime_row = runtime_rows.get(descriptor.session_id, {})
        already_active = bool(
            runtime_row.get("helper_mutex_active")
            or runtime_row.get("helper_heartbeat_active")
            or runtime_row.get("helper_pids")
        )
        started = False
        launch_result: dict = {}
        if not already_active:
            launch_result = self.bridge.launch_high_integrity_helper_for_session(descriptor.session_id) or {}
            started = bool(launch_result.get("started"))

        return {
            "session": descriptor.to_dict(),
            "already_active": already_active,
            "started": started,
            "launch_result": launch_result,
            "runtime_status": self._find_runtime_status(descriptor.session_id),
        }

    def restart_high_integrity_helper(self, session_id: int | None = None, wait_seconds: float = 5.0) -> dict:
        descriptor = self._resolve_input_helper_target_descriptor(session_id)
        if descriptor is None:
            raise RuntimeError("no interactive session available")

        terminated_pids: list[int] = []
        for pid in self.bridge.list_high_integrity_helper_pids(descriptor.session_id):
            if self.bridge.terminate_process(int(pid)):
                terminated_pids.append(int(pid))

        deadline = time.time() + max(2.0, float(wait_seconds or 2.0))
        while time.time() < deadline:
            helper_pids = self.bridge.list_high_integrity_helper_pids(descriptor.session_id)
            mutex_active = self.bridge.is_role_mutex_active(
                "high-integrity-helper",
                session_bound=True,
                session_id=descriptor.session_id,
            )
            if not helper_pids and not mutex_active:
                break
            time.sleep(0.5)

        launch_result = self.bridge.launch_high_integrity_helper_for_session(descriptor.session_id) or {}
        return {
            "session": descriptor.to_dict(),
            "terminated_pids": terminated_pids,
            "started": bool(launch_result.get("started")),
            "launch_result": launch_result,
            "runtime_status": self._find_runtime_status(descriptor.session_id),
        }

    def restart_capture_helper(self, session_id: int | None = None, wait_seconds: float = 5.0) -> dict:
        descriptor = self._resolve_capture_helper_target_descriptor(session_id)
        if descriptor is None:
            raise RuntimeError("no interactive session available")

        terminated_pids: list[int] = []
        for pid in self.bridge.list_high_integrity_helper_pids(descriptor.session_id):
            if self.bridge.terminate_process(int(pid)):
                terminated_pids.append(int(pid))

        deadline = time.time() + max(2.0, float(wait_seconds or 2.0))
        while time.time() < deadline:
            helper_pids = self.bridge.list_high_integrity_helper_pids(descriptor.session_id)
            mutex_active = self.bridge.is_role_mutex_active(
                "high-integrity-helper",
                session_bound=True,
                session_id=descriptor.session_id,
            )
            if not helper_pids and not mutex_active:
                break
            time.sleep(0.5)

        launch_result = self.bridge.launch_high_integrity_helper_for_session(descriptor.session_id) or {}
        return {
            "session": descriptor.to_dict(),
            "terminated_pids": terminated_pids,
            "started": bool(launch_result.get("started")),
            "launch_result": launch_result,
            "runtime_status": self._find_runtime_status(descriptor.session_id),
        }

    def terminate_high_integrity_helper(self, session_id: int | None = None, wait_seconds: float = 5.0) -> dict:
        descriptor = self._resolve_input_helper_target_descriptor(session_id)
        if descriptor is None:
            raise RuntimeError("no interactive session available")

        terminated_pids: list[int] = []
        for pid in self.bridge.list_high_integrity_helper_pids(descriptor.session_id):
            if self.bridge.terminate_process(int(pid)):
                terminated_pids.append(int(pid))

        deadline = time.time() + max(1.0, float(wait_seconds or 1.0))
        while time.time() < deadline:
            helper_pids = self.bridge.list_high_integrity_helper_pids(descriptor.session_id)
            mutex_active = self.bridge.is_role_mutex_active(
                "high-integrity-helper",
                session_bound=True,
                session_id=descriptor.session_id,
            )
            if not helper_pids and not mutex_active:
                break
            time.sleep(0.25)

        return {
            "session": descriptor.to_dict(),
            "terminated_pids": terminated_pids,
            "runtime_status": self._find_runtime_status(descriptor.session_id),
        }

    def terminate_capture_helper(self, session_id: int | None = None, wait_seconds: float = 5.0) -> dict:
        descriptor = self._resolve_capture_helper_target_descriptor(session_id)
        if descriptor is None:
            raise RuntimeError("no interactive session available")

        terminated_pids: list[int] = []
        for pid in self.bridge.list_high_integrity_helper_pids(descriptor.session_id):
            if self.bridge.terminate_process(int(pid)):
                terminated_pids.append(int(pid))

        deadline = time.time() + max(1.0, float(wait_seconds or 1.0))
        while time.time() < deadline:
            helper_pids = self.bridge.list_high_integrity_helper_pids(descriptor.session_id)
            mutex_active = self.bridge.is_role_mutex_active(
                "high-integrity-helper",
                session_bound=True,
                session_id=descriptor.session_id,
            )
            if not helper_pids and not mutex_active:
                break
            time.sleep(0.25)

        return {
            "session": descriptor.to_dict(),
            "terminated_pids": terminated_pids,
            "runtime_status": self._find_runtime_status(descriptor.session_id),
        }

    def terminate_user_session_agent(self, session_id: int | None = None, wait_seconds: float = 8.0) -> dict:
        descriptor = self._resolve_target_descriptor(session_id)
        if descriptor is None:
            raise RuntimeError("no interactive session available")

        terminated_pids: list[int] = []
        for pid in self.bridge.list_user_session_agent_pids(descriptor.session_id):
            if self.bridge.terminate_process(int(pid)):
                terminated_pids.append(int(pid))

        deadline = time.time() + max(1.0, float(wait_seconds or 1.0))
        while time.time() < deadline:
            session_pids = self.bridge.list_user_session_agent_pids(descriptor.session_id)
            mutex_active = self.bridge.is_role_mutex_active(
                "user-session-agent",
                session_bound=True,
                session_id=descriptor.session_id,
            )
            if not session_pids and not mutex_active:
                break
            time.sleep(0.25)

        return {
            "session": descriptor.to_dict(),
            "terminated_pids": terminated_pids,
            "runtime_status": self._find_runtime_status(descriptor.session_id),
        }

    def invoke_admin_action(self, action: str, payload: dict | None = None) -> dict:
        normalized_action = str(action or "").strip().lower()
        if not normalized_action:
            raise ValueError("missing admin action name")

        action_payload = payload or {}
        if normalized_action == "describe_desktop_context":
            return self._describe_desktop_context(action_payload)
        if normalized_action == "get_virtual_display_status":
            return {
                "accepted": True,
                "action": normalized_action,
                "result": self.get_virtual_display_status(
                    force_refresh=bool(action_payload.get("force_refresh", False))
                ),
            }
        if normalized_action == "ensure_virtual_display":
            return {
                "accepted": True,
                "action": normalized_action,
                "result": self.ensure_virtual_display(),
            }
        if normalized_action == "repair_virtual_display":
            return {
                "accepted": True,
                "action": normalized_action,
                "result": self.repair_virtual_display(),
            }
        if normalized_action == "handoff_session_to_console":
            return {
                "accepted": True,
                "action": normalized_action,
                "result": self.handoff_session_to_console(
                    self._payload_session_id(action_payload),
                    reason=str(action_payload.get("reason") or ""),
                    wait_seconds=float(action_payload.get("wait_seconds") or 4.0),
                ),
            }
        if normalized_action == "get_runtime_status":
            return {
                "accepted": True,
                "action": normalized_action,
                "runtime_status": self.collect_runtime_status(),
            }
        if normalized_action == "ensure_user_session_agent":
            return {
                "accepted": True,
                "action": normalized_action,
                "result": self.ensure_user_session_agent(self._payload_session_id(action_payload)),
            }
        if normalized_action == "ensure_high_integrity_helper":
            return {
                "accepted": True,
                "action": normalized_action,
                "result": self.ensure_high_integrity_helper(self._payload_session_id(action_payload)),
            }
        if normalized_action == "ensure_capture_helper":
            return {
                "accepted": True,
                "action": normalized_action,
                "result": self.ensure_capture_helper(self._payload_session_id(action_payload)),
            }
        if normalized_action == "restart_user_session_agent":
            return {
                "accepted": True,
                "action": normalized_action,
                "result": self.restart_user_session_agent(
                    self._payload_session_id(action_payload),
                    wait_seconds=float(action_payload.get("wait_seconds") or 8.0),
                ),
            }
        if normalized_action == "restart_high_integrity_helper":
            return {
                "accepted": True,
                "action": normalized_action,
                "result": self.restart_high_integrity_helper(
                    self._payload_session_id(action_payload),
                    wait_seconds=float(action_payload.get("wait_seconds") or 5.0),
                ),
            }
        if normalized_action == "restart_capture_helper":
            return {
                "accepted": True,
                "action": normalized_action,
                "result": self.restart_capture_helper(
                    self._payload_session_id(action_payload),
                    wait_seconds=float(action_payload.get("wait_seconds") or 5.0),
                ),
            }
        if normalized_action == "terminate_user_session_agent":
            return {
                "accepted": True,
                "action": normalized_action,
                "result": self.terminate_user_session_agent(
                    self._payload_session_id(action_payload),
                    wait_seconds=float(action_payload.get("wait_seconds") or 3.0),
                ),
            }
        if normalized_action == "terminate_high_integrity_helper":
            return {
                "accepted": True,
                "action": normalized_action,
                "result": self.terminate_high_integrity_helper(
                    self._payload_session_id(action_payload),
                    wait_seconds=float(action_payload.get("wait_seconds") or 3.0),
                ),
            }
        if normalized_action == "terminate_capture_helper":
            return {
                "accepted": True,
                "action": normalized_action,
                "result": self.terminate_capture_helper(
                    self._payload_session_id(action_payload),
                    wait_seconds=float(action_payload.get("wait_seconds") or 3.0),
                ),
            }
        if normalized_action == "inject_mouse_event":
            return self._invoke_session_helper_action(
                normalized_action,
                action_payload,
                "inject_mouse_event",
            )
        if normalized_action == "inject_keyboard_event":
            return self._invoke_session_helper_action(
                normalized_action,
                action_payload,
                "inject_keyboard_event",
            )
        if normalized_action == "release_input_state":
            return self._invoke_session_helper_action(
                normalized_action,
                action_payload,
                "release_input_state",
            )
        if normalized_action == "capture_frame":
            return self._invoke_capture_frame_action(normalized_action, action_payload)

        raise ValueError(f"unsupported admin action: {normalized_action}")

    def start_supervisor(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._supervisor_loop, name="cmdb-service-session-supervisor", daemon=True)
        self._thread.start()

    def stop_supervisor(self) -> None:
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=3)

    def _supervisor_loop(self) -> None:
        self.bridge.log_runtime_event("ServiceRuntime", "session supervisor started")
        launch_cooldowns: dict[int, float] = {}
        helper_launch_cooldowns: dict[int, float] = {}
        last_health_snapshots: dict[int, tuple[bool, bool, tuple[int, ...], int]] = {}
        last_helper_health_snapshots: dict[int, tuple[bool, bool, tuple[int, ...], int]] = {}
        last_logged_sessions: tuple[int, ...] | None = None
        last_topology_fingerprint = ""
        session_id_sentinel = object()
        signature_sentinel = object()
        capture_divergence_sentinel = object()
        last_primary_session_id: int | None | object = session_id_sentinel
        last_preferred_capture_session_id: int | None | object = session_id_sentinel
        last_input_helper_target_session_id: int | None | object = session_id_sentinel
        last_capture_helper_target_session_id: int | None | object = session_id_sentinel
        last_primary_session_signature: str | object = signature_sentinel
        last_preferred_capture_signature: str | object = signature_sentinel
        last_input_helper_target_signature: str | object = signature_sentinel
        last_capture_helper_target_signature: str | object = signature_sentinel
        last_desired_helper_session_ids: tuple[int, ...] | None = None
        last_capture_divergence: bool | object = capture_divergence_sentinel
        last_capture_continuity_signature = ""
        last_display_substrate_signature = ""
        last_display_substrate_provider_state = ""
        last_blocked_substrate_signature = ""
        last_virtual_display_ensure_signature = ""
        last_virtual_display_ensure_at = 0.0
        last_virtual_display_repair_signature = ""
        last_virtual_display_repair_at = 0.0

        while not self._stop_event.is_set():
            try:
                descriptors = self.list_interactive_sessions()
                topology_fingerprint = self._session_topology_fingerprint(descriptors)
                session_ids = [item.session_id for item in descriptors]
                session_snapshot = tuple(session_ids)
                primary_descriptor = descriptors[0] if descriptors else None
                primary_session_id = primary_descriptor.session_id if primary_descriptor is not None else None
                primary_descriptor_signature = self._descriptor_topology_signature(primary_descriptor)
                preferred_capture_descriptor = self._select_preferred_capture_descriptor(descriptors)
                preferred_capture_session_id = (
                    preferred_capture_descriptor.session_id if preferred_capture_descriptor is not None else None
                )
                preferred_capture_signature = self._descriptor_topology_signature(preferred_capture_descriptor)
                helper_recycle_reasons: dict[int, str] = {}
                if session_snapshot != last_logged_sessions:
                    session_text = ", ".join(
                        self.bridge.get_user_session_debug_label(session_id)
                        for session_id in session_ids
                    ) if session_ids else "none"
                    self.bridge.log_runtime_event("ServiceRuntime", f"interactive sessions: {session_text}")
                    last_logged_sessions = session_snapshot
                if topology_fingerprint != last_topology_fingerprint:
                    self.bridge.log_runtime_event(
                        "ServiceRuntime",
                        "session topology changed: "
                        f"fingerprint={topology_fingerprint or 'none'}",
                    )
                    active_capture_descriptor = self._get_active_capture_descriptor()
                    remembered_active_signature = self._get_active_capture_host_descriptor_signature()
                    current_active_signature = self._descriptor_topology_signature(active_capture_descriptor)
                    if (
                        remembered_active_signature
                        and active_capture_descriptor is not None
                        and current_active_signature != remembered_active_signature
                    ):
                        helper_recycle_reasons[int(active_capture_descriptor.session_id)] = (
                            "active_capture_host_topology_changed"
                        )
                        self._clear_active_capture_host("active_capture_host_topology_changed")
                    last_topology_fingerprint = topology_fingerprint
                if primary_session_id != last_primary_session_id:
                    if primary_descriptor is None:
                        self.bridge.log_runtime_event("ServiceRuntime", "primary remote host session: none")
                    else:
                        self.bridge.log_runtime_event(
                            "ServiceRuntime",
                            "primary remote host session: "
                            f"{self.bridge.get_user_session_debug_label(primary_session_id)}",
                        )
                    last_primary_session_id = primary_session_id
                if primary_descriptor_signature != last_primary_session_signature:
                    if (
                        last_primary_session_signature is not signature_sentinel
                        and primary_descriptor is not None
                    ):
                        self.bridge.log_runtime_event(
                            "ServiceRuntime",
                            "primary remote host topology updated: "
                            f"session={primary_descriptor.session_id} "
                            f"signature={primary_descriptor_signature}",
                        )
                    last_primary_session_signature = primary_descriptor_signature
                if preferred_capture_session_id != last_preferred_capture_session_id:
                    if preferred_capture_descriptor is None:
                        self.bridge.log_runtime_event("ServiceRuntime", "preferred capture host session: none")
                    else:
                        self.bridge.log_runtime_event(
                            "ServiceRuntime",
                            "preferred capture host session: "
                            f"{self.bridge.get_user_session_debug_label(preferred_capture_session_id)} "
                            f"(console={preferred_capture_descriptor.is_console_session} "
                            f"remote={preferred_capture_descriptor.is_remote_session} "
                            f"state={preferred_capture_descriptor.state})",
                        )
                    last_preferred_capture_session_id = preferred_capture_session_id
                if preferred_capture_signature != last_preferred_capture_signature:
                    if (
                        last_preferred_capture_signature is not signature_sentinel
                        and preferred_capture_descriptor is not None
                    ):
                        self.bridge.log_runtime_event(
                            "ServiceRuntime",
                            "preferred capture host topology updated: "
                            f"session={preferred_capture_descriptor.session_id} "
                            f"signature={preferred_capture_signature}",
                        )
                        helper_recycle_reasons[int(preferred_capture_descriptor.session_id)] = (
                            "preferred_capture_host_topology_changed"
                        )
                        self._clear_active_capture_host("preferred_capture_host_topology_changed")
                    last_preferred_capture_signature = preferred_capture_signature
                capture_diverges = bool(
                    preferred_capture_session_id is not None
                    and primary_session_id is not None
                    and preferred_capture_session_id != primary_session_id
                )
                if capture_diverges != last_capture_divergence:
                    if capture_diverges:
                        self.bridge.log_runtime_event(
                            "ServiceRuntime",
                            "capture host diverges from primary remote host: "
                            f"capture_session={preferred_capture_session_id} "
                            f"primary_remote_host_session={primary_session_id}",
                        )
                    else:
                        self.bridge.log_runtime_event(
                            "ServiceRuntime",
                            "capture host aligned with primary remote host: "
                            f"session={preferred_capture_session_id}",
                        )
                    last_capture_divergence = capture_diverges

                input_helper_target_descriptor = self._select_input_helper_target_descriptor()
                input_helper_target_session_id = (
                    input_helper_target_descriptor.session_id
                    if input_helper_target_descriptor is not None
                    else None
                )
                input_helper_target_signature = self._descriptor_topology_signature(input_helper_target_descriptor)
                if input_helper_target_session_id != last_input_helper_target_session_id:
                    if input_helper_target_descriptor is None:
                        self.bridge.log_runtime_event("ServiceRuntime", "input helper target session: none")
                    else:
                        self.bridge.log_runtime_event(
                            "ServiceRuntime",
                            "input helper target session: "
                            f"{self.bridge.get_user_session_debug_label(input_helper_target_session_id)} "
                            f"(console={input_helper_target_descriptor.is_console_session} "
                            f"remote={input_helper_target_descriptor.is_remote_session} "
                            f"state={input_helper_target_descriptor.state})",
                        )
                    last_input_helper_target_session_id = input_helper_target_session_id
                if input_helper_target_signature != last_input_helper_target_signature:
                    if (
                        last_input_helper_target_signature is not signature_sentinel
                        and input_helper_target_descriptor is not None
                    ):
                        self.bridge.log_runtime_event(
                            "ServiceRuntime",
                            "input helper target topology updated: "
                            f"session={input_helper_target_descriptor.session_id} "
                            f"signature={input_helper_target_signature}",
                        )
                        helper_recycle_reasons[int(input_helper_target_descriptor.session_id)] = (
                            "input_helper_target_topology_changed"
                        )
                    last_input_helper_target_signature = input_helper_target_signature

                capture_helper_target_descriptor = self._select_capture_helper_target_descriptor()
                capture_helper_target_session_id = (
                    capture_helper_target_descriptor.session_id
                    if capture_helper_target_descriptor is not None
                    else None
                )
                capture_helper_target_signature = self._descriptor_topology_signature(
                    capture_helper_target_descriptor
                )
                if capture_helper_target_session_id != last_capture_helper_target_session_id:
                    if capture_helper_target_descriptor is None:
                        self.bridge.log_runtime_event("ServiceRuntime", "capture helper target session: none")
                    else:
                        self.bridge.log_runtime_event(
                            "ServiceRuntime",
                            "capture helper target session: "
                            f"{self.bridge.get_user_session_debug_label(capture_helper_target_session_id)} "
                            f"(console={capture_helper_target_descriptor.is_console_session} "
                            f"remote={capture_helper_target_descriptor.is_remote_session} "
                            f"state={capture_helper_target_descriptor.state})",
                        )
                    if (
                        last_capture_helper_target_session_id is not session_id_sentinel
                        and last_capture_helper_target_session_id is not None
                        and capture_helper_target_session_id != last_capture_helper_target_session_id
                    ):
                        self._clear_active_capture_host("capture_helper_target_session_changed")
                    last_capture_helper_target_session_id = capture_helper_target_session_id
                if capture_helper_target_signature != last_capture_helper_target_signature:
                    if (
                        last_capture_helper_target_signature is not signature_sentinel
                        and capture_helper_target_descriptor is not None
                    ):
                        self.bridge.log_runtime_event(
                            "ServiceRuntime",
                            "capture helper target topology updated: "
                            f"session={capture_helper_target_descriptor.session_id} "
                            f"signature={capture_helper_target_signature}",
                        )
                        helper_recycle_reasons[int(capture_helper_target_descriptor.session_id)] = (
                            "capture_helper_target_topology_changed"
                        )
                        self._clear_active_capture_host("capture_helper_target_topology_changed")
                    last_capture_helper_target_signature = capture_helper_target_signature

                active_capture_descriptor = self._get_active_capture_descriptor()
                capture_continuity = self._build_capture_continuity_policy(
                    descriptors,
                    primary_descriptor=primary_descriptor,
                    preferred_capture_descriptor=preferred_capture_descriptor,
                    active_capture_descriptor=active_capture_descriptor,
                    input_helper_descriptor=input_helper_target_descriptor,
                    capture_helper_descriptor=capture_helper_target_descriptor,
                )
                capture_continuity_signature = self._capture_continuity_signature(capture_continuity)
                if capture_continuity_signature != last_capture_continuity_signature:
                    self.bridge.log_runtime_event(
                        "ServiceRuntime",
                        "capture continuity policy: "
                        f"mode={capture_continuity.get('continuity_mode')} "
                        f"best_effort_only={capture_continuity.get('best_effort_only')} "
                        f"force_migration={capture_continuity.get('force_capture_host_migration')} "
                        f"from_session={capture_continuity.get('migration_from_session_id')} "
                        f"to_session={capture_continuity.get('migration_to_session_id')} "
                        f"notes={capture_continuity.get('notes') or []}",
                    )
                    last_capture_continuity_signature = capture_continuity_signature
                display_substrate = dict(capture_continuity.get("display_substrate") or {})
                display_substrate_provider_state = str(
                    display_substrate.get("provider_state") or "unknown"
                )
                display_substrate_blocked = bool(
                    display_substrate.get("continuity_blocked_by_missing_substrate", False)
                )
                if display_substrate_provider_state != last_display_substrate_provider_state:
                    self.bridge.log_runtime_event(
                        "ServiceRuntime",
                        "display substrate provider state: "
                        f"state={display_substrate_provider_state} "
                        f"persistent_ready={display_substrate.get('persistent_ready_for_unattended')} "
                        f"physical_display={display_substrate.get('physical_display_attached')} "
                        f"virtual_display={display_substrate.get('virtual_display_attached')} "
                        f"render_monitors={display_substrate.get('render_monitor_count')} "
                        f"attached_displays={display_substrate.get('attached_display_count')} "
                        f"provisioning_state={display_substrate.get('virtual_display_provisioning_state') or 'unknown'}",
                    )
                    if (
                        last_display_substrate_provider_state.startswith(
                            "blocked_missing_persistent_surface"
                        )
                        and not display_substrate_blocked
                    ):
                        self.bridge.log_runtime_event(
                            "ServiceRuntime",
                            "persistent capture substrate restored: "
                            f"state={display_substrate_provider_state} "
                            f"preferred_capture_session={capture_continuity.get('preferred_capture_host_session_id') or 'none'} "
                            f"authoritative_capture_session={capture_continuity.get('authoritative_capture_host_session_id') or 'none'}",
                        )
                    last_display_substrate_provider_state = display_substrate_provider_state
                display_substrate_signature = self._display_substrate_signature(display_substrate)
                if display_substrate_signature != last_display_substrate_signature:
                    if last_display_substrate_signature:
                        self.bridge.log_runtime_event(
                            "ServiceRuntime",
                            "display substrate changed: "
                            f"provider_state={display_substrate.get('provider_state') or 'unknown'} "
                            f"physical_display={display_substrate.get('physical_display_attached')} "
                            f"virtual_display={display_substrate.get('virtual_display_attached')} "
                            f"render_monitors={display_substrate.get('render_monitor_count')} "
                            f"attached_displays={display_substrate.get('attached_display_count')} "
                            f"provisioning_state={display_substrate.get('virtual_display_provisioning_state') or 'unknown'}",
                        )
                        self._schedule_helper_recycle(
                            helper_recycle_reasons,
                            "display_substrate_changed",
                            active_capture_descriptor=active_capture_descriptor,
                            input_helper_descriptor=input_helper_target_descriptor,
                            capture_helper_descriptor=capture_helper_target_descriptor,
                        )
                        self._clear_active_capture_host("display_substrate_changed")
                    last_display_substrate_signature = display_substrate_signature

                continuity_blocked_by_missing_substrate = bool(
                    capture_continuity.get("continuity_blocked_by_missing_substrate", False)
                )
                if continuity_blocked_by_missing_substrate:
                    blocked_signature = "|".join(
                        [
                            display_substrate_signature or "unknown",
                            str(
                                capture_continuity.get("virtual_display_provisioning_state")
                                or "unknown"
                            ),
                            str(
                                capture_continuity.get("authoritative_capture_host_session_id")
                                or "none"
                            ),
                        ]
                    )
                    if blocked_signature != last_blocked_substrate_signature:
                        self.bridge.log_runtime_event(
                            "ServiceRuntime",
                            "capture continuity blocked by missing persistent substrate: "
                            f"provider_state={display_substrate.get('provider_state') or 'unknown'} "
                            f"provisioning_state={capture_continuity.get('virtual_display_provisioning_state') or 'unknown'} "
                            f"can_provision_virtual_display={capture_continuity.get('can_provision_virtual_display')} "
                            f"authoritative_capture_host_session={capture_continuity.get('authoritative_capture_host_session_id') or 'none'}",
                        )
                        last_blocked_substrate_signature = blocked_signature

                    now = time.time()
                    if capture_continuity.get("can_provision_virtual_display"):
                        ensure_signature = "|".join(
                            [
                                blocked_signature,
                                str(
                                    display_substrate.get("virtual_display_provisioning_state")
                                    or "unknown"
                                ),
                            ]
                        )
                        ensure_cooldown_seconds = max(float(self.retry_seconds), 20.0)
                        if (
                            ensure_signature != last_virtual_display_ensure_signature
                            or (now - last_virtual_display_ensure_at) >= ensure_cooldown_seconds
                        ):
                            last_virtual_display_ensure_signature = ensure_signature
                            last_virtual_display_ensure_at = now
                            try:
                                ensure_status = self.ensure_virtual_display()
                            except Exception as exc:
                                self.bridge.log_runtime_event(
                                    "VirtualDisplay",
                                    "automatic ensure_virtual_display failed: "
                                    f"error={exc} blocked_signature={blocked_signature}",
                                )
                            else:
                                ensure_state = str(
                                    ensure_status.get("provisioning_state") or "unknown"
                                )
                                ensure_changed = bool(ensure_status.get("changed", False))
                                ensure_attached = bool(
                                    ensure_status.get("attached_virtual_display", False)
                                )
                                self.bridge.log_runtime_event(
                                    "VirtualDisplay",
                                    "automatic ensure_virtual_display result: "
                                    f"changed={ensure_changed} attached={ensure_attached} "
                                    f"state={ensure_state}",
                                )
                                if (
                                    not ensure_status.get("skipped_by_env")
                                    and (
                                        ensure_changed
                                        or ensure_attached
                                        or ensure_state
                                        != str(
                                            display_substrate.get(
                                                "virtual_display_provisioning_state"
                                            )
                                            or "unknown"
                                        )
                                    )
                                ):
                                    self._schedule_helper_recycle(
                                        helper_recycle_reasons,
                                        "virtual_display_ensure_changed",
                                        active_capture_descriptor=active_capture_descriptor,
                                        input_helper_descriptor=input_helper_target_descriptor,
                                        capture_helper_descriptor=capture_helper_target_descriptor,
                                    )
                                    self._clear_active_capture_host(
                                        "virtual_display_ensure_changed"
                                    )
                                    last_capture_continuity_signature = ""
                                    last_display_substrate_signature = ""

                                if (
                                    not ensure_attached
                                    and ensure_state
                                    in {
                                        "installed_detached",
                                        "installed_missing_enablement",
                                        "driver_package_ready_install_pending",
                                    }
                                ):
                                    repair_signature = "|".join(
                                        [
                                            blocked_signature,
                                            ensure_state,
                                            ensure_status.get("device_instance_id") or "none",
                                        ]
                                    )
                                    repair_cooldown_seconds = max(
                                        float(self.retry_seconds) * 2.0,
                                        45.0,
                                    )
                                    if (
                                        repair_signature
                                        != last_virtual_display_repair_signature
                                        or (now - last_virtual_display_repair_at)
                                        >= repair_cooldown_seconds
                                    ):
                                        last_virtual_display_repair_signature = (
                                            repair_signature
                                        )
                                        last_virtual_display_repair_at = now
                                        try:
                                            repair_status = self.repair_virtual_display()
                                        except Exception as exc:
                                            self.bridge.log_runtime_event(
                                                "VirtualDisplay",
                                                "automatic repair_virtual_display failed: "
                                                f"error={exc} blocked_signature={blocked_signature}",
                                            )
                                        else:
                                            repair_state = str(
                                                repair_status.get("provisioning_state")
                                                or "unknown"
                                            )
                                            repair_changed = bool(
                                                repair_status.get("changed", False)
                                            )
                                            repair_attached = bool(
                                                repair_status.get(
                                                    "attached_virtual_display",
                                                    False,
                                                )
                                            )
                                            self.bridge.log_runtime_event(
                                                "VirtualDisplay",
                                                "automatic repair_virtual_display result: "
                                                f"changed={repair_changed} attached={repair_attached} "
                                                f"state={repair_state}",
                                            )
                                            if (
                                                repair_changed
                                                or repair_attached
                                                or repair_state != ensure_state
                                            ):
                                                self._schedule_helper_recycle(
                                                    helper_recycle_reasons,
                                                    "virtual_display_repair_changed",
                                                    active_capture_descriptor=active_capture_descriptor,
                                                    input_helper_descriptor=input_helper_target_descriptor,
                                                    capture_helper_descriptor=capture_helper_target_descriptor,
                                                )
                                                self._clear_active_capture_host(
                                                    "virtual_display_repair_changed"
                                                )
                                                last_capture_continuity_signature = ""
                                                last_display_substrate_signature = ""
                else:
                    last_blocked_substrate_signature = ""
                    proactive_recovery_needed = bool(
                        capture_continuity.get("preferred_capture_host_transient")
                        or (
                            capture_continuity.get("active_capture_host_transient")
                            and not capture_continuity.get("persistent_capture_substrate_detected")
                        )
                        or (
                            capture_continuity.get("requires_virtual_display_for_full_continuity")
                            and not capture_continuity.get("persistent_capture_substrate_detected")
                        )
                    )
                    if proactive_recovery_needed:
                        preferred_substrate_class = str(
                            capture_continuity.get("preferred_capture_substrate_class") or ""
                        )
                        active_substrate_class = str(
                            capture_continuity.get("active_capture_substrate_class") or ""
                        )
                        recovery_substrate_class = preferred_substrate_class or active_substrate_class
                        recovery_descriptor = preferred_capture_descriptor or active_capture_descriptor
                        recovery_result = self._ensure_persistent_display_substrate_for_capture(
                            reason="supervisor_transient_capture_surface",
                            descriptor=recovery_descriptor,
                            helper_response={
                                "blocker": "remote_display_surface_without_persistent_substrate",
                                "display_presence": {
                                    "substrate_class": recovery_substrate_class,
                                    "persistent": False,
                                    "best_effort_only": True,
                                },
                            },
                            cooldown_seconds=max(float(self.retry_seconds) * 2.0, 45.0),
                        )
                        if recovery_result.get("changed") or recovery_result.get("recovered"):
                            self._schedule_helper_recycle(
                                helper_recycle_reasons,
                                "supervisor_transient_capture_surface_recovered",
                                active_capture_descriptor=active_capture_descriptor,
                                input_helper_descriptor=input_helper_target_descriptor,
                                capture_helper_descriptor=capture_helper_target_descriptor,
                            )
                            if preferred_capture_descriptor is not None:
                                helper_recycle_reasons[int(preferred_capture_descriptor.session_id)] = (
                                    "supervisor_transient_capture_surface_recovered"
                                )
                            self._clear_active_capture_host(
                                "supervisor_transient_capture_surface_recovered"
                            )
                            last_capture_continuity_signature = ""
                            last_display_substrate_signature = ""
                if (
                    preferred_capture_descriptor is not None
                    and preferred_capture_descriptor.is_disconnected
                    and preferred_capture_descriptor.is_remote_session
                    and not continuity_blocked_by_missing_substrate
                ):
                    handoff_result = self._maybe_handoff_capture_host_to_console(
                        preferred_capture_descriptor,
                        reason="supervisor_disconnected_remote_capture_host",
                        continuity_policy=capture_continuity,
                        display_substrate=display_substrate,
                    )
                    if handoff_result.get("success"):
                        self._schedule_helper_recycle(
                            helper_recycle_reasons,
                            "console_handoff_completed",
                            active_capture_descriptor=active_capture_descriptor,
                            input_helper_descriptor=input_helper_target_descriptor,
                            capture_helper_descriptor=capture_helper_target_descriptor,
                        )
                        self._clear_active_capture_host("console_handoff_completed")
                        last_capture_continuity_signature = ""
                        last_display_substrate_signature = ""
                if capture_continuity.get("force_capture_host_migration"):
                    migration_reason = str(
                        capture_continuity.get("force_capture_host_migration_reason")
                        or "capture_continuity_policy"
                    )
                    migration_from_session_id = capture_continuity.get("migration_from_session_id")
                    migration_to_session_id = capture_continuity.get("migration_to_session_id")
                    if migration_from_session_id is not None:
                        helper_recycle_reasons[int(migration_from_session_id)] = migration_reason
                    if (
                        migration_to_session_id is not None
                        and migration_to_session_id != migration_from_session_id
                    ):
                        helper_recycle_reasons[int(migration_to_session_id)] = (
                            f"{migration_reason}_target_refresh"
                        )
                    if active_capture_descriptor is not None:
                        self._clear_active_capture_host(migration_reason)

                desired_helper_session_ids = tuple(
                    sorted(
                        {
                            int(session_id)
                            for session_id in (
                                input_helper_target_session_id,
                                capture_helper_target_session_id,
                            )
                            if session_id is not None
                        }
                    )
                )
                if desired_helper_session_ids != last_desired_helper_session_ids:
                    helper_labels = [
                        self.bridge.get_user_session_debug_label(session_id)
                        for session_id in desired_helper_session_ids
                    ]
                    self.bridge.log_runtime_event(
                        "ServiceRuntime",
                        "desired helper sessions: "
                        f"{helper_labels if helper_labels else 'none'} "
                        f"(input_session={input_helper_target_session_id} "
                        f"capture_session={capture_helper_target_session_id})",
                    )
                    last_desired_helper_session_ids = desired_helper_session_ids

                for descriptor in descriptors:
                    session_id = descriptor.session_id
                    (
                        heartbeat_pid,
                        mutex_active,
                        heartbeat_active,
                        session_pids,
                    ) = self._get_role_process_health(
                        "user-session-agent",
                        session_id,
                        self.bridge.list_user_session_agent_pids,
                    )
                    if len(session_pids) > 1:
                        session_pids = self.bridge.cleanup_duplicate_user_session_agent_processes(
                            session_id,
                            preferred_pid=heartbeat_pid or None,
                        )

                    snapshot = (bool(mutex_active), bool(heartbeat_active), tuple(session_pids), heartbeat_pid)
                    if last_health_snapshots.get(session_id) != snapshot:
                        self.bridge.log_runtime_event(
                            "ServiceRuntime",
                            "session health: "
                            f"{self.bridge.get_user_session_debug_label(session_id)} "
                            f"mutex_active={mutex_active} heartbeat_active={heartbeat_active} "
                            f"heartbeat_pid={heartbeat_pid if heartbeat_pid > 0 else 'none'} "
                            f"session_pids={session_pids if session_pids else 'none'}",
                        )
                        last_health_snapshots[session_id] = snapshot

                    if primary_session_id is None:
                        continue

                    # If the primary session has no live user-session agent
                    # (e.g. the console session is headless / has no logged-on
                    # user, so the helper can never launch there), do NOT kill
                    # non-primary sessions. The RDP/active-user session is the
                    # authoritative capture host in that case.
                    if session_id != primary_session_id:
                        primary_alive_for_supervisor = False
                        try:
                            primary_hb = self.bridge.read_role_runtime_state(
                                "user-session-agent",
                                session_bound=True,
                                session_id=primary_session_id,
                            ) or {}
                            primary_pids = self.bridge.list_user_session_agent_pids(primary_session_id)
                            primary_heartbeat_recent = self.bridge.has_recent_role_runtime_state(
                                "user-session-agent",
                                session_bound=True,
                                session_id=primary_session_id,
                            )
                            primary_mutex = self.bridge.is_role_mutex_active(
                                "user-session-agent",
                                session_bound=True,
                                session_id=primary_session_id,
                            )
                            primary_alive_for_supervisor = bool(
                                primary_heartbeat_recent
                                and (primary_mutex or primary_pids)
                            )
                        except Exception:
                            primary_alive_for_supervisor = False

                        if not primary_alive_for_supervisor:
                            # Primary can't host a working agent; let this
                            # session keep running as the real host. Fall
                            # through to the launch attempt below so a healthy
                            # user-session agent is started here even though
                            # the primary is dead.
                            self.bridge.log_runtime_event(
                                "ServiceRuntime",
                                f"primary not alive; treating session={session_id} as authoritative host",
                            )
                        else:
                            stray_pids: list[int] = []
                            candidate_pids = {
                                int(pid)
                                for pid in session_pids + ([heartbeat_pid] if heartbeat_pid > 0 else [])
                                if int(pid) > 0
                            }
                            for pid in sorted(candidate_pids):
                                if self.bridge.terminate_process(pid):
                                    stray_pids.append(pid)
                            if stray_pids:
                                self.bridge.log_runtime_event(
                                    "ServiceRuntime",
                                    "terminated non-primary user-session agent(s): "
                                    f"session={session_id} primary_session={primary_session_id} pids={stray_pids}",
                                )
                            elif mutex_active or heartbeat_active or session_pids:
                                self.bridge.log_runtime_event(
                                    "ServiceRuntime",
                                    "non-primary user-session agent state detected but no terminable pid found: "
                                    f"session={session_id} primary_session={primary_session_id} "
                                    f"mutex_active={mutex_active} heartbeat_active={heartbeat_active}",
                                )
                            continue

                    if mutex_active or heartbeat_active or session_pids:
                        continue

                    now = time.time()
                    last_attempt_at = launch_cooldowns.get(session_id, 0.0)
                    if now - last_attempt_at < self.launch_cooldown_seconds:
                        continue

                    launch_cooldowns[session_id] = now
                    self.bridge.log_runtime_event(
                        "ServiceRuntime",
                        f"launch attempt for {self.bridge.get_user_session_debug_label(session_id)}",
                        )
                    self.bridge.launch_user_session_agent_for_session(session_id)

                for descriptor in descriptors:
                    session_id = descriptor.session_id
                    (
                        helper_heartbeat_pid,
                        helper_mutex_active,
                        helper_heartbeat_active,
                        helper_pids,
                    ) = self._get_role_process_health(
                        "high-integrity-helper",
                        session_id,
                        self.bridge.list_high_integrity_helper_pids,
                    )
                    if len(helper_pids) > 1:
                        helper_pids = self._cleanup_duplicate_role_processes(
                            "high-integrity-helper",
                            session_id,
                            helper_pids,
                            preferred_pid=helper_heartbeat_pid or None,
                        )

                    helper_snapshot = (
                        bool(helper_mutex_active),
                        bool(helper_heartbeat_active),
                        tuple(helper_pids),
                        helper_heartbeat_pid,
                    )
                    if last_helper_health_snapshots.get(session_id) != helper_snapshot:
                        self.bridge.log_runtime_event(
                            "ServiceRuntime",
                            "helper health: "
                            f"{self.bridge.get_user_session_debug_label(session_id)} "
                            f"mutex_active={helper_mutex_active} heartbeat_active={helper_heartbeat_active} "
                            f"heartbeat_pid={helper_heartbeat_pid if helper_heartbeat_pid > 0 else 'none'} "
                            f"helper_pids={helper_pids if helper_pids else 'none'}",
                        )
                        last_helper_health_snapshots[session_id] = helper_snapshot

                    recycle_reason = helper_recycle_reasons.get(session_id)
                    if recycle_reason is not None:
                        terminated_pids = self._terminate_role_processes(
                            "high-integrity-helper",
                            session_id,
                            helper_pids,
                            helper_heartbeat_pid,
                        )
                        if terminated_pids:
                            self.bridge.log_runtime_event(
                                "ServiceRuntime",
                                "recycled helper for topology transition: "
                                f"session={session_id} reason={recycle_reason} pids={terminated_pids}",
                            )
                        elif helper_mutex_active or helper_heartbeat_active or helper_pids:
                            self.bridge.log_runtime_event(
                                "ServiceRuntime",
                                "helper recycle requested but no terminable pid found: "
                                f"session={session_id} reason={recycle_reason} "
                                f"mutex_active={helper_mutex_active} heartbeat_active={helper_heartbeat_active}",
                            )
                        helper_launch_cooldowns.pop(session_id, None)
                        last_helper_health_snapshots.pop(session_id, None)
                        helper_heartbeat_pid = 0
                        helper_mutex_active = False
                        helper_heartbeat_active = False
                        helper_pids = []

                    if not desired_helper_session_ids:
                        if helper_mutex_active or helper_heartbeat_active or helper_pids:
                            terminated_pids = self._terminate_role_processes(
                                "high-integrity-helper",
                                session_id,
                                helper_pids,
                                helper_heartbeat_pid,
                            )
                            if terminated_pids:
                                self.bridge.log_runtime_event(
                                    "ServiceRuntime",
                                    "terminated helper without active target: "
                                    f"session={session_id} pids={terminated_pids}",
                                )
                        continue

                    if session_id not in desired_helper_session_ids:
                        if helper_mutex_active or helper_heartbeat_active or helper_pids:
                            terminated_pids = self._terminate_role_processes(
                                "high-integrity-helper",
                                session_id,
                                helper_pids,
                                helper_heartbeat_pid,
                            )
                            if terminated_pids:
                                self.bridge.log_runtime_event(
                                    "ServiceRuntime",
                                    "terminated stale helper outside target session: "
                                    f"session={session_id} target_sessions={list(desired_helper_session_ids)} "
                                    f"pids={terminated_pids}",
                                )
                            else:
                                self.bridge.log_runtime_event(
                                    "ServiceRuntime",
                                    "stale helper state detected but no terminable pid found: "
                                    f"session={session_id} target_sessions={list(desired_helper_session_ids)} "
                                    f"mutex_active={helper_mutex_active} heartbeat_active={helper_heartbeat_active}",
                                )
                        continue

                    if helper_mutex_active or helper_heartbeat_active or helper_pids:
                        continue

                    now = time.time()
                    last_attempt_at = helper_launch_cooldowns.get(session_id, 0.0)
                    if now - last_attempt_at < self.launch_cooldown_seconds:
                        continue

                    helper_launch_cooldowns[session_id] = now
                    self.bridge.log_runtime_event(
                        "ServiceRuntime",
                        "helper launch attempt for "
                        f"{self.bridge.get_user_session_debug_label(session_id)}",
                    )
                    launch_result = self.bridge.launch_high_integrity_helper_for_session(session_id) or {}
                    self.bridge.log_runtime_event(
                        "ServiceRuntime",
                        "helper launch result: "
                        f"session={session_id} started={bool(launch_result.get('started'))} "
                        f"mode={launch_result.get('launch_mode') or 'unknown'} "
                        f"pid={launch_result.get('pid') or 'none'}",
                    )

                active_session_set = set(session_ids)
                for session_id in list(launch_cooldowns.keys()):
                    if session_id not in active_session_set:
                        launch_cooldowns.pop(session_id, None)
                        last_health_snapshots.pop(session_id, None)
                for session_id in list(helper_launch_cooldowns.keys()):
                    if session_id not in active_session_set:
                        helper_launch_cooldowns.pop(session_id, None)
                        last_helper_health_snapshots.pop(session_id, None)
            except Exception as exc:
                self.bridge.log_runtime_event("ServiceRuntime", f"session supervisor loop error: {exc}")

            self._stop_event.wait(self.retry_seconds)

        self.bridge.log_runtime_event("ServiceRuntime", "session supervisor stopped")

    def _resolve_target_descriptor(self, session_id: int | None) -> SessionDescriptor | None:
        if session_id is None:
            return self.get_primary_remote_host_session() or self.get_console_session()

        return self._get_session_descriptor_by_id(int(session_id))

    def _resolve_capture_descriptor(self, session_id: int | None) -> SessionDescriptor | None:
        if session_id is not None:
            return self._resolve_target_descriptor(session_id)

        return (
            self.get_preferred_capture_host_session()
            or self.get_console_session()
            or self.get_primary_remote_host_session()
            or self._get_active_capture_descriptor()
        )

    def _resolve_input_helper_target_descriptor(self, session_id: int | None) -> SessionDescriptor | None:
        if session_id is not None:
            return self._resolve_target_descriptor(session_id)

        return (
            self._select_input_helper_target_descriptor()
            or self._get_active_capture_descriptor()
            or self.get_console_session()
            or self.get_preferred_capture_host_session()
        )

    def _resolve_capture_helper_target_descriptor(self, session_id: int | None) -> SessionDescriptor | None:
        descriptors = self.list_interactive_sessions()
        primary_descriptor = self._find_best_primary_remote_descriptor(descriptors)
        authoritative_descriptor = self._select_capture_helper_target_descriptor()
        if session_id is None:
            return (
                authoritative_descriptor
                or self._find_best_console_descriptor(descriptors)
                or primary_descriptor
                or self._get_active_capture_descriptor()
            )

        requested_descriptor = self._resolve_target_descriptor(session_id)
        return self._resolve_capture_host_request_hint(
            requested_descriptor,
            authoritative_descriptor=authoritative_descriptor,
            primary_descriptor=primary_descriptor,
        )

    def _select_input_helper_target_descriptor(self) -> SessionDescriptor | None:
        capture_helper_descriptor = self._select_capture_helper_target_descriptor()
        primary_descriptor = self.get_primary_remote_host_session()
        active_descriptor = self._get_active_capture_descriptor()
        preferred_capture_descriptor = self.get_preferred_capture_host_session()
        console_descriptor = self.get_console_session()
        # 优先用 capture helper 的 session（输入和捕获应在同一会话）
        if capture_helper_descriptor is not None and capture_helper_descriptor.identity:
            return capture_helper_descriptor
        # 禁用虚拟显示器时，优先选有用户identity的active session（RDP session 2）
        if primary_descriptor is not None and not primary_descriptor.is_disconnected and primary_descriptor.identity:
            return primary_descriptor
        if active_descriptor is not None and active_descriptor.identity:
            return active_descriptor
        if capture_helper_descriptor is not None:
            return capture_helper_descriptor
        if preferred_capture_descriptor is not None and not preferred_capture_descriptor.is_disconnected:
            return preferred_capture_descriptor
        if console_descriptor is not None and console_descriptor.identity:
            return console_descriptor
        return primary_descriptor or preferred_capture_descriptor

    def _select_capture_helper_target_descriptor(self) -> SessionDescriptor | None:
        descriptors = self.list_interactive_sessions()
        preferred_descriptor = self._select_preferred_capture_descriptor(descriptors)
        active_descriptor = self._get_active_capture_descriptor()

        if preferred_descriptor is not None:
            if (
                active_descriptor is not None
                and preferred_descriptor.session_id != active_descriptor.session_id
            ):
                self.bridge.log_runtime_event(
                    "ServiceRuntime",
                    "helper target migrated to preferred capture host: "
                    f"preferred_session={preferred_descriptor.session_id} "
                    f"active_capture_session={active_descriptor.session_id} "
                    f"preferred_state={preferred_descriptor.state or 'unknown'} "
                    f"active_state={active_descriptor.state or 'unknown'} "
                    f"preferred_remote={preferred_descriptor.is_remote_session} "
                    f"active_remote={active_descriptor.is_remote_session}",
                )
            return preferred_descriptor

        return (
            active_descriptor
            or self._find_best_console_descriptor(descriptors)
            or self._find_best_primary_remote_descriptor(descriptors)
        )

    def _find_runtime_status(self, session_id: int) -> dict:
        for row in self.collect_runtime_status():
            if int(row.get("session_id") or -1) == int(session_id):
                return row
        return {}

    def _get_role_process_health(
        self,
        role_name: str,
        session_id: int,
        pid_lister: Callable[[int], list[int]],
    ) -> tuple[int, bool, bool, list[int]]:
        heartbeat_payload = self.bridge.read_role_runtime_state(
            role_name,
            session_bound=True,
            session_id=session_id,
        ) or {}
        heartbeat_pid = int(heartbeat_payload.get("pid") or 0)
        mutex_active = bool(
            self.bridge.is_role_mutex_active(
                role_name,
                session_bound=True,
                session_id=session_id,
            )
        )
        heartbeat_active = bool(
            self.bridge.has_recent_role_runtime_state(
                role_name,
                session_bound=True,
                session_id=session_id,
            )
        )
        process_ids = sorted(
            {
                int(pid)
                for pid in pid_lister(session_id)
                if int(pid) > 0
            }
        )
        return heartbeat_pid, mutex_active, heartbeat_active, process_ids

    def _cleanup_duplicate_role_processes(
        self,
        role_name: str,
        session_id: int,
        process_ids: list[int],
        *,
        preferred_pid: int | None = None,
    ) -> list[int]:
        unique_pids = sorted({int(pid) for pid in process_ids if int(pid) > 0})
        if len(unique_pids) <= 1:
            return unique_pids

        keep_pid = preferred_pid if preferred_pid in unique_pids else max(unique_pids)
        terminated_pids: list[int] = []
        for pid in unique_pids:
            if pid == keep_pid:
                continue
            if self.bridge.terminate_process(pid):
                terminated_pids.append(pid)

        if terminated_pids:
            self.bridge.log_runtime_event(
                "ServiceRuntime",
                "terminated duplicate role process(es): "
                f"role={role_name} session={session_id} keep_pid={keep_pid} terminated={terminated_pids}",
            )
        return [pid for pid in unique_pids if pid == keep_pid or pid not in terminated_pids]

    def _terminate_role_processes(
        self,
        role_name: str,
        session_id: int,
        process_ids: list[int],
        heartbeat_pid: int = 0,
    ) -> list[int]:
        candidate_pids = sorted(
            {
                int(pid)
                for pid in process_ids + ([heartbeat_pid] if int(heartbeat_pid) > 0 else [])
                if int(pid) > 0
            }
        )
        terminated_pids: list[int] = []
        for pid in candidate_pids:
            if self.bridge.terminate_process(pid):
                terminated_pids.append(pid)
        if terminated_pids:
            self.bridge.log_runtime_event(
                "ServiceRuntime",
                "terminated role process(es): "
                f"role={role_name} session={session_id} pids={terminated_pids}",
            )
        return terminated_pids

    def _payload_session_id(self, payload: dict) -> int | None:
        raw_value = payload.get("session_id")
        if raw_value in (None, ""):
            return None
        return int(raw_value)

    def _describe_desktop_context(self, payload: dict) -> dict:
        console_session = self.get_console_session()
        capture_session = self._get_active_capture_descriptor()
        preferred_capture_session = self.get_preferred_capture_host_session()
        primary_remote_host_session = self.get_primary_remote_host_session()
        input_helper_target = self.get_input_helper_host_session()
        capture_helper_target = self.get_capture_helper_host_session()
        target_session_id = self._payload_session_id(payload)
        input_target_descriptor = (
            self._resolve_input_helper_target_descriptor(target_session_id)
            if target_session_id is not None
            else input_helper_target or console_session or primary_remote_host_session
        )
        capture_target_descriptor = (
            self._resolve_capture_helper_target_descriptor(target_session_id)
            if target_session_id is not None
            else capture_helper_target or console_session or primary_remote_host_session
        )
        desktop_state = self._desktop_controller.describe_current_state()
        desktop_binding_state = self._desktop_controller.describe_binding_state()
        desktop_transition_state = self._desktop_controller.describe_transition_state()
        helper_status = None
        input_helper_status = None
        capture_helper_status = None
        if input_target_descriptor is not None:
            with contextlib.suppress(Exception):
                input_helper_status = self._invoke_helper_command(
                    input_target_descriptor.session_id,
                    "describe_desktop_context",
                    payload,
                    allow_restart=False,
                    ensure_helper=False,
                    helper_role="input",
                )
                helper_status = input_helper_status
        if capture_target_descriptor is not None:
            with contextlib.suppress(Exception):
                if (
                    input_target_descriptor is not None
                    and capture_target_descriptor.session_id == input_target_descriptor.session_id
                ):
                    capture_helper_status = input_helper_status
                else:
                    capture_helper_status = self._invoke_helper_command(
                        capture_target_descriptor.session_id,
                        "describe_desktop_context",
                        payload,
                        allow_restart=False,
                        ensure_helper=False,
                        helper_role="capture",
                    )

        return {
            "accepted": True,
            "action": "describe_desktop_context",
            "reason": str(payload.get("reason") or "").strip(),
            "requested_session_id": target_session_id,
            "console_session": console_session.to_dict() if console_session is not None else None,
            "primary_remote_host_session": (
                primary_remote_host_session.to_dict() if primary_remote_host_session is not None else None
            ),
            "preferred_capture_host_session": (
                preferred_capture_session.to_dict() if preferred_capture_session is not None else None
            ),
            "active_capture_session": capture_session.to_dict() if capture_session is not None else None,
            "input_helper_host_session": (
                input_helper_target.to_dict() if input_helper_target is not None else None
            ),
            "capture_helper_host_session": (
                capture_helper_target.to_dict() if capture_helper_target is not None else None
            ),
            "target_session": input_target_descriptor.to_dict() if input_target_descriptor is not None else None,
            "input_target_session": (
                input_target_descriptor.to_dict() if input_target_descriptor is not None else None
            ),
            "capture_target_session": (
                capture_target_descriptor.to_dict() if capture_target_descriptor is not None else None
            ),
            "capture_host_diverges_from_primary_remote_host": bool(
                preferred_capture_session is not None
                and primary_remote_host_session is not None
                and preferred_capture_session.session_id != primary_remote_host_session.session_id
            ),
            "helper_hosts_diverge": bool(
                input_helper_target is not None
                and capture_helper_target is not None
                and input_helper_target.session_id != capture_helper_target.session_id
            ),
            "capture_topology": self._build_capture_topology_diagnostics(
                self.list_interactive_sessions(),
                primary_descriptor=primary_remote_host_session,
                preferred_capture_descriptor=preferred_capture_session,
            ),
            "capture_continuity": self._build_capture_continuity_policy(
                self.list_interactive_sessions(),
                primary_descriptor=primary_remote_host_session,
                preferred_capture_descriptor=preferred_capture_session,
                active_capture_descriptor=capture_session,
                input_helper_descriptor=input_helper_target,
                capture_helper_descriptor=capture_helper_target,
            ),
            "display_substrate": self._build_display_substrate_diagnostics(
                preferred_capture_descriptor=preferred_capture_session,
                active_capture_descriptor=capture_session,
            ),
            "remote_desktop_readiness": self.get_remote_desktop_readiness(),
            "interactive_sessions": [item.to_dict() for item in self.list_interactive_sessions()],
            "desktop_context": {
                "input_desktop_available": bool(self._desktop_controller.has_input_desktop()),
                "state": desktop_state,
                "binding_state": desktop_binding_state,
                "transition_state": desktop_transition_state,
            },
            "helper_context": helper_status,
            "input_helper_context": input_helper_status,
            "capture_helper_context": capture_helper_status,
            "runtime_status": self.collect_runtime_status(),
        }

    def _invoke_session_helper_action(
        self,
        normalized_action: str,
        payload: dict,
        helper_command: str,
    ) -> dict:
        session_id = self._payload_session_id(payload)
        descriptor = self._resolve_input_helper_target_descriptor(session_id)
        if descriptor is None:
            raise RuntimeError("no interactive session available")

        helper_payload = dict(payload)
        helper_payload.pop("session_id", None)
        helper_response = self._invoke_helper_command(
            descriptor.session_id,
            helper_command,
            helper_payload,
        )
        return {
            "accepted": True,
            "action": normalized_action,
            "target_session": descriptor.to_dict(),
            "helper_response": helper_response,
        }

    def _invoke_capture_frame_action(self, normalized_action: str, payload: dict) -> dict:
        frame_started_at = time.perf_counter()
        original_payload = dict(payload)
        requested_session_id = self._payload_session_id(original_payload)
        descriptors = self._build_capture_candidate_descriptors(requested_session_id)
        descriptors_done_at = time.perf_counter()
        if not descriptors:
            raise RuntimeError("no session available for capture")
        binding_descriptors = [
            self._decorate_capture_descriptor_with_binding_policy(
                descriptor,
                requested_session_id=requested_session_id,
                descriptors=descriptors,
            )
            for descriptor in descriptors
        ]
        binding_done_at = time.perf_counter()

        helper_payload = dict(original_payload)
        helper_payload.pop("session_id", None)
        retry_after_console_handoff = bool(helper_payload.pop("_retry_after_console_handoff", False))
        retry_after_display_substrate_recovery = bool(
            helper_payload.pop("_retry_after_display_substrate_recovery", False)
        )
        retry_payload_base = dict(original_payload)
        retry_payload_base.pop("_retry_after_console_handoff", None)
        retry_payload_base.pop("_retry_after_display_substrate_recovery", None)
        attempted_sessions: list[dict] = []
        last_error: Exception | None = None
        console_handoff_attempts: list[dict] = []
        display_substrate_recovery_attempts: list[dict] = []
        for attempt_index, descriptor in enumerate(binding_descriptors):
            candidate_payload = dict(helper_payload)
            candidate_payload["capture_target"] = descriptor.to_dict()
            candidate_payload["capture_attempt_index"] = attempt_index
            candidate_payload["capture_attempt_count"] = len(binding_descriptors)
            try:
                helper_call_started_at = time.perf_counter()
                helper_response = self._invoke_helper_command(
                    descriptor.session_id,
                    "capture_frame",
                    candidate_payload,
                    helper_role="capture",
                )
                helper_call_elapsed = time.perf_counter() - helper_call_started_at
                if helper_call_elapsed > 0.25:
                    self.bridge.log_runtime_event(
                        "ServiceRuntime",
                        "capture_frame slow: "
                        f"descriptor_setup={(helper_call_started_at - frame_started_at):.3f}s "
                        f"helper_pipe_roundtrip={helper_call_elapsed:.3f}s "
                        f"session={descriptor.session_id} "
                        f"resp_bytes={len(json.dumps(helper_response, ensure_ascii=False, default=str))}",
                    )
            except Exception as exc:
                last_error = exc
                attempted_sessions.append(
                    {
                        "session": descriptor.to_dict(),
                        "captured": False,
                        "error": str(exc),
                    }
                )
                continue

            captured = bool(helper_response.get("captured", False))
            attempted_sessions.append(
                {
                    "session": descriptor.to_dict(),
                    "captured": captured,
                    "empty": bool(helper_response.get("empty", False)),
                    "backend": str(helper_response.get("backend") or ""),
                }
            )
            if not captured:
                display_presence = helper_response.get("display_presence") or {}
                substrate_class = str(display_presence.get("substrate_class") or "").strip().lower()
                helper_blocker = str(helper_response.get("blocker") or "").strip().lower()
                if (
                    substrate_class in _NON_PERSISTENT_CAPTURE_SUBSTRATES
                    or helper_blocker in _NON_PERSISTENT_CAPTURE_BLOCKERS
                    or (
                        bool(display_presence.get("best_effort_only", False))
                        and not bool(display_presence.get("persistent", False))
                    )
                ):
                    attempted_sessions[-1]["substrate_class"] = substrate_class
                    attempted_sessions[-1]["blocker"] = helper_blocker
                    self._clear_active_capture_host(
                        f"capture_failed_{helper_blocker or substrate_class or 'unknown_transient_surface'}"
                    )
                    recovery_result = self._ensure_persistent_display_substrate_for_capture(
                        reason=f"capture_frame_empty_{helper_blocker or substrate_class or 'transient_surface'}",
                        descriptor=descriptor,
                        helper_response=helper_response,
                    )
                    if recovery_result.get("attempted") or recovery_result.get("queried"):
                        display_substrate_recovery_attempts.append(recovery_result)
                        attempted_sessions[-1]["display_substrate_recovery"] = {
                            "attempted": bool(recovery_result.get("attempted")),
                            "recovered": bool(recovery_result.get("recovered")),
                            "changed": bool(recovery_result.get("changed")),
                            "blocker": str(recovery_result.get("blocker") or ""),
                        }
                    recovery_diagnostics = recovery_result.get("diagnostics") or {}
                    recovery_substrate = recovery_diagnostics.get("display_substrate") or {}
                    recovery_continuity = recovery_diagnostics.get("capture_continuity") or {}
                    if recovery_result.get("recovered") or recovery_result.get("changed"):
                        continue
                    if recovery_substrate.get("continuity_blocked_by_missing_substrate"):
                        continue
                    handoff_result = self._maybe_handoff_capture_host_to_console(
                        descriptor,
                        reason=f"capture_frame_empty_{helper_blocker or substrate_class or 'transient_surface'}",
                        continuity_policy=recovery_continuity,
                        display_substrate=recovery_substrate,
                    )
                    if handoff_result.get("attempted"):
                        console_handoff_attempts.append(handoff_result)
                continue

            desktop_context = helper_response.get("desktop_context") or {}
            self._remember_active_capture_host(
                descriptor,
                str(helper_response.get("backend") or ""),
                reason="capture_success",
                desktop_signature=str(
                    helper_response.get("desktop_signature")
                    or desktop_context.get("desktop_signature")
                    or ""
                ),
                desktop_kind=str(desktop_context.get("desktop_kind") or ""),
            )
            return {
                "accepted": True,
                "action": normalized_action,
                "requested_session_id": requested_session_id,
                "target_session": descriptor.to_dict(),
                "helper_response": helper_response,
                "attempted_sessions": attempted_sessions,
                "console_handoff_attempts": console_handoff_attempts,
                "display_substrate_recovery_attempts": display_substrate_recovery_attempts,
            }

        if display_substrate_recovery_attempts and not retry_after_display_substrate_recovery:
            successful_recovery = any(
                item.get("recovered") or item.get("changed")
                for item in display_substrate_recovery_attempts
            )
            if successful_recovery:
                retry_payload = dict(retry_payload_base)
                retry_payload["_retry_after_display_substrate_recovery"] = True
                try:
                    retry_result = self._invoke_capture_frame_action(normalized_action, retry_payload)
                except Exception as exc:
                    raise RuntimeError(
                        "capture retry after display substrate recovery failed: "
                        + json.dumps(
                            {
                                "requested_session_id": requested_session_id,
                                "attempted_sessions": attempted_sessions,
                                "display_substrate_recovery_attempts": display_substrate_recovery_attempts,
                                "console_handoff_attempts": console_handoff_attempts,
                                "retry_payload": retry_payload,
                                "error": str(exc),
                            },
                            ensure_ascii=False,
                            default=str,
                        )
                    ) from exc
                retry_result["attempted_sessions"] = attempted_sessions + list(
                    retry_result.get("attempted_sessions") or []
                )
                retry_result["display_substrate_recovery_attempts"] = (
                    display_substrate_recovery_attempts
                    + list(retry_result.get("display_substrate_recovery_attempts") or [])
                )
                retry_result["console_handoff_attempts"] = console_handoff_attempts + list(
                    retry_result.get("console_handoff_attempts") or []
                )
                return retry_result

        if console_handoff_attempts and not retry_after_console_handoff:
            successful_handoff = any(item.get("success") for item in console_handoff_attempts)
            if successful_handoff:
                retry_payload = dict(retry_payload_base)
                retry_payload["_retry_after_console_handoff"] = True
                try:
                    retry_result = self._invoke_capture_frame_action(normalized_action, retry_payload)
                except Exception as exc:
                    raise RuntimeError(
                        "capture retry after console handoff failed: "
                        + json.dumps(
                            {
                                "requested_session_id": requested_session_id,
                                "attempted_sessions": attempted_sessions,
                                "console_handoff_attempts": console_handoff_attempts,
                                "retry_payload": retry_payload,
                                "error": str(exc),
                            },
                            ensure_ascii=False,
                            default=str,
                        )
                    ) from exc
                retry_attempts = list(retry_result.get("attempted_sessions") or [])
                retry_result["attempted_sessions"] = attempted_sessions + retry_attempts
                retry_result["console_handoff_attempts"] = console_handoff_attempts + list(
                    retry_result.get("console_handoff_attempts") or []
                )
                retry_result["display_substrate_recovery_attempts"] = (
                    display_substrate_recovery_attempts
                    + list(retry_result.get("display_substrate_recovery_attempts") or [])
                )
                return retry_result

        self._clear_active_capture_host("capture_failed_all_candidates")
        failure_diagnostics = self._build_capture_recovery_diagnostics(None)
        readiness = failure_diagnostics.get("remote_desktop_readiness") or {}
        last_attempt = attempted_sessions[-1] if attempted_sessions else {}
        last_session = last_attempt.get("session") or {}
        blocker = (
            last_attempt.get("blocker")
            or readiness.get("commercial_continuity_blocker")
            or "capture_failed_all_candidates"
        )
        return {
            "accepted": True,
            "action": normalized_action,
            "requested_session_id": requested_session_id,
            "target_session": last_session or None,
            "helper_response": {
                "captured": False,
                "empty": True,
                "captured_at": time.time(),
                "session_id": last_session.get("session_id"),
                "backend": str(last_attempt.get("backend") or ""),
                "blocker": str(blocker or ""),
                "error": str(last_error or ""),
                "display_presence": {
                    "substrate_class": str(last_attempt.get("substrate_class") or ""),
                    "persistent": False,
                },
                "desktop_context": {},
            },
            "attempted_sessions": attempted_sessions,
            "console_handoff_attempts": console_handoff_attempts,
            "display_substrate_recovery_attempts": display_substrate_recovery_attempts,
            "remote_desktop_readiness": readiness,
            "display_substrate": failure_diagnostics.get("display_substrate") or {},
            "capture_continuity": failure_diagnostics.get("capture_continuity") or {},
        }

    def _virtual_display_repairable_states(self) -> set[str]:
        return {
            "installed_detached",
            "installed_missing_enablement",
            "driver_package_ready_install_pending",
        }

    def _display_substrate_recovery_signature(
        self,
        diagnostics: dict,
        status: dict | None = None,
    ) -> str:
        display_substrate = diagnostics.get("display_substrate") or {}
        virtual_status = status or display_substrate.get("virtual_display_status") or {}
        return "|".join(
            [
                f"blocked={1 if diagnostics.get('continuity_blocked_by_missing_substrate') else 0}",
                f"provider={display_substrate.get('provider_state') or 'unknown'}",
                f"state={virtual_status.get('provisioning_state') or diagnostics.get('virtual_display_provisioning_state') or 'unknown'}",
                f"package={1 if virtual_status.get('driver_package_complete') else 0}",
                f"tools={json.dumps(virtual_status.get('available_tools') or {}, sort_keys=True, default=str)}",
            ]
        )

    def _build_capture_recovery_diagnostics(
        self,
        descriptor: SessionDescriptor | None,
    ) -> dict:
        descriptors = self.list_interactive_sessions()
        primary_descriptor = self._find_best_primary_remote_descriptor(descriptors)
        preferred_capture_descriptor = self._select_preferred_capture_descriptor(descriptors)
        active_capture_descriptor = descriptor or self._get_active_capture_descriptor()
        input_helper_descriptor = self._select_input_helper_target_descriptor()
        capture_helper_descriptor = self._select_capture_helper_target_descriptor()
        continuity = self._build_capture_continuity_policy(
            descriptors,
            primary_descriptor=primary_descriptor,
            preferred_capture_descriptor=preferred_capture_descriptor,
            active_capture_descriptor=active_capture_descriptor,
            input_helper_descriptor=input_helper_descriptor,
            capture_helper_descriptor=capture_helper_descriptor,
        )
        display_substrate = dict(continuity.get("display_substrate") or {})
        readiness = self._build_remote_desktop_readiness(
            descriptors,
            primary_descriptor=primary_descriptor,
            preferred_capture_descriptor=preferred_capture_descriptor,
            active_capture_descriptor=active_capture_descriptor,
            input_helper_descriptor=input_helper_descriptor,
            capture_helper_descriptor=capture_helper_descriptor,
            capture_continuity=continuity,
            display_substrate=display_substrate,
        )
        return {
            "continuity_blocked_by_missing_substrate": bool(
                continuity.get("continuity_blocked_by_missing_substrate")
                or display_substrate.get("continuity_blocked_by_missing_substrate")
            ),
            "can_provision_virtual_display": bool(
                continuity.get("can_provision_virtual_display")
                or display_substrate.get("can_provision_virtual_display")
            ),
            "virtual_display_provisioning_state": str(
                continuity.get("virtual_display_provisioning_state")
                or display_substrate.get("virtual_display_provisioning_state")
                or "unknown"
            ),
            "capture_continuity": continuity,
            "display_substrate": display_substrate,
            "remote_desktop_readiness": readiness,
        }

    def _restart_capture_helper_after_substrate_change(
        self,
        descriptor: SessionDescriptor | None,
        *,
        reason: str,
    ) -> dict:
        target = (
            self.get_preferred_capture_host_session()
            or self.get_capture_helper_host_session()
            or descriptor
        )
        if target is None:
            return {
                "attempted": False,
                "reason": reason,
                "error": "no_capture_helper_target",
            }
        try:
            result = self.restart_capture_helper(target.session_id, wait_seconds=3.0)
        except Exception as exc:
            return {
                "attempted": True,
                "reason": reason,
                "session": target.to_dict(),
                "error": str(exc),
            }
        result["attempted"] = True
        result["reason"] = reason
        return result

    def _refresh_display_substrate_after_virtual_display_change(
        self,
        *,
        reason: str,
        status: dict | None,
        descriptor: SessionDescriptor | None,
    ) -> dict:
        result: dict = {
            "reason": reason,
            "hints_updated": False,
            "inventory_refreshed": False,
            "diagnostics_refreshed": False,
        }
        try:
            if isinstance(status, dict):
                result["hints_updated"] = bool(
                    self._display_presence_probe.update_virtual_display_hints(status)
                )
            inventory = self._display_presence_probe.get_display_inventory(force_refresh=True)
            diagnostics = self._build_capture_recovery_diagnostics(descriptor)
            result.update(
                {
                    "inventory_refreshed": True,
                    "diagnostics_refreshed": True,
                    "display_inventory": inventory,
                    "diagnostics": diagnostics,
                }
            )
            self.bridge.log_runtime_event(
                "VirtualDisplay",
                "display substrate inventory refreshed after virtual display change: "
                f"reason={reason} "
                f"physical={1 if inventory.get('physical_display_attached') else 0} "
                f"virtual={1 if inventory.get('virtual_display_attached') else 0} "
                f"attached={inventory.get('attached_display_count') or 0} "
                f"render={inventory.get('render_monitor_count') or 0}",
            )
        except Exception as exc:
            result["error"] = str(exc)
            self.bridge.log_runtime_event(
                "VirtualDisplay",
                "display substrate refresh after virtual display change failed: "
                f"reason={reason} error={exc}",
            )
        return result

    def _ensure_persistent_display_substrate_for_capture(
        self,
        *,
        reason: str,
        descriptor: SessionDescriptor | None,
        helper_response: dict | None = None,
        cooldown_seconds: float = 15.0,
    ) -> dict:
        helper_response = helper_response or {}
        display_presence = helper_response.get("display_presence") or {}
        substrate_class = str(display_presence.get("substrate_class") or "").strip().lower()
        helper_blocker = str(helper_response.get("blocker") or "").strip().lower()
        diagnostics = self._build_capture_recovery_diagnostics(descriptor)
        display_substrate = diagnostics.get("display_substrate") or {}
        active_substrate_class = str(
            display_substrate.get("active_capture_substrate_class") or ""
        ).lower()
        should_probe = bool(
            diagnostics.get("continuity_blocked_by_missing_substrate")
            or substrate_class in _NON_PERSISTENT_CAPTURE_SUBSTRATES
            or active_substrate_class in _NON_PERSISTENT_CAPTURE_SUBSTRATES
            or helper_blocker in _NON_PERSISTENT_CAPTURE_BLOCKERS
            or (
                bool(display_presence.get("best_effort_only", False))
                and not bool(display_presence.get("persistent", False))
            )
        )
        if not should_probe:
            return {
                "attempted": False,
                "queried": False,
                "recovered": False,
                "changed": False,
                "reason": reason,
                "blocker": "persistent_display_substrate_already_available",
                "diagnostics": diagnostics,
            }

        try:
            status = self.get_virtual_display_status(force_refresh=True)
        except Exception as exc:
            self.bridge.log_runtime_event(
                "VirtualDisplay",
                "capture-triggered virtual display status query failed: "
                f"reason={reason} error={exc}",
            )
            return {
                "attempted": False,
                "queried": True,
                "recovered": False,
                "changed": False,
                "reason": reason,
                "blocker": "virtual_display_status_query_failed",
                "error": str(exc),
                "diagnostics": diagnostics,
            }

        signature = self._display_substrate_recovery_signature(diagnostics, status)
        now = time.time()
        with self._state_lock:
            last_signature = self._last_persistent_substrate_recovery_signature
            last_at = self._last_persistent_substrate_recovery_at
            if signature == last_signature and now - last_at < max(1.0, float(cooldown_seconds)):
                return {
                    "attempted": False,
                    "queried": True,
                    "recovered": False,
                    "changed": False,
                    "rate_limited": True,
                    "retry_after_seconds": round(max(0.0, float(cooldown_seconds) - (now - last_at)), 2),
                    "reason": reason,
                    "blocker": "recovery_cooldown_active",
                    "virtual_display_status": status,
                    "diagnostics": diagnostics,
                }
            self._last_persistent_substrate_recovery_signature = signature
            self._last_persistent_substrate_recovery_at = now

        provisioning_state = str(
            status.get("provisioning_state")
            or diagnostics.get("virtual_display_provisioning_state")
            or "unknown"
        )
        if bool(status.get("attached_virtual_display")) or provisioning_state == "attached":
            self.bridge.log_runtime_event(
                "VirtualDisplay",
                "capture-triggered virtual display already attached: "
                f"reason={reason} state={provisioning_state}",
            )
            refresh_result = self._refresh_display_substrate_after_virtual_display_change(
                reason=f"display_substrate_attached:{reason}",
                status=status,
                descriptor=descriptor,
            )
            refreshed_diagnostics = refresh_result.get("diagnostics") or diagnostics
            self._clear_active_capture_host(f"display_substrate_attached:{reason}")
            restart_result = self._restart_capture_helper_after_substrate_change(
                descriptor,
                reason=f"display_substrate_attached:{reason}",
            )
            return {
                "attempted": True,
                "queried": True,
                "recovered": True,
                "changed": False,
                "reason": reason,
                "virtual_display_status": status,
                "display_substrate_refresh": refresh_result,
                "restart_capture_helper": restart_result,
                "diagnostics": refreshed_diagnostics,
            }

        can_provision = bool(
            status.get("can_provision_virtual_display")
            or diagnostics.get("can_provision_virtual_display")
            or display_substrate.get("can_provision_virtual_display")
        )
        if not can_provision:
            self.bridge.log_runtime_event(
                "VirtualDisplay",
                "capture-triggered display substrate recovery blocked: "
                f"reason={reason} state={provisioning_state} "
                f"package_complete={status.get('driver_package_complete')} "
                f"package_root={status.get('package_root') or ''} "
                f"tools={status.get('available_tools') or {}}",
            )
            return {
                "attempted": False,
                "queried": True,
                "recovered": False,
                "changed": False,
                "reason": reason,
                "blocker": provisioning_state,
                "virtual_display_status": status,
                "diagnostics": diagnostics,
            }

        ensure_status: dict = {}
        repair_status: dict = {}
        try:
            ensure_status = self.ensure_virtual_display()
        except Exception as exc:
            self.bridge.log_runtime_event(
                "VirtualDisplay",
                "capture-triggered ensure_virtual_display failed: "
                f"reason={reason} state={provisioning_state} error={exc}",
            )
            return {
                "attempted": True,
                "queried": True,
                "recovered": False,
                "changed": False,
                "reason": reason,
                "blocker": "ensure_virtual_display_failed",
                "error": str(exc),
                "virtual_display_status": status,
                "diagnostics": diagnostics,
            }

        ensure_state = str(ensure_status.get("provisioning_state") or provisioning_state)
        ensure_attached = bool(
            ensure_status.get("attached_virtual_display")
            or ensure_state == "attached"
        )
        if not ensure_attached and ensure_state in self._virtual_display_repairable_states():
            try:
                repair_status = self.repair_virtual_display()
            except Exception as exc:
                self.bridge.log_runtime_event(
                    "VirtualDisplay",
                    "capture-triggered repair_virtual_display failed: "
                    f"reason={reason} state={ensure_state} error={exc}",
                )
            else:
                ensure_state = str(repair_status.get("provisioning_state") or ensure_state)
                ensure_attached = bool(
                    repair_status.get("attached_virtual_display")
                    or ensure_state == "attached"
                )

        final_status = repair_status or ensure_status or status
        refresh_result = self._refresh_display_substrate_after_virtual_display_change(
            reason=f"display_substrate_recovery:{reason}",
            status=final_status,
            descriptor=descriptor,
        )
        refreshed_diagnostics = refresh_result.get("diagnostics") or diagnostics
        refreshed_substrate = refreshed_diagnostics.get("display_substrate") or {}
        refreshed_continuity = refreshed_diagnostics.get("capture_continuity") or {}
        changed = bool(
            ensure_status.get("changed")
            or repair_status.get("changed")
            or refresh_result.get("hints_updated")
        )
        recovered = bool(
            ensure_attached
            or final_status.get("attached_virtual_display")
            or str(final_status.get("provisioning_state") or "").lower() == "attached"
            or (
                refreshed_substrate.get("persistent_available")
                and not refreshed_continuity.get("continuity_blocked_by_missing_substrate")
            )
        )
        self.bridge.log_runtime_event(
            "VirtualDisplay",
            "capture-triggered display substrate recovery result: "
            f"reason={reason} changed={changed} recovered={recovered} "
            f"ensure_state={ensure_status.get('provisioning_state') or 'unknown'} "
            f"repair_state={repair_status.get('provisioning_state') or 'none'}",
        )

        restart_result: dict = {}
        if changed or recovered:
            self._clear_active_capture_host(f"display_substrate_recovery:{reason}")
            restart_result = self._restart_capture_helper_after_substrate_change(
                descriptor,
                reason=f"display_substrate_recovery:{reason}",
            )

        return {
            "attempted": True,
            "queried": True,
            "recovered": recovered,
            "changed": changed,
            "reason": reason,
            "virtual_display_status": final_status,
            "ensure_status": ensure_status,
            "repair_status": repair_status,
            "display_substrate_refresh": refresh_result,
            "restart_capture_helper": restart_result,
            "diagnostics": refreshed_diagnostics,
        }

    def _get_tscon_path(self) -> str:
        system_root = str(os.environ.get("SystemRoot") or r"C:\Windows").strip() or r"C:\Windows"
        candidate = os.path.join(system_root, "System32", "tscon.exe")
        if os.path.isfile(candidate):
            return candidate
        return ""

    def _should_rate_limit_console_handoff(
        self,
        signature: str,
        *,
        cooldown_seconds: float = 20.0,
    ) -> tuple[bool, float]:
        now = time.time()
        with self._state_lock:
            last_at = float(self._last_console_handoff_at or 0.0)
            last_signature = str(self._last_console_handoff_signature or "")
        elapsed = now - last_at
        if signature and signature == last_signature and elapsed < max(1.0, float(cooldown_seconds)):
            return True, max(0.0, float(cooldown_seconds) - elapsed)
        return False, 0.0

    def _build_console_handoff_signature(
        self,
        descriptor: SessionDescriptor | None,
        *,
        reason: str = "",
        continuity_policy: dict | None = None,
        display_substrate: dict | None = None,
    ) -> str:
        descriptor_signature = self._descriptor_topology_signature(descriptor)
        continuity = continuity_policy or {}
        substrate = display_substrate or {}
        return "|".join(
            [
                descriptor_signature,
                f"reason={self._normalize_signature_value(reason)}",
                f"mode={self._normalize_signature_value(continuity.get('continuity_mode'))}",
                f"blocked={1 if continuity.get('continuity_blocked_by_missing_substrate') else 0}",
                f"provider={self._normalize_signature_value(substrate.get('provider_state'))}",
            ]
        )

    def _can_attempt_console_handoff(
        self,
        descriptor: SessionDescriptor | None,
        *,
        continuity_policy: dict | None = None,
        display_substrate: dict | None = None,
    ) -> tuple[bool, str]:
        if descriptor is None:
            return False, "no_session_descriptor"
        if descriptor.is_console_session:
            return False, "session_already_console"
        if descriptor.is_disconnected and not descriptor.is_remote_session:
            return False, "non_remote_disconnected_session"
        if not (descriptor.is_remote_session or descriptor.is_connected or descriptor.is_active):
            return False, "session_not_remote_or_interactive"
        if not self._get_tscon_path():
            return False, "tscon_binary_not_found"

        continuity = continuity_policy or {}
        substrate = display_substrate or {}
        if continuity.get("continuity_blocked_by_missing_substrate") or substrate.get(
            "continuity_blocked_by_missing_substrate"
        ):
            return False, "continuity_blocked_by_missing_persistent_surface"
        return True, ""

    def _maybe_handoff_capture_host_to_console(
        self,
        descriptor: SessionDescriptor | None,
        *,
        reason: str,
        continuity_policy: dict | None = None,
        display_substrate: dict | None = None,
        cooldown_seconds: float = 20.0,
        wait_seconds: float = 4.0,
    ) -> dict:
        signature = self._build_console_handoff_signature(
            descriptor,
            reason=reason,
            continuity_policy=continuity_policy,
            display_substrate=display_substrate,
        )
        rate_limited, retry_after = self._should_rate_limit_console_handoff(
            signature,
            cooldown_seconds=cooldown_seconds,
        )
        if rate_limited:
            return {
                "attempted": False,
                "success": False,
                "rate_limited": True,
                "retry_after_seconds": round(retry_after, 2),
                "reason": reason,
                "signature": signature,
            }
        return self.handoff_session_to_console(
            descriptor.session_id if descriptor is not None else None,
            reason=reason,
            wait_seconds=wait_seconds,
            continuity_policy=continuity_policy,
            display_substrate=display_substrate,
            signature=signature,
        )

    def handoff_session_to_console(
        self,
        session_id: int | None = None,
        *,
        reason: str = "",
        wait_seconds: float = 4.0,
        continuity_policy: dict | None = None,
        display_substrate: dict | None = None,
        signature: str | None = None,
    ) -> dict:
        requested_session_id = int(session_id) if session_id is not None else None
        topology_before = self.describe_session_topology()
        resolved_descriptor = (
            self._get_session_descriptor_by_id(requested_session_id)
            if requested_session_id is not None
            else self.get_primary_remote_host_session()
        )
        if resolved_descriptor is None:
            resolved_descriptor = self.get_preferred_capture_host_session() or self.get_primary_remote_host_session()
        if resolved_descriptor is None:
            return {
                "requested_session_id": requested_session_id,
                "attempted": False,
                "success": False,
                "reason": reason,
                "failure_reason": "no_target_session",
                "topology_before": topology_before,
            }

        continuity = continuity_policy or (topology_before.get("capture_continuity") or {})
        substrate = display_substrate or (topology_before.get("display_substrate") or {})
        allowed, failure_reason = self._can_attempt_console_handoff(
            resolved_descriptor,
            continuity_policy=continuity,
            display_substrate=substrate,
        )
        result: dict[str, object] = {
            "requested_session_id": requested_session_id,
            "resolved_session": resolved_descriptor.to_dict(),
            "attempted": False,
            "success": False,
            "reason": reason,
            "failure_reason": failure_reason,
            "topology_before": topology_before,
        }
        if not allowed:
            return result

        handoff_signature = signature or self._build_console_handoff_signature(
            resolved_descriptor,
            reason=reason,
            continuity_policy=continuity,
            display_substrate=substrate,
        )
        rate_limited, retry_after = self._should_rate_limit_console_handoff(
            handoff_signature,
            cooldown_seconds=max(5.0, float(wait_seconds or 4.0) * 3.0),
        )
        if rate_limited:
            result["rate_limited"] = True
            result["retry_after_seconds"] = round(retry_after, 2)
            result["failure_reason"] = "cooldown_active"
            return result

        tscon_path = self._get_tscon_path()
        command = [tscon_path, str(int(resolved_descriptor.session_id)), "/dest:console"]
        self.bridge.log_runtime_event(
            "ServiceRuntime",
            "attempting session handoff to console: "
            f"session={resolved_descriptor.session_id} "
            f"label={resolved_descriptor.label or 'unknown'} "
            f"reason={reason or 'unspecified'}",
        )
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=max(5.0, float(wait_seconds or 4.0) + 5.0),
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            check=False,
        )
        stdout_text = str(completed.stdout or "").strip()
        stderr_text = str(completed.stderr or "").strip()
        if wait_seconds > 0:
            time.sleep(min(max(0.5, float(wait_seconds)), 8.0))

        topology_after = self.describe_session_topology()
        refreshed_descriptor = self._get_session_descriptor_by_id(resolved_descriptor.session_id)
        success = bool(completed.returncode == 0)
        if refreshed_descriptor is not None and refreshed_descriptor.is_console_session:
            success = True
        result.update(
            {
                "attempted": True,
                "success": success,
                "returncode": int(completed.returncode),
                "stdout": stdout_text,
                "stderr": stderr_text,
                "topology_after": topology_after,
                "refreshed_session": refreshed_descriptor.to_dict()
                if refreshed_descriptor is not None
                else None,
                "signature": handoff_signature,
            }
        )
        with self._state_lock:
            self._last_console_handoff_at = time.time()
            self._last_console_handoff_signature = handoff_signature
            self._last_console_handoff_result = dict(result)
        if success:
            self._clear_active_capture_host("console_handoff_success")
            self.bridge.log_runtime_event(
                "ServiceRuntime",
                "session handoff to console completed: "
                f"session={resolved_descriptor.session_id} "
                f"returncode={completed.returncode} "
                f"stdout={stdout_text or 'none'} "
                f"stderr={stderr_text or 'none'}",
            )
        else:
            self.bridge.log_runtime_event(
                "ServiceRuntime",
                "session handoff to console failed: "
                f"session={resolved_descriptor.session_id} "
                f"returncode={completed.returncode} "
                f"stdout={stdout_text or 'none'} "
                f"stderr={stderr_text or 'none'}",
            )
        return result

    def _invoke_helper_command(
        self,
        session_id: int,
        command: str,
        payload: dict | None = None,
        allow_restart: bool = True,
        ensure_helper: bool = True,
        helper_role: str = "input",
    ) -> dict:
        from Common.runtime_paths import get_high_integrity_helper_pipe_name
        from IPC.named_pipe import NamedPipeCommandClient

        normalized_helper_role = str(helper_role or "input").strip().lower()
        if ensure_helper:
            if normalized_helper_role == "capture":
                ensure_result = self.ensure_capture_helper(session_id)
            else:
                ensure_result = self.ensure_high_integrity_helper(session_id)
            self.bridge.log_runtime_event(
                "ServiceRuntime",
                "session helper ensure: "
                f"role={normalized_helper_role} "
                f"session={session_id} already_active={ensure_result.get('already_active')} "
                f"started={ensure_result.get('started')}",
            )
        else:
            self.bridge.log_runtime_event(
                "ServiceRuntime",
                "session helper ensure skipped: "
                f"role={normalized_helper_role} session={session_id} command={command}",
            )
        client = NamedPipeCommandClient(get_high_integrity_helper_pipe_name(session_id))
        try:
            response = client.request(
                {
                    "command": command,
                    "payload": payload or {},
                },
                timeout_seconds=5.0,
            )
        except Exception as exc:
            if allow_restart:
                self.bridge.log_runtime_event(
                    "ServiceRuntime",
                    "helper command failed, restarting helper: "
                    f"role={normalized_helper_role} session={session_id} command={command} error={exc}",
                )
                if normalized_helper_role == "capture":
                    self.restart_capture_helper(session_id, wait_seconds=2.0)
                else:
                    self.restart_high_integrity_helper(session_id, wait_seconds=2.0)
                return self._invoke_helper_command(
                    session_id,
                    command,
                    payload,
                    allow_restart=False,
                    ensure_helper=True,
                    helper_role=normalized_helper_role,
                )
            raise

        if not response.get("ok"):
            raise RuntimeError(str(response.get("error") or f"helper command failed: {command}"))
        return response.get("payload") or {}

    def _get_active_console_session_id(self) -> int | None:
        apis = self._setup_wts_apis()
        if apis is None:
            return None
        try:
            kernel32, _ = apis
            session_id = int(kernel32.WTSGetActiveConsoleSessionId())
        except Exception:
            return None

        if session_id in (-1, 0xFFFFFFFF):
            return None
        return session_id

    def _build_session_descriptor(self, session_id: int, console_preferred: bool) -> SessionDescriptor:
        snapshot = self._get_session_snapshot_by_id(int(session_id))
        if snapshot is not None:
            descriptor = snapshot.to_descriptor()
            descriptor.is_console_preferred = bool(console_preferred or descriptor.is_console_session)
            return descriptor

        label = self._safe_debug_label(int(session_id))
        return SessionDescriptor(
            session_id=int(session_id),
            label=label,
            identity=label,
            is_console_preferred=bool(console_preferred),
            station_name="",
            state="Unknown",
            is_remote_session=False,
            is_active=False,
            is_connected=False,
            is_disconnected=False,
            is_console_session=bool(console_preferred),
        )

    def _next_capture_binding_generation(self) -> int:
        with self._state_lock:
            self._capture_binding_generation = int(self._capture_binding_generation) + 1
            return int(self._capture_binding_generation)

    def _build_authoritative_capture_binding_policy(
        self,
        descriptor: SessionDescriptor,
        *,
        requested_session_id: int | None = None,
        descriptors: list[SessionDescriptor] | None = None,
    ) -> dict[str, object]:
        interactive_descriptors = descriptors if descriptors is not None else self.list_interactive_sessions()
        primary_descriptor = self._find_best_primary_remote_descriptor(interactive_descriptors)
        assessment = self._assess_capture_surface(descriptor)
        substrate_class = str(assessment.substrate_class or "").strip().lower()
        allow_secure_desktop = False
        allow_screensaver_desktop = False
        preferred_desktops: list[str] = []
        preferred_kind = ""
        transition_reason = "interactive_capture_binding"

        if descriptor.is_disconnected or substrate_class == "disconnected_surface":
            preferred_desktops = ["Disconnect", "Winlogon", "Default"]
            preferred_kind = "disconnected_shell"
            allow_secure_desktop = True
            transition_reason = "capture_binding_disconnected_session"
        elif substrate_class == "secure_console_surface":
            preferred_desktops = ["Winlogon", "Screen-Saver", "Default"]
            preferred_kind = "secure_winlogon"
            allow_secure_desktop = True
            allow_screensaver_desktop = True
            transition_reason = "capture_binding_secure_console_surface"
        elif descriptor.is_console_session and self._descriptor_identity_matches(descriptor, primary_descriptor):
            preferred_desktops = ["Default", "Screen-Saver", "Winlogon", "Disconnect"]
            preferred_kind = "interactive_default"
            allow_secure_desktop = True
            allow_screensaver_desktop = True
            transition_reason = "capture_binding_console_affine_persistent"
        elif descriptor.is_console_session:
            preferred_desktops = ["Default", "Screen-Saver", "Winlogon", "Disconnect"]
            preferred_kind = "interactive_default"
            allow_secure_desktop = True
            allow_screensaver_desktop = True
            transition_reason = "capture_binding_console_persistent"
        elif substrate_class == "remote_session_surface":
            preferred_desktops = ["Default", "Disconnect", "Winlogon"]
            preferred_kind = "interactive_default"
            allow_secure_desktop = True
            transition_reason = "capture_binding_remote_session_best_effort"
        else:
            preferred_desktops = ["Default", "Screen-Saver", "Winlogon", "Disconnect"]
            preferred_kind = "interactive_default"
            allow_secure_desktop = True
            allow_screensaver_desktop = True
            transition_reason = "capture_binding_interactive_fallback"

        continuity_policy = self._build_capture_continuity_policy(
            interactive_descriptors,
            primary_descriptor=primary_descriptor,
            preferred_capture_descriptor=descriptor,
            active_capture_descriptor=self._get_active_capture_descriptor(),
        )
        continuity_mode = str(continuity_policy.get("continuity_mode") or "").strip()
        descriptor_signature = self._descriptor_topology_signature(descriptor)
        policy_signature = "|".join(
            [
                f"session={int(descriptor.session_id)}",
                f"requested={requested_session_id if requested_session_id is not None else 'none'}",
                f"desktops={','.join(preferred_desktops) or 'none'}",
                f"preferred_kind={preferred_kind or 'unknown'}",
                f"allow_secure={1 if allow_secure_desktop else 0}",
                f"allow_screensaver={1 if allow_screensaver_desktop else 0}",
                f"reason={transition_reason or 'unknown'}",
                f"continuity={continuity_mode or 'unknown'}",
                f"substrate={substrate_class or 'unknown'}",
                f"descriptor={descriptor_signature or 'unknown'}",
            ]
        )
        return {
            "preferred_capture_desktops": preferred_desktops,
            "preferred_capture_desktop_kind": preferred_kind,
            "desktop_transition_reason": transition_reason,
            "allow_secure_desktop": bool(allow_secure_desktop),
            "allow_screensaver_desktop": bool(allow_screensaver_desktop),
            "authoritative_capture_binding": True,
            "capture_binding_generation": self._next_capture_binding_generation(),
            "capture_binding_policy_signature": policy_signature,
        }

    def _decorate_capture_descriptor_with_binding_policy(
        self,
        descriptor: SessionDescriptor,
        *,
        requested_session_id: int | None = None,
        descriptors: list[SessionDescriptor] | None = None,
    ) -> SessionDescriptor:
        payload = descriptor.to_dict()
        payload.update(
            self._build_authoritative_capture_binding_policy(
                descriptor,
                requested_session_id=requested_session_id,
                descriptors=descriptors,
            )
        )
        return SessionDescriptor(**payload)

    def _get_session_descriptor_by_id(self, session_id: int) -> SessionDescriptor | None:
        for descriptor in self.list_interactive_sessions():
            if descriptor.session_id == int(session_id):
                return descriptor

        active_console_session_id = self._get_active_console_session_id()
        if active_console_session_id is not None and int(session_id) == active_console_session_id:
            return self._build_session_descriptor(active_console_session_id, console_preferred=True)

        snapshot = self._get_session_snapshot_by_id(int(session_id))
        if snapshot is not None:
            return snapshot.to_descriptor()
        return None

    def _get_session_snapshot_by_id(self, session_id: int) -> _SessionRuntimeSnapshot | None:
        for snapshot in self._list_session_runtime_snapshots():
            if snapshot.session_id == int(session_id):
                return snapshot
        return None

    def _list_session_runtime_snapshots(self) -> list[_SessionRuntimeSnapshot]:
        apis = self._setup_wts_apis()
        if apis is None:
            return []

        _, wtsapi32 = apis
        session_pointer = ctypes.POINTER(_WTSSessionInfo)()
        session_count = wintypes.DWORD(0)
        active_console_session_id = self._get_active_console_session_id()
        snapshots: list[_SessionRuntimeSnapshot] = []

        try:
            success = wtsapi32.WTSEnumerateSessionsW(
                WTS_CURRENT_SERVER_HANDLE,
                0,
                1,
                ctypes.byref(session_pointer),
                ctypes.byref(session_count),
            )
            if not success:
                return []

            for index in range(int(session_count.value)):
                session_info = session_pointer[index]
                session_id = int(session_info.SessionId)
                station_name = str(session_info.pWinStationName or "").strip()
                if station_name.lower() == "services":
                    continue

                state_code = int(session_info.State)
                state_name = _WTS_STATE_NAMES.get(state_code, f"Unknown({state_code})")
                identity = self._query_session_identity(wtsapi32, session_id)
                is_active = state_code == WTS_ACTIVE
                is_connected = state_code == WTS_CONNECTED
                is_disconnected = state_code == WTS_DISCONNECTED
                is_console_session = (
                    active_console_session_id is not None
                    and session_id == active_console_session_id
                )
                is_remote_session = self._is_remote_session_station(
                    station_name,
                    session_id=session_id,
                    active_console_session_id=active_console_session_id,
                )
                if not identity:
                    identity = ""
                snapshots.append(
                    _SessionRuntimeSnapshot(
                        session_id=session_id,
                        label=self._safe_debug_label(session_id),
                        identity=identity,
                        station_name=station_name,
                        state=state_name,
                        is_remote_session=is_remote_session,
                        is_active=is_active,
                        is_connected=is_connected,
                        is_disconnected=is_disconnected,
                        is_console_session=is_console_session,
                    )
                )
        except Exception as exc:
            self.bridge.log_runtime_event("ServiceRuntime", f"session enumeration failed: {exc}")
            return []
        finally:
            if session_pointer:
                with contextlib.suppress(Exception):
                    wtsapi32.WTSFreeMemory(session_pointer)

        return snapshots

    def _setup_wts_apis(self):
        if ctypes is None:
            return None

        try:
            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            wtsapi32 = ctypes.WinDLL("wtsapi32", use_last_error=True)
            kernel32.WTSGetActiveConsoleSessionId.restype = wintypes.DWORD
            wtsapi32.WTSEnumerateSessionsW.argtypes = [
                wintypes.HANDLE,
                wintypes.DWORD,
                wintypes.DWORD,
                ctypes.POINTER(ctypes.POINTER(_WTSSessionInfo)),
                ctypes.POINTER(wintypes.DWORD),
            ]
            wtsapi32.WTSEnumerateSessionsW.restype = wintypes.BOOL
            wtsapi32.WTSQuerySessionInformationW.argtypes = [
                wintypes.HANDLE,
                wintypes.DWORD,
                wintypes.DWORD,
                ctypes.POINTER(wintypes.LPWSTR),
                ctypes.POINTER(wintypes.DWORD),
            ]
            wtsapi32.WTSQuerySessionInformationW.restype = wintypes.BOOL
            wtsapi32.WTSFreeMemory.argtypes = [wintypes.LPVOID]
            wtsapi32.WTSFreeMemory.restype = None
        except Exception:
            return None

        return kernel32, wtsapi32

    def _query_session_identity(self, wtsapi32, session_id: int) -> str:
        username = self._query_session_text(wtsapi32, session_id, WTS_USERNAME)
        if not username:
            return ""

        domain = self._query_session_text(wtsapi32, session_id, WTS_DOMAIN_NAME)
        if domain:
            return f"{domain}\\{username}"
        return username

    def _query_session_text(self, wtsapi32, session_id: int, info_class: int) -> str:
        buffer = wintypes.LPWSTR()
        bytes_returned = wintypes.DWORD(0)
        try:
            success = wtsapi32.WTSQuerySessionInformationW(
                WTS_CURRENT_SERVER_HANDLE,
                int(session_id),
                int(info_class),
                ctypes.byref(buffer),
                ctypes.byref(bytes_returned),
            )
            if not success or not buffer:
                return ""
            return str(buffer.value or "").strip()
        except Exception:
            return ""
        finally:
            if buffer:
                with contextlib.suppress(Exception):
                    wtsapi32.WTSFreeMemory(buffer)

    def _is_remote_session_station(
        self,
        station_name: str,
        session_id: int,
        active_console_session_id: int | None,
    ) -> bool:
        normalized_station = str(station_name or "").strip().lower()
        if not normalized_station:
            return active_console_session_id is not None and session_id != active_console_session_id
        if normalized_station == "console":
            return False
        if normalized_station.startswith("rdp-"):
            return True
        return active_console_session_id is not None and session_id != active_console_session_id

    def _session_priority_key(self, snapshot: _SessionRuntimeSnapshot) -> tuple[int, int, int]:
        return self._descriptor_priority_key(snapshot.to_descriptor())

    def _descriptor_priority_key(self, descriptor: SessionDescriptor) -> tuple[int, int, int]:
        if descriptor.is_active and descriptor.is_remote_session:
            tier = 0
        elif descriptor.is_connected and descriptor.is_remote_session:
            tier = 1
        elif descriptor.is_active and descriptor.is_console_session:
            tier = 2
        elif descriptor.is_connected and descriptor.is_console_session:
            tier = 3
        elif descriptor.is_active:
            tier = 4
        elif descriptor.is_connected:
            tier = 5
        elif descriptor.is_disconnected and descriptor.is_console_session:
            tier = 6
        elif descriptor.is_disconnected and descriptor.is_remote_session:
            tier = 7
        elif descriptor.is_disconnected:
            tier = 8
        elif descriptor.is_console_session:
            tier = 9
        else:
            tier = 10
        return (tier, 0 if descriptor.identity else 1, int(descriptor.session_id))

    def _capture_descriptor_priority_key(self, descriptor: SessionDescriptor) -> tuple[int, int, int]:
        tier = self._descriptor_capture_persistence_rank(descriptor)
        return (tier, 0 if descriptor.identity else 1, int(descriptor.session_id))

    def _select_preferred_capture_descriptor(
        self,
        descriptors: list[SessionDescriptor],
    ) -> SessionDescriptor | None:
        if not descriptors:
            return None
        # 无用户identity的session（如RDP-Tcp监听session 65536）不能做capture host
        eligible = [d for d in descriptors if d.identity]
        if not eligible:
            eligible = descriptors
        primary_descriptor = self._find_best_primary_remote_descriptor(eligible)
        active_descriptor = self._get_active_capture_descriptor()
        console_affine_descriptor = self._find_console_affine_descriptor(primary_descriptor, eligible)
        best_console_descriptor = self._find_best_console_descriptor(eligible)

        if (
            console_affine_descriptor is not None
            and self._descriptor_has_persistent_capture_surface(console_affine_descriptor)
        ):
            return console_affine_descriptor
        if (
            best_console_descriptor is not None
            and self._descriptor_has_persistent_capture_surface(best_console_descriptor)
        ):
            return best_console_descriptor
        if (
            active_descriptor is not None
            and self._descriptor_has_persistent_capture_surface(active_descriptor)
        ):
            return active_descriptor
        return min(
            eligible,
            key=lambda item: self._capture_descriptor_selection_key(
                item,
                primary_descriptor=primary_descriptor,
            ),
        )

    def _safe_debug_label(self, session_id: int) -> str:
        try:
            label = str(self.bridge.get_user_session_debug_label(int(session_id)) or "").strip()
        except Exception:
            label = ""
        return label or f"session={int(session_id)}"

    def _get_active_capture_descriptor(self) -> SessionDescriptor | None:
        with self._state_lock:
            session_id = self._active_capture_session_id
        if session_id is None:
            return None
        descriptor = self._get_session_descriptor_by_id(session_id)
        if descriptor is None:
            self._clear_active_capture_host("capture_session_disappeared")
        return descriptor

    def _remember_active_capture_host(
        self,
        descriptor: SessionDescriptor,
        backend: str,
        *,
        reason: str,
        desktop_signature: str = "",
        desktop_kind: str = "",
    ) -> None:
        normalized_backend = str(backend or "").strip()
        normalized_desktop_signature = str(desktop_signature or "").strip()
        normalized_desktop_kind = str(desktop_kind or "").strip()
        descriptor_signature = self._descriptor_topology_signature(descriptor)
        with self._state_lock:
            previous_session_id = self._active_capture_session_id
            previous_backend = self._active_capture_backend
            previous_desktop_signature = self._active_capture_desktop_signature
            previous_desktop_kind = self._active_capture_desktop_kind
            previous_descriptor_signature = self._active_capture_host_descriptor_signature
            self._active_capture_session_id = int(descriptor.session_id)
            self._active_capture_backend = normalized_backend
            self._active_capture_desktop_signature = normalized_desktop_signature
            self._active_capture_desktop_kind = normalized_desktop_kind
            self._active_capture_host_descriptor_signature = descriptor_signature

        if (
            previous_session_id == descriptor.session_id
            and previous_backend == normalized_backend
            and previous_desktop_signature == normalized_desktop_signature
            and previous_desktop_kind == normalized_desktop_kind
            and previous_descriptor_signature == descriptor_signature
        ):
            return

        self.bridge.log_runtime_event(
            "ServiceRuntime",
            "capture host switched: "
            f"reason={reason} session={descriptor.session_id} "
            f"station={descriptor.station_name or 'unknown'} state={descriptor.state or 'unknown'} "
            f"remote={descriptor.is_remote_session} console={descriptor.is_console_session} "
            f"backend={normalized_backend or 'unknown'} "
            f"desktop_kind={normalized_desktop_kind or 'unknown'} "
            f"desktop_signature={normalized_desktop_signature or 'unknown'} "
            f"descriptor_signature={descriptor_signature or 'unknown'}",
        )

    def _clear_active_capture_host(self, reason: str) -> None:
        with self._state_lock:
            previous_session_id = self._active_capture_session_id
            previous_backend = self._active_capture_backend
            previous_desktop_signature = self._active_capture_desktop_signature
            previous_desktop_kind = self._active_capture_desktop_kind
            previous_descriptor_signature = self._active_capture_host_descriptor_signature
            self._active_capture_session_id = None
            self._active_capture_backend = ""
            self._active_capture_desktop_signature = ""
            self._active_capture_desktop_kind = ""
            self._active_capture_host_descriptor_signature = ""

        if (
            previous_session_id is None
            and not previous_backend
            and not previous_desktop_signature
            and not previous_desktop_kind
            and not previous_descriptor_signature
        ):
            return

        self.bridge.log_runtime_event(
            "ServiceRuntime",
            "capture host cleared: "
            f"reason={reason} previous_session={previous_session_id} "
            f"previous_backend={previous_backend or 'unknown'} "
            f"previous_desktop_kind={previous_desktop_kind or 'unknown'} "
            f"previous_desktop_signature={previous_desktop_signature or 'unknown'} "
            f"previous_descriptor_signature={previous_descriptor_signature or 'unknown'}",
        )

    def _get_active_capture_host_descriptor_signature(self) -> str:
        with self._state_lock:
            return str(self._active_capture_host_descriptor_signature or "")

    def _build_capture_candidate_descriptors(self, requested_session_id: int | None) -> list[SessionDescriptor]:
        seen_session_ids: set[int] = set()
        candidates: list[SessionDescriptor] = []
        descriptors = self.list_interactive_sessions()
        primary_descriptor = self._find_best_primary_remote_descriptor(descriptors)
        preferred_capture_descriptor = self._select_preferred_capture_descriptor(descriptors)
        active_capture_descriptor = self._get_active_capture_descriptor()
        console_affine_descriptor = self._find_console_affine_descriptor(primary_descriptor, descriptors)
        best_console_descriptor = self._find_best_console_descriptor(descriptors)
        authoritative_descriptor = (
            preferred_capture_descriptor
            or console_affine_descriptor
            or active_capture_descriptor
            or best_console_descriptor
            or primary_descriptor
        )
        requested_descriptor = (
            self._get_session_descriptor_by_id(requested_session_id)
            if requested_session_id is not None
            else None
        )
        prioritized_requested_descriptor = self._resolve_capture_host_request_hint(
            requested_descriptor,
            authoritative_descriptor=authoritative_descriptor,
            primary_descriptor=primary_descriptor,
        )

        def add_candidate(descriptor: SessionDescriptor | None):
            if descriptor is None:
                return
            session_id = int(descriptor.session_id)
            if (
                preferred_capture_descriptor is not None
                and session_id != int(preferred_capture_descriptor.session_id)
                and self._descriptor_has_persistent_capture_surface(
                    preferred_capture_descriptor
                )
                and self._descriptor_is_non_persistent_capture_surface(descriptor)
            ):
                return
            if session_id in seen_session_ids:
                return
            seen_session_ids.add(session_id)
            candidates.append(descriptor)

        add_candidate(prioritized_requested_descriptor)
        add_candidate(authoritative_descriptor)
        add_candidate(preferred_capture_descriptor)
        add_candidate(console_affine_descriptor)
        add_candidate(active_capture_descriptor)
        add_candidate(best_console_descriptor)
        add_candidate(primary_descriptor)
        add_candidate(requested_descriptor)
        for descriptor in sorted(
            descriptors,
            key=lambda item: self._capture_descriptor_selection_key(
                item,
                primary_descriptor=primary_descriptor,
            ),
        ):
            add_candidate(descriptor)

        return candidates

    def _normalized_identity(self, value: str | None) -> str:
        return str(value or "").strip().lower()

    def _descriptor_identity_key(self, descriptor: SessionDescriptor | None) -> str:
        if descriptor is None:
            return ""
        return self._normalized_identity(descriptor.identity)

    def _descriptor_identity_matches(
        self,
        left: SessionDescriptor | None,
        right: SessionDescriptor | None,
    ) -> bool:
        left_key = self._descriptor_identity_key(left)
        right_key = self._descriptor_identity_key(right)
        return bool(left_key and right_key and left_key == right_key)

    def _descriptor_capture_persistence_class(self, descriptor: SessionDescriptor | None) -> str:
        if descriptor is None:
            return "none"
        return self._assess_capture_surface(descriptor).substrate_class

    def _descriptor_capture_persistence_rank(self, descriptor: SessionDescriptor) -> int:
        return self._assess_capture_surface(descriptor).rank_hint

    def _descriptor_has_persistent_capture_surface(self, descriptor: SessionDescriptor | None) -> bool:
        if descriptor is None:
            return False
        return bool(self._assess_capture_surface(descriptor).persistent)

    def _descriptor_is_transient_remote_surface(self, descriptor: SessionDescriptor | None) -> bool:
        if descriptor is None:
            return False
        assessment = self._assess_capture_surface(descriptor)
        if assessment.substrate_class != "remote_session_surface":
            return False
        # 禁用虚拟显示器时，RDP session 视为持久，不算 transient（避免 supervisor 反复 recycle）
        if assessment.persistent:
            return False
        return True

    def _descriptor_is_non_persistent_capture_surface(
        self,
        descriptor: SessionDescriptor | None,
    ) -> bool:
        return self._assessment_is_non_persistent_capture_surface(
            self._assess_capture_surface(descriptor)
        )

    def _assessment_is_non_persistent_capture_surface(
        self,
        assessment: DisplayPresenceAssessment | None,
    ) -> bool:
        if assessment is None:
            return False
        # persistent=True 的表面（如禁用虚拟显示器时的RDP会话）不算non-persistent，
        # 否则 supervisor 会反复 recycle helper 导致 capture_loop 中断。
        if assessment.persistent:
            return False
        substrate_class = str(assessment.substrate_class or "").strip().lower()
        if substrate_class in _NON_PERSISTENT_CAPTURE_SUBSTRATES:
            return True
        return bool(assessment.best_effort_only and not assessment.persistent)

    def _assess_capture_surface(
        self,
        descriptor: SessionDescriptor | None,
        *,
        desktop_state: dict | None = None,
    ) -> DisplayPresenceAssessment:
        return self._display_presence_probe.assess(descriptor, desktop_state=desktop_state)

    def _capture_descriptor_selection_key(
        self,
        descriptor: SessionDescriptor,
        *,
        primary_descriptor: SessionDescriptor | None,
    ) -> tuple[int, int, int, int, int]:
        same_identity = self._descriptor_identity_matches(descriptor, primary_descriptor)
        return (
            self._descriptor_capture_persistence_rank(descriptor),
            0 if (descriptor.is_console_session and same_identity) else 1,
            0 if same_identity else 1,
            0 if descriptor.identity else 1,
            int(descriptor.session_id),
        )

    def _resolve_capture_host_request_hint(
        self,
        requested_descriptor: SessionDescriptor | None,
        *,
        authoritative_descriptor: SessionDescriptor | None,
        primary_descriptor: SessionDescriptor | None,
    ) -> SessionDescriptor | None:
        if requested_descriptor is None:
            return authoritative_descriptor
        if authoritative_descriptor is None:
            return requested_descriptor
        if requested_descriptor.session_id == authoritative_descriptor.session_id:
            return requested_descriptor

        requested_rank = self._descriptor_capture_persistence_rank(requested_descriptor)
        authoritative_rank = self._descriptor_capture_persistence_rank(authoritative_descriptor)
        requested_is_non_persistent = self._descriptor_is_non_persistent_capture_surface(requested_descriptor)
        authoritative_is_persistent = self._descriptor_has_persistent_capture_surface(authoritative_descriptor)

        if requested_is_non_persistent and authoritative_is_persistent:
            return authoritative_descriptor
        if requested_descriptor.is_disconnected and not authoritative_descriptor.is_disconnected:
            return authoritative_descriptor
        if requested_rank > authoritative_rank:
            return authoritative_descriptor
        if (
            requested_rank == authoritative_rank
            and self._descriptor_identity_matches(authoritative_descriptor, primary_descriptor)
            and not self._descriptor_identity_matches(requested_descriptor, primary_descriptor)
        ):
            return authoritative_descriptor
        return requested_descriptor

    def _find_best_primary_remote_descriptor(
        self,
        descriptors: list[SessionDescriptor],
    ) -> SessionDescriptor | None:
        if not descriptors:
            return None
        return min(descriptors, key=self._descriptor_priority_key)

    def _find_best_console_descriptor(
        self,
        descriptors: list[SessionDescriptor],
        *,
        identity_key: str = "",
    ) -> SessionDescriptor | None:
        console_descriptors = [item for item in descriptors if item.is_console_session]
        if identity_key:
            keyed = [
                item
                for item in console_descriptors
                if self._descriptor_identity_key(item) == identity_key
            ]
            if keyed:
                console_descriptors = keyed
        if not console_descriptors:
            return None
        return min(console_descriptors, key=self._capture_descriptor_priority_key)

    def _find_console_affine_descriptor(
        self,
        anchor_descriptor: SessionDescriptor | None,
        descriptors: list[SessionDescriptor],
    ) -> SessionDescriptor | None:
        identity_key = self._descriptor_identity_key(anchor_descriptor)
        if not identity_key:
            return self._find_best_console_descriptor(descriptors)
        return self._find_best_console_descriptor(descriptors, identity_key=identity_key)

    def _build_capture_topology_diagnostics(
        self,
        descriptors: list[SessionDescriptor] | None = None,
        *,
        primary_descriptor: SessionDescriptor | None = None,
        preferred_capture_descriptor: SessionDescriptor | None = None,
    ) -> dict:
        interactive_descriptors = descriptors if descriptors is not None else self.list_interactive_sessions()
        primary = primary_descriptor or self._find_best_primary_remote_descriptor(interactive_descriptors)
        preferred_capture = preferred_capture_descriptor or self._select_preferred_capture_descriptor(
            interactive_descriptors
        )
        console_affine = self._find_console_affine_descriptor(primary, interactive_descriptors)
        best_console = self._find_best_console_descriptor(interactive_descriptors)
        display_inventory = self._display_presence_probe.get_display_inventory()
        preferred_assessment = self._assess_capture_surface(preferred_capture)
        display_substrate = self._build_display_substrate_diagnostics(
            preferred_capture_descriptor=preferred_capture,
            active_capture_descriptor=self._get_active_capture_descriptor(),
            preferred_assessment=preferred_assessment,
        )
        notes: list[str] = []
        if primary is None:
            notes.append("no_primary_remote_host_detected")
        if best_console is None:
            notes.append("no_console_session_detected")
        if preferred_capture is None:
            notes.append("no_capture_host_candidate_detected")
        elif self._descriptor_is_non_persistent_capture_surface(preferred_capture):
            notes.append("capture_host_fell_back_to_non_persistent_surface")
        if console_affine is not None and preferred_capture is not None:
            if console_affine.session_id == preferred_capture.session_id:
                notes.append("capture_host_console_affine_to_primary_identity")
            else:
                notes.append("console_affine_capture_candidate_available_but_not_selected")
        has_persistent_capture_substrate = bool(preferred_assessment.persistent)
        if not has_persistent_capture_substrate:
            notes.append("persistent_capture_substrate_not_available_in_current_topology")
        if preferred_assessment.requires_virtual_display_for_full_continuity:
            notes.append("virtual_display_required_for_full_continuity")
        if display_substrate.get("continuity_blocked_by_missing_substrate"):
            notes.append("continuity_blocked_by_missing_persistent_surface")

        if preferred_capture is None:
            strategy = "none"
        elif preferred_assessment.substrate_class in {
            "physical_console_surface",
            "virtual_display_surface",
            "secure_console_surface",
        }:
            strategy = "console_affine" if self._descriptor_identity_matches(preferred_capture, primary) else "console"
        elif self._assessment_is_non_persistent_capture_surface(preferred_assessment):
            strategy = "non_persistent_fallback"
        else:
            strategy = "interactive_fallback"

        return {
            "capture_strategy": strategy,
            "console_session_available": bool(best_console is not None),
            "console_affine_session": console_affine.to_dict() if console_affine is not None else None,
            "preferred_capture_persistence_class": self._descriptor_capture_persistence_class(preferred_capture),
            "preferred_capture_substrate_class": preferred_assessment.substrate_class,
            "has_persistent_capture_substrate": bool(has_persistent_capture_substrate),
            "capture_prefers_primary_identity_console": bool(
                preferred_capture is not None
                and preferred_assessment.substrate_class
                in {"physical_console_surface", "virtual_display_surface", "secure_console_surface"}
                and self._descriptor_identity_matches(preferred_capture, primary)
            ),
            "capture_host_on_transient_remote_surface": bool(
                self._descriptor_is_non_persistent_capture_surface(preferred_capture)
            ),
            "capture_host_on_non_persistent_surface": bool(
                self._descriptor_is_non_persistent_capture_surface(preferred_capture)
            ),
            "physical_display_attached": bool(display_inventory.get("physical_display_attached", False)),
            "virtual_display_attached": bool(display_inventory.get("virtual_display_attached", False)),
            "render_monitor_count": int(display_inventory.get("render_monitor_count") or 0),
            "attached_display_count": int(display_inventory.get("attached_display_count") or 0),
            "display_inventory": display_inventory,
            "display_substrate": display_substrate,
            "preferred_capture_surface_assessment": preferred_assessment.to_dict(),
            "requires_virtual_display_for_full_continuity": bool(
                preferred_assessment.requires_virtual_display_for_full_continuity
            ),
            "continuity_blocked_by_missing_substrate": bool(
                display_substrate.get("continuity_blocked_by_missing_substrate")
            ),
            "can_provision_virtual_display": bool(
                display_substrate.get("can_provision_virtual_display", False)
            ),
            "virtual_display_provisioning_state": str(
                display_substrate.get("virtual_display_provisioning_state")
                or "not_supported_in_current_build"
            ),
            "notes": notes,
        }

    def _build_capture_continuity_policy(
        self,
        descriptors: list[SessionDescriptor] | None = None,
        *,
        primary_descriptor: SessionDescriptor | None = None,
        preferred_capture_descriptor: SessionDescriptor | None = None,
        active_capture_descriptor: SessionDescriptor | None = None,
        input_helper_descriptor: SessionDescriptor | None = None,
        capture_helper_descriptor: SessionDescriptor | None = None,
    ) -> dict:
        interactive_descriptors = descriptors if descriptors is not None else self.list_interactive_sessions()
        primary = primary_descriptor or self._find_best_primary_remote_descriptor(interactive_descriptors)
        preferred_capture = preferred_capture_descriptor or self._select_preferred_capture_descriptor(
            interactive_descriptors
        )
        active_capture = active_capture_descriptor or self._get_active_capture_descriptor()
        input_helper = input_helper_descriptor or self._select_input_helper_target_descriptor()
        capture_helper = capture_helper_descriptor or self._select_capture_helper_target_descriptor()
        console_affine = self._find_console_affine_descriptor(primary, interactive_descriptors)
        best_console = self._find_best_console_descriptor(interactive_descriptors)
        preferred_assessment = self._assess_capture_surface(preferred_capture)
        active_assessment = self._assess_capture_surface(active_capture)
        display_substrate = self._build_display_substrate_diagnostics(
            preferred_capture_descriptor=preferred_capture,
            active_capture_descriptor=active_capture,
            preferred_assessment=preferred_assessment,
            active_assessment=active_assessment,
        )
        force_migration, migration_reason = self._should_force_capture_host_migration(
            active_capture,
            preferred_capture,
            primary_descriptor=primary,
            capture_helper_descriptor=capture_helper,
        )
        persistent_capture_substrate = bool(preferred_assessment.persistent)
        best_effort_only = bool(preferred_assessment.best_effort_only)
        notes: list[str] = []

        if preferred_capture is None:
            continuity_mode = "none"
        else:
            continuity_mode = preferred_assessment.continuity_mode

        if preferred_capture is not None and preferred_assessment.substrate_class in {
            "physical_console_surface",
            "virtual_display_surface",
            "secure_console_surface",
        }:
            if self._descriptor_identity_matches(preferred_capture, primary):
                continuity_mode = "console_affine_persistent"
            else:
                continuity_mode = "console_persistent"
        if display_substrate.get("continuity_blocked_by_missing_substrate"):
            continuity_mode = "blocked_missing_persistent_surface"
            best_effort_only = True

        if best_effort_only:
            notes.append("capture_continuity_best_effort_only")
        else:
            notes.append("capture_continuity_persistent_host_available")
        if console_affine is not None:
            notes.append("console_affine_capture_candidate_detected")
        if best_console is not None:
            notes.append("console_capture_candidate_detected")
        if preferred_capture is not None and self._descriptor_is_non_persistent_capture_surface(preferred_capture):
            notes.append("preferred_capture_host_is_non_persistent_surface")
        if active_capture is not None and self._descriptor_is_non_persistent_capture_surface(active_capture):
            notes.append("active_capture_host_is_non_persistent_surface")
        if preferred_assessment.requires_virtual_display_for_full_continuity:
            notes.append("virtual_display_required_for_full_continuity")
        if display_substrate.get("continuity_blocked_by_missing_substrate"):
            notes.append("continuity_blocked_by_missing_persistent_surface")
            notes.append("session_migration_alone_cannot_restore_rendering")
        if (
            input_helper is not None
            and capture_helper is not None
            and input_helper.session_id == capture_helper.session_id
        ):
            notes.append("input_helper_aligned_with_capture_helper")
        elif input_helper is not None and capture_helper is not None:
            notes.append("input_helper_diverges_from_capture_helper")
        if force_migration:
            notes.append("capture_host_migration_required")
            if migration_reason:
                notes.append(migration_reason)

        return {
            "continuity_mode": continuity_mode,
            "best_effort_only": bool(best_effort_only),
            "persistent_capture_substrate_detected": bool(persistent_capture_substrate),
            "preferred_capture_substrate_class": preferred_assessment.substrate_class,
            "active_capture_substrate_class": active_assessment.substrate_class,
            "console_affine_candidate_available": bool(console_affine is not None),
            "console_candidate_available": bool(best_console is not None),
            "active_capture_host_transient": bool(
                self._descriptor_is_non_persistent_capture_surface(active_capture)
            ),
            "active_capture_host_non_persistent": bool(
                self._descriptor_is_non_persistent_capture_surface(active_capture)
            ),
            "continuity_blocked_by_missing_substrate": bool(
                display_substrate.get("continuity_blocked_by_missing_substrate", False)
            ),
            "preferred_capture_host_transient": bool(
                self._descriptor_is_non_persistent_capture_surface(preferred_capture)
            ),
            "preferred_capture_host_non_persistent": bool(
                self._descriptor_is_non_persistent_capture_surface(preferred_capture)
            ),
            "force_capture_host_migration": bool(force_migration),
            "force_capture_host_migration_reason": migration_reason,
            "migration_from_session_id": (
                int(active_capture.session_id) if active_capture is not None else None
            ),
            "migration_to_session_id": (
                int(preferred_capture.session_id) if preferred_capture is not None else None
            ),
            "preferred_capture_host_session_id": (
                int(preferred_capture.session_id) if preferred_capture is not None else None
            ),
            "authoritative_capture_host_session_id": (
                int(preferred_capture.session_id) if preferred_capture is not None else None
            ),
            "active_capture_host_session_id": (
                int(active_capture.session_id) if active_capture is not None else None
            ),
            "capture_helper_host_session_id": (
                int(capture_helper.session_id) if capture_helper is not None else None
            ),
            "input_helper_host_session_id": (
                int(input_helper.session_id) if input_helper is not None else None
            ),
            "requires_virtual_display_for_full_continuity": bool(
                preferred_assessment.requires_virtual_display_for_full_continuity
            ),
            "can_provision_virtual_display": bool(
                display_substrate.get("can_provision_virtual_display", False)
            ),
            "virtual_display_provisioning_state": str(
                display_substrate.get("virtual_display_provisioning_state")
                or "not_supported_in_current_build"
            ),
            "display_substrate": display_substrate,
            "preferred_capture_surface_assessment": preferred_assessment.to_dict(),
            "active_capture_surface_assessment": active_assessment.to_dict(),
            "notes": notes,
        }

    def _build_display_substrate_diagnostics(
        self,
        *,
        preferred_capture_descriptor: SessionDescriptor | None = None,
        active_capture_descriptor: SessionDescriptor | None = None,
        preferred_assessment: DisplayPresenceAssessment | None = None,
        active_assessment: DisplayPresenceAssessment | None = None,
    ) -> dict:
        preferred_descriptor = preferred_capture_descriptor or self.get_preferred_capture_host_session()
        active_descriptor = active_capture_descriptor or self._get_active_capture_descriptor()
        return self._display_substrate_manager.get_status_dict(
            preferred_descriptor=preferred_descriptor,
            active_descriptor=active_descriptor,
            preferred_assessment=preferred_assessment,
            active_assessment=active_assessment,
        )

    def _should_force_capture_host_migration(
        self,
        active_descriptor: SessionDescriptor | None,
        preferred_descriptor: SessionDescriptor | None,
        *,
        primary_descriptor: SessionDescriptor | None = None,
        capture_helper_descriptor: SessionDescriptor | None = None,
    ) -> tuple[bool, str]:
        if active_descriptor is None or preferred_descriptor is None:
            return False, ""
        if active_descriptor.session_id == preferred_descriptor.session_id:
            return False, ""

        active_rank = self._descriptor_capture_persistence_rank(active_descriptor)
        preferred_rank = self._descriptor_capture_persistence_rank(preferred_descriptor)
        active_is_non_persistent = self._descriptor_is_non_persistent_capture_surface(active_descriptor)
        preferred_is_persistent = self._descriptor_has_persistent_capture_surface(preferred_descriptor)

        if active_is_non_persistent and preferred_is_persistent:
            if (
                preferred_descriptor.is_console_session
                and self._descriptor_identity_matches(preferred_descriptor, primary_descriptor)
            ):
                return True, "migrate_non_persistent_to_console_affine_persistent_host"
            if preferred_descriptor.is_console_session:
                return True, "migrate_non_persistent_to_console_persistent_host"
            return True, "migrate_non_persistent_to_persistent_host"

        if active_descriptor.is_disconnected and not preferred_descriptor.is_disconnected:
            return True, "migrate_disconnected_capture_host_to_live_host"

        if preferred_rank + 2 < active_rank:
            return True, "migrate_capture_host_to_higher_persistence_host"

        if (
            capture_helper_descriptor is not None
            and preferred_descriptor.session_id == capture_helper_descriptor.session_id
            and active_descriptor.session_id != capture_helper_descriptor.session_id
            and preferred_rank < active_rank
        ):
            return True, "migrate_capture_host_to_authoritative_helper_host"

        return False, ""

    def _capture_continuity_signature(self, continuity_policy: dict) -> str:
        notes = ",".join(sorted(str(item) for item in (continuity_policy.get("notes") or [])))
        return "|".join(
            [
                f"mode={continuity_policy.get('continuity_mode') or 'none'}",
                f"best_effort={1 if continuity_policy.get('best_effort_only') else 0}",
                f"blocked={1 if continuity_policy.get('continuity_blocked_by_missing_substrate') else 0}",
                f"force={1 if continuity_policy.get('force_capture_host_migration') else 0}",
                f"preferred={continuity_policy.get('preferred_capture_host_session_id') or 'none'}",
                f"from={continuity_policy.get('migration_from_session_id') or 'none'}",
                f"to={continuity_policy.get('migration_to_session_id') or 'none'}",
                f"notes={notes or 'none'}",
            ]
        )

    def _display_substrate_signature(self, display_substrate: dict) -> str:
        return "|".join(
            [
                f"provider={display_substrate.get('provider_name') or 'none'}",
                f"state={display_substrate.get('provider_state') or 'unknown'}",
                f"blocked={1 if display_substrate.get('continuity_blocked_by_missing_substrate') else 0}",
                f"physical={1 if display_substrate.get('physical_display_attached') else 0}",
                f"virtual={1 if display_substrate.get('virtual_display_attached') else 0}",
                f"render={int(display_substrate.get('render_monitor_count') or 0)}",
                f"attached={int(display_substrate.get('attached_display_count') or 0)}",
                f"provision={display_substrate.get('virtual_display_provisioning_state') or 'unknown'}",
            ]
        )

    def _schedule_helper_recycle(
        self,
        helper_recycle_reasons: dict[int, str],
        reason: str,
        *,
        active_capture_descriptor: SessionDescriptor | None = None,
        input_helper_descriptor: SessionDescriptor | None = None,
        capture_helper_descriptor: SessionDescriptor | None = None,
    ) -> None:
        target_session_ids = {
            int(descriptor.session_id)
            for descriptor in (
                active_capture_descriptor,
                input_helper_descriptor,
                capture_helper_descriptor,
            )
            if descriptor is not None
        }
        for session_id in target_session_ids:
            helper_recycle_reasons[session_id] = reason

    def _descriptor_topology_signature(self, descriptor: SessionDescriptor | None) -> str:
        if descriptor is None:
            return "none"
        return "|".join(
            [
                f"session={int(descriptor.session_id)}",
                f"identity={self._normalize_signature_value(descriptor.identity)}",
                f"station={self._normalize_signature_value(descriptor.station_name)}",
                f"state={self._normalize_signature_value(descriptor.state)}",
                f"remote={1 if descriptor.is_remote_session else 0}",
                f"console={1 if descriptor.is_console_session else 0}",
                f"active={1 if descriptor.is_active else 0}",
                f"connected={1 if descriptor.is_connected else 0}",
                f"disconnected={1 if descriptor.is_disconnected else 0}",
            ]
        )

    def _session_topology_fingerprint(self, descriptors: list[SessionDescriptor]) -> str:
        if not descriptors:
            return "none"
        ordered = sorted(descriptors, key=lambda item: int(item.session_id))
        return "||".join(self._descriptor_topology_signature(item) for item in ordered)

    def _normalize_signature_value(self, value: str | None) -> str:
        text = str(value or "").strip().lower()
        return text or "unknown"
