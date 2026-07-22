"""
远程桌面协议辅助模块
统一解析前端鼠标消息，并维护会话级输入状态。
"""

from dataclasses import dataclass, field
from enum import Enum

from input_injector import MouseButton


class RemoteMouseAction(Enum):
    """远程鼠标动作枚举。"""

    MOVE = "move"
    DRAG_MOVE = "drag_move"
    BUTTON_DOWN = "button_down"
    BUTTON_UP = "button_up"
    WHEEL = "wheel"


@dataclass
class RemoteMouseMessage:
    """前端鼠标消息的标准化结果。"""

    action: RemoteMouseAction
    normalized_x: float
    normalized_y: float
    button: MouseButton = MouseButton.LEFT
    wheel_steps: int = 0
    buttons_mask: int = 0
    delta_x: int = 0
    delta_y: int = 0


@dataclass
class RemoteMouseState:
    """会话级鼠标状态。"""

    pressed_buttons: set[MouseButton] = field(default_factory=set)
    last_normalized_x: float = 0.5
    last_normalized_y: float = 0.5
    current_screen_x: int = 0
    current_screen_y: int = 0
    drag_in_progress: bool = False

    def remember_position(self, normalized_x: float, normalized_y: float):
        self.last_normalized_x = normalized_x
        self.last_normalized_y = normalized_y

    def remember_screen_position(self, screen_x: int, screen_y: int):
        self.current_screen_x = int(screen_x)
        self.current_screen_y = int(screen_y)

    def press(self, button: MouseButton) -> bool:
        already_pressed = button in self.pressed_buttons
        self.pressed_buttons.add(button)
        return not already_pressed

    def release(self, button: MouseButton) -> bool:
        was_pressed = button in self.pressed_buttons
        self.pressed_buttons.discard(button)
        return was_pressed

    def release_all(self) -> list[MouseButton]:
        buttons = list(self.pressed_buttons)
        self.pressed_buttons.clear()
        return buttons


_ACTION_ALIASES = {
    "move": RemoteMouseAction.MOVE,
    "mousemove": RemoteMouseAction.MOVE,
    "drag_move": RemoteMouseAction.DRAG_MOVE,
    "dragmove": RemoteMouseAction.DRAG_MOVE,
    "mousedown": RemoteMouseAction.BUTTON_DOWN,
    "button_down": RemoteMouseAction.BUTTON_DOWN,
    "buttondown": RemoteMouseAction.BUTTON_DOWN,
    "mouseup": RemoteMouseAction.BUTTON_UP,
    "button_up": RemoteMouseAction.BUTTON_UP,
    "buttonup": RemoteMouseAction.BUTTON_UP,
    "wheel": RemoteMouseAction.WHEEL,
}

_BUTTON_MAP = {
    0: MouseButton.LEFT,
    1: MouseButton.MIDDLE,
    2: MouseButton.RIGHT,
}


def parse_mouse_message(message: dict) -> RemoteMouseMessage | None:
    """解析前端鼠标消息，兼容旧动作名。"""
    action_name = str(message.get("action", "") or "").strip().lower()
    action = _ACTION_ALIASES.get(action_name)
    if not action:
        return None

    normalized_x = _clamp(_to_float(message.get("normalized_x"), 0.5), 0.0, 1.0)
    normalized_y = _clamp(_to_float(message.get("normalized_y"), 0.5), 0.0, 1.0)
    button = _BUTTON_MAP.get(_to_int(message.get("button"), 0), MouseButton.LEFT)
    wheel_steps = _parse_wheel_steps(message)
    buttons_mask = max(0, _to_int(message.get("buttons"), 0))
    delta_x = _to_int(message.get("delta_x"), 0)
    delta_y = _to_int(message.get("delta_y"), 0)

    return RemoteMouseMessage(
        action=action,
        normalized_x=normalized_x,
        normalized_y=normalized_y,
        button=button,
        wheel_steps=wheel_steps,
        buttons_mask=buttons_mask,
        delta_x=delta_x,
        delta_y=delta_y,
    )


def _parse_wheel_steps(message: dict) -> int:
    wheel_steps = _to_int(message.get("wheel_steps"), 0)
    if wheel_steps != 0:
        return max(-12, min(12, wheel_steps))

    delta_y = _to_float(message.get("deltaY"), 0.0)
    if delta_y == 0:
        return 0

    delta_mode = _to_int(message.get("deltaMode"), 0)
    if delta_mode == 1:
        delta_y *= 40
    elif delta_mode == 2:
        delta_y *= 400

    magnitude = max(1, min(6, int(round(abs(delta_y) / 96.0))))
    return magnitude if delta_y < 0 else -magnitude


def _to_float(value, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _to_int(value, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _clamp(value: float, minimum: float, maximum: float) -> float:
    return max(minimum, min(value, maximum))
