from __future__ import annotations

import ctypes
import json
import os

from Common.logging_utils import make_component_logger
from agent_consent_ipc import get_current_process_session_id
from input_injector import InputInjector as LegacyInputInjector
from input_injector import MouseButton, MouseEvent, MouseEventType

_KEYEVENTF_UNICODE = 0x0004
_KEYEVENTF_KEYUP = 0x0002
_INPUT_KEYBOARD = 1


class _KeybdInput(ctypes.Structure):
    _fields_ = [
        ("wVk", ctypes.c_ushort),
        ("wScan", ctypes.c_ushort),
        ("dwFlags", ctypes.c_uint),
        ("time", ctypes.c_uint),
        ("dwExtraInfo", ctypes.c_void_p),
    ]


class _MouseInput(ctypes.Structure):
    _fields_ = [
        ("dx", ctypes.c_long),
        ("dy", ctypes.c_long),
        ("mouseData", ctypes.c_uint),
        ("dwFlags", ctypes.c_uint),
        ("time", ctypes.c_uint),
        ("dwExtraInfo", ctypes.c_void_p),
    ]


class _HardwareInput(ctypes.Structure):
    _fields_ = [
        ("uMsg", ctypes.c_uint),
        ("wParamL", ctypes.c_ushort),
        ("wParamH", ctypes.c_ushort),
    ]


class _InputUnion(ctypes.Union):
    _fields_ = [
        ("ki", _KeybdInput),
        ("mi", _MouseInput),
        ("hi", _HardwareInput),
    ]


class _SendInputRecord(ctypes.Structure):
    _anonymous_ = ("u",)
    _fields_ = [
        ("type", ctypes.c_uint),
        ("u", _InputUnion),
    ]


def send_unicode_text(text: str) -> bool:
    """以 KEYEVENTF_UNICODE 注入文本。

    逐字符 SendInput 直接投递 Unicode 码点，绕过键盘布局与输入法——
    pyautogui 的逐键模拟会被中文输入法拦截成拼音组合（如 cmd -> c'm'd）。
    """
    if os.name != "nt" or not text:
        return False
    try:
        user32 = ctypes.windll.user32
        user32.SendInput.restype = ctypes.c_uint
        sent = 0
        for ch in text:
            code = ord(ch)
            if code > 0xFFFF:
                continue
            down = _SendInputRecord(type=_INPUT_KEYBOARD)
            down.ki = _KeybdInput(0, code, _KEYEVENTF_UNICODE, 0, None)
            up = _SendInputRecord(type=_INPUT_KEYBOARD)
            up.ki = _KeybdInput(
                0, code, _KEYEVENTF_UNICODE | _KEYEVENTF_KEYUP, 0, None
            )
            batch = (_SendInputRecord * 2)(down, up)
            sent += user32.SendInput(2, batch, ctypes.sizeof(_SendInputRecord))
        return sent >= len(text) * 2 and sent > 0
    except Exception:
        return False


class InputInjector(LegacyInputInjector):
    """第一阶段兼容适配器。

    当前仍复用稳定的 SendInput 实现，接口位置已经切到新的分层目录，
    后续可以在这里接入 Service 下发的高权限辅助策略，而不影响上层远控会话代码。
    """

    def __init__(
        self,
        manage_cursor_visibility: bool = False,
        privileged_client=None,
        session_id: int | None = None,
        follow_service_session: bool = False,
    ):
        self.privileged_client = privileged_client
        self.follow_service_session = bool(follow_service_session)
        if self.follow_service_session:
            self.target_session_id = None
        else:
            self.target_session_id = (
                int(session_id)
                if session_id is not None
                else get_current_process_session_id()
            )
        self._logger = make_component_logger("InputController")
        self._last_privileged_diagnostic_signature: str | None = None
        super().__init__(manage_cursor_visibility=manage_cursor_visibility)

    def describe_stack(self) -> dict:
        return {
            "injection_mode": (
                "service_managed_session_helper_preferred"
                if self.privileged_client
                else "send_input"
            ),
            "service_bridge": bool(self.privileged_client),
            "execution_path": "service_helper_with_local_fallback" if self.privileged_client else "user_agent",
            "admin_operations_via_service": bool(self.privileged_client),
            "uac_bypass": False,
            "follow_service_session": self.follow_service_session,
            "high_integrity_delegate": (
                "service_managed_session_helper_with_local_fallback"
                if self.privileged_client
                else "unavailable"
            ),
            "service_admin_dispatch": "ready" if self.privileged_client else "unavailable",
            "target_session_id": self.target_session_id,
        }

    def request_privileged_action(self, action: str, payload: dict | None = None) -> dict:
        if self.privileged_client is None:
            raise RuntimeError("privileged service bridge unavailable")

        request_payload = dict(payload or {})
        if (
            not self.follow_service_session
            and self.target_session_id is not None
            and request_payload.get("session_id") in (None, "")
        ):
            request_payload["session_id"] = int(self.target_session_id)
        self._logger(f"privileged action requested: action={action}")
        return self.privileged_client.invoke_admin_action(action, request_payload)

    def _execute_mouse_event(self, event: MouseEvent):
        if self.privileged_client is not None:
            try:
                self._delegate_mouse_event_to_service(event)
                self._update_local_mouse_state(event)
                return
            except Exception as exc:
                diagnostic = self._collect_injection_diagnostic(event, exc)
                self._log_injection_diagnostic(event, exc, diagnostic)

        try:
            super()._execute_mouse_event(event)
        except Exception as exc:
            diagnostic = self._collect_injection_diagnostic(event, exc)
            self._log_injection_diagnostic(event, exc, diagnostic)
            raise

    def _delegate_mouse_event_to_service(self, event: MouseEvent) -> None:
        response = self.request_privileged_action(
            "inject_mouse_event",
            {
                "event": {
                    "type": event.type.value,
                    "button": event.button.value,
                    "x": event.x,
                    "y": event.y,
                    "normalized_x": event.normalized_x,
                    "normalized_y": event.normalized_y,
                    "delta": event.delta,
                },
            },
        )
        helper_response = response.get("helper_response") or {}
        if not helper_response.get("injected", False):
            raise RuntimeError(f"service helper mouse injection failed: {response}")

    def _update_local_mouse_state(self, event: MouseEvent) -> None:
        self.refresh_virtual_desktop_metrics()
        target_x, target_y = self._resolve_event_coordinates(event)

        if event.type == MouseEventType.MOVE:
            self.last_position = (target_x, target_y)
            return

        if event.type == MouseEventType.DOWN:
            self.mouse_down = True
            self.pressed_buttons.add(event.button)
            self.last_position = (target_x, target_y)
            return

        if event.type == MouseEventType.UP:
            self.mouse_down = False
            self.pressed_buttons.discard(event.button)
            self.last_position = (target_x, target_y)
            return

        if event.type == MouseEventType.CLICK:
            self.last_position = (target_x, target_y)
            return

        if event.type == MouseEventType.WHEEL:
            self.last_position = (target_x, target_y)

    def _collect_injection_diagnostic(self, event: MouseEvent, exc: Exception) -> dict:
        base_payload = {
            "reason": "mouse_injection_failure",
            "event": {
                "type": event.type.value,
                "button": event.button.value,
                "x": event.x,
                "y": event.y,
                "normalized_x": event.normalized_x,
                "normalized_y": event.normalized_y,
                "delta": event.delta,
            },
            "error": str(exc),
        }
        if self.privileged_client is None:
            return {
                "service_bridge": False,
                "message": "privileged service bridge unavailable",
                "payload": base_payload,
            }

        try:
            return self.request_privileged_action("describe_desktop_context", base_payload)
        except Exception as diagnostic_exc:
            return {
                "service_bridge": True,
                "diagnostic_error": str(diagnostic_exc),
                "payload": base_payload,
            }

    def _log_injection_diagnostic(self, event: MouseEvent, exc: Exception, diagnostic: dict):
        diagnostic_signature = json.dumps(
            {
                "event_type": event.type.value,
                "button": event.button.value,
                "error": str(exc),
                "diagnostic": diagnostic,
            },
            ensure_ascii=True,
            sort_keys=True,
            default=str,
        )
        if diagnostic_signature == self._last_privileged_diagnostic_signature:
            return

        self._last_privileged_diagnostic_signature = diagnostic_signature
        self._logger(
            "mouse injection failure diagnostic: "
            f"type={event.type.value} button={event.button.value} "
            f"target=({event.x},{event.y}) error={exc} "
            f"diagnostic={json.dumps(diagnostic, ensure_ascii=False, default=str)}"
        )
