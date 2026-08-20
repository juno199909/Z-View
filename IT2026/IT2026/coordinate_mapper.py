"""
坐标映射模块 - 企业级实现
解决DPI缩放、多显示器、坐标转换问题
"""

import ctypes
from ctypes import wintypes


def _enable_dpi_awareness(user32):
    """在读取屏幕尺寸前尽早启用 DPI 感知。"""
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)  # PROCESS_PER_MONITOR_DPI_AWARE
        return True, 'per-monitor'
    except Exception:
        try:
            user32.SetProcessDPIAware()
            return True, 'system'
        except Exception:
            return False, None


class CoordinateMapper:
    """坐标映射器 - 处理控制端到被控端的坐标转换"""

    def __init__(self):
        self.user32 = ctypes.windll.user32
        self.dpi_aware, self.dpi_mode = _enable_dpi_awareness(self.user32)
        self.refresh_metrics(initial=True)

    def refresh_metrics(self, initial: bool = False):
        """刷新虚拟桌面和主显示器尺寸，供分辨率切换后重新对账。"""
        previous_metrics = (
            getattr(self, "virtual_screen_x", None),
            getattr(self, "virtual_screen_y", None),
            getattr(self, "virtual_screen_width", None),
            getattr(self, "virtual_screen_height", None),
            getattr(self, "primary_width", None),
            getattr(self, "primary_height", None),
        )
        # 线程级强制 Per-Monitor v2 上下文：进程 DPI 感知可能被其他组件抢先设置成
        # 不感知/系统感知，导致本线程读到 1536x864 一类虚拟化度量；线程上下文
        # 优先级最高且不可被否决，读取完毕后恢复原上下文。
        set_thread_context = getattr(
            self.user32, "SetThreadDpiAwarenessContext", None
        )
        old_context = None
        if callable(set_thread_context):
            try:
                set_thread_context.argtypes = [ctypes.c_void_p]
                set_thread_context.restype = ctypes.c_void_p
                old_context = set_thread_context(ctypes.c_void_p(-4))
            except Exception:
                old_context = None
        try:
            self.virtual_screen_x = self.user32.GetSystemMetrics(76)  # SM_XVIRTUALSCREEN
            self.virtual_screen_y = self.user32.GetSystemMetrics(77)  # SM_YVIRTUALSCREEN
            self.virtual_screen_width = self.user32.GetSystemMetrics(78)  # SM_CXVIRTUALSCREEN
            self.virtual_screen_height = self.user32.GetSystemMetrics(79)  # SM_CYVIRTUALSCREEN

            self.primary_width = self.user32.GetSystemMetrics(0)  # SM_CXSCREEN
            self.primary_height = self.user32.GetSystemMetrics(1)  # SM_CYSCREEN
        finally:
            if old_context is not None:
                try:
                    set_thread_context(old_context)
                except Exception:
                    pass

        current_metrics = (
            self.virtual_screen_x,
            self.virtual_screen_y,
            self.virtual_screen_width,
            self.virtual_screen_height,
            self.primary_width,
            self.primary_height,
        )

        if initial or current_metrics != previous_metrics:
            action_text = "初始化" if initial else "刷新"
            print(f"📐 坐标映射器{action_text}:")
            print(f"   虚拟屏幕: ({self.virtual_screen_x}, {self.virtual_screen_y}) {self.virtual_screen_width}x{self.virtual_screen_height}")
            print(f"   主显示器: {self.primary_width}x{self.primary_height}")
            if self.dpi_aware:
                print(f"   DPI感知: 已启用 ({self.dpi_mode})")
            else:
                print("   DPI感知: 未启用")

    def normalize_coordinate(self, x, y, canvas_width, canvas_height):
        """
        将控制端Canvas坐标转换为归一化坐标 (0.0-1.0)

        参数:
            x, y: Canvas上的像素坐标
            canvas_width, canvas_height: Canvas实际尺寸

        返回:
            (normalized_x, normalized_y): 归一化坐标
        """
        normalized_x = x / canvas_width
        normalized_y = y / canvas_height

        return normalized_x, normalized_y

    def denormalize_coordinate(self, normalized_x, normalized_y):
        """
        将归一化坐标 (0.0-1.0) 转换为被控端屏幕坐标

        与前端 normalize 约定一致：normalized 按位图全宽/全高计算，
        解算时同样按全尺寸比例四舍五入（int 截断会产生 ≤1px 的左/上系统性偏移），
        并与 InputInjector._denormalize_to_virtual_desktop 保持同一换算。

        参数:
            normalized_x, normalized_y: 归一化坐标 (0.0-1.0)

        返回:
            (screen_x, screen_y): 被控端屏幕绝对坐标
        """
        # 转换为虚拟屏幕坐标
        screen_x = int(round(normalized_x * self.virtual_screen_width)) + self.virtual_screen_x
        screen_y = int(round(normalized_y * self.virtual_screen_height)) + self.virtual_screen_y

        # 边界检查
        max_x = self.virtual_screen_x + self.virtual_screen_width - 1
        max_y = self.virtual_screen_y + self.virtual_screen_height - 1

        screen_x = max(self.virtual_screen_x, min(screen_x, max_x))
        screen_y = max(self.virtual_screen_y, min(screen_y, max_y))

        return screen_x, screen_y

    def clamp_screen_coordinate(self, x, y):
        """将屏幕坐标钳制到当前虚拟桌面范围内。"""
        max_x = self.virtual_screen_x + self.virtual_screen_width - 1
        max_y = self.virtual_screen_y + self.virtual_screen_height - 1

        screen_x = max(self.virtual_screen_x, min(int(x), max_x))
        screen_y = max(self.virtual_screen_y, min(int(y), max_y))
        return screen_x, screen_y

    def get_current_mouse_position(self):
        """获取当前鼠标位置"""
        point = wintypes.POINT()
        self.user32.GetCursorPos(ctypes.byref(point))
        return point.x, point.y

    def get_screen_info(self):
        """获取屏幕信息"""
        return {
            'virtual_x': self.virtual_screen_x,
            'virtual_y': self.virtual_screen_y,
            'virtual_width': self.virtual_screen_width,
            'virtual_height': self.virtual_screen_height,
            'primary_width': self.primary_width,
            'primary_height': self.primary_height
        }


class DPIAwareMapper(CoordinateMapper):
    """DPI感知的坐标映射器（扩展）"""
    pass
