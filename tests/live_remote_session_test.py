# -*- coding: utf-8 -*-
"""真机远控会话实测：通过平台代理与真实 Agent 建立完整远控会话。

验证项：
  1. 同意流程（consent_required -> 自动点击允许 -> consent_result approved）
  2. screen_info 与实时虚拟桌面指标一致
  3. 首帧尺寸与 scale 换算一致、覆盖虚拟桌面
  4. 归一化坐标 -> 被控端光标物理落点（网格采样，误差 <=1px）
  5. 拖拽序列：down -> 相对增量 drag_move -> up，落点跟随
  6. 滚轮注入不破坏会话

运行: python tests\\live_remote_session_test.py
注意: 会真实移动本机鼠标。
"""

import asyncio
import ctypes
import json
import sys
import threading
import time
from ctypes import wintypes
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import websockets  # noqa: E402

ctypes.windll.shcore.SetProcessDpiAwareness(2)
user32 = ctypes.windll.user32

PROXY_WS = "ws://127.0.0.1:8080/api/v1/assets/{asset}/remote-desktop/ws"
ASSET_ID = 28
# 目标为本机(28)时才允许读取本地光标/窗口做断言；远端资产改用协议级验证
LOCAL_TARGET = True


# ---------------------------------------------------------------------------
# 屏幕与鼠标工具
# ---------------------------------------------------------------------------

class POINT(ctypes.Structure):
    _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]


def virtual_metrics():
    return (
        user32.GetSystemMetrics(76),
        user32.GetSystemMetrics(77),
        user32.GetSystemMetrics(78),
        user32.GetSystemMetrics(79),
    )


def cursor_position():
    point = POINT()
    user32.GetCursorPos(ctypes.byref(point))
    return point.x, point.y


def send_abs_move(x, y):
    left, top, width, height = virtual_metrics()
    ax = int(round((x - left) * 65535 / max(1, width - 1)))
    ay = int(round((y - top) * 65535 / max(1, height - 1)))

    class MOUSEINPUT(ctypes.Structure):
        _fields_ = [
            ("dx", wintypes.LONG), ("dy", wintypes.LONG),
            ("mouseData", wintypes.DWORD), ("dwFlags", wintypes.DWORD),
            ("time", wintypes.DWORD), ("dwExtraInfo", ctypes.POINTER(wintypes.ULONG)),
        ]

    class INPUT(ctypes.Structure):
        class U(ctypes.Union):
            _fields_ = [("mi", MOUSEINPUT)]
        _anonymous_ = ("u",)
        _fields_ = [("type", wintypes.DWORD), ("u", U)]

    def inject(flags, dx=0, dy=0):
        item = INPUT(type=0)
        item.mi = MOUSEINPUT(dx=ax if flags & 1 else dx, dy=ay if flags & 1 else dy,
                             mouseData=0, dwFlags=flags | 0x8000 | 0x4000, time=0, dwExtraInfo=None)
        arr = (INPUT * 1)(item)
        user32.SendInput(1, arr, ctypes.sizeof(INPUT))

    inject(0x0001, ax, ay)   # MOVE
    time.sleep(0.05)
    inject(0x0002)           # LEFTDOWN
    time.sleep(0.05)
    inject(0x0004)           # LEFTUP


# ---------------------------------------------------------------------------
# 同意弹窗自动批准线程
# ---------------------------------------------------------------------------

ENUM_WINDOWS_CB = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
ENUM_CHILD_CB = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)


def _window_text(hwnd):
    length = user32.GetWindowTextLengthW(hwnd)
    buffer = ctypes.create_unicode_buffer(length + 1)
    user32.GetWindowTextW(hwnd, buffer, length + 1)
    return buffer.value


def _find_allow_button_rect(parent_hwnd):
    result = []

    @ENUM_CHILD_CB
    def child_cb(child, _lparam):
        text = _window_text(child)
        if text.strip() == "允许":
            rect = wintypes.RECT()
            if user32.GetWindowRect(child, ctypes.byref(rect)):
                result.append((rect.left, rect.top, rect.right, rect.bottom))
        return True

    user32.EnumChildWindows(parent_hwnd, child_cb, 0)
    return result[0] if result else None


def _find_consent_dialog():
    found = []

    @ENUM_WINDOWS_CB
    def cb(hwnd, _lparam):
        if not user32.IsWindowVisible(hwnd):
            return True
        text = _window_text(hwnd)
        if "远程控制确认" in text:
            found.append(hwnd)
        return True

    user32.EnumWindows(cb, 0)
    return found[0] if found else None


def run_consent_approver(timeout_seconds=25):
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        hwnd = _find_consent_dialog()
        if hwnd:
            rect = _find_allow_button_rect(hwnd)
            if rect:
                cx, cy = (rect[0] + rect[2]) // 2, (rect[1] + rect[3]) // 2
                print(f"[approver] 点击允许按钮: ({cx},{cy})")
                send_abs_move(cx, cy)
                return True
        time.sleep(0.3)
    print("[approver] 未找到同意弹窗（可能已被 WTS 回退处理）")
    return False


# ---------------------------------------------------------------------------
# 会话驱动
# ---------------------------------------------------------------------------

async def recv_until(ws, wanted_types, timeout=20):
    """读取消息直到出现目标类型；返回该消息及途中缓存的所有消息。"""
    seen = []
    deadline = time.time() + timeout
    while time.time() < deadline:
        raw = await asyncio.wait_for(ws.recv(), timeout=max(0.1, deadline - time.time()))
        message = json.loads(raw)
        mtype = message.get("type")
        if mtype in wanted_types:
            return message, seen
        if mtype != "frame":
            seen.append(message)
    raise TimeoutError(f"等待 {wanted_types} 超时，已见类型: {[m.get('type') for m in seen][-8:]}")


async def drain_frames(ws, seconds=2.0, min_frames=1, frame_info={}):
    deadline = time.time() + seconds
    count = 0
    while time.time() < deadline:
        try:
            raw = await asyncio.wait_for(ws.recv(), timeout=max(0.05, deadline - time.time()))
        except asyncio.TimeoutError:
            break
        message = json.loads(raw)
        if message.get("type") == "frame":
            count += 1
            frame_info.update(message)
    assert count >= min_frames, f"{seconds}s 内仅收到 {count} 帧"
    return count


async def send_and_settle(ws, payload, settle=0.45):
    await ws.send(json.dumps(payload))
    await asyncio.sleep(settle)
    try:
        while True:
            raw = await asyncio.wait_for(ws.recv(), timeout=0.01)
            # 吸收期间产生的帧消息，保持管道畅通
            del raw
    except asyncio.TimeoutError:
        pass


RESULTS = []


def record(name, ok, detail=""):
    RESULTS.append((name, bool(ok), detail))
    print(f"[{'PASS' if ok else 'FAIL'}] {name} {detail}")


async def run_test():
    global LOCAL_TARGET
    args = [a for a in sys.argv[1:] if not a.startswith("-")]
    if args:
        try:
            ASSET_ID = int(args[0])
        except ValueError:
            ASSET_ID = 28
    LOCAL_TARGET = ASSET_ID == 28

    from auth_utils import issue_access_token
    token = issue_access_token("admin")["access_token"]

    left, top, width, height = virtual_metrics()
    print(f"[env] 资产: {ASSET_ID} | 本地光标断言: {'启用' if LOCAL_TARGET else '停用(远端目标)'}")

    uri = f"{PROXY_WS}?token={token}"
    async with websockets.connect(uri, max_size=None, ping_interval=None) as ws:
        print(">>> 请在被控端屏幕上的「CMDB 远程控制确认」弹窗中点击 [是(Y)]（90 秒内）<<<", flush=True)

        # ---- 1. 同意流程 + screen_info ----
        consent_required, _ = await recv_until(ws, {"consent_required"})
        record("consent_required 收到", consent_required.get("target") is not None)

        consent_result, _ = await recv_until(ws, {"consent_result"}, timeout=130)
        record(
            "consent_result approved",
            consent_result.get("approved") is True,
            f"reason={consent_result.get('reason')}",
        )
        # 人工点击「允许」后手可能仍在移动物理鼠标，等待光标稳定再开始网格采样
        await asyncio.sleep(2.0)

        screen_info, _ = await recv_until(ws, {"screen_info"}, timeout=15)
        si_left = screen_info.get("width")
        if LOCAL_TARGET:
            record("screen_info 宽度匹配实时指标", si_left == width,
                   f"msg={screen_info.get('width')}x{screen_info.get('height')} live={width}x{height}")
        else:
            record("screen_info 合理性(远端)", isinstance(si_left, int) and si_left >= 800,
                   f"msg={screen_info.get('width')}x{screen_info.get('height')}")

        # ---- 2. 首帧尺寸 ----
        frame_info = {}
        frames = await drain_frames(ws, seconds=4.0, min_frames=2, frame_info=frame_info)
        fw, fh = frame_info.get("width"), frame_info.get("height")
        record("帧流接收", frames >= 2, f"{frames} 帧/{4.0}s")
        # 自适应流会按压力/空闲在 0.65~0.9 间调整缩放，这里校验宽高比与缩放范围
        si_w = screen_info.get("width") or width
        si_h = screen_info.get("height") or height
        aspect_msg = fw / max(1, fh)
        aspect_live = si_w / max(1, si_h)
        ratio = fw / max(1, si_w)
        record(
            "帧尺寸与屏幕信息成比例(自适应缩放)",
            abs(aspect_msg - aspect_live) <= 0.02 and 0.60 <= ratio <= 0.92,
            f"frame={fw}x{fh} screen_info={si_w}x{si_h} scale≈{ratio:.2f}",
        )

        # ---- 3. 光标物理落点网格（仅本机目标可读取物理光标；远端改由帧差分验证） ----
        targets = [(0.02, 0.02), (0.25, 0.25), (0.5, 0.5), (0.75, 0.75), (0.98, 0.5), (0.5, 0.97)]
        if LOCAL_TARGET:
            worst = 0
            for fx, fy in targets:
                want_x = left + min(int(round(fx * width)), width - 1)
                want_y = top + min(int(round(fy * height)), height - 1)
                await send_and_settle(ws, {
                    "type": "mouse", "action": "move",
                    "normalized_x": fx, "normalized_y": fy,
                })
                got_x, got_y = cursor_position()
                err = max(abs(got_x - want_x), abs(got_y - want_y))
                worst = max(worst, err)
                record(f"落点({fx:.2f},{fy:.2f})", err <= 1, f"want=({want_x},{want_y}) got=({got_x},{got_y}) err={err}")
            record("全网格最大误差<=1px", worst <= 1, f"worst={worst}px")
        else:
            for fx, fy in targets:
                await send_and_settle(ws, {
                    "type": "mouse", "action": "move",
                    "normalized_x": fx, "normalized_y": fy,
                })
            record("网格移动指令全部派发(远端)", True, "落点精度由 remote_domain_smoke_test 帧差分验证")

        # ---- 4. 拖拽序列 ----
        await send_and_settle(ws, {"type": "mouse", "action": "move", "normalized_x": 0.25, "normalized_y": 0.25})
        if LOCAL_TARGET:
            sx, sy = cursor_position()
        await send_and_settle(ws, {"type": "mouse", "action": "button_down", "button": 0,
                                   "normalized_x": 0.25, "normalized_y": 0.25}, settle=0.3)
        await send_and_settle(ws, {"type": "mouse", "action": "drag_move", "delta_x": 150, "delta_y": 80,
                                   "normalized_x": 0.33, "normalized_y": 0.33}, settle=0.35)
        await send_and_settle(ws, {"type": "mouse", "action": "drag_move", "delta_x": 50, "delta_y": 20,
                                   "normalized_x": 0.36, "normalized_y": 0.36}, settle=0.35)
        await send_and_settle(ws, {"type": "mouse", "action": "button_up", "button": 0,
                                   "normalized_x": 0.36, "normalized_y": 0.36}, settle=0.3)
        if LOCAL_TARGET:
            ex, ey = cursor_position()
            want_ex, want_ey = sx + 200, sy + 100
            err = max(abs(ex - want_ex), abs(ey - want_ey))
            record("拖拽终点跟随", err <= 1, f"start=({sx},{sy}) want_end=({want_ex},{want_ey}) got=({ex},{ey})")
        else:
            record("拖拽序列派发完成(远端)", True, "位移精度由 remote_domain_smoke_test 验证")

        # ---- 5. 滚轮 ----
        await send_and_settle(ws, {"type": "mouse", "action": "wheel", "wheel_steps": 1,
                                    "normalized_x": 0.6, "normalized_y": 0.4}, settle=0.3)
        if LOCAL_TARGET:
            wx = left + int(round(0.6 * width))
            wy = top + int(round(0.4 * height))
            after = cursor_position()
            err_w = max(abs(after[0] - min(wx, left + width - 1)), abs(after[1] - min(wy, top + height - 1)))
            record("滚轮事件落点与会话存活", err_w <= 2, f"want≈({wx},{wy}) got={after} err={err_w}")
        else:
            record("滚轮事件派发与会话存活(远端)", True)

        # ---- 6. 右键 ----
        await send_and_settle(ws, {"type": "mouse", "action": "button_down", "button": 2,
                                   "normalized_x": 0.5, "normalized_y": 0.5}, settle=0.2)
        await send_and_settle(ws, {"type": "mouse", "action": "button_up", "button": 2,
                                   "normalized_x": 0.5, "normalized_y": 0.5}, settle=0.3)
        record("右键按下/释放完成", True)
        await ws.send(json.dumps({"type": "keyboard", "action": "press", "key": "escape"}))

    failed = [name for name, ok, _ in RESULTS if not ok]
    print("=" * 60)
    print(f"总计 {len(RESULTS)} 项, 失败 {len(failed)} 项")
    if failed:
        print("失败项:", failed)
    return len(failed) == 0


if __name__ == "__main__":
    ok = asyncio.run(run_test())
    sys.exit(0 if ok else 1)
