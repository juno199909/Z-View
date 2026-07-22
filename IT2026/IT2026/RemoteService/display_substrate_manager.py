from __future__ import annotations

from Capture.display_presence import DisplayPresenceAssessment, DisplayPresenceProbe
from Capture.display_substrate import (
    DisplaySubstrateStatus,
    InventoryDisplaySubstrateProvider,
)
from Common.models import SessionDescriptor
from RemoteService.virtual_display_provider import VirtualDisplayProvider


class DisplaySubstrateManager:
    def __init__(
        self,
        probe: DisplayPresenceProbe,
        *,
        logger=None,
        can_provision_virtual_display: bool = False,
        virtual_display_provisioning_state: str = "not_supported_in_current_build",
    ):
        self.probe = probe
        self.virtual_display_provider = VirtualDisplayProvider(logger=logger)
        self.provider = InventoryDisplaySubstrateProvider(
            probe,
            virtual_display_provider=self.virtual_display_provider,
            can_provision_virtual_display=can_provision_virtual_display,
            virtual_display_provisioning_state=virtual_display_provisioning_state,
        )

    def get_status(
        self,
        *,
        preferred_descriptor: SessionDescriptor | None = None,
        active_descriptor: SessionDescriptor | None = None,
        preferred_assessment: DisplayPresenceAssessment | None = None,
        active_assessment: DisplayPresenceAssessment | None = None,
    ) -> DisplaySubstrateStatus:
        return self.provider.get_status(
            preferred_descriptor=preferred_descriptor,
            active_descriptor=active_descriptor,
            preferred_assessment=preferred_assessment,
            active_assessment=active_assessment,
        )

    def get_status_dict(
        self,
        *,
        preferred_descriptor: SessionDescriptor | None = None,
        active_descriptor: SessionDescriptor | None = None,
        preferred_assessment: DisplayPresenceAssessment | None = None,
        active_assessment: DisplayPresenceAssessment | None = None,
    ) -> dict:
        return self.get_status(
            preferred_descriptor=preferred_descriptor,
            active_descriptor=active_descriptor,
            preferred_assessment=preferred_assessment,
            active_assessment=active_assessment,
        ).to_dict()

    def get_virtual_display_status(self, *, force_refresh: bool = False) -> dict:
        return self.virtual_display_provider.get_status(force_refresh=force_refresh)

    def ensure_virtual_display(self) -> dict:
        return self.virtual_display_provider.ensure_attached()

    def repair_virtual_display(self) -> dict:
        return self.virtual_display_provider.repair()
