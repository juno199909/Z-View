"""
远程桌面引擎 - 使用pyautogui实现
功能：屏幕捕获、鼠标键盘控制、WebSocket通信
"""

import asyncio
import json
import base64
import io
import time
from datetime import datetime
from PIL import Image
import pyautogui
from fastapi import WebSocket
import threading

from console_utils import enable_utf8_stdio, safe_console_print

enable_utf8_stdio()
print = safe_console_print

# 禁用pyautogui的安全检查
pyautogui.FAILSAFE = False

# 禁用pyautogui的安全检查
pyautogui.FAILSAFE = False

# ============================================
# 鼠标键盘控制器（使用pyautogui）
# ============================================

class MouseController:
    """鼠标控制器 - 使用pyautogui"""

    def __init__(self):
        print("✅ 鼠标控制器初始化完成（pyautogui）")

    def move_to(self, x, y):
        """移动鼠标到绝对坐标"""
        pyautogui.moveTo(x, y, duration=0)
        return 1

    def mouse_down(self, button='left'):
        """按下鼠标按钮"""
        pyautogui.mouseDown(button=button)
        return 1

    def mouse_up(self, button='left'):
        """松开鼠标按钮"""
        pyautogui.mouseUp(button=button)
        return 1

    def click(self, x, y, button='left'):
        """点击"""
        pyautogui.click(x, y, button=button)
        return 1

    def scroll(self, amount):
        """滚轮滚动"""
        pyautogui.scroll(amount * 120)
        return 1


class KeyboardController:
    """键盘控制器 - 使用pyautogui"""

    def __init__(self):
        print("✅ 键盘控制器初始化完成（pyautogui）")

    def key_down(self, key):
        """按下键"""
        try:
            pyautogui.keyDown(key)
        except:
            pass

    def key_up(self, key):
        """松开键"""
        try:
            pyautogui.keyUp(key)
        except:
            pass

    def press(self, key):
        """按键"""
        try:
            pyautogui.press(key)
        except:
            pass


class ScreenCapturer:
    """屏幕捕获器 - 使用pyautogui"""

    def __init__(self):
        print("✅ 屏幕捕获器初始化完成（pyautogui）")

class ScreenCapturer:
    """屏幕捕获器 - 使用pyautogui"""

    def __init__(self):
        print("✅ 屏幕捕获器初始化完成（pyautogui）")

    def capture(self, quality=85, scale=0.9):
        """捕获屏幕"""
        try:
            # 使用pyautogui截图
            screenshot = pyautogui.screenshot()

            # 缩放
            if scale != 1.0:
                new_width = int(screenshot.width * scale)
                new_height = int(screenshot.height * scale)
                screenshot = screenshot.resize((new_width, new_height), Image.Resampling.LANCZOS)

            # 编码为JPEG
            buffer = io.BytesIO()
            screenshot.save(buffer, format='JPEG', quality=quality, optimize=True)
            jpeg_data = buffer.getvalue()

            return {
                'data': base64.b64encode(jpeg_data).decode('utf-8'),
                'width': screenshot.width,
                'height': screenshot.height,
                'size': len(jpeg_data)
            }

        except Exception as e:
            print(f"❌ 屏幕捕获失败: {e}")
            return None


class RemoteDesktopSession:
    """远程桌面会话"""

    def __init__(self, websocket: WebSocket, session_id: str):
        self.websocket = websocket
        self.session_id = session_id
        self.running = False
        self.quality = 95  # 内网高画质
        self.fps = 30  # 内网30帧
        self.scale = 1.0  # 内网100%原始分辨率

        # 初始化控制器
        self.mouse = MouseController()
        self.keyboard = KeyboardController()
        self.capturer = ScreenCapturer()

        # 统计
        self.frame_count = 0
        self.start_time = time.time()

        print(f"🖥️  远程桌面会话创建: {session_id}")

    async def start(self):
        """启动会话"""
        self.running = True

        # 启动屏幕捕获循环
        asyncio.create_task(self.capture_loop())

        # 处理控制消息
        await self.message_loop()

    async def capture_loop(self):
        """屏幕捕获循环"""
        frame_interval = 1.0 / self.fps

        while self.running:
            try:
                start = time.time()

                # 捕获屏幕
                frame = self.capturer.capture(quality=self.quality, scale=self.scale)

                if frame:
                    # 发送帧数据
                    await self.websocket.send_json({
                        'type': 'frame',
                        'data': frame['data'],
                        'width': frame['width'],
                        'height': frame['height']
                    })

                    self.frame_count += 1

                    # 每10帧输出一次统计
                    if self.frame_count % 10 == 0:
                        elapsed = time.time() - self.start_time
                        actual_fps = self.frame_count / elapsed
                        print(f"📊 已发送 {self.frame_count} 帧 ({frame['width']}x{frame['height']}) | "
                              f"实际FPS: {actual_fps:.1f} | 帧大小: {frame['size']/1024:.1f}KB")

                # 控制帧率
                elapsed = time.time() - start
                sleep_time = max(0, frame_interval - elapsed)
                await asyncio.sleep(sleep_time)

            except Exception as e:
                print(f"❌ 捕获循环错误: {e}")
                break

    async def message_loop(self):
        """消息处理循环"""
        try:
            while self.running:
                message = await self.websocket.receive_text()
                data = json.loads(message)

                # 处理控制消息
                await self.handle_control(data)

        except Exception as e:
            print(f"❌ 消息循环错误: {e}")
        finally:
            self.running = False

    async def handle_control(self, message: dict):
        """处理控制消息"""
        msg_type = message.get('type')

        try:
            if msg_type == 'mouse':
                self.handle_mouse(message)
            elif msg_type == 'keyboard':
                self.handle_keyboard(message)
            elif msg_type == 'settings':
                self.update_settings(message)
        except Exception as e:
            print(f"❌ 控制处理错误: {e}")

    def handle_mouse(self, message: dict):
        """处理鼠标事件（参考RustDesk实现）"""
        action = message.get('action')
        x = message.get('x', 0)
        y = message.get('y', 0)
        button = message.get('button', 0)

        button_map = {0: 'left', 1: 'middle', 2: 'right'}
        btn = button_map.get(button, 'left')

        if action == 'mousemove':
            # 移动鼠标
            print(f"🖱️ Move to ({x}, {y})")
            self.mouse.move_to(x, y)
        elif action == 'mousedown':
            # RustDesk风格：先移动到位置，再按下
            print(f"🖱️ MouseDown at ({x}, {y})")
            self.mouse.move_to(x, y)
            self.mouse.mouse_down(btn)
        elif action == 'mouseup':
            # 关键修复：MouseUp必须携带最终坐标
            # 确保释放位置 = 最后移动位置，避免窗口跳跃
            print(f"🖱️ MouseUp at ({x}, {y})")
            self.mouse.move_to(x, y)
            self.mouse.mouse_up(btn)
        elif action == 'click':
            # 完整的点击：移动+按下+松开
            print(f"🖱️ Click at ({x}, {y})")
            self.mouse.click(x, y, btn)
        elif action == 'wheel':
            delta = message.get('deltaY', 0)
            scroll_amount = int(-delta / 50)
            if scroll_amount != 0:
                self.mouse.scroll(scroll_amount)

    def handle_keyboard(self, message: dict):
        """处理键盘事件"""
        action = message.get('action')
        key = message.get('key', '')

        if action == 'keydown':
            self.keyboard.key_down(key)
        elif action == 'keyup':
            self.keyboard.key_up(key)
        elif action == 'press':
            self.keyboard.press(key)

    def update_settings(self, message: dict):
        """更新设置"""
        if 'quality' in message:
            self.quality = message['quality']
            print(f"⚙️  更新画质: {self.quality}")

        if 'fps' in message:
            self.fps = message['fps']
            print(f"⚙️  更新帧率: {self.fps}")

        if 'scale' in message:
            self.scale = message['scale']
            print(f"⚙️  更新缩放: {self.scale}")

    def stop(self):
        """停止会话"""
        self.running = False
        print(f"🛑 远程桌面会话结束: {self.session_id}")


# 导出主要类
__all__ = ['RemoteDesktopSession', 'MouseController', 'KeyboardController', 'ScreenCapturer']
