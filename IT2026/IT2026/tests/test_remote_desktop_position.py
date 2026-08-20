# -*- coding: utf-8 -*-
"""远程桌面坐标链路位置正确性测试。

覆盖链路: 前端像素 -> normalized -> parse_mouse_message -> CoordinateMapper.denormalize
        -> InputInjector._move_to(SendInput 绝对坐标) -> 反向解算像素。

所有注入路径均被打桩(mock)，测试过程不会移动真实鼠标。
运行: python tests/test_remote_desktop_position.py
"""

import sys
import unittest
from pathlib import Path
from unittest import mock

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from coordinate_mapper import CoordinateMapper  # noqa: E402
from input_injector import InputInjector, MouseButton, MouseEvent, MouseEventType  # noqa: E402
from remote_desktop_protocol import (  # noqa: E402
    RemoteMouseAction,
    parse_mouse_message,
)


# ---------------------------------------------------------------------------
# 前端 WebRemoteDesktop.vue resolveCanvasPosition 的等价实现
# ---------------------------------------------------------------------------

def frontend_resolve_position(
    canvas_width,
    canvas_height,
    render_width,
    render_height,
    border_left,
    border_top,
    client_x,
    client_y,
    rect_left=0,
    rect_top=0,
):
    """复刻前端归一化公式（按位图全宽/全高换算，与后端解算约定一致）。"""
    usable_width = max(1, render_width)
    usable_height = max(1, render_height)
    max_canvas_x = max(0, canvas_width - 1)
    max_canvas_y = max(0, canvas_height - 1)

    offset_x = client_x - rect_left - border_left
    offset_y = client_y - rect_top - border_top
    raw_x = (offset_x / usable_width) * canvas_width
    raw_y = (offset_y / usable_height) * canvas_height
    canvas_x = max(0, min(raw_x, max_canvas_x))
    canvas_y = max(0, min(raw_y, max_canvas_y))
    normalized_x = max(0, min(canvas_x / canvas_width, 1)) if canvas_width > 0 else 0
    normalized_y = max(0, min(canvas_y / canvas_height, 1)) if canvas_height > 0 else 0
    return normalized_x, normalized_y


# ---------------------------------------------------------------------------
# SendInput 绝对坐标的 Windows 反向解算（0..65535 -> 虚拟桌面像素）
# ---------------------------------------------------------------------------

def sendinput_reverse_map(dx, dy, left, top, width, height):
    x = left + round(dx * (width - 1) / 65535)
    y = top + round(dy * (height - 1) / 65535)
    return x, y


class GeometryCase:
    def __init__(self, name, left, top, width, height):
        self.name = name
        self.left = left
        self.top = top
        self.width = width
        self.height = height


GEOMETRIES = [
    GeometryCase("single_1920x1080", 0, 0, 1920, 1080),
    GeometryCase("dual_left_of_primary", -1920, 0, 3840, 1080),
    GeometryCase("dual_above_primary", 0, -1080, 1920, 2160),
]


def make_mapper(geometry: GeometryCase) -> CoordinateMapper:
    mapper = CoordinateMapper()
    mapper.virtual_screen_x = geometry.left
    mapper.virtual_screen_y = geometry.top
    mapper.virtual_screen_width = geometry.width
    mapper.virtual_screen_height = geometry.height
    return mapper


def make_injector(geometry: GeometryCase) -> InputInjector:
    injector = InputInjector.__new__(InputInjector)
    injector.user32 = None
    injector.metrics_lock = __import__("threading").Lock()
    injector.mouse_down = False
    injector.last_position = (geometry.left, geometry.top)
    injector.pressed_buttons = set()

    injector.MOUSEEVENTF_MOVE = 0x0001
    injector.MOUSEEVENTF_LEFTDOWN = 0x0002
    injector.MOUSEEVENTF_LEFTUP = 0x0004
    injector.MOUSEEVENTF_RIGHTDOWN = 0x0008
    injector.MOUSEEVENTF_RIGHTUP = 0x0010
    injector.MOUSEEVENTF_MIDDLEDOWN = 0x0020
    injector.MOUSEEVENTF_MIDDLEUP = 0x0040
    injector.MOUSEEVENTF_WHEEL = 0x0800
    injector.MOUSEEVENTF_ABSOLUTE = 0x8000
    injector.MOUSEEVENTF_VIRTUALDESK = 0x4000
    injector.WHEEL_DELTA = 120

    injector.virtual_left = geometry.left
    injector.virtual_top = geometry.top
    injector.virtual_width = geometry.width
    injector.virtual_height = geometry.height
    injector.refresh_virtual_desktop_metrics = lambda initial=False: {
        "left": geometry.left,
        "top": geometry.top,
        "width": geometry.width,
        "height": geometry.height,
    }
    injector.sent_calls = []
    injector._send_mouse_input = lambda flags, data=0, dx=0, dy=0: (
        injector.sent_calls.append((flags, int(dx), int(dy), int(data)))
    )
    return injector


class TestProtocolParsing(unittest.TestCase):
    """协议层：动作解析、按钮映射、坐标钳制、滚轮换算。"""

    def test_unknown_action_returns_none(self):
        self.assertIsNone(parse_mouse_message({"action": "teleport"}))
        self.assertIsNone(parse_mouse_message({}))

    def test_move_aliases(self):
        for name in ("move", "mousemove", "MOVE"):
            message = parse_mouse_message({"action": name, "normalized_x": 0.25, "normalized_y": 0.75})
            self.assertEqual(message.action, RemoteMouseAction.MOVE)
            self.assertEqual((message.normalized_x, message.normalized_y), (0.25, 0.75))

    def test_button_mapping(self):
        cases = {0: MouseButton.LEFT, 1: MouseButton.MIDDLE, 2: MouseButton.RIGHT, 99: MouseButton.LEFT}
        for raw, expected in cases.items():
            message = parse_mouse_message({"action": "button_down", "button": raw})
            self.assertEqual(message.button, expected)

    def test_normalized_clamped(self):
        message = parse_mouse_message({"action": "move", "normalized_x": 1.7, "normalized_y": -0.4})
        self.assertEqual((message.normalized_x, message.normalized_y), (1.0, 0.0))

    def test_non_numeric_coordinates_use_default(self):
        message = parse_mouse_message({"action": "move", "normalized_x": "abc"})
        self.assertEqual(message.normalized_x, 0.5)

    def test_wheel_steps_direct_and_clamped(self):
        self.assertEqual(parse_mouse_message({"action": "wheel", "wheel_steps": 3}).wheel_steps, 3)
        self.assertEqual(parse_mouse_message({"action": "wheel", "wheel_steps": 99}).wheel_steps, 12)
        self.assertEqual(parse_mouse_message({"action": "wheel", "wheel_steps": -99}).wheel_steps, -12)

    def test_wheel_delta_fallback_sign(self):
        # 浏览器 deltaY>0 表示向下滚动，后端应为负步进（Windows 负值向下）。
        down = parse_mouse_message({"action": "wheel", "deltaY": 120})
        up = parse_mouse_message({"action": "wheel", "deltaY": -120})
        self.assertLess(down.wheel_steps, 0)
        self.assertGreater(up.wheel_steps, 0)

    def test_drag_move_alias(self):
        message = parse_mouse_message({"action": "dragmove", "delta_x": 5, "delta_y": -7})
        self.assertEqual(message.action, RemoteMouseAction.DRAG_MOVE)
        self.assertEqual((message.delta_x, message.delta_y), (5, -7))

    def test_wheel_delta_fallback_cap_matches_frontend(self):
        """回归：deltaY 回退路径的步长上限应与前端一致（±12）。"""
        message = parse_mouse_message({"action": "wheel", "deltaY": 1200})
        self.assertEqual(message.wheel_steps, -12)
        message = parse_mouse_message({"action": "wheel", "deltaY": -1200})
        self.assertEqual(message.wheel_steps, 12)


class TestPyAutoGuiFallbackRegion(unittest.TestCase):
    """回归：PyAutoGUI 兜底抓屏必须覆盖整个虚拟桌面（与输入空间一致）。"""

    def test_grab_uses_all_screens(self):
        from Capture import desktop_capture as capture_module
        from PIL import Image

        capturer = capture_module.DesktopFrameCapturer.__new__(capture_module.DesktopFrameCapturer)
        capturer.capture_backend = None

        recorded = {}

        def fake_grab(all_screens=False):
            recorded["all_screens"] = all_screens
            return Image.new("RGB", (10, 10))

        with mock.patch.object(capture_module.ImageGrab, "grab", side_effect=fake_grab):
            screenshot = capturer._grab_with_pyautogui()

        self.assertTrue(recorded.get("all_screens"))
        self.assertEqual(screenshot.size, (10, 10))
        self.assertEqual(capturer.capture_backend, "pyautogui")

    def test_grab_falls_back_when_all_screens_unsupported(self):
        from Capture import desktop_capture as capture_module
        from PIL import Image

        capturer = capture_module.DesktopFrameCapturer.__new__(capture_module.DesktopFrameCapturer)
        capturer.capture_backend = None

        calls = []

        def failing_grab(all_screens=False):
            calls.append(all_screens)
            raise RuntimeError("all_screens unsupported")

        pyautogui_module = mock.Mock()
        pyautogui_module.screenshot.return_value = Image.new("RGB", (8, 6))
        pyautogui_module.FAILSAFE = False

        with mock.patch.object(capture_module.ImageGrab, "grab", side_effect=failing_grab), \
                mock.patch.object(capture_module, "pyautogui", pyautogui_module):
            screenshot = capturer._grab_with_pyautogui()

        self.assertEqual(calls, [True])
        self.assertEqual(screenshot.size, (8, 6))
        pyautogui_module.screenshot.assert_called_once()


class TestCoordinateMapper(unittest.TestCase):
    """坐标映射层：归一化到虚拟桌面（含负原点多显示器）。"""

    def test_denormalize_corners(self):
        for geometry in GEOMETRIES:
            with self.subTest(geometry=geometry.name):
                mapper = make_mapper(geometry)
                max_x = geometry.left + geometry.width - 1
                max_y = geometry.top + geometry.height - 1

                self.assertEqual(mapper.denormalize_coordinate(0.0, 0.0), (geometry.left, geometry.top))
                self.assertEqual(mapper.denormalize_coordinate(1.0, 1.0), (max_x, max_y))

    def test_denormalize_center(self):
        for geometry in GEOMETRIES:
            with self.subTest(geometry=geometry.name):
                mapper = make_mapper(geometry)
                cx, cy = mapper.denormalize_coordinate(0.5, 0.5)
                self.assertAlmostEqual(cx, geometry.left + geometry.width / 2, delta=1)
                self.assertAlmostEqual(cy, geometry.top + geometry.height / 2, delta=1)

    def test_clamp_out_of_range(self):
        geometry = GEOMETRIES[1]
        mapper = make_mapper(geometry)
        self.assertEqual(
            mapper.clamp_screen_coordinate(geometry.left - 500, geometry.top - 500),
            (geometry.left, geometry.top),
        )
        self.assertEqual(
            mapper.clamp_screen_coordinate(geometry.left + 99999, geometry.top + 99999),
            (geometry.left + geometry.width - 1, geometry.top + geometry.height - 1),
        )

    def test_denormalize_matches_injector_fallback(self):
        """回归：映射层与注入层回退换算必须逐点一致（同一约定）。"""
        for geometry in GEOMETRIES:
            mapper = make_mapper(geometry)
            injector = make_injector(geometry)
            for step in range(0, 101):
                nx = step / 100
                for ny in (0.0, 0.3, 0.7, 1.0):
                    want = injector._denormalize_to_virtual_desktop(nx, ny)
                    got = mapper.denormalize_coordinate(nx, ny)
                    self.assertEqual(
                        got,
                        want,
                        msg=f"{geometry.name} n=({nx},{ny}) mapper={got} injector={want}",
                    )

    def test_denormalize_no_truncation_bias(self):
        """回归：小归一化坐标不应被截断到同一像素（旧实现 int() 截断）。"""
        geometry = GEOMETRIES[0]
        mapper = make_mapper(geometry)
        # nx=0.002 * 1919 ≈ 3.84 -> 应为 4，旧实现 int(0.002*1920)=3
        self.assertEqual(mapper.denormalize_coordinate(0.002, 0.0), (4, geometry.top))


class TestEndToEndPosition(unittest.TestCase):
    """全链路：前端像素 -> normalized -> 协议 -> 解算 -> SendInput -> 反算像素。"""

    FRAME_W, FRAME_H = 1728, 864  # scale=0.9 时前端画布位图尺寸

    def test_grid_accuracy(self):
        failures = []
        for geometry in GEOMETRIES:
            mapper = make_mapper(geometry)
            injector = make_injector(geometry)
            render_w, render_h = 1200, 700  # 前端 CSS 显示尺寸（比例可与位图不同）

            samples = [(fx, fy) for fx in (0.0, 0.25, 0.5, 0.75, 1.0) for fy in (0.0, 0.5, 1.0)]
            for fx, fy in samples:
                client_x = fx * render_w
                client_y = fy * render_h
                nx, ny = frontend_resolve_position(
                    self.FRAME_W, self.FRAME_H, render_w, render_h, 0, 0, client_x, client_y
                )
                message = parse_mouse_message({"action": "move", "normalized_x": nx, "normalized_y": ny})
                screen_x, screen_y = mapper.denormalize_coordinate(message.normalized_x, message.normalized_y)
                injector._move_to(screen_x, screen_y)

                flags, dx, dy, _ = injector.sent_calls[-1]
                self.assertEqual(flags & injector.MOUSEEVENTF_VIRTUALDESK, injector.MOUSEEVENTF_VIRTUALDESK)
                self.assertEqual(flags & injector.MOUSEEVENTF_ABSOLUTE, injector.MOUSEEVENTF_ABSOLUTE)
                got_x, got_y = sendinput_reverse_map(
                    dx, dy, geometry.left, geometry.top, geometry.width, geometry.height
                )
                # 物理真值：目标比例在虚拟桌面上的像素位置（右/下边界钳制到末像素）
                want_x = min(
                    geometry.left + int(round(fx * geometry.width)),
                    geometry.left + geometry.width - 1,
                )
                want_y = min(
                    geometry.top + int(round(fy * geometry.height)),
                    geometry.top + geometry.height - 1,
                )
                if abs(got_x - want_x) > 1 or abs(got_y - want_y) > 1:
                    failures.append(
                        f"{geometry.name} fraction=({fx},{fy}) want=({want_x},{want_y}) got=({got_x},{got_y})"
                    )
        self.assertEqual(failures, [])

    def test_click_sequence_targets(self):
        geometry = GEOMETRIES[0]
        mapper = make_mapper(geometry)
        injector = make_injector(geometry)
        nx, ny = 0.5, 0.5
        sx, sy = mapper.denormalize_coordinate(nx, ny)
        injector._execute_mouse_event(MouseEvent(MouseEventType.CLICK, sx, sy))
        flags = [call[0] for call in injector.sent_calls]
        self.assertIn(injector.MOUSEEVENTF_LEFTDOWN, flags)
        self.assertIn(injector.MOUSEEVENTF_LEFTUP, flags)
        # 点击前应先把光标移动到目标点（首条为 MOVE|ABSOLUTE|VIRTUALDESK）
        first_flags = injector.sent_calls[0][0]
        self.assertTrue(first_flags & injector.MOUSEEVENTF_MOVE)

    def test_wheel_direction_end_to_end(self):
        geometry = GEOMETRIES[0]
        mapper = make_mapper(geometry)
        injector = make_injector(geometry)
        # 浏览器向下滚动 deltaY=+120 -> 后端负 data
        message = parse_mouse_message({"action": "wheel", "deltaY": 120, "normalized_x": 0.5, "normalized_y": 0.5})
        sx, sy = mapper.denormalize_coordinate(message.normalized_x, message.normalized_y)
        injector._execute_mouse_event(MouseEvent(MouseEventType.WHEEL, sx, sy, delta=message.wheel_steps))
        last = injector.sent_calls[-1]
        self.assertEqual(last[0], injector.MOUSEEVENTF_WHEEL)
        self.assertLess(last[3], 0)  # data<0 => 向下滚动


class TestInputInjectorMath(unittest.TestCase):
    """SendInput 换算与虚拟桌面钳制。"""

    def test_move_to_bounds(self):
        geometry = GEOMETRIES[1]
        injector = make_injector(geometry)
        injector._move_to(geometry.left - 100, geometry.top - 100)
        dx, dy = injector.sent_calls[-1][1], injector.sent_calls[-1][2]
        self.assertEqual(sendinput_reverse_map(dx, dy, geometry.left, geometry.top, geometry.width, geometry.height),
                         (geometry.left, geometry.top))

        injector._move_to(geometry.left + geometry.width + 50, geometry.top + geometry.height + 50)
        dx, dy = injector.sent_calls[-1][1], injector.sent_calls[-1][2]
        self.assertEqual(
            sendinput_reverse_map(dx, dy, geometry.left, geometry.top, geometry.width, geometry.height),
            (geometry.left + geometry.width - 1, geometry.top + geometry.height - 1),
        )

    def test_absolute_conversion_monotonic(self):
        geometry = GEOMETRIES[2]
        injector = make_injector(geometry)
        previous_dx = -1
        for offset in range(0, geometry.width, 97):
            injector._move_to(geometry.left + offset, geometry.top)
            dx = injector.sent_calls[-1][1]
            self.assertGreaterEqual(dx, previous_dx)
            previous_dx = dx

    def test_roundtrip_error_bounded(self):
        geometry = GEOMETRIES[0]
        injector = make_injector(geometry)
        worst = 0
        for offset in range(0, geometry.width, 53):
            want_x = geometry.left + offset
            injector._move_to(want_x, geometry.top + 10)
            _, dx, dy, _ = injector.sent_calls[-1]
            got_x, _ = sendinput_reverse_map(dx, dy, geometry.left, geometry.top, geometry.width, geometry.height)
            worst = max(worst, abs(got_x - want_x))
        self.assertLessEqual(worst, 1)


class StubCoordinateMapper:
    def __init__(self, geometry):
        self.virtual_screen_x = geometry.left
        self.virtual_screen_y = geometry.top
        self.virtual_screen_width = geometry.width
        self.virtual_screen_height = geometry.height

    def denormalize_coordinate(self, nx, ny):
        screen_x = int(nx * self.virtual_screen_width + self.virtual_screen_x)
        screen_y = int(ny * self.virtual_screen_height + self.virtual_screen_y)
        max_x = self.virtual_screen_x + self.virtual_screen_width - 1
        max_y = self.virtual_screen_y + self.virtual_screen_height - 1
        return (
            max(self.virtual_screen_x, min(screen_x, max_x)),
            max(self.virtual_screen_y, min(screen_y, max_y)),
        )

    def clamp_screen_coordinate(self, x, y):
        max_x = self.virtual_screen_x + self.virtual_screen_width - 1
        max_y = self.virtual_screen_y + self.virtual_screen_height - 1
        return (
            max(self.virtual_screen_x, min(int(x), max_x)),
            max(self.virtual_screen_y, min(int(y), max_y)),
        )


def make_engine_session(geometry: GeometryCase):
    """构造仅含鼠标状态机的会话对象，绕过重量级构造函数。"""
    from remote_desktop_engine_v2 import RemoteDesktopSession
    from remote_desktop_protocol import RemoteMouseState

    session = RemoteDesktopSession.__new__(RemoteDesktopSession)
    session.session_id = "test"
    session.mouse_state = RemoteMouseState()
    session.mouse_state.remember_screen_position(geometry.left, geometry.top)
    session.coordinate_mapper = StubCoordinateMapper(geometry)
    session.wheel_speed = 1.0
    session.mouse_sensitivity = 1.0
    return session


class TestEngineMouseStateMachine(unittest.TestCase):
    """引擎层：move/down/drag/up/wheel 事件序列与目标坐标。"""

    GEOMETRY = GEOMETRIES[0]

    def test_move_updates_state(self):
        session = make_engine_session(self.GEOMETRY)
        message = parse_mouse_message({"action": "move", "normalized_x": 0.25, "normalized_y": 0.75})
        sx, sy = session.coordinate_mapper.denormalize_coordinate(message.normalized_x, message.normalized_y)
        events = session._build_mouse_events(message, sx, sy)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].type, MouseEventType.MOVE)
        self.assertEqual((events[0].x, events[0].y), (480, 810))
        self.assertEqual((session.mouse_state.current_screen_x, session.mouse_state.current_screen_y), (480, 810))

    def test_down_then_up_at_same_point(self):
        session = make_engine_session(self.GEOMETRY)
        down = parse_mouse_message({"action": "button_down", "button": 0, "normalized_x": 0.5, "normalized_y": 0.5})
        sx, sy = session.coordinate_mapper.denormalize_coordinate(down.normalized_x, down.normalized_y)
        events = session._build_mouse_events(down, sx, sy)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].type, MouseEventType.DOWN)
        self.assertEqual((events[0].x, events[0].y), (960, 540))

        up = parse_mouse_message({"action": "button_up", "button": 0, "normalized_x": 0.5, "normalized_y": 0.5})
        events = session._build_mouse_events(up, sx, sy)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].type, MouseEventType.UP)

    def test_duplicate_down_degrades_to_move(self):
        session = make_engine_session(self.GEOMETRY)
        first = parse_mouse_message({"action": "button_down", "button": 0, "normalized_x": 0.5, "normalized_y": 0.5})
        sx, sy = session.coordinate_mapper.denormalize_coordinate(first.normalized_x, first.normalized_y)
        session._build_mouse_events(first, sx, sy)

        second = parse_mouse_message({"action": "button_up", "button": 0, "normalized_x": 0.5, "normalized_y": 0.5})
        session._build_mouse_events(second, sx, sy)
        # 未按下时再次 up：不应再注入 DOWN，且不应抛异常
        extra_up = parse_mouse_message({"action": "button_up", "button": 0, "normalized_x": 0.4, "normalized_y": 0.4})
        events = session._build_mouse_events(extra_up, 768, 432)
        self.assertTrue(all(event.type != MouseEventType.DOWN for event in events))

    def test_drag_sequence_release_follows_cursor(self):
        session = make_engine_session(self.GEOMETRY)
        # 1) 在 (960,540) 按下左键
        down = parse_mouse_message({"action": "button_down", "button": 0, "normalized_x": 0.5, "normalized_y": 0.5})
        sx, sy = session.coordinate_mapper.denormalize_coordinate(down.normalized_x, down.normalized_y)
        events = session._build_mouse_events(down, sx, sy)
        self.assertEqual((events[0].x, events[0].y), (960, 540))

        # 2) 拖拽 delta (+100, -50) -> 光标应到 (1060,490)，事件类型为 MOVE（按键保持按下）
        drag = parse_mouse_message({
            "action": "drag_move", "delta_x": 100, "delta_y": -50,
            "normalized_x": 0.6, "normalized_y": 0.4,
        })
        events = session._build_mouse_events(drag, 1152, 432)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].type, MouseEventType.MOVE)
        self.assertEqual((events[0].x, events[0].y), (1060, 490))

        # 3) 释放时应落在最后拖拽位置，而非 button_up 消息自带的归一化位置
        up = parse_mouse_message({"action": "button_up", "button": 0, "normalized_x": 0.1, "normalized_y": 0.1})
        events = session._build_mouse_events(up, 192, 108)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].type, MouseEventType.UP)
        self.assertEqual((events[0].x, events[0].y), (1060, 490))
        self.assertFalse(session.mouse_state.drag_in_progress or False)

    def test_wheel_event_target(self):
        session = make_engine_session(self.GEOMETRY)
        wheel = parse_mouse_message({"action": "wheel", "wheel_steps": 2, "normalized_x": 0.5, "normalized_y": 0.5})
        sx, sy = session.coordinate_mapper.denormalize_coordinate(wheel.normalized_x, wheel.normalized_y)
        events = session._build_mouse_events(wheel, sx, sy)
        self.assertEqual(events[0].type, MouseEventType.WHEEL)
        self.assertEqual((events[0].x, events[0].y), (960, 540))
        self.assertEqual(events[0].delta, 2)


if __name__ == "__main__":
    unittest.main(verbosity=2)
