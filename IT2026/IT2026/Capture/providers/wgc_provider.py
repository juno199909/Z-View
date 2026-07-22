from __future__ import annotations

import contextlib
import importlib.util
from typing import Any

from PIL import Image

try:
    import dxcam
except Exception as exc:  # pragma: no cover - optional dependency
    dxcam = None
    _DXCAM_IMPORT_ERROR = str(exc)
else:  # pragma: no cover - runtime state
    _DXCAM_IMPORT_ERROR = ""


_WINRT_REQUIRED_MODULES = (
    "winrt.windows.graphics.capture",
    "winrt.windows.graphics.capture.interop",
    "winrt.windows.graphics.directx",
    "winrt.windows.graphics.directx.direct3d11.interop",
)


def _format_module_token(module_name: str) -> str:
    normalized = str(module_name or "").strip()
    if normalized.startswith("winrt."):
        normalized = normalized[len("winrt.") :]
    return normalized.replace("windows.", "")


class Provider:
    def __init__(self, backend_name: str = "wgc") -> None:
        self.backend_name = str(backend_name or "wgc").strip().lower() or "wgc"
        self._camera = None
        self._last_error = ""
        self._support_reason = ""
        self._missing_modules: list[str] = []

    def get_support_status(self, capturer=None) -> tuple[bool, str]:
        self._support_reason = ""
        self._missing_modules = []
        if dxcam is None:
            self._support_reason = f"wgc_dxcam_module_unavailable:{_DXCAM_IMPORT_ERROR or 'import_failed'}"
            return False, self._support_reason

        missing_modules: list[str] = []
        for module_name in _WINRT_REQUIRED_MODULES:
            try:
                spec = importlib.util.find_spec(module_name)
            except Exception:
                spec = None
            if spec is None:
                missing_modules.append(module_name)
        if missing_modules:
            self._missing_modules = missing_modules
            short_names = ",".join(_format_module_token(module_name) for module_name in missing_modules)
            self._support_reason = f"wgc_winrt_dependency_missing:{short_names}"
            return False, self._support_reason

        factory = getattr(dxcam, "create", None)
        if not callable(factory):
            self._support_reason = "wgc_dxcam_create_missing"
            return False, self._support_reason

        return True, ""

    def prepare(self, capturer=None) -> None:
        supported, reason = self.get_support_status(capturer)
        if not supported:
            raise RuntimeError(reason or "wgc_unsupported")
        if self._camera is None:
            self._camera = dxcam.create(
                output_color="RGB",
                max_buffer_len=4,
                backend="winrt",
                processor_backend="numpy",
            )

    def grab(self, capturer=None):
        frame = self._camera.grab(copy=True, new_frame_only=False)
        if frame is None:
            raise RuntimeError("wgc returned no frame")
        screenshot = Image.fromarray(frame, "RGB")
        if capturer is not None:
            capturer._validate_virtual_capture_geometry("WGC", screenshot)
        return screenshot

    def reset(self, capturer=None, reason: str = "") -> None:
        self._last_error = str(reason or "")
        self._release_camera()

    def close(self, capturer=None) -> None:
        self._release_camera()

    def describe_state(self) -> dict[str, Any]:
        supported, support_reason = self.get_support_status()
        return {
            "backend": self.backend_name,
            "provider": "dxcam_winrt",
            "supported": bool(supported),
            "support_reason": str(support_reason or ""),
            "camera_active": self._camera is not None,
            "missing_modules": list(self._missing_modules),
            "last_error": str(self._last_error or ""),
        }

    def _release_camera(self) -> None:
        camera = self._camera
        self._camera = None
        if camera is None:
            return
        with contextlib.suppress(Exception):
            camera.stop()
        with contextlib.suppress(Exception):
            camera.release()


def create_capture_backend_provider(backend_name: str = "wgc", **_: Any) -> Provider:
    return Provider(backend_name=backend_name)
