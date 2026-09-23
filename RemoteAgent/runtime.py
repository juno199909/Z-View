from __future__ import annotations

import os
import socket
import threading
import time
from typing import Callable

from RemoteAgent.privileged_client import PrivilegedServiceClient


class RemoteDesktopUserAgentRuntime:
    def __init__(
        self,
        session_id: int | None,
        start_remote_desktop_server: Callable[[], None],
        launch_consent_ui_background: Callable[[], bool],
        keepalive: Callable[[], None],
        log_runtime_event: Callable[[str, str], None],
        service_client: PrivilegedServiceClient | None = None,
        cleanup_runtime_state: Callable[[], None] | None = None,
    ):
        self.session_id = session_id
        self.start_remote_desktop_server = start_remote_desktop_server
        self.launch_consent_ui_background = launch_consent_ui_background
        self.keepalive = keepalive
        self.log_runtime_event = log_runtime_event
        self.service_client = service_client
        self.cleanup_runtime_state = cleanup_runtime_state

    def _wait_for_remote_desktop_listener(
        self,
        port: int = 9000,
        timeout_seconds: float = 12.0,
    ) -> tuple[bool, int, str | None]:
        deadline = time.time() + timeout_seconds
        attempts = 0
        last_error: str | None = None

        while time.time() < deadline:
            attempts += 1
            try:
                with socket.create_connection(("127.0.0.1", port), timeout=0.75):
                    return True, attempts, None
            except OSError as exc:
                last_error = f"{type(exc).__name__}: {exc}"
                time.sleep(0.5)

        return False, attempts, last_error

    def run(self) -> None:
        primary_remote_host_session_id = None
        preferred_capture_host_session_id = None
        if self.service_client is not None:
            try:
                payload = self.service_client.ping()
                self.log_runtime_event("UserSessionAgent", f"service IPC ready: pipe={payload.get('pipe_name')}")
            except Exception as exc:
                self.log_runtime_event("UserSessionAgent", f"service IPC unavailable, continue degraded: {exc}")
            else:
                try:
                    capabilities = self.service_client.get_capabilities()
                    self.log_runtime_event(
                        "UserSessionAgent",
                        "service capabilities: "
                        f"ipc={capabilities.get('ipc_transport')} "
                        f"uac_compliance={capabilities.get('uac_compliance')} "
                        f"console_session={capabilities.get('console_session_id')} "
                        f"primary_remote_host_session={capabilities.get('primary_remote_host_session_id')} "
                        f"preferred_capture_host_session={capabilities.get('preferred_capture_host_session_id')} "
                        f"input_helper_host_session={capabilities.get('input_helper_host_session_id')} "
                        f"capture_helper_host_session={capabilities.get('capture_helper_host_session_id')} "
                        f"active_sessions={capabilities.get('active_session_ids')}",
                    )
                    primary_remote_host_session_id = capabilities.get("primary_remote_host_session_id")
                    preferred_capture_host_session_id = capabilities.get("preferred_capture_host_session_id")
                except Exception as exc:
                    self.log_runtime_event("UserSessionAgent", f"service capability query failed: {exc}")

                try:
                    topology = self.service_client.get_session_topology()
                    console_session = topology.get("console_session") or {}
                    primary_remote_host_session = topology.get("primary_remote_host_session") or {}
                    preferred_capture_host_session = topology.get("preferred_capture_host_session") or {}
                    console_session_id = console_session.get("session_id")
                    primary_remote_host_session_id = primary_remote_host_session.get(
                        "session_id",
                        primary_remote_host_session_id,
                    )
                    preferred_capture_host_session_id = preferred_capture_host_session.get(
                        "session_id",
                        preferred_capture_host_session_id,
                    )
                    if console_session_id is not None and self.session_id is not None:
                        self.log_runtime_event(
                            "UserSessionAgent",
                            "session routing: "
                            f"current_session={self.session_id} "
                            f"console_session={console_session_id} "
                            f"primary_remote_host_session={primary_remote_host_session_id} "
                            f"preferred_capture_host_session={preferred_capture_host_session_id}",
                        )
                    if (
                        primary_remote_host_session_id is not None
                        and self.session_id is not None
                        and int(self.session_id) != int(primary_remote_host_session_id)
                    ):
                        self.log_runtime_event(
                            "UserSessionAgent",
                            "current session is not the primary remote desktop host; "
                            "keeping user-session agent alive because capture/input routing is now "
                            "service-managed and may diverge from the remote server host. "
                            f"current_session={self.session_id} "
                            f"primary_remote_host_session={primary_remote_host_session_id} "
                            f"preferred_capture_host_session={preferred_capture_host_session_id}",
                        )
                except Exception as exc:
                    self.log_runtime_event("UserSessionAgent", f"session topology query failed: {exc}")

        def remote_desktop_server_thread():
            try:
                self.log_runtime_event("UserSessionAgent", "remote desktop server thread starting")
                self.start_remote_desktop_server()
            except Exception as exc:
                self.log_runtime_event("UserSessionAgent", f"remote desktop server exited: {exc}")
                raise

        def remote_desktop_server_guard(server_thread: threading.Thread):
            server_thread.join()
            self.log_runtime_event(
                "UserSessionAgent",
                "remote desktop server thread stopped; exiting user-session role for backend recovery",
            )
            os._exit(1)

        thread = threading.Thread(
            target=remote_desktop_server_thread,
            name="cmdb-remote-desktop-server",
            daemon=True,
        )
        thread.start()
        self.log_runtime_event("UserSessionAgent", f"remote desktop server thread started: ident={thread.ident}")

        guard_thread = threading.Thread(
            target=remote_desktop_server_guard,
            args=(thread,),
            name="cmdb-remote-desktop-server-guard",
            daemon=True,
        )
        guard_thread.start()
        self.log_runtime_event("UserSessionAgent", f"remote desktop guard thread started: ident={guard_thread.ident}")

        time.sleep(1)
        self.log_runtime_event(
            "UserSessionAgent",
            f"post-start health check: server_thread_alive={thread.is_alive()} guard_thread_alive={guard_thread.is_alive()}",
        )
        if not thread.is_alive():
            raise RuntimeError("remote desktop server startup failed")

        listener_ready, listener_attempts, listener_error = self._wait_for_remote_desktop_listener(port=9000)
        if listener_ready:
            self.log_runtime_event(
                "UserSessionAgent",
                f"remote desktop listener ready: port=9000 session={self.session_id if self.session_id is not None else 'unknown'} attempts={listener_attempts}",
            )
        else:
            self.log_runtime_event(
                "UserSessionAgent",
                "remote desktop listener not ready within bootstrap window: "
                f"port=9000 session={self.session_id if self.session_id is not None else 'unknown'} "
                f"attempts={listener_attempts} last_error={listener_error or 'unknown'}",
            )

        self.log_runtime_event("UserSessionAgent", "launching consent UI helper in background")
        launched = self.launch_consent_ui_background()
        if not launched:
            self.log_runtime_event(
                "UserSessionAgent",
                "consent UI helper launch failed; remote desktop server will continue without blocking startup",
            )

        self.keepalive()
