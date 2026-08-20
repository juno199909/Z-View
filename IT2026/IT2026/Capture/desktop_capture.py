from __future__ import annotations

import base64
import contextlib
import ctypes
import io
import os
import sys
import time
from collections import deque
import zlib
from dataclasses import dataclass
from ctypes import wintypes

from PIL import Image, ImageGrab

try:
    import dxcam
except Exception:  # pragma: no cover - optional dependency in packaging
    dxcam = None

try:
    import mss
except Exception:  # pragma: no cover - optional dependency in packaging
    mss = None

try:
    import pyautogui
except Exception:  # pragma: no cover - optional dependency in packaging
    pyautogui = None

from Common.models import CaptureCapabilities

try:
    from .native_backend_provider import create_capture_backend_provider
except Exception:  # pragma: no cover - optional provider wiring during partial packaging
    create_capture_backend_provider = None

if pyautogui is not None:
    pyautogui.FAILSAFE = False


@dataclass(slots=True)
class CaptureStack:
    capabilities: CaptureCapabilities

    def to_dict(self) -> dict:
        return self.capabilities.to_dict()


class ICaptureBackend:
    backend_id = ""
    display_name = ""

    def is_supported(self, capturer: "DesktopFrameCapturer") -> bool:
        return True

    def get_support_status(self, capturer: "DesktopFrameCapturer") -> tuple[bool, str]:
        try:
            supported = bool(self.is_supported(capturer))
        except Exception as exc:
            return False, f"support_probe_failed:{exc}"
        return supported, "" if supported else "unsupported_by_runtime"

    def prepare(self, capturer: "DesktopFrameCapturer") -> None:
        return None

    def grab(self, capturer: "DesktopFrameCapturer"):
        raise NotImplementedError

    def reset(self, capturer: "DesktopFrameCapturer", reason: str = "") -> None:
        return None

    def close(self, capturer: "DesktopFrameCapturer") -> None:
        return None

    def classify_failure(self, capturer: "DesktopFrameCapturer", exc: Exception) -> str:
        return capturer._classify_backend_failure(self.backend_id, exc)


class BlockedCaptureBackend(ICaptureBackend):
    backend_id = "blocked"
    display_name = "Blocked"

    def get_support_status(self, capturer: "DesktopFrameCapturer") -> tuple[bool, str]:
        blocker_reason = str(capturer.capture_blocker_reason or "").strip()
        if blocker_reason:
            return True, blocker_reason
        return False, "capture_not_blocked"

    def grab(self, capturer: "DesktopFrameCapturer"):
        raise RuntimeError(capturer.capture_blocker_reason or "blocked_non_persistent_capture_surface")


class DxgiCaptureBackend(ICaptureBackend):
    backend_id = "dxgi"
    display_name = "DXGI Desktop Duplication"

    def get_support_status(self, capturer: "DesktopFrameCapturer") -> tuple[bool, str]:
        if dxcam is None:
            return False, "dxcam_module_unavailable"
        if not _is_dxgi_os_supported():
            return False, "dxgi_unsupported_os"
        return True, ""

    def grab(self, capturer: "DesktopFrameCapturer"):
        return capturer._grab_with_dxgi()

    def is_supported(self, capturer: "DesktopFrameCapturer") -> bool:
        return dxcam is not None and _is_dxgi_os_supported()

    def reset(self, capturer: "DesktopFrameCapturer", reason: str = "") -> None:
        capturer._release_dxgi_camera()

    def close(self, capturer: "DesktopFrameCapturer") -> None:
        capturer._release_dxgi_camera()


class WindowsGraphicsCaptureBackend(ICaptureBackend):
    backend_id = "wgc"
    display_name = "Windows Graphics Capture"

    def get_support_status(self, capturer: "DesktopFrameCapturer") -> tuple[bool, str]:
        if not _is_wgc_os_supported():
            return False, "wgc_unsupported_os"
        provider = capturer._get_native_backend_provider(self.backend_id)
        if provider is None:
            return False, "wgc_provider_unavailable"
        return provider.get_support_status(capturer)

    def is_supported(self, capturer: "DesktopFrameCapturer") -> bool:
        supported, _ = self.get_support_status(capturer)
        return supported

    def prepare(self, capturer: "DesktopFrameCapturer") -> None:
        capturer._prepare_native_backend(self.backend_id)

    def grab(self, capturer: "DesktopFrameCapturer"):
        return capturer._grab_with_wgc()

    def reset(self, capturer: "DesktopFrameCapturer", reason: str = "") -> None:
        capturer._reset_native_backend(self.backend_id, reason=reason)

    def close(self, capturer: "DesktopFrameCapturer") -> None:
        capturer._close_native_backend(self.backend_id)


class DwmSharedSurfaceCaptureBackend(ICaptureBackend):
    backend_id = "dwm"
    display_name = "DWM Shared Surface"

    def get_support_status(self, capturer: "DesktopFrameCapturer") -> tuple[bool, str]:
        if not _is_dwm_capture_os_supported():
            return False, "dwm_shared_surface_unsupported_os"
        provider = capturer._get_native_backend_provider(self.backend_id)
        if provider is None:
            return False, "dwm_provider_unavailable"
        return provider.get_support_status(capturer)

    def is_supported(self, capturer: "DesktopFrameCapturer") -> bool:
        supported, _ = self.get_support_status(capturer)
        return supported

    def prepare(self, capturer: "DesktopFrameCapturer") -> None:
        capturer._prepare_native_backend(self.backend_id)

    def grab(self, capturer: "DesktopFrameCapturer"):
        return capturer._grab_with_dwm()

    def reset(self, capturer: "DesktopFrameCapturer", reason: str = "") -> None:
        capturer._reset_native_backend(self.backend_id, reason=reason)

    def close(self, capturer: "DesktopFrameCapturer") -> None:
        capturer._close_native_backend(self.backend_id)


class MssCaptureBackend(ICaptureBackend):
    backend_id = "mss"
    display_name = "MSS"

    def get_support_status(self, capturer: "DesktopFrameCapturer") -> tuple[bool, str]:
        if mss is None:
            return False, "mss_module_unavailable"
        return True, ""

    def is_supported(self, capturer: "DesktopFrameCapturer") -> bool:
        return mss is not None

    def grab(self, capturer: "DesktopFrameCapturer"):
        return capturer._grab_with_mss()

    def reset(self, capturer: "DesktopFrameCapturer", reason: str = "") -> None:
        capturer._release_mss_client()

    def close(self, capturer: "DesktopFrameCapturer") -> None:
        capturer._release_mss_client()


class GdiCaptureBackend(ICaptureBackend):
    backend_id = "gdi"
    display_name = "GDI BitBlt"

    def grab(self, capturer: "DesktopFrameCapturer"):
        return capturer._grab_with_gdi()


class ImageGrabCaptureBackend(ICaptureBackend):
    backend_id = "imagegrab"
    display_name = "PIL ImageGrab"

    def grab(self, capturer: "DesktopFrameCapturer"):
        return capturer._grab_with_imagegrab()


class PyAutoGuiCaptureBackend(ICaptureBackend):
    backend_id = "pyautogui"
    display_name = "PyAutoGUI"

    def get_support_status(self, capturer: "DesktopFrameCapturer") -> tuple[bool, str]:
        if pyautogui is None:
            return False, "pyautogui_module_unavailable"
        return True, ""

    def is_supported(self, capturer: "DesktopFrameCapturer") -> bool:
        return pyautogui is not None

    def grab(self, capturer: "DesktopFrameCapturer"):
        return capturer._grab_with_pyautogui()


def _is_dxgi_os_supported() -> bool:
    if os.name != "nt":
        return False
    try:
        version = sys.getwindowsversion()
    except Exception:
        return False
    return (int(version.major), int(version.minor)) >= (6, 2)


def _is_wgc_os_supported() -> bool:
    if os.name != "nt":
        return False
    try:
        version = sys.getwindowsversion()
    except Exception:
        return False
    return int(version.major) >= 10 and int(getattr(version, "build", 0) or 0) >= 18362


def _is_dwm_capture_os_supported() -> bool:
    if os.name != "nt":
        return False
    try:
        version = sys.getwindowsversion()
    except Exception:
        return False
    return int(version.major) >= 10


def create_capture_stack(
    legacy_capturer=None,
    *,
    runtime_mode: str | None = None,
    helper_backend: str | None = None,
    helper_session_id: int | None = None,
    helper_available: bool | None = None,
) -> CaptureStack:
    current_backend = str(getattr(legacy_capturer, "capture_backend", "") or "")
    helper_backend = str(helper_backend or "")
    runtime_mode = str(runtime_mode or "").strip() or (
        "service_capture_pending" if helper_available else "legacy_local_capture"
    )
    service_managed_mode = runtime_mode.startswith("service_")
    if runtime_mode == "service_helper_session_capture":
        implementation = "session_helper_hosted_capture"
    elif runtime_mode == "service_capture_pending":
        implementation = "service_managed_capture_pending"
    elif runtime_mode == "service_capture_unavailable":
        implementation = "service_managed_capture_unavailable"
    else:
        implementation = "legacy_screen_capturer_adapter"
    notes = [
        f"runtime_mode={runtime_mode}",
        "live_capture_pipeline=dxgi->wgc->dwm->mss->gdi->imagegrab->pyautogui",
        "non_persistent_remote_surfaces_block_transient_fallbacks",
        "freeze_detection_is_diagnostic_only",
    ]
    if service_managed_mode:
        notes.append("service_managed_capture_authoritative")
        notes.append("local_capture_fallback_disabled_for_remote_desktop")
    else:
        notes.append("legacy_local_capture_reserved_for_diagnostics")
    if current_backend:
        notes.append(f"legacy_backend_runtime={current_backend}")
    if helper_backend:
        notes.append(f"helper_backend={helper_backend}")
    if helper_session_id is not None:
        notes.append(f"helper_session_id={int(helper_session_id)}")

    return CaptureStack(
        capabilities=CaptureCapabilities(
            preferred_backend="session_helper_capture",
            dxgi_supported=_is_dxgi_os_supported(),
            gdi_supported=os.name == "nt",
            fallback_backend=(
                "disabled_in_remote_desktop_path"
                if service_managed_mode
                else ("mss_capture" if mss is not None else "gdi_capture")
            ),
            current_backend=helper_backend or current_backend or "pending",
            implementation=implementation,
            supports_frame_diff=True,
            target_fps=30,
            notes=notes,
        )
    )


class DesktopFrameCapturer:
    """共享的屏幕抓屏能力。

    这里优先使用 DXGI Desktop Duplication / MSS，
    保留本地兜底用的 ImageGrab / PyAutoGUI / GDI 能力，
    但真正的远控主链路应当优先由 Service 管理的 session helper 调用。
    """

    def __init__(self, backend_order: tuple[str, ...] | None = None):
        self.user32 = ctypes.WinDLL("user32", use_last_error=True)
        self.gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)
        self.kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        self.capture_backend = None
        self.failure_count = 0
        self.last_failure_reason = None
        self._default_backend_order = tuple(
            backend_order or ("dxgi", "wgc", "dwm", "mss", "gdi", "imagegrab", "pyautogui")
        )
        self.backend_order = self._default_backend_order
        self._backend_retry_after: dict[str, float] = {}
        self._backend_last_failure: dict[str, str] = {}
        self._backend_last_error: dict[str, str] = {}
        self._backend_last_attempt_at: dict[str, float] = {}
        self._backend_last_success_at: dict[str, float] = {}
        self._dxgi_camera = None
        self._mss_client = None
        self._desktop_signature = ""
        self._desktop_generation = 0
        self._virtual_screen_signature = ""
        self._capture_context_signature = ""
        self._display_substrate_signature = ""
        self._capture_context_label = "interactive_live"
        self._capture_blocker_reason = ""
        self._transient_backends: frozenset[str] = frozenset()
        self._last_prepare_reason = ""
        self._last_prepare_at = 0.0
        self._last_prepare_changed = False
        self._last_context_change_reasons: tuple[str, ...] = ()
        self._last_reset_reason = ""
        self._last_reset_at = 0.0
        self._last_reset_change_reasons: tuple[str, ...] = ()
        self._capture_attempt_sequence = 0
        self._last_capture_attempt: dict = {}
        self._last_recovery_hint: dict[str, object] = {}
        self._recent_capture_attempts: deque[dict] = deque(maxlen=8)
        self._native_backend_providers = {
            backend_name: self._build_native_backend_provider(backend_name)
            for backend_name in ("wgc", "dwm")
        }
        self._backend_registry: dict[str, ICaptureBackend] = {
            "blocked": BlockedCaptureBackend(),
            "dxgi": DxgiCaptureBackend(),
            "wgc": WindowsGraphicsCaptureBackend(),
            "dwm": DwmSharedSurfaceCaptureBackend(),
            "mss": MssCaptureBackend(),
            "gdi": GdiCaptureBackend(),
            "imagegrab": ImageGrabCaptureBackend(),
            "pyautogui": PyAutoGuiCaptureBackend(),
        }
        self._setup_windows_apis()

    @property
    def capture_blocker_reason(self) -> str:
        return self._capture_blocker_reason

    def describe_backend_state(self) -> dict:
        now = time.monotonic()
        backends: list[dict] = []
        enabled_backends = set(self.backend_order)

        for backend_name, backend in self._backend_registry.items():
            supported, support_reason = backend.get_support_status(self)
            retry_after = float(self._backend_retry_after.get(backend_name, 0.0) or 0.0)
            retry_after_seconds = max(0.0, retry_after - now)
            last_attempt_at = self._backend_last_attempt_at.get(backend_name)
            last_success_at = self._backend_last_success_at.get(backend_name)
            backend_failure = str(self._backend_last_failure.get(backend_name, "") or "")
            recovery_hint = self._build_recovery_hint(
                backend_name=backend_name,
                failure=backend_failure,
                support_reason=support_reason,
                transient_mode=backend_name in self._transient_backends,
                current_context_label=self._capture_context_label,
            )
            backends.append(
                {
                    "backend": backend_name,
                    "display_name": str(getattr(backend, "display_name", backend_name) or backend_name),
                    "enabled_in_strategy": backend_name in enabled_backends,
                    "selected_backend": backend_name == str(self.capture_backend or ""),
                    "transient_mode": backend_name in self._transient_backends,
                    "supported": bool(supported),
                    "support_reason": str(support_reason or ""),
                    "retry_ready": retry_after_seconds <= 0.0,
                    "retry_after_seconds": round(retry_after_seconds, 3),
                    "last_failure": backend_failure,
                    "last_error": str(self._backend_last_error.get(backend_name, "") or ""),
                    "last_attempt_age_seconds": (
                        None if last_attempt_at is None else round(max(0.0, now - last_attempt_at), 3)
                    ),
                    "last_success_age_seconds": (
                        None if last_success_at is None else round(max(0.0, now - last_success_at), 3)
                    ),
                    "native_provider": self._describe_native_backend_provider(backend_name),
                    "recovery_hint": recovery_hint,
                }
            )

        return {
            "current_backend": str(self.capture_backend or ""),
            "active_backend": str(self.capture_backend or ""),
            "failure_count": int(self.failure_count or 0),
            "last_failure_reason": str(self.last_failure_reason or ""),
            "blocker_reason": str(self._capture_blocker_reason or ""),
            "context_label": str(self._capture_context_label or ""),
            "recovery_hint": dict(self._last_recovery_hint or {}),
            "last_prepare_reason": str(self._last_prepare_reason or ""),
            "last_prepare_changed": bool(self._last_prepare_changed),
            "last_prepare_age_seconds": (
                None
                if not self._last_prepare_at
                else round(max(0.0, now - self._last_prepare_at), 3)
            ),
            "last_context_change_reasons": list(self._last_context_change_reasons),
            "last_reset_reason": str(self._last_reset_reason or ""),
            "last_reset_age_seconds": (
                None
                if not self._last_reset_at
                else round(max(0.0, now - self._last_reset_at), 3)
            ),
            "last_reset_change_reasons": list(self._last_reset_change_reasons),
            "desktop_generation": int(self._desktop_generation or 0),
            "desktop_signature": str(self._desktop_signature or ""),
            "virtual_screen_signature": str(self._virtual_screen_signature or ""),
            "capture_context_signature": str(self._capture_context_signature or ""),
            "display_substrate_signature": str(self._display_substrate_signature or ""),
            "backend_order": list(self.backend_order),
            "transient_backends": list(self._transient_backends),
            "last_capture_attempt": dict(self._last_capture_attempt or {}),
            "recent_capture_attempts": [dict(item) for item in self._recent_capture_attempts],
            "backends": backends,
        }

    def _setup_windows_apis(self):
        self.user32.GetDesktopWindow.restype = wintypes.HWND
        self.user32.GetWindowDC.argtypes = [wintypes.HWND]
        self.user32.GetWindowDC.restype = wintypes.HDC
        self.user32.ReleaseDC.argtypes = [wintypes.HWND, wintypes.HDC]
        self.user32.ReleaseDC.restype = wintypes.INT
        self.user32.GetSystemMetrics.argtypes = [wintypes.INT]
        self.user32.GetSystemMetrics.restype = wintypes.INT
        self.user32.OpenInputDesktop.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        self.user32.OpenInputDesktop.restype = wintypes.HANDLE
        self.user32.CloseDesktop.argtypes = [wintypes.HANDLE]
        self.user32.CloseDesktop.restype = wintypes.BOOL
        self.kernel32.GetCurrentProcessId.restype = wintypes.DWORD
        self.kernel32.ProcessIdToSessionId.argtypes = [wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)]
        self.kernel32.ProcessIdToSessionId.restype = wintypes.BOOL
        self.kernel32.WTSGetActiveConsoleSessionId.restype = wintypes.DWORD

        # GDI handles are pointer-sized on 64-bit Windows. Without these
        # signatures ctypes truncates them to C ints before BitBlt/GetDIBits.
        self.gdi32.CreateCompatibleDC.argtypes = [wintypes.HDC]
        self.gdi32.CreateCompatibleDC.restype = wintypes.HDC
        self.gdi32.CreateCompatibleBitmap.argtypes = [wintypes.HDC, wintypes.INT, wintypes.INT]
        self.gdi32.CreateCompatibleBitmap.restype = wintypes.HANDLE
        self.gdi32.SelectObject.argtypes = [wintypes.HDC, wintypes.HANDLE]
        self.gdi32.SelectObject.restype = wintypes.HANDLE
        self.gdi32.BitBlt.argtypes = [
            wintypes.HDC,
            wintypes.INT,
            wintypes.INT,
            wintypes.INT,
            wintypes.INT,
            wintypes.HDC,
            wintypes.INT,
            wintypes.INT,
            wintypes.DWORD,
        ]
        self.gdi32.BitBlt.restype = wintypes.BOOL
        self.gdi32.GetDIBits.argtypes = [
            wintypes.HDC,
            wintypes.HANDLE,
            wintypes.UINT,
            wintypes.UINT,
            ctypes.c_void_p,
            ctypes.c_void_p,
            wintypes.UINT,
        ]
        self.gdi32.GetDIBits.restype = wintypes.INT
        self.gdi32.DeleteObject.argtypes = [wintypes.HANDLE]
        self.gdi32.DeleteObject.restype = wintypes.BOOL
        self.gdi32.DeleteDC.argtypes = [wintypes.HDC]
        self.gdi32.DeleteDC.restype = wintypes.BOOL

    def capture_raw(self):
        """捕获原始屏幕图像。"""
        capture_attempt = self._begin_capture_attempt()
        if self._capture_blocker_reason:
            self._finalize_capture_attempt(
                capture_attempt,
                outcome="blocked",
                blocker_reason=self._capture_blocker_reason,
                final_failure=self._capture_blocker_reason,
            )
            self._record_capture_failure([self._capture_blocker_reason])
            return None

        errors: list[str] = []

        for backend_name in self.backend_order:
            backend_step = {
                "backend": backend_name,
                "transient_mode": backend_name in self._transient_backends,
            }
            if not self._backend_ready_for_retry(backend_name):
                retry_after = float(self._backend_retry_after.get(backend_name, 0.0) or 0.0)
                backend_step["status"] = "skipped_retry_cooldown"
                backend_step["retry_after_seconds"] = round(
                    max(0.0, retry_after - time.monotonic()),
                    3,
                )
                backend_step["recovery_hint"] = self._build_recovery_hint(
                    backend_name=backend_name,
                    support_reason="retry_cooldown_active",
                    step_status="skipped_retry_cooldown",
                    transient_mode=backend_name in self._transient_backends,
                    current_context_label=self._capture_context_label,
                )
                capture_attempt["steps"].append(backend_step)
                continue
            backend = self._backend_registry.get(backend_name)
            if backend is None:
                backend_step["status"] = "skipped_unregistered"
                backend_step["recovery_hint"] = self._build_recovery_hint(
                    backend_name=backend_name,
                    support_reason="backend_unregistered",
                    step_status="skipped_unregistered",
                    transient_mode=backend_name in self._transient_backends,
                    current_context_label=self._capture_context_label,
                )
                capture_attempt["steps"].append(backend_step)
                continue
            supported, support_reason = backend.get_support_status(self)
            if not supported:
                backend_step["status"] = "skipped_unsupported"
                backend_step["support_reason"] = str(support_reason or "")
                backend_step["recovery_hint"] = self._build_recovery_hint(
                    backend_name=backend_name,
                    support_reason=support_reason,
                    step_status="skipped_unsupported",
                    transient_mode=backend_name in self._transient_backends,
                    current_context_label=self._capture_context_label,
                )
                capture_attempt["steps"].append(backend_step)
                continue
            self._backend_last_attempt_at[backend_name] = time.monotonic()
            try:
                backend.prepare(self)
                screenshot = backend.grab(self)
            except Exception as exc:
                failure_classification = backend.classify_failure(self, exc)
                self._backend_last_failure[backend_name] = str(failure_classification or "")
                self._backend_last_error[backend_name] = str(exc)
                with contextlib.suppress(Exception):
                    backend.reset(self, reason=failure_classification)
                self._mark_backend_retry_after(backend_name, failure_classification)
                retry_after = float(self._backend_retry_after.get(backend_name, 0.0) or 0.0)
                backend_step["status"] = "failed"
                backend_step["failure"] = str(failure_classification or "")
                backend_step["error"] = str(exc)
                backend_step["retry_after_seconds"] = round(
                    max(0.0, retry_after - time.monotonic()),
                    3,
                )
                backend_step["recovery_hint"] = self._build_recovery_hint(
                    backend_name=backend_name,
                    failure=failure_classification,
                    step_status="failed",
                    transient_mode=backend_name in self._transient_backends,
                    current_context_label=self._capture_context_label,
                )
                capture_attempt["steps"].append(backend_step)
                errors.append(f"{backend_name}:{failure_classification}: {exc}")
                screenshot = None

            if screenshot is not None:
                self._backend_last_success_at[backend_name] = time.monotonic()
                self._backend_last_failure[backend_name] = ""
                self._backend_last_error[backend_name] = ""
                backend_step["status"] = "captured"
                backend_step["width"] = int(getattr(screenshot, "width", 0) or 0)
                backend_step["height"] = int(getattr(screenshot, "height", 0) or 0)
                backend_step["recovery_hint"] = {}
                capture_attempt["steps"].append(backend_step)
                self._last_recovery_hint = {}
                self._finalize_capture_attempt(
                    capture_attempt,
                    outcome="captured",
                    selected_backend=backend_name,
                )
                return screenshot

        self._finalize_capture_attempt(
            capture_attempt,
            outcome=self._derive_failed_capture_attempt_outcome(capture_attempt),
            final_failure=(errors[-1] if errors else str(self.last_failure_reason or "")),
        )
        self._record_capture_failure(errors)
        return None

    def _begin_capture_attempt(self) -> dict:
        self._capture_attempt_sequence += 1
        return {
            "sequence": int(self._capture_attempt_sequence),
            "started_at": round(time.time(), 3),
            "desktop_generation": int(self._desktop_generation or 0),
            "context_label": str(self._capture_context_label or ""),
            "blocker_reason": str(self._capture_blocker_reason or ""),
            "backend_order": list(self.backend_order),
            "steps": [],
            "_started_monotonic": time.monotonic(),
        }

    def _finalize_capture_attempt(
        self,
        capture_attempt: dict,
        *,
        outcome: str,
        selected_backend: str = "",
        blocker_reason: str = "",
        final_failure: str = "",
    ) -> None:
        finished_monotonic = time.monotonic()
        started_monotonic = float(capture_attempt.pop("_started_monotonic", finished_monotonic) or 0.0)
        capture_attempt["finished_at"] = round(time.time(), 3)
        capture_attempt["duration_ms"] = round(max(0.0, finished_monotonic - started_monotonic) * 1000.0, 3)
        capture_attempt["outcome"] = str(outcome or "")
        capture_attempt["selected_backend"] = str(selected_backend or "")
        capture_attempt["blocker_reason"] = str(
            blocker_reason or capture_attempt.get("blocker_reason") or ""
        )
        capture_attempt["final_failure"] = str(final_failure or "")
        capture_attempt["recovery_hint"] = self._derive_capture_attempt_recovery_hint(capture_attempt)
        snapshot = dict(capture_attempt)
        snapshot["backend_order"] = list(capture_attempt.get("backend_order") or [])
        snapshot["steps"] = [dict(item) for item in (capture_attempt.get("steps") or [])]
        snapshot["recovery_hint"] = dict(capture_attempt.get("recovery_hint") or {})
        self._last_capture_attempt = snapshot
        self._last_recovery_hint = dict(snapshot.get("recovery_hint") or {})
        self._recent_capture_attempts.append(snapshot)

    def _derive_failed_capture_attempt_outcome(self, capture_attempt: dict) -> str:
        steps = list(capture_attempt.get("steps") or [])
        if not steps:
            return "no_backend_attempted"
        statuses = {str((item or {}).get("status") or "").strip().lower() for item in steps}
        if "failed" in statuses:
            return "failed"
        if statuses == {"skipped_retry_cooldown"}:
            return "all_backends_in_cooldown"
        if statuses.issubset({"skipped_unsupported", "skipped_unregistered"}):
            return "no_supported_backend"
        if "skipped_retry_cooldown" in statuses and statuses.issubset(
            {"skipped_retry_cooldown", "skipped_unsupported", "skipped_unregistered"}
        ):
            return "no_retry_ready_backend"
        return "capture_unavailable"

    def build_signature(self, screenshot, sample_size=(48, 27)):
        """构建低成本签名，用于检测画面是否变化。"""
        thumb = None
        grayscale = None
        try:
            grayscale = screenshot.convert("L")
            thumb = grayscale.resize(sample_size, Image.Resampling.BILINEAR)
            return zlib.adler32(thumb.tobytes())
        finally:
            if thumb is not None:
                thumb.close()
            if grayscale is not None:
                grayscale.close()

    def encode_frame(self, screenshot, quality=85, scale=1.0):
        """编码截图为 JPEG base64。"""
        working = screenshot
        converted = None
        resized = None
        buffer = None

        try:
            if screenshot.mode != "RGB":
                converted = screenshot.convert("RGB")
                working = converted

            if scale != 1.0:
                new_width = max(1, int(working.width * scale))
                new_height = max(1, int(working.height * scale))
                # BILINEAR 比 LANCZOS 快 2 倍以上，远控场景画质差异可忽略
                resized = working.resize((new_width, new_height), Image.Resampling.BILINEAR)
                working = resized

            buffer = io.BytesIO()
            # optimize=True 是慢速优化模式（2-5倍耗时），远控低延迟场景关闭
            working.save(buffer, format="JPEG", quality=quality, optimize=False)
            jpeg_data = buffer.getvalue()

            return {
                "data": base64.b64encode(jpeg_data).decode("utf-8"),
                "width": working.width,
                "height": working.height,
                "size": len(jpeg_data),
            }
        finally:
            if buffer is not None:
                buffer.close()
            if resized is not None:
                resized.close()
            if converted is not None:
                converted.close()

    def capture(self, quality=85, scale=1.0):
        """兼容旧接口：捕获并编码屏幕。"""
        screenshot = self.capture_raw()
        if screenshot is None:
            return None

        try:
            return self.encode_frame(screenshot, quality=quality, scale=scale)
        except Exception as exc:
            self._record_capture_failure([f"JPEG编码: {exc}"])
            return None
        finally:
            with contextlib.suppress(Exception):
                screenshot.close()

    def close(self):
        for backend in self._backend_registry.values():
            with contextlib.suppress(Exception):
                backend.close(self)

    def __del__(self):
        with contextlib.suppress(Exception):
            self.close()

    def prepare_for_desktop(
        self,
        desktop_signature: str | None,
        *,
        desktop_state: dict | None = None,
        reason: str = "",
    ) -> bool:
        normalized_signature = str(desktop_signature or "").strip()
        current_virtual_signature = self._build_virtual_screen_signature(
            desktop_state=desktop_state,
        )
        current_display_substrate_signature = self._build_display_substrate_signature(
            desktop_state=desktop_state,
        )
        current_context_signature = self._build_capture_context_signature(
            desktop_signature=normalized_signature,
            desktop_state=desktop_state,
        )
        next_backend_order, next_context_label, next_transient_backends = self._resolve_backend_strategy(
            desktop_state=desktop_state,
        )
        signature_changed = normalized_signature and normalized_signature != self._desktop_signature
        virtual_signature_changed = bool(current_virtual_signature) and (
            current_virtual_signature != self._virtual_screen_signature
        )
        context_signature_changed = bool(current_context_signature) and (
            current_context_signature != self._capture_context_signature
        )
        display_substrate_changed = bool(current_display_substrate_signature) and (
            current_display_substrate_signature != self._display_substrate_signature
        )
        backend_strategy_changed = (
            tuple(next_backend_order) != tuple(self.backend_order)
            or next_context_label != self._capture_context_label
            or frozenset(next_transient_backends) != self._transient_backends
        )
        context_change_reasons: list[str] = []
        if signature_changed:
            context_change_reasons.append("desktop_signature_changed")
        if virtual_signature_changed:
            context_change_reasons.append("virtual_screen_changed")
        if context_signature_changed:
            context_change_reasons.append("capture_context_changed")
        if display_substrate_changed:
            context_change_reasons.append("display_substrate_changed")
        if backend_strategy_changed:
            context_change_reasons.append("backend_strategy_changed")

        self._last_prepare_reason = str(reason or "")
        self._last_prepare_at = time.monotonic()
        self._last_prepare_changed = bool(context_change_reasons)
        self._last_context_change_reasons = tuple(context_change_reasons)

        if (
            signature_changed
            or virtual_signature_changed
            or context_signature_changed
            or display_substrate_changed
            or backend_strategy_changed
        ):
            self._reset_capture_resources(
                reason=reason or "capture_context_changed",
                next_signature=normalized_signature or self._desktop_signature,
                next_virtual_signature=current_virtual_signature or self._virtual_screen_signature,
                next_context_signature=current_context_signature or self._capture_context_signature,
                next_display_substrate_signature=(
                    current_display_substrate_signature or self._display_substrate_signature
                ),
                next_backend_order=next_backend_order,
                next_context_label=next_context_label,
                next_transient_backends=next_transient_backends,
                context_change_reasons=tuple(context_change_reasons),
                desktop_state=desktop_state,
            )
            return True

        if normalized_signature and not self._desktop_signature:
            self._desktop_signature = normalized_signature
        if current_virtual_signature and not self._virtual_screen_signature:
            self._virtual_screen_signature = current_virtual_signature
        if current_context_signature and not self._capture_context_signature:
            self._capture_context_signature = current_context_signature
        if current_display_substrate_signature and not self._display_substrate_signature:
            self._display_substrate_signature = current_display_substrate_signature
        self.backend_order = tuple(next_backend_order)
        self._capture_context_label = str(next_context_label or self._capture_context_label)
        self._transient_backends = frozenset(next_transient_backends or ())
        return False

    def _grab_with_dxgi(self):
        if dxcam is None:
            raise RuntimeError("dxcam unavailable")
        if not _is_dxgi_os_supported():
            raise RuntimeError("dxgi unsupported on current OS")

        try:
            camera = self._get_dxgi_camera()
            frame = camera.grab(copy=True, new_frame_only=False)
            if frame is None:
                raise RuntimeError("dxgi returned no frame")

            screenshot = Image.fromarray(frame, "RGB")
            self._validate_virtual_capture_geometry("DXGI", screenshot)
            self._note_backend("DXGI")
            return screenshot
        except Exception:
            self._release_dxgi_camera()
            raise

    def _grab_with_wgc(self):
        return self._grab_with_native_backend("wgc")

    def _grab_with_dwm(self):
        return self._grab_with_native_backend("dwm")

    def _grab_with_mss(self):
        if mss is None:
            raise RuntimeError("mss unavailable")

        transient_client = None
        try:
            if "mss" in self._transient_backends:
                factory = getattr(mss, "MSS", None) or getattr(mss, "mss", None)
                if factory is None:
                    raise RuntimeError("mss factory unavailable")
                transient_client = factory()
            client = transient_client or self._get_mss_client()
            monitor = client.monitors[0]
            shot = client.grab(monitor)
            screenshot = Image.frombytes("RGB", shot.size, shot.rgb)
            self._note_backend("MSS")
            return screenshot
        except Exception:
            self._release_mss_client()
            raise
        finally:
            if transient_client is not None:
                with contextlib.suppress(Exception):
                    transient_client.close()

    def _grab_with_imagegrab(self):
        screenshot = ImageGrab.grab(all_screens=True)
        self._note_backend("ImageGrab")
        return screenshot

    def _grab_with_pyautogui(self):
        if pyautogui is None:
            raise RuntimeError("pyautogui unavailable")
        # 输入注入按整个虚拟桌面解算坐标，兜底抓屏必须覆盖同一区域，
        # 否则多显示器场景下画面与鼠标位置会系统性错位。
        try:
            screenshot = ImageGrab.grab(all_screens=True)
        except Exception:
            screenshot = pyautogui.screenshot()
        self._note_backend("PyAutoGUI")
        return screenshot

    def _grab_with_gdi(self):
        virtual_x = self.user32.GetSystemMetrics(76)
        virtual_y = self.user32.GetSystemMetrics(77)
        screen_width = self.user32.GetSystemMetrics(78)
        screen_height = self.user32.GetSystemMetrics(79)

        if screen_width <= 0 or screen_height <= 0:
            raise RuntimeError(f"invalid virtual screen size: {screen_width}x{screen_height}")

        hdesktop = self.user32.GetDesktopWindow()
        desktop_dc = self.user32.GetWindowDC(hdesktop)
        if not desktop_dc:
            raise ctypes.WinError(ctypes.get_last_error())

        img_dc = self.gdi32.CreateCompatibleDC(desktop_dc)
        bitmap = self.gdi32.CreateCompatibleBitmap(desktop_dc, screen_width, screen_height)
        if not img_dc or not bitmap:
            if bitmap:
                self.gdi32.DeleteObject(bitmap)
            if img_dc:
                self.gdi32.DeleteDC(img_dc)
            self.user32.ReleaseDC(hdesktop, desktop_dc)
            raise ctypes.WinError(ctypes.get_last_error())

        old_bitmap = self.gdi32.SelectObject(img_dc, bitmap)

        class BITMAPINFOHEADER(ctypes.Structure):
            _fields_ = [
                ("biSize", wintypes.DWORD),
                ("biWidth", wintypes.LONG),
                ("biHeight", wintypes.LONG),
                ("biPlanes", wintypes.WORD),
                ("biBitCount", wintypes.WORD),
                ("biCompression", wintypes.DWORD),
                ("biSizeImage", wintypes.DWORD),
                ("biXPelsPerMeter", wintypes.LONG),
                ("biYPelsPerMeter", wintypes.LONG),
                ("biClrUsed", wintypes.DWORD),
                ("biClrImportant", wintypes.DWORD),
            ]

        class BITMAPINFO(ctypes.Structure):
            _fields_ = [
                ("bmiHeader", BITMAPINFOHEADER),
                ("bmiColors", wintypes.DWORD * 3),
            ]

        try:
            SRCCOPY = 0x00CC0020
            CAPTUREBLT = 0x40000000
            copied = self.gdi32.BitBlt(
                img_dc,
                0,
                0,
                screen_width,
                screen_height,
                desktop_dc,
                virtual_x,
                virtual_y,
                SRCCOPY | CAPTUREBLT,
            )
            if not copied:
                raise ctypes.WinError(ctypes.get_last_error())

            bmi = BITMAPINFO()
            bmi.bmiHeader.biSize = ctypes.sizeof(BITMAPINFOHEADER)
            bmi.bmiHeader.biWidth = screen_width
            bmi.bmiHeader.biHeight = -screen_height
            bmi.bmiHeader.biPlanes = 1
            bmi.bmiHeader.biBitCount = 32
            bmi.bmiHeader.biCompression = 0

            buffer_size = screen_width * screen_height * 4
            buffer = ctypes.create_string_buffer(buffer_size)
            result = self.gdi32.GetDIBits(
                img_dc,
                bitmap,
                0,
                screen_height,
                buffer,
                ctypes.byref(bmi),
                0,
            )
            if result == 0:
                raise ctypes.WinError(ctypes.get_last_error())

            self._note_backend("GDI")
            image = Image.frombuffer("RGBA", (screen_width, screen_height), buffer, "raw", "BGRA", 0, 1)
            return image.convert("RGB")
        finally:
            self.gdi32.SelectObject(img_dc, old_bitmap)
            self.gdi32.DeleteObject(bitmap)
            self.gdi32.DeleteDC(img_dc)
            self.user32.ReleaseDC(hdesktop, desktop_dc)

    def _get_dxgi_camera(self):
        if self._dxgi_camera is None:
            self._dxgi_camera = dxcam.create(
                output_color="RGB",
                max_buffer_len=4,
                backend="dxgi",
                processor_backend="numpy",
            )
        return self._dxgi_camera

    def _release_dxgi_camera(self):
        camera = self._dxgi_camera
        self._dxgi_camera = None
        if camera is None:
            return
        with contextlib.suppress(Exception):
            camera.stop()
        with contextlib.suppress(Exception):
            camera.release()

    def _get_mss_client(self):
        if self._mss_client is None:
            factory = getattr(mss, "MSS", None) or getattr(mss, "mss", None)
            if factory is None:
                raise RuntimeError("mss factory unavailable")
            self._mss_client = factory()
        return self._mss_client

    def _release_mss_client(self):
        client = self._mss_client
        self._mss_client = None
        if client is None:
            return
        with contextlib.suppress(Exception):
            client.close()

    def _build_native_backend_provider(self, backend_name: str):
        normalized_backend_name = str(backend_name or "").strip().lower()
        if not normalized_backend_name or create_capture_backend_provider is None:
            return None
        try:
            return create_capture_backend_provider(normalized_backend_name)
        except Exception:
            return None

    def _get_native_backend_provider(self, backend_name: str):
        normalized_backend_name = str(backend_name or "").strip().lower()
        return self._native_backend_providers.get(normalized_backend_name)

    def _prepare_native_backend(self, backend_name: str) -> None:
        provider = self._get_native_backend_provider(backend_name)
        if provider is None:
            raise RuntimeError(f"{backend_name}_provider_unavailable")
        provider.prepare(self)

    def _grab_with_native_backend(self, backend_name: str):
        provider = self._get_native_backend_provider(backend_name)
        if provider is None:
            raise RuntimeError(f"{backend_name}_provider_unavailable")
        screenshot = provider.grab(self)
        self._note_backend(backend_name.upper())
        return screenshot

    def _reset_native_backend(self, backend_name: str, *, reason: str = "") -> None:
        provider = self._get_native_backend_provider(backend_name)
        if provider is None:
            return
        provider.reset(self, reason=reason)

    def _close_native_backend(self, backend_name: str) -> None:
        provider = self._get_native_backend_provider(backend_name)
        if provider is None:
            return
        provider.close(self)

    def _describe_native_backend_provider(self, backend_name: str) -> dict:
        provider = self._get_native_backend_provider(backend_name)
        if provider is None:
            return {
                "backend": str(backend_name or "").strip().lower(),
                "provider_loaded": False,
                "load_error": "provider_not_configured",
            }
        try:
            return dict(provider.describe_state() or {})
        except Exception as exc:
            return {
                "backend": str(backend_name or "").strip().lower(),
                "provider_loaded": False,
                "load_error": f"provider_describe_error:{exc}",
            }

    def _reset_capture_resources(
        self,
        *,
        reason: str,
        next_signature: str,
        next_virtual_signature: str,
        next_context_signature: str,
        next_display_substrate_signature: str,
        next_backend_order: tuple[str, ...],
        next_context_label: str,
        next_transient_backends: frozenset[str] | set[str] | tuple[str, ...],
        context_change_reasons: tuple[str, ...] | list[str] = (),
        desktop_state: dict | None = None,
    ) -> None:
        previous_signature = self._desktop_signature or "uninitialized"
        previous_virtual_signature = self._virtual_screen_signature or "uninitialized"
        previous_context_signature = self._capture_context_signature or "uninitialized"
        previous_display_substrate_signature = self._display_substrate_signature or "uninitialized"
        previous_backend_order = ",".join(self.backend_order) or "uninitialized"
        previous_context_label = self._capture_context_label or "uninitialized"
        for backend in self._backend_registry.values():
            with contextlib.suppress(Exception):
                backend.close(self)
        self._backend_retry_after.clear()
        self._backend_last_failure.clear()
        self._backend_last_error.clear()
        self._backend_last_attempt_at.clear()
        self._backend_last_success_at.clear()
        self.failure_count = 0
        self.last_failure_reason = None
        self.capture_backend = None
        self._last_recovery_hint = {}
        self._desktop_signature = str(next_signature or "")
        self._virtual_screen_signature = str(next_virtual_signature or "")
        self._capture_context_signature = str(next_context_signature or "")
        self._display_substrate_signature = str(next_display_substrate_signature or "")
        self.backend_order = tuple(
            self._default_backend_order if next_backend_order is None else next_backend_order
        )
        self._capture_context_label = str(next_context_label or "interactive_live")
        self._capture_blocker_reason = (
            f"blocked_non_persistent_capture_surface:{self._capture_context_label}"
            if not self.backend_order
            else ""
        )
        self._transient_backends = frozenset(next_transient_backends or ())
        self._last_reset_reason = str(reason or "")
        self._last_reset_at = time.monotonic()
        self._last_reset_change_reasons = tuple(
            str(item).strip() for item in (context_change_reasons or ()) if str(item).strip()
        )
        self._desktop_generation += 1
        print(
            "[RemoteDesktop] Capture resources reset: "
            f"reason={reason or 'unknown'} generation={self._desktop_generation} "
            f"desktop_signature={self._desktop_signature or 'unknown'} "
            f"virtual_screen={self._virtual_screen_signature or 'unknown'} "
            f"display_substrate={self._display_substrate_signature or 'unknown'} "
            f"context={self._capture_context_label or 'unknown'} "
            f"backend_order={','.join(self.backend_order) or 'unknown'} "
            f"previous_desktop_signature={previous_signature} "
            f"previous_virtual_screen={previous_virtual_signature} "
            f"previous_display_substrate={previous_display_substrate_signature} "
            f"previous_context={previous_context_label} "
            f"previous_backend_order={previous_backend_order}"
        )
        print(
            "[RemoteDesktop] Capture context signature: "
            f"current={self._capture_context_signature or 'unknown'} "
            f"previous={previous_context_signature}"
        )
        if self._last_reset_change_reasons:
            print(
                "[RemoteDesktop] Capture context change reasons: "
                f"{','.join(self._last_reset_change_reasons)}"
            )
        print(
            "[RemoteDesktop] Capture substrate signature: "
            f"current={self._display_substrate_signature or 'unknown'} "
            f"previous={previous_display_substrate_signature}"
        )
        if desktop_state:
            print(
                "[RemoteDesktop] Capture desktop topology: "
                f"session={desktop_state.get('session_id', 'unknown')} "
                f"console={desktop_state.get('active_console_session_id', 'unknown')} "
                f"input={desktop_state.get('input_desktop', 'unknown')} "
                f"thread={desktop_state.get('thread_desktop', 'unknown')} "
                f"capture={desktop_state.get('capture_desktop', 'unknown')} "
                f"kind={desktop_state.get('capture_desktop_kind', desktop_state.get('desktop_kind', 'unknown'))} "
                f"target_session={desktop_state.get('capture_target_session_id', 'unknown')} "
                f"target_state={desktop_state.get('capture_target_state', 'unknown')} "
                f"target_station={desktop_state.get('capture_target_station_name', 'unknown')} "
                f"remote={desktop_state.get('is_remote_session', False)} "
                f"substrate={desktop_state.get('display_substrate_class', 'unknown')} "
                f"continuity={desktop_state.get('display_continuity_mode', 'unknown')} "
                f"persistent={desktop_state.get('display_persistent', False)} "
                f"surface_available={desktop_state.get('display_surface_available', False)} "
                f"virtual_origin={desktop_state.get('virtual_screen_origin', '0,0')} "
                f"virtual_size={desktop_state.get('virtual_screen_size', '0x0')}"
            )

    def _resolve_backend_strategy(
        self,
        *,
        desktop_state: dict | None = None,
    ) -> tuple[tuple[str, ...], str, frozenset[str]]:
        snapshot = dict(desktop_state or {})
        substrate_class = self._normalize_capture_value(
            snapshot.get("display_substrate_class") or snapshot.get("substrate_class")
        )
        continuity_mode = self._normalize_capture_value(
            snapshot.get("display_continuity_mode") or snapshot.get("continuity_mode")
        )
        capture_desktop_kind = self._normalize_capture_value(
            snapshot.get("capture_desktop_kind")
            or snapshot.get("desktop_kind")
            or snapshot.get("input_desktop_kind")
        )
        capture_target_state = self._normalize_capture_value(snapshot.get("capture_target_state"))
        capture_desktop_name = self._normalize_capture_value(snapshot.get("capture_desktop"))
        is_console_target = self._coerce_bool(snapshot.get("capture_target_is_console_session"))
        is_remote_target = self._coerce_bool(
            snapshot.get("capture_target_is_remote_session"),
            self._coerce_bool(snapshot.get("is_remote_session")),
        )
        display_surface_available = self._coerce_bool(
            snapshot.get("display_surface_available"),
            True,
        )
        display_persistent = self._coerce_bool(snapshot.get("display_persistent"))
        virtual_display_attached = self._coerce_bool(snapshot.get("virtual_display_attached"))
        physical_display_attached = self._coerce_bool(snapshot.get("physical_display_attached"))
        secure_desktop_surface = self._coerce_bool(snapshot.get("secure_desktop_surface"))
        disconnected_surface = self._coerce_bool(snapshot.get("disconnected_surface"))

        if disconnected_surface or substrate_class == "disconnected_surface":
            return (
                (),
                "disconnected_surface",
                frozenset(),
            )

        if not display_surface_available and not display_persistent:
            return (
                (),
                "display_surface_unavailable",
                frozenset(),
            )

        if substrate_class == "remote_session_surface":
            return (
                (),
                "remote_session_surface",
                frozenset(),
            )

        if substrate_class == "virtual_display_surface":
            return (
                ("dxgi", "wgc", "dwm", "mss", "gdi", "imagegrab", "pyautogui"),
                "virtual_console_surface",
                frozenset(),
            )

        if substrate_class == "physical_console_surface":
            return (
                ("dxgi", "wgc", "dwm", "mss", "gdi", "imagegrab", "pyautogui"),
                "physical_console_surface",
                frozenset(),
            )

        if substrate_class == "secure_console_surface" or secure_desktop_surface:
            return (
                ("dwm", "gdi", "mss", "imagegrab", "pyautogui"),
                "secure_console_surface",
                frozenset({"mss"}),
            )

        if substrate_class == "console_headless_surface":
            return (
                (),
                "console_headless_surface",
                frozenset(),
            )

        if substrate_class == "interactive_display_surface":
            return (
                ("dxgi", "wgc", "dwm", "mss", "gdi", "imagegrab", "pyautogui"),
                "interactive_display_surface",
                frozenset(),
            )

        if (
            capture_desktop_kind in {"secure_winlogon", "disconnected_shell", "screensaver", "unavailable"}
            or capture_desktop_name in {"winlogon", "disconnect"}
            or capture_target_state in {"disconnected", "down", "reset"}
        ):
            return (
                (),
                "secure_or_disconnected",
                frozenset(),
            )

        if (
            capture_target_state in {"connected", "connectquery", "shadow", "listen"}
            or capture_desktop_kind not in {"interactive_default", ""}
        ):
            return (
                (),
                continuity_mode or "session_transition",
                frozenset(),
            )

        if is_console_target and not is_remote_target and (physical_display_attached or virtual_display_attached):
            return (
                ("dxgi", "wgc", "dwm", "mss", "gdi", "imagegrab", "pyautogui"),
                "interactive_console",
                frozenset(),
            )

        return (
            self._default_backend_order,
            "interactive_live",
            frozenset(),
        )

    def _build_capture_context_signature(
        self,
        *,
        desktop_signature: str = "",
        desktop_state: dict | None = None,
    ) -> str:
        snapshot = dict(desktop_state or {})
        target_signature = str(snapshot.get("capture_target_signature") or "").strip()
        capture_desktop = self._normalize_capture_value(snapshot.get("capture_desktop"))
        capture_kind = self._normalize_capture_value(
            snapshot.get("capture_desktop_kind")
            or snapshot.get("desktop_kind")
            or snapshot.get("input_desktop_kind")
        )
        thread_desktop = self._normalize_capture_value(snapshot.get("thread_desktop"))
        input_desktop = self._normalize_capture_value(snapshot.get("input_desktop"))
        capture_candidates = self._normalize_capture_value(snapshot.get("capture_desktop_candidates"))
        target_session_id = self._normalize_capture_value(
            snapshot.get("capture_target_session_id") or snapshot.get("session_id")
        )
        target_state = self._normalize_capture_value(snapshot.get("capture_target_state"))
        target_station = self._normalize_capture_value(snapshot.get("capture_target_station_name"))
        virtual_signature = self._build_virtual_screen_signature(desktop_state=desktop_state)
        display_substrate_signature = self._build_display_substrate_signature(
            desktop_state=desktop_state
        )
        capture_matches_target = 1 if self._coerce_bool(snapshot.get("capture_thread_matches_target")) else 0
        target_is_remote = 1 if self._coerce_bool(snapshot.get("capture_target_is_remote_session")) else 0
        target_is_console = 1 if self._coerce_bool(snapshot.get("capture_target_is_console_session")) else 0
        return "|".join(
            [
                f"desktop={desktop_signature or 'unknown'}",
                f"target={target_signature or 'unknown'}",
                f"capture={capture_desktop or 'unknown'}",
                f"kind={capture_kind or 'unknown'}",
                f"thread={thread_desktop or 'unknown'}",
                f"input={input_desktop or 'unknown'}",
                f"candidates={capture_candidates or 'unknown'}",
                f"target_session={target_session_id or 'unknown'}",
                f"target_state={target_state or 'unknown'}",
                f"target_station={target_station or 'unknown'}",
                f"target_remote={target_is_remote}",
                f"target_console={target_is_console}",
                f"thread_match={capture_matches_target}",
                f"virtual={virtual_signature or '0,0:0x0'}",
                f"substrate={display_substrate_signature or 'unknown'}",
            ]
        )

    def _backend_ready_for_retry(self, backend_name: str) -> bool:
        return time.monotonic() >= float(self._backend_retry_after.get(backend_name, 0.0))

    def _mark_backend_retry_after(self, backend_name: str, failure_classification: str = ""):
        cooldown_seconds = 1.0
        normalized_classification = self._normalize_capture_value(failure_classification)
        if backend_name == "dxgi":
            cooldown_seconds = 5.0
        elif backend_name == "wgc":
            cooldown_seconds = 4.0
        elif backend_name == "dwm":
            cooldown_seconds = 4.0
        elif backend_name == "mss":
            cooldown_seconds = 2.0
        if normalized_classification in {"dxgi_access_lost", "session_changed"}:
            cooldown_seconds = max(cooldown_seconds, 8.0)
        elif normalized_classification in {"invalid_desktop", "access_denied"}:
            cooldown_seconds = max(cooldown_seconds, 4.0)
        elif normalized_classification in {"dxgi_timeout", "no_frame"}:
            cooldown_seconds = max(cooldown_seconds, 2.0)
        self._backend_retry_after[backend_name] = time.monotonic() + cooldown_seconds

    def _build_recovery_hint(
        self,
        *,
        backend_name: str = "",
        failure: str = "",
        support_reason: str = "",
        blocker_reason: str = "",
        step_status: str = "",
        transient_mode: bool = False,
        current_context_label: str = "",
    ) -> dict[str, object]:
        normalized_backend = self._normalize_capture_value(backend_name)
        normalized_failure = self._normalize_capture_value(failure)
        normalized_support_reason = self._normalize_capture_value(support_reason)
        normalized_blocker_reason = self._normalize_capture_value(blocker_reason)
        normalized_step_status = self._normalize_capture_value(step_status)
        normalized_context_label = self._normalize_capture_value(current_context_label)

        hint: dict[str, object] = {
            "backend": normalized_backend,
            "transient_mode": bool(transient_mode),
            "context_label": normalized_context_label,
        }

        if normalized_blocker_reason:
            hint.update(
                {
                    "action": "retry_later",
                    "reason": normalized_blocker_reason,
                    "recycle_worker": False,
                }
            )
            return hint

        if normalized_failure in {"session_changed", "invalid_desktop", "access_denied"}:
            hint.update(
                {
                    "action": "rebind_desktop",
                    "reason": normalized_failure,
                    "recycle_worker": True,
                }
            )
            return hint

        if normalized_failure in {"dxgi_access_lost"}:
            hint.update(
                {
                    "action": "reset_backend",
                    "reason": normalized_failure,
                    "recycle_worker": normalized_backend in {"wgc", "dwm"},
                }
            )
            return hint

        if normalized_failure in {"dxgi_timeout", "no_frame", "backend_error"}:
            hint.update(
                {
                    "action": "reset_backend",
                    "reason": normalized_failure,
                    "recycle_worker": False,
                }
            )
            return hint

        if normalized_support_reason in {
            "retry_cooldown_active",
            "backend_unregistered",
            "provider_not_configured",
        }:
            hint.update(
                {
                    "action": "retry_later",
                    "reason": normalized_support_reason,
                    "recycle_worker": False,
                }
            )
            return hint

        if normalized_support_reason:
            if any(
                token in normalized_support_reason
                for token in (
                    "module_unavailable",
                    "provider_unavailable",
                    "provider_module_not_found",
                    "provider_import_error",
                    "provider_init_error",
                    "provider_factory_missing",
                    "unsupported_os",
                    "dependency_missing",
                    "not_implemented",
                    "disabled_pending_native_implementation",
                    "enabled_without_native_provider",
                )
            ):
                hint.update(
                    {
                        "action": "retry_later",
                        "reason": normalized_support_reason,
                        "recycle_worker": False,
                    }
                )
                return hint

        if normalized_step_status == "captured":
            return {}
        if normalized_step_status.startswith("skipped"):
            hint.update(
                {
                    "action": "retry_later",
                    "reason": normalized_step_status or "skipped",
                    "recycle_worker": False,
                }
            )
            return hint
        return {}

    def _derive_capture_attempt_recovery_hint(self, capture_attempt: dict) -> dict[str, object]:
        blocker_reason = str(capture_attempt.get("blocker_reason") or "").strip()
        if blocker_reason:
            return self._build_recovery_hint(
                blocker_reason=blocker_reason,
                current_context_label=self._capture_context_label,
            )
        for item in reversed(capture_attempt.get("steps") or []):
            recovery_hint = dict((item or {}).get("recovery_hint") or {})
            if recovery_hint:
                return recovery_hint
        selected_backend = str(capture_attempt.get("selected_backend") or "").strip().lower()
        final_failure = str(capture_attempt.get("final_failure") or "").strip()
        if final_failure:
            return self._build_recovery_hint(
                backend_name=selected_backend,
                failure=final_failure,
                current_context_label=self._capture_context_label,
            )
        return {}

    def _classify_backend_failure(self, backend_name: str, exc: Exception) -> str:
        backend = self._normalize_capture_value(backend_name)
        message = self._normalize_capture_value(str(exc))
        if backend == "dxgi":
            if any(token in message for token in ("access lost", "dxgi_error_access_lost", "device removed")):
                return "dxgi_access_lost"
            if "timeout" in message or "wait timed out" in message:
                return "dxgi_timeout"
        if backend in {"wgc", "dwm"}:
            if any(token in message for token in ("access lost", "graphics capture item closed", "device removed")):
                return "session_changed"
            if "unavailable" in message:
                return "access_denied"
        if any(
            token in message
            for token in (
                "invalid virtual screen size",
                "geometry mismatch",
                "invalid desktop",
                "desktop unavailable",
            )
        ):
            return "invalid_desktop"
        if any(token in message for token in ("session changed", "session disconnected", "session is changing")):
            return "session_changed"
        if any(token in message for token in ("returned no frame", "no frame", "empty frame")):
            return "no_frame"
        if any(token in message for token in ("access is denied", "permission denied", "denied")):
            return "access_denied"
        return "backend_error"

    def _validate_virtual_capture_geometry(self, backend_name: str, screenshot):
        if backend_name != "DXGI":
            return

        _, _, expected_width, expected_height = self._get_virtual_screen_metrics()
        if expected_width <= 0 or expected_height <= 0:
            return

        if screenshot.width != expected_width or screenshot.height != expected_height:
            with contextlib.suppress(Exception):
                screenshot.close()
            raise RuntimeError(
                "dxgi frame geometry mismatch: "
                f"expected={expected_width}x{expected_height} got={screenshot.width}x{screenshot.height}"
            )

    def _get_virtual_screen_metrics(self):
        if os.name != "nt":
            return 0, 0, 0, 0
        virtual_x = self.user32.GetSystemMetrics(76)
        virtual_y = self.user32.GetSystemMetrics(77)
        screen_width = self.user32.GetSystemMetrics(78)
        screen_height = self.user32.GetSystemMetrics(79)
        return virtual_x, virtual_y, screen_width, screen_height

    def _build_virtual_screen_signature(self, *, desktop_state: dict | None = None) -> str:
        if desktop_state is not None:
            origin = str(desktop_state.get("virtual_screen_origin") or "").strip()
            size = str(desktop_state.get("virtual_screen_size") or "").strip()
            if origin or size:
                return f"{origin}:{size}"
        virtual_x, virtual_y, screen_width, screen_height = self._get_virtual_screen_metrics()
        return f"{int(virtual_x)},{int(virtual_y)}:{int(screen_width)}x{int(screen_height)}"

    def _build_display_substrate_signature(self, *, desktop_state: dict | None = None) -> str:
        snapshot = dict(desktop_state or {})
        substrate_class = self._normalize_capture_value(
            snapshot.get("display_substrate_class") or snapshot.get("substrate_class")
        )
        continuity_mode = self._normalize_capture_value(
            snapshot.get("display_continuity_mode") or snapshot.get("continuity_mode")
        )
        persistent = 1 if self._coerce_bool(snapshot.get("display_persistent")) else 0
        best_effort_only = 1 if self._coerce_bool(snapshot.get("display_best_effort_only")) else 0
        display_surface_available = 1 if self._coerce_bool(snapshot.get("display_surface_available")) else 0
        physical_display_attached = 1 if self._coerce_bool(snapshot.get("physical_display_attached")) else 0
        virtual_display_attached = 1 if self._coerce_bool(snapshot.get("virtual_display_attached")) else 0
        remote_display_surface = 1 if self._coerce_bool(snapshot.get("remote_display_surface")) else 0
        secure_desktop_surface = 1 if self._coerce_bool(snapshot.get("secure_desktop_surface")) else 0
        disconnected_surface = 1 if self._coerce_bool(snapshot.get("disconnected_surface")) else 0
        render_monitor_count = int(snapshot.get("render_monitor_count") or 0)
        attached_display_count = int(snapshot.get("attached_display_count") or 0)
        needs_virtual_display = 1 if self._coerce_bool(
            snapshot.get("requires_virtual_display_for_full_continuity")
        ) else 0
        remote_adapter_present = 1 if self._coerce_bool(
            snapshot.get("display_inventory_remote_adapter_present")
        ) else 0
        return "|".join(
            [
                f"class={substrate_class or 'unknown'}",
                f"mode={continuity_mode or 'unknown'}",
                f"persistent={persistent}",
                f"best_effort={best_effort_only}",
                f"surface={display_surface_available}",
                f"physical={physical_display_attached}",
                f"virtual={virtual_display_attached}",
                f"remote={remote_display_surface}",
                f"secure={secure_desktop_surface}",
                f"disconnected={disconnected_surface}",
                f"render_monitors={render_monitor_count}",
                f"attached_displays={attached_display_count}",
                f"needs_virtual={needs_virtual_display}",
                f"remote_adapter={remote_adapter_present}",
            ]
        )

    def _normalize_capture_value(self, value) -> str:
        return str(value or "").strip().lower()

    def _coerce_bool(self, value, default: bool = False) -> bool:
        if isinstance(value, bool):
            return value
        if value in (None, ""):
            return bool(default)
        if isinstance(value, (int, float)):
            return bool(value)
        normalized = str(value).strip().lower()
        if normalized in {"1", "true", "yes", "y", "on"}:
            return True
        if normalized in {"0", "false", "no", "n", "off"}:
            return False
        return bool(default)

    def _note_backend(self, backend_name: str):
        normalized_backend_name = str(backend_name or "").strip().lower()
        if self.capture_backend != normalized_backend_name:
            print(f"[RemoteDesktop] Capture backend switched: {backend_name}")
            self.capture_backend = normalized_backend_name
        self.failure_count = 0
        self.last_failure_reason = None
        self._last_recovery_hint = {}

    def _record_capture_failure(self, errors: list[str]):
        self.failure_count += 1
        reason = " | ".join(errors) if errors else "unknown error"
        session_id = self._get_current_session_id()
        active_session_id = self.kernel32.WTSGetActiveConsoleSessionId()
        has_input_desktop = self._has_input_desktop()
        reason_changed = reason != self.last_failure_reason
        self.last_failure_reason = reason

        if reason_changed or self.failure_count in (1, 5, 20) or self.failure_count % 60 == 0:
            print(
                "[RemoteDesktop] Screen capture failed: "
                f"{reason} | session={session_id} active_console={active_session_id} "
                f"input_desktop={'yes' if has_input_desktop else 'no'} pid={os.getpid()}"
            )
            if session_id == 0 or (active_session_id not in (0xFFFFFFFF, session_id)):
                print(
                    "[RemoteDesktop] Process is not in the active desktop session; "
                    "capture may fail under SYSTEM or startup task context"
                )

    def _get_current_session_id(self):
        session_id = wintypes.DWORD()
        current_pid = self.kernel32.GetCurrentProcessId()
        if self.kernel32.ProcessIdToSessionId(current_pid, ctypes.byref(session_id)):
            return session_id.value
        return -1

    def _has_input_desktop(self):
        try:
            desktop = self.user32.OpenInputDesktop(0, False, 0x0100)
            if desktop:
                self.user32.CloseDesktop(desktop)
                return True
        except Exception:
            return False
        return False
