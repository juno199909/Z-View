# -*- coding: utf-8 -*-
"""P5-02：统一异常体系。"""
from __future__ import annotations


class ZViewError(Exception):
    """Z-View 平台基础异常。"""

    def __init__(self, message: str, code: str = "INTERNAL_ERROR", status_code: int = 500):
        super().__init__(message)
        self.message = message
        self.code = code
        self.status_code = status_code


class AuthenticationError(ZViewError):
    def __init__(self, message: str = "Authentication failed"):
        super().__init__(message, code="AUTH_FAILED", status_code=401)


class AuthorizationError(ZViewError):
    def __init__(self, message: str = "Permission denied"):
        super().__init__(message, code="PERMISSION_DENIED", status_code=403)


class AgentOfflineError(ZViewError):
    def __init__(self, agent_id: int, hostname: str = ""):
        msg = f"Agent {agent_id} ({hostname}) is offline"
        super().__init__(msg, code="AGENT_OFFLINE", status_code=502)


class UpgradeError(ZViewError):
    def __init__(self, message: str, agent_id: int | None = None):
        super().__init__(message, code="UPGRADE_FAILED", status_code=500)


class RemoteSessionError(ZViewError):
    def __init__(self, message: str, session_id: int | None = None):
        super().__init__(message, code="REMOTE_SESSION_ERROR", status_code=500)


class PolicyError(ZViewError):
    def __init__(self, message: str, policy_id: int | None = None):
        super().__init__(message, code="POLICY_ERROR", status_code=422)


class ValidationError(ZViewError):
    def __init__(self, message: str, field: str | None = None):
        super().__init__(message, code="VALIDATION_ERROR", status_code=422)
