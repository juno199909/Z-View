from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from Capture.display_presence import DisplayPresenceAssessment, DisplayPresenceProbe
from Common.models import SessionDescriptor


@dataclass(slots=True)
class DisplaySubstrateStatus:
    provider_name: str
    provider_state: str
    persistent_available: bool
    persistent_ready_for_unattended: bool
    continuity_blocked_by_missing_substrate: bool
    persistent_required_for_full_continuity: bool
    requires_virtual_display_for_full_continuity: bool
    can_provision_virtual_display: bool
    virtual_display_provisioning_state: str
    physical_display_attached: bool
    virtual_display_attached: bool
    remote_adapter_present: bool
    render_monitor_count: int
    attached_display_count: int
    preferred_capture_substrate_class: str
    active_capture_substrate_class: str
    preferred_capture_continuity_mode: str
    active_capture_continuity_mode: str
    preferred_capture_host_session_id: int | None
    active_capture_host_session_id: int | None
    virtual_display_status: dict[str, Any] = field(default_factory=dict)
    preferred_capture_surface_assessment: dict[str, Any] = field(default_factory=dict)
    active_capture_surface_assessment: dict[str, Any] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class DisplaySubstrateProvider:
    def get_status(
        self,
        *,
        preferred_descriptor: SessionDescriptor | None = None,
        active_descriptor: SessionDescriptor | None = None,
        preferred_assessment: DisplayPresenceAssessment | None = None,
        active_assessment: DisplayPresenceAssessment | None = None,
    ) -> DisplaySubstrateStatus:
        raise NotImplementedError


class InventoryDisplaySubstrateProvider(DisplaySubstrateProvider):
    def __init__(
        self,
        probe: DisplayPresenceProbe,
        *,
        virtual_display_provider: Any | None = None,
        can_provision_virtual_display: bool = False,
        virtual_display_provisioning_state: str = "not_supported_in_current_build",
    ):
        self.probe = probe
        self.virtual_display_provider = virtual_display_provider
        self.can_provision_virtual_display = bool(can_provision_virtual_display)
        self.virtual_display_provisioning_state = str(
            virtual_display_provisioning_state or "unknown"
        )

    def get_status(
        self,
        *,
        preferred_descriptor: SessionDescriptor | None = None,
        active_descriptor: SessionDescriptor | None = None,
        preferred_assessment: DisplayPresenceAssessment | None = None,
        active_assessment: DisplayPresenceAssessment | None = None,
    ) -> DisplaySubstrateStatus:
        virtual_display_status = {}
        inventory_force_refresh = False
        if self.virtual_display_provider is not None:
            try:
                virtual_display_status = self.virtual_display_provider.get_status(force_refresh=False) or {}
            except Exception as exc:
                virtual_display_status = {
                    "provider": "virtual_display_provider",
                    "provisioning_state": "provider_error",
                    "error": str(exc),
                    "notes": ["virtual_display_provider_error"],
                }
            inventory_force_refresh = self.probe.update_virtual_display_hints(virtual_display_status)

        inventory = self.probe.get_display_inventory(force_refresh=inventory_force_refresh)
        preferred = preferred_assessment or self.probe.assess(
            preferred_descriptor,
            inventory=inventory,
        )
        active = active_assessment or self.probe.assess(
            active_descriptor,
            inventory=inventory,
        )

        physical_display_attached = bool(inventory.get("physical_display_attached"))
        virtual_display_attached = bool(inventory.get("virtual_display_attached"))
        remote_adapter_present = bool(inventory.get("remote_adapter_present"))
        attached_display_count = int(inventory.get("attached_display_count") or 0)
        render_monitor_count = int(inventory.get("render_monitor_count") or 0)
        if virtual_display_status.get("attached_virtual_display"):
            virtual_display_attached = True

        persistent_substrate_attached = physical_display_attached or virtual_display_attached
        persistent_available = bool(
            persistent_substrate_attached or preferred.persistent or active.persistent
        )
        can_provision_virtual_display = bool(
            virtual_display_status.get("can_provision_virtual_display", self.can_provision_virtual_display)
        )
        virtual_display_provisioning_state = str(
            virtual_display_status.get("provisioning_state")
            or self.virtual_display_provisioning_state
        )
        requires_virtual_display = bool(
            preferred.requires_virtual_display_for_full_continuity
            or active.requires_virtual_display_for_full_continuity
            or not persistent_substrate_attached
        )
        continuity_blocked_by_missing_substrate = bool(
            not persistent_substrate_attached and requires_virtual_display
        )

        if continuity_blocked_by_missing_substrate:
            provider_state = "blocked_missing_persistent_surface"
        elif preferred.persistent:
            provider_state = "persistent_ready"
        elif active.persistent:
            provider_state = "active_host_persistent_preferred_host_pending"
        else:
            provider_state = "best_effort_only"
        if (
            virtual_display_provisioning_state not in {"", "unknown", "not_supported_in_current_build"}
            and provider_state == "blocked_missing_persistent_surface"
        ):
            provider_state = f"{provider_state}:{virtual_display_provisioning_state}"

        persistent_ready_for_unattended = bool(
            preferred.persistent and not continuity_blocked_by_missing_substrate
        )

        notes: list[str] = []
        for item in inventory.get("notes") or []:
            text = str(item or "").strip()
            if text and text not in notes:
                notes.append(text)
        for item in preferred.notes + active.notes:
            text = str(item or "").strip()
            if text and text not in notes:
                notes.append(text)
        for item in virtual_display_status.get("notes") or []:
            text = str(item or "").strip()
            if text and text not in notes:
                notes.append(text)

        if continuity_blocked_by_missing_substrate:
            notes.append("continuity_blocked_by_missing_persistent_surface")
            if not can_provision_virtual_display:
                notes.append("virtual_display_provisioning_not_available_in_current_build")
        if can_provision_virtual_display:
            notes.append("virtual_display_provisioning_supported")

        return DisplaySubstrateStatus(
            provider_name=(
                "service_managed_display_substrate_provider"
                if self.virtual_display_provider is not None
                else "inventory_display_substrate_provider"
            ),
            provider_state=provider_state,
            persistent_available=bool(persistent_available),
            persistent_ready_for_unattended=bool(persistent_ready_for_unattended),
            continuity_blocked_by_missing_substrate=bool(continuity_blocked_by_missing_substrate),
            persistent_required_for_full_continuity=True,
            requires_virtual_display_for_full_continuity=bool(requires_virtual_display),
            can_provision_virtual_display=bool(can_provision_virtual_display),
            virtual_display_provisioning_state=virtual_display_provisioning_state,
            physical_display_attached=bool(physical_display_attached),
            virtual_display_attached=bool(virtual_display_attached),
            remote_adapter_present=bool(remote_adapter_present),
            render_monitor_count=render_monitor_count,
            attached_display_count=attached_display_count,
            preferred_capture_substrate_class=str(preferred.substrate_class or "none"),
            active_capture_substrate_class=str(active.substrate_class or "none"),
            preferred_capture_continuity_mode=str(preferred.continuity_mode or "none"),
            active_capture_continuity_mode=str(active.continuity_mode or "none"),
            preferred_capture_host_session_id=(
                int(preferred_descriptor.session_id) if preferred_descriptor is not None else None
            ),
            active_capture_host_session_id=(
                int(active_descriptor.session_id) if active_descriptor is not None else None
            ),
            virtual_display_status=dict(virtual_display_status),
            preferred_capture_surface_assessment=preferred.to_dict(),
            active_capture_surface_assessment=active.to_dict(),
            notes=notes,
        )
