from __future__ import annotations

from typing import Any, Callable

from Common.logging_utils import make_component_logger
from Common.models import ServiceRuntimeCapabilities
from Common.runtime_paths import get_default_service_pipe_name
from IPC.named_pipe import NamedPipeCommandServer
from RemoteService.session_manager import SessionManager


class ServiceRuntime:
    def __init__(
        self,
        session_manager: SessionManager,
        pipe_name: str | None = None,
        logger: Callable[[str], None] | None = None,
    ):
        self.session_manager = session_manager
        self.pipe_name = pipe_name or get_default_service_pipe_name()
        self.logger = logger or make_component_logger("ServiceRuntime")
        self._extra_handlers: dict[str, Callable[[dict[str, Any]], dict[str, Any]]] = {}
        self._pipe_server = NamedPipeCommandServer(
            self.pipe_name,
            self._dispatch_request,
            logger=self.logger,
            allow_all_users=True,
            # R4：客户端会话必须属于当前已知的交互会话集合
            # （阻断同机无关进程/其他会话连接服务控制管道）
            client_validator=self._validate_pipe_client_session,
        )

    def _validate_pipe_client_session(self, client_session_id: int) -> bool:
        """管道客户端会话必须在活跃交互会话集合中（Session 0 服务视角的访问控制）。"""
        try:
            sessions = self.session_manager.list_interactive_sessions()
            known = {int(d.session_id) for d in sessions}
            return int(client_session_id) in known
        except Exception as exc:
            self.logger(f"pipe client session validation error (fail-open): {exc}")
            return True

    def register_handler(self, command: str, handler: Callable[[dict[str, Any]], dict[str, Any]]) -> None:
        self._extra_handlers[command] = handler

    def start(self) -> None:
        self.session_manager.start_supervisor()
        self._pipe_server.start()
        self.logger(f"service runtime active: pipe={self.pipe_name}")

    def stop(self) -> None:
        self._pipe_server.stop()
        self.session_manager.stop_supervisor()

    def _dispatch_request(self, request: dict[str, Any]) -> dict[str, Any]:
        command = str(request.get("command") or "").strip().lower()
        payload = request.get("payload") or {}
        request_id = request.get("request_id")

        try:
            if command == "ping":
                response_payload = {
                    "service_runtime": "ready",
                    "pipe_name": self.pipe_name,
                }
            elif command == "get_active_sessions":
                response_payload = {
                    "sessions": [item.to_dict() for item in self.session_manager.list_interactive_sessions()],
                }
            elif command == "get_console_session":
                descriptor = self.session_manager.get_console_session()
                response_payload = {
                    "session": descriptor.to_dict() if descriptor is not None else None,
                }
            elif command == "get_session_topology":
                response_payload = self.session_manager.describe_session_topology()
            elif command == "get_display_substrate":
                response_payload = self.session_manager.describe_session_topology().get("display_substrate") or {}
            elif command == "get_remote_desktop_readiness":
                response_payload = self.session_manager.get_remote_desktop_readiness()
            elif command == "get_virtual_display_status":
                response_payload = self.session_manager.get_virtual_display_status(
                    force_refresh=bool(payload.get("force_refresh", False))
                )
            elif command == "ensure_virtual_display":
                response_payload = self.session_manager.ensure_virtual_display()
            elif command == "repair_virtual_display":
                response_payload = self.session_manager.repair_virtual_display()
            elif command == "runtime_status":
                response_payload = {
                    "sessions": self.session_manager.collect_runtime_status(),
                }
            elif command == "get_capabilities":
                topology = self.session_manager.describe_session_topology()
                capture_continuity = topology.get("capture_continuity") or {}
                active_capture_descriptor = topology.get("active_capture_session") or {}
                capture_topology = topology.get("capture_topology") or {}
                display_substrate = topology.get("display_substrate") or {}
                readiness = topology.get("remote_desktop_readiness") or {}
                descriptor = self.session_manager.get_console_session()
                primary_descriptor = self.session_manager.get_primary_remote_host_session()
                preferred_capture_descriptor = self.session_manager.get_preferred_capture_host_session()
                input_helper_descriptor = self.session_manager.get_input_helper_host_session()
                capture_helper_descriptor = self.session_manager.get_capture_helper_host_session()
                active_sessions = self.session_manager.list_interactive_sessions()
                response_payload = ServiceRuntimeCapabilities(
                    high_integrity_agent_mode="service_managed_dual_session_helper_authoritative",
                    console_session_id=descriptor.session_id if descriptor is not None else None,
                    primary_remote_host_session_id=(
                        primary_descriptor.session_id if primary_descriptor is not None else None
                    ),
                    preferred_capture_host_session_id=(
                        preferred_capture_descriptor.session_id
                        if preferred_capture_descriptor is not None
                        else None
                    ),
                    active_capture_host_session_id=active_capture_descriptor.get("session_id"),
                    input_helper_host_session_id=(
                        input_helper_descriptor.session_id
                        if input_helper_descriptor is not None
                        else None
                    ),
                    capture_helper_host_session_id=(
                        capture_helper_descriptor.session_id
                        if capture_helper_descriptor is not None
                        else None
                    ),
                    capture_continuity_mode=str(
                        capture_continuity.get("continuity_mode") or "best_effort"
                    ),
                    capture_continuity_best_effort_only=bool(
                        capture_continuity.get("best_effort_only", True)
                    ),
                    persistent_capture_substrate_detected=bool(
                        capture_continuity.get("persistent_capture_substrate_detected", False)
                    ),
                    preferred_capture_substrate_class=str(
                        capture_topology.get("preferred_capture_substrate_class")
                        or "unknown_best_effort"
                    ),
                    active_capture_substrate_class=str(
                        capture_continuity.get("active_capture_substrate_class")
                        or "unknown_best_effort"
                    ),
                    physical_display_attached=bool(
                        capture_topology.get("physical_display_attached", False)
                    ),
                    virtual_display_attached=bool(
                        capture_topology.get("virtual_display_attached", False)
                    ),
                    requires_virtual_display_for_full_continuity=bool(
                        capture_continuity.get(
                            "requires_virtual_display_for_full_continuity",
                            True,
                        )
                    ),
                    continuity_blocked_by_missing_substrate=bool(
                        capture_continuity.get("continuity_blocked_by_missing_substrate", False)
                    ),
                    can_provision_virtual_display=bool(
                        display_substrate.get("can_provision_virtual_display", False)
                    ),
                    virtual_display_provisioning_state=str(
                        display_substrate.get("virtual_display_provisioning_state")
                        or "not_supported_in_current_build"
                    ),
                    commercial_continuity_ready=bool(
                        readiness.get("commercial_continuity_ready", False)
                    ),
                    continuity_grade=str(
                        readiness.get("continuity_grade") or "best_effort_rdp_only"
                    ),
                    continuity_blockers=list(readiness.get("continuity_blockers") or []),
                    continuity_requirements=list(readiness.get("continuity_requirements") or []),
                    required_persistent_substrate=str(
                        readiness.get("required_persistent_substrate")
                        or "physical_display_or_signed_virtual_display_idd"
                    ),
                    target_matrix_verified=bool(readiness.get("target_matrix_verified", False)),
                    target_matrix_unverified=list(readiness.get("target_matrix_unverified") or []),
                    commercial_continuity_blocker=str(
                        readiness.get("commercial_continuity_blocker") or ""
                    ),
                    remote_desktop_readiness=dict(readiness),
                    display_substrate=dict(display_substrate),
                    active_session_ids=[item.session_id for item in active_sessions],
                    notes=[
                        "service_runs_as_windows_service",
                        "admin_sensitive_operations_should_route_via_service",
                        "service_admin_dispatch_ready",
                        "desktop_context_diagnostics_available",
                        "session_helper_ipc_ready",
                        "service_token_session_launch_attempted_with_user_session_fallback",
                        "single_primary_remote_desktop_server_host_enforced",
                        "live_host_session_routing_enabled",
                        "capture_host_selection_service_managed",
                        "capture_host_prefers_console_persistent_sessions",
                        "capture_host_can_diverge_from_primary_remote_host",
                        "input_helper_host_selection_service_managed",
                        "capture_helper_host_selection_service_managed",
                        "capture_and_input_hosts_can_diverge",
                        "session_helper_capture_ready",
                        "capture_hosted_in_service_managed_session_helper",
                        "input_injected_via_service_managed_session_helper",
                        "local_capture_fallback_disabled_for_remote_desktop",
                        "display_presence_probe_active",
                        "display_substrate_manager_active",
                    ]
                    + self.session_manager.get_capability_notes(),
                ).to_dict()
            elif command == "launch_user_session_agent":
                session_id = int(payload.get("session_id"))
                response_payload = {
                    "session_id": session_id,
                    "started": bool(self.session_manager.launch_user_session_agent(session_id)),
                }
            elif command == "ensure_user_session_agent":
                response_payload = self.session_manager.ensure_user_session_agent(
                    payload.get("session_id"),
                )
            elif command == "restart_user_session_agent":
                response_payload = self.session_manager.restart_user_session_agent(
                    payload.get("session_id"),
                    wait_seconds=float(payload.get("wait_seconds") or 8.0),
                )
            elif command == "invoke_admin_action":
                action = str(payload.get("action") or "").strip()
                if not action:
                    raise ValueError("missing admin action name")
                response_payload = self.session_manager.invoke_admin_action(
                    action,
                    payload.get("payload") or {},
                )
            elif command in self._extra_handlers:
                response_payload = self._extra_handlers[command](payload)
            else:
                raise ValueError(f"unsupported service command: {command}")

            return {
                "ok": True,
                "payload": response_payload,
                "request_id": request_id,
            }
        except Exception as exc:
            return {
                "ok": False,
                "error": str(exc),
                "request_id": request_id,
            }
