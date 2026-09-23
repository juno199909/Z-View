from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(slots=True)
class SessionDescriptor:
    session_id: int
    label: str
    identity: str = ""
    is_console_preferred: bool = False
    station_name: str = ""
    state: str = ""
    is_remote_session: bool = False
    is_active: bool = False
    is_connected: bool = False
    is_disconnected: bool = False
    is_console_session: bool = False
    preferred_capture_desktops: list[str] = field(default_factory=list)
    preferred_capture_desktop_kind: str = ""
    desktop_transition_reason: str = ""
    allow_secure_desktop: bool = False
    allow_screensaver_desktop: bool = False
    authoritative_capture_binding: bool = False
    capture_binding_generation: int = 0
    capture_binding_policy_signature: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class PipeRequest:
    command: str
    payload: dict[str, Any] = field(default_factory=dict)
    request_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class PipeResponse:
    ok: bool
    payload: dict[str, Any] = field(default_factory=dict)
    error: str | None = None
    request_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class CaptureCapabilities:
    preferred_backend: str
    dxgi_supported: bool
    gdi_supported: bool
    fallback_backend: str
    current_backend: str = ""
    implementation: str = "legacy_screen_capturer"
    supports_frame_diff: bool = True
    target_fps: int = 30
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class TransportSettings:
    protocol: str = "tcp"
    tls_enabled: bool = False
    cipher: str = "AES-GCM"
    auto_reconnect: bool = True
    heartbeat_seconds: int = 15
    implementation: str = "websocket-compat"
    target_protocol: str = "tcp+tls"
    target_tls_enabled: bool = True
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class ServiceRuntimeCapabilities:
    service_mode: str = "windows_service_localsystem"
    ipc_transport: str = "named_pipe"
    session_supervisor: bool = True
    unattended_access: bool = True
    uac_compliance: bool = True
    admin_operations_via_service: bool = True
    secure_desktop_bypass: bool = False
    high_integrity_agent_mode: str = "planned"
    console_session_id: int | None = None
    primary_remote_host_session_id: int | None = None
    preferred_capture_host_session_id: int | None = None
    active_capture_host_session_id: int | None = None
    input_helper_host_session_id: int | None = None
    capture_helper_host_session_id: int | None = None
    capture_continuity_mode: str = "best_effort"
    capture_continuity_best_effort_only: bool = True
    persistent_capture_substrate_detected: bool = False
    preferred_capture_substrate_class: str = "unknown_best_effort"
    active_capture_substrate_class: str = "unknown_best_effort"
    physical_display_attached: bool = False
    virtual_display_attached: bool = False
    requires_virtual_display_for_full_continuity: bool = True
    continuity_blocked_by_missing_substrate: bool = False
    can_provision_virtual_display: bool = False
    virtual_display_provisioning_state: str = "not_supported_in_current_build"
    commercial_continuity_ready: bool = False
    continuity_grade: str = "best_effort_rdp_only"
    continuity_blockers: list[str] = field(default_factory=list)
    continuity_requirements: list[str] = field(default_factory=list)
    required_persistent_substrate: str = "physical_display_or_signed_virtual_display_idd"
    target_matrix_verified: bool = False
    target_matrix_unverified: list[str] = field(default_factory=list)
    commercial_continuity_blocker: str = ""
    remote_desktop_readiness: dict[str, Any] = field(default_factory=dict)
    display_substrate: dict[str, Any] = field(default_factory=dict)
    active_session_ids: list[int] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
