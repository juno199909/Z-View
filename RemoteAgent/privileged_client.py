from __future__ import annotations

import uuid

from Common.runtime_paths import get_default_service_pipe_name
from IPC.named_pipe import NamedPipeCommandClient


class PrivilegedServiceClient:
    def __init__(self, pipe_name: str | None = None):
        self.pipe_name = pipe_name or get_default_service_pipe_name()
        self.client = NamedPipeCommandClient(self.pipe_name)

    def ping(self) -> dict:
        return self._request("ping")

    def get_active_sessions(self) -> dict:
        return self._request("get_active_sessions")

    def get_console_session(self) -> dict:
        return self._request("get_console_session")

    def get_session_topology(self) -> dict:
        return self._request("get_session_topology")

    def get_display_substrate(self) -> dict:
        return self._request("get_display_substrate")

    def get_remote_desktop_readiness(self) -> dict:
        return self._request("get_remote_desktop_readiness")

    def get_virtual_display_status(self, *, force_refresh: bool = False) -> dict:
        return self._request("get_virtual_display_status", {"force_refresh": bool(force_refresh)})

    def describe_desktop_context(
        self,
        *,
        reason: str = "",
        session_id: int | None = None,
        compact: bool = False,
    ) -> dict:
        payload = {}
        if reason:
            payload["reason"] = str(reason)
        if session_id is not None:
            payload["session_id"] = int(session_id)
        if compact:
            payload["compact"] = True
        return self.invoke_admin_action("describe_desktop_context", payload)

    def ensure_virtual_display(self) -> dict:
        return self._request("ensure_virtual_display")

    def repair_virtual_display(self) -> dict:
        return self._request("repair_virtual_display")

    def get_runtime_status(self) -> dict:
        return self._request("runtime_status")

    def get_capabilities(self) -> dict:
        return self._request("get_capabilities")

    def launch_user_session_agent(self, session_id: int) -> dict:
        return self._request("launch_user_session_agent", {"session_id": int(session_id)})

    def ensure_user_session_agent(self, session_id: int | None = None) -> dict:
        payload = {}
        if session_id is not None:
            payload["session_id"] = int(session_id)
        return self._request("ensure_user_session_agent", payload)

    def restart_user_session_agent(self, session_id: int | None = None, wait_seconds: float = 8.0) -> dict:
        payload = {"wait_seconds": float(wait_seconds)}
        if session_id is not None:
            payload["session_id"] = int(session_id)
        return self._request("restart_user_session_agent", payload)

    def invoke_admin_action(self, action: str, payload: dict | None = None) -> dict:
        return self._request(
            "invoke_admin_action",
            {
                "action": str(action or "").strip(),
                "payload": payload or {},
            },
        )

    def capture_frame(self, payload: dict | None = None) -> dict:
        return self.invoke_admin_action("capture_frame", payload or {})

    def request(self, command: str, payload: dict | None = None) -> dict:
        return self._request(command, payload)

    def _request(self, command: str, payload: dict | None = None) -> dict:
        response = self.client.request(
            {
                "command": command,
                "payload": payload or {},
                "request_id": str(uuid.uuid4()),
            }
        )
        if not response.get("ok"):
            raise RuntimeError(str(response.get("error") or f"service command failed: {command}"))
        return response.get("payload") or {}
