# -*- coding: utf-8 -*-
"""多显示器生产路径集成测试。

真实硬件第二屏在本机无法提供（RDP 空闲适配器无显示模式、仓库无签名 IDD 驱动），
因此用与打包版本完全一致的源码模块，在双屏几何（含负原点）下驱动
RemoteDesktopSession.handle_mouse 生产全链路：
  wire 消息 -> parse_mouse_message -> CoordinateMapper.denormalize
           -> _build_mouse_events -> InputInjector 队列 -> SendInput 绝对换算。
SendInput 打桩捕获，按 Windows 官方 0..65535 映射反解像素后断言落点。

运行: python tests\\multimon_engine_harness.py
"""

import sys
import threading
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from coordinate_mapper import DPIAwareMapper  # noqa: E402
from input_injector import InputInjector  # noqa: E402
from remote_desktop_engine_v2 import RemoteDesktopSession  # noqa: E402


def sendinput_reverse(dx, dy, left, top, width, height):
    return (
        left + round(dx * (width - 1) / 65535),
        top + round(dy * (height - 1) / 65535),
    )


class DualMonitorCase(unittest.TestCase):
    """虚拟桌面 = 副屏(-1920..-1) + 主屏(0..1919)，3840x1080。"""

    LEFT, TOP, WIDTH, HEIGHT = -1920, 0, 3840, 1080

    def make_session(self):
        session = RemoteDesktopSession.__new__(RemoteDesktopSession)
        session.session_id = "multimon-harness"

        mapper = DPIAwareMapper()
        mapper.virtual_screen_x = self.LEFT
        mapper.virtual_screen_y = self.TOP
        mapper.virtual_screen_width = self.WIDTH
        mapper.virtual_screen_height = self.HEIGHT
        mapper.refresh_metrics = lambda initial=False: None  # 冻结模拟拓扑
        session.coordinate_mapper = mapper

        injector = InputInjector.__new__(InputInjector)
        injector.user32 = None
        injector.metrics_lock = threading.Lock()
        injector.mouse_down = False
        injector.last_position = (self.LEFT, self.TOP)
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
        injector.virtual_left = self.LEFT
        injector.virtual_top = self.TOP
        injector.virtual_width = self.WIDTH
        injector.virtual_height = self.HEIGHT

        def fake_refresh(initial=False):
            return {
                "left": self.LEFT,
                "top": self.TOP,
                "width": self.WIDTH,
                "height": self.HEIGHT,
            }

        injector.refresh_virtual_desktop_metrics = fake_refresh
        injector.sent_calls = []

        def fake_send(flags, data=0, dx=0, dy=0):
            injector.sent_calls.append((flags, int(dx), int(dy), int(data)))

        injector._send_mouse_input = fake_send

        def sync_inject(event):
            injector._execute_mouse_event(event)

        injector.inject_mouse_event = sync_inject
        session.input_injector = injector

        from remote_desktop_protocol import RemoteMouseState

        session.mouse_state = RemoteMouseState()
        session.mouse_state.remember_screen_position(self.LEFT, self.TOP)
        session.wheel_speed = 1.0
        session.mouse_sensitivity = 1.0
        return session

    def last_landed(self, session):
        move_flags = 0x0001 | 0x8000 | 0x4000  # MOVE|ABSOLUTE|VIRTUALDESK
        for flags, dx, dy, _data in reversed(session.input_injector.sent_calls):
            if flags & move_flags == move_flags:
                landed = sendinput_reverse(dx, dy, self.LEFT, self.TOP, self.WIDTH, self.HEIGHT)
                return landed
        raise AssertionError("未找到包含绝对移动的 SendInput 调用")

    def test_move_spans_both_monitors(self):
        session = self.make_session()
        cases = [0.10, 0.40, 0.50, 0.75, 0.98]
        for nx in cases:
            with self.subTest(nx=nx):
                want_x = min(self.LEFT + int(round(nx * self.WIDTH)), self.LEFT + self.WIDTH - 1)
                session.handle_mouse({
                    "action": "move", "normalized_x": nx, "normalized_y": 0.5,
                })
                got_x, got_y = self.last_landed(session)
                self.assertEqual(got_x, want_x, f"nx={nx} want={want_x} got={got_x}")
                self.assertEqual(got_y, 540)

    def test_click_on_secondary_monitor(self):
        session = self.make_session()
        session.handle_mouse({"action": "move", "normalized_x": 0.2, "normalized_y": 0.3})
        session.handle_mouse({"action": "button_down", "button": 0,
                              "normalized_x": 0.2, "normalized_y": 0.3})
        session.handle_mouse({"action": "button_up", "button": 0,
                              "normalized_x": 0.2, "normalized_y": 0.3})
        got_x, got_y = self.last_landed(session)
        self.assertEqual((got_x, got_y), (-1152, 324))
        flags_last = session.input_injector.sent_calls[-1][0]
        self.assertEqual(flags_last, 0x0004)  # 最后一次为 LEFTUP

    def test_drag_crossing_monitor_boundary(self):
        session = self.make_session()
        # 从主屏中部 (nx=0.75 -> x=961) 向左拖拽 1200px，跨越边界进入副屏
        session.handle_mouse({"action": "move", "normalized_x": 0.75, "normalized_y": 0.5})
        session.handle_mouse({"action": "button_down", "button": 0,
                              "normalized_x": 0.75, "normalized_y": 0.5})
        start_x, _ = self.last_landed(session)
        self.assertEqual(start_x, 960)

        for delta in (-400, -400, -400):
            session.handle_mouse({"action": "drag_move", "delta_x": delta, "delta_y": 0,
                                  "normalized_x": 0.1, "normalized_y": 0.5})
        drag_x, drag_y = self.last_landed(session)
        self.assertEqual(drag_x, start_x - 1200)
        self.assertEqual(drag_y, 540)

        session.handle_mouse({"action": "button_up", "button": 0,
                              "normalized_x": 0.1, "normalized_y": 0.5})
        up_x, up_y = self.last_landed(session)
        self.assertEqual(up_x, start_x - 1200)
        self.assertEqual(up_y, 540)

    def test_screen_info_reflects_virtual_desktop(self):
        session = self.make_session()
        info = session.coordinate_mapper.get_screen_info()
        self.assertEqual(info["virtual_width"], 3840)
        self.assertEqual(info["virtual_height"], 1080)
        self.assertEqual(info["virtual_x"], -1920)


if __name__ == "__main__":
    unittest.main(verbosity=2)
