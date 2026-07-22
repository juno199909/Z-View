"""
输入注入模块 - 企业级实现
线程安全的鼠标键盘事件注入
"""

import ctypes
from ctypes import wintypes
import time
import threading
from collections import deque
from enum import Enum

from console_utils import enable_utf8_stdio, safe_console_print

enable_utf8_stdio()
print = safe_console_print


class MouseButton(Enum):
    """鼠标按钮枚举"""
    LEFT = 'left'
    RIGHT = 'right'
    MIDDLE = 'middle'


class MouseEventType(Enum):
    """鼠标事件类型"""
    MOVE = 'move'
    DOWN = 'down'
    UP = 'up'
    CLICK = 'click'
    WHEEL = 'wheel'


class MouseEvent:
    """鼠标事件"""
    def __init__(self, event_type, x, y, button=MouseButton.LEFT, delta=0):
        self.type = event_type
        self.x = x
        self.y = y
        self.button = button
        self.delta = delta
        self.timestamp = time.time()


class InputInjector:
    """输入注入器 - 线程安全"""

    def __init__(self, manage_cursor_visibility: bool = False):
        self.user32 = ctypes.windll.user32
        self.metrics_lock = threading.Lock()

        # 输入队列：高频 MOVE 只保留最后一条，避免点击/拖拽被过期坐标拖慢。
        self.event_queue = deque()
        self.pending_move_event = None
        self.event_condition = threading.Condition()

        # 鼠标状态跟踪
        self.mouse_down = False
        self.last_position = (0, 0)
        self.pressed_buttons = set()

        # 鼠标光标状态
        self.manage_cursor_visibility = manage_cursor_visibility
        self.cursor_hidden = False
        self.cursor_count = 0

        self.virtual_left = 0
        self.virtual_top = 0
        self.virtual_width = 0
        self.virtual_height = 0

        # 定义INPUT结构体
        self._define_structures()
        self.refresh_virtual_desktop_metrics(initial=True)

        # 启动事件处理线程
        self.running = True
        self.worker_thread = threading.Thread(target=self._process_events, daemon=True)
        self.worker_thread.start()

        if self.manage_cursor_visibility:
            self.hide_cursor()

        print("[Input] Injector initialized: mode=SendInput thread_safe=true")

    def refresh_virtual_desktop_metrics(self, initial: bool = False):
        """分辨率切换后刷新 SendInput 所依赖的虚拟桌面范围。"""
        with self.metrics_lock:
            self.virtual_left = self.user32.GetSystemMetrics(76)
            self.virtual_top = self.user32.GetSystemMetrics(77)
            self.virtual_width = self.user32.GetSystemMetrics(78)
            self.virtual_height = self.user32.GetSystemMetrics(79)

            max_x = self.virtual_left + max(1, self.virtual_width) - 1
            max_y = self.virtual_top + max(1, self.virtual_height) - 1
            self.last_position = (
                max(self.virtual_left, min(int(self.last_position[0]), max_x)),
                max(self.virtual_top, min(int(self.last_position[1]), max_y)),
            )

        action_text = "initialized" if initial else "refreshed"
        print(
            f"[Input] Virtual desktop metrics {action_text}: "
            f"left={self.virtual_left} top={self.virtual_top} "
            f"width={self.virtual_width} height={self.virtual_height}"
        )

    def _define_structures(self):
        """定义Windows INPUT结构体"""
        ulong_ptr = wintypes.WPARAM

        class MOUSEINPUT(ctypes.Structure):
            _fields_ = [
                ("dx", wintypes.LONG),
                ("dy", wintypes.LONG),
                ("mouseData", wintypes.DWORD),
                ("dwFlags", wintypes.DWORD),
                ("time", wintypes.DWORD),
                ("dwExtraInfo", ulong_ptr)
            ]

        class KEYBDINPUT(ctypes.Structure):
            _fields_ = [
                ("wVk", wintypes.WORD),
                ("wScan", wintypes.WORD),
                ("dwFlags", wintypes.DWORD),
                ("time", wintypes.DWORD),
                ("dwExtraInfo", ctypes.POINTER(wintypes.ULONG))
            ]

        class HARDWAREINPUT(ctypes.Structure):
            _fields_ = [
                ("uMsg", wintypes.DWORD),
                ("wParamL", wintypes.WORD),
                ("wParamH", wintypes.WORD)
            ]

        class INPUT_UNION(ctypes.Union):
            _fields_ = [
                ("mi", MOUSEINPUT),
                ("ki", KEYBDINPUT),
                ("hi", HARDWAREINPUT)
            ]

        class INPUT(ctypes.Structure):
            _fields_ = [
                ("type", wintypes.DWORD),
                ("union", INPUT_UNION)
            ]

        self.MOUSEINPUT = MOUSEINPUT
        self.INPUT = INPUT
        self.INPUT_MOUSE = 0

        # 鼠标事件标志
        self.MOUSEEVENTF_MOVE = 0x0001
        self.MOUSEEVENTF_LEFTDOWN = 0x0002
        self.MOUSEEVENTF_LEFTUP = 0x0004
        self.MOUSEEVENTF_RIGHTDOWN = 0x0008
        self.MOUSEEVENTF_RIGHTUP = 0x0010
        self.MOUSEEVENTF_MIDDLEDOWN = 0x0020
        self.MOUSEEVENTF_MIDDLEUP = 0x0040
        self.MOUSEEVENTF_WHEEL = 0x0800
        self.MOUSEEVENTF_ABSOLUTE = 0x8000
        self.MOUSEEVENTF_VIRTUALDESK = 0x4000
        self.WHEEL_DELTA = 120

        self.user32.SendInput.argtypes = (wintypes.UINT, ctypes.POINTER(INPUT), ctypes.c_int)
        self.user32.SendInput.restype = wintypes.UINT

    def inject_mouse_event(self, event: MouseEvent):
        """
        注入鼠标事件（线程安全）

        参数:
            event: MouseEvent对象
        """
        with self.event_condition:
            if event.type == MouseEventType.MOVE:
                self.pending_move_event = event
            else:
                if self.pending_move_event is not None:
                    self.event_queue.append(self.pending_move_event)
                    self.pending_move_event = None
                self.event_queue.append(event)
            self.event_condition.notify()

    def _process_events(self):
        """事件处理线程"""
        while self.running:
            try:
                with self.event_condition:
                    while self.running and not self.event_queue and self.pending_move_event is None:
                        self.event_condition.wait(timeout=0.1)

                    if not self.running:
                        break

                    if self.event_queue:
                        event = self.event_queue.popleft()
                    else:
                        event = self.pending_move_event
                        self.pending_move_event = None

                self._execute_mouse_event(event)
            except Exception as e:
                print(f"[Input] Event processing error: {e}")

    def _execute_mouse_event(self, event: MouseEvent):
        """执行鼠标事件"""

        if event.type == MouseEventType.MOVE:
            self._move_to(event.x, event.y)

        elif event.type == MouseEventType.DOWN:
            self._move_to(event.x, event.y)
            self._mouse_down(event.button)
            self.mouse_down = True
            self.pressed_buttons.add(event.button)
            self.last_position = (event.x, event.y)

        elif event.type == MouseEventType.UP:
            self._move_to(event.x, event.y)
            self._mouse_up(event.button)
            self.mouse_down = False
            self.pressed_buttons.discard(event.button)

        elif event.type == MouseEventType.CLICK:
            self._move_to(event.x, event.y)
            self._mouse_down(event.button)
            self._mouse_up(event.button)

        elif event.type == MouseEventType.WHEEL:
            self._move_to(event.x, event.y)
            self._scroll(event.delta)

    def _move_to(self, x, y):
        """基于 SendInput 的虚拟桌面绝对坐标移动。"""
        with self.metrics_lock:
            virtual_left = self.virtual_left
            virtual_top = self.virtual_top
            virtual_width = self.virtual_width
            virtual_height = self.virtual_height

        max_x = virtual_left + max(1, virtual_width) - 1
        max_y = virtual_top + max(1, virtual_height) - 1

        clamped_x = max(virtual_left, min(int(x), max_x))
        clamped_y = max(virtual_top, min(int(y), max_y))

        denominator_x = max(1, virtual_width - 1)
        denominator_y = max(1, virtual_height - 1)

        absolute_x = int(round((clamped_x - virtual_left) * 65535 / denominator_x))
        absolute_y = int(round((clamped_y - virtual_top) * 65535 / denominator_y))

        self._send_mouse_input(
            self.MOUSEEVENTF_MOVE | self.MOUSEEVENTF_ABSOLUTE | self.MOUSEEVENTF_VIRTUALDESK,
            dx=absolute_x,
            dy=absolute_y,
        )
        self.last_position = (clamped_x, clamped_y)

    def _mouse_down(self, button: MouseButton):
        """按下鼠标"""
        flag_map = {
            MouseButton.LEFT: self.MOUSEEVENTF_LEFTDOWN,
            MouseButton.RIGHT: self.MOUSEEVENTF_RIGHTDOWN,
            MouseButton.MIDDLE: self.MOUSEEVENTF_MIDDLEDOWN,
        }
        self._send_mouse_input(flag_map.get(button, self.MOUSEEVENTF_LEFTDOWN))

    def _mouse_up(self, button: MouseButton):
        """松开鼠标"""
        flag_map = {
            MouseButton.LEFT: self.MOUSEEVENTF_LEFTUP,
            MouseButton.RIGHT: self.MOUSEEVENTF_RIGHTUP,
            MouseButton.MIDDLE: self.MOUSEEVENTF_MIDDLEUP,
        }
        self._send_mouse_input(flag_map.get(button, self.MOUSEEVENTF_LEFTUP))

    def _scroll(self, delta):
        """滚轮滚动"""
        if delta == 0:
            return
        self._send_mouse_input(self.MOUSEEVENTF_WHEEL, data=int(delta) * self.WHEEL_DELTA)

    def _send_mouse_input(self, flags, data=0, dx=0, dy=0):
        input_struct = self.INPUT()
        input_struct.type = self.INPUT_MOUSE
        input_struct.union.mi = self.MOUSEINPUT(
            dx=int(dx),
            dy=int(dy),
            mouseData=int(data),
            dwFlags=int(flags),
            time=0,
            dwExtraInfo=0,
        )

        # 使用真正的 LP_INPUT 实例，避免某些运行环境下 byref() 被 ctypes 拒绝。
        input_array = (self.INPUT * 1)(input_struct)
        sent = self.user32.SendInput(1, input_array, ctypes.sizeof(self.INPUT))
        if sent != 1:
            raise ctypes.WinError(ctypes.get_last_error())

    def release_mouse_buttons(self, buttons):
        """兜底释放仍处于按下态的鼠标按钮。"""
        for button in list(buttons):
            try:
                self._mouse_up(button)
            except Exception:
                pass
            self.pressed_buttons.discard(button)

    def hide_cursor(self):
        """隐藏鼠标光标"""
        if not self.cursor_hidden:
            # 循环调用ShowCursor(FALSE)直到返回值<0
            while self.user32.ShowCursor(False) >= 0:
                pass
            self.cursor_hidden = True
            print("[Input] Remote cursor hidden")

    def show_cursor(self):
        """显示鼠标光标"""
        if self.cursor_hidden:
            # 循环调用ShowCursor(TRUE)直到返回值>=0
            while self.user32.ShowCursor(True) < 0:
                pass
            self.cursor_hidden = False
            print("[Input] Remote cursor restored")

    def stop(self):
        """停止事件处理"""
        with self.event_condition:
            self.running = False
            self.event_condition.notify_all()
        if self.worker_thread.is_alive():
            self.worker_thread.join(timeout=2)

        self.release_mouse_buttons(list(self.pressed_buttons))

        if self.manage_cursor_visibility:
            self.show_cursor()
