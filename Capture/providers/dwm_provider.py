from __future__ import annotations

import os
from typing import Any


_SUPPORT_REASON = "dwm_shared_surface_disabled_pending_native_implementation"
_ENABLED = str(os.getenv("CMDB_EXPERIMENTAL_DWM_CAPTURE", "")).strip().lower() in {
    "1",
    "true",
    "yes",
    "on",
}


class Provider:
    def __init__(self, backend_name: str = "dwm") -> None:
        self.backend_name = str(backend_name or "dwm").strip().lower() or "dwm"
        self._last_error = ""

    def get_support_status(self, capturer=None) -> tuple[bool, str]:
        return False, (
            "dwm_shared_surface_enabled_without_native_provider"
            if _ENABLED
            else _SUPPORT_REASON
        )

    def prepare(self, capturer=None) -> None:
        raise RuntimeError(
            "dwm_shared_surface_enabled_without_native_provider"
            if _ENABLED
            else _SUPPORT_REASON
        )

    def grab(self, capturer=None):
        raise RuntimeError(
            "dwm_shared_surface_enabled_without_native_provider"
            if _ENABLED
            else _SUPPORT_REASON
        )

    def reset(self, capturer=None, reason: str = "") -> None:
        self._last_error = str(reason or "")

    def close(self, capturer=None) -> None:
        return None

    def describe_state(self) -> dict[str, Any]:
        return {
            "backend": self.backend_name,
            "provider": "native_dwm_shared_surface",
            "supported": False,
            "support_reason": (
                "dwm_shared_surface_enabled_without_native_provider"
                if _ENABLED
                else _SUPPORT_REASON
            ),
            "experimental_opt_in": bool(_ENABLED),
            "last_error": str(self._last_error or ""),
        }


def create_capture_backend_provider(backend_name: str = "dwm", **_: Any) -> Provider:
    return Provider(backend_name=backend_name)
