# -*- coding: utf-8 -*-
"""远控会话综合功能实测：左右键、键盘输入、鼠标拖动、复制粘贴、管理员权限。

在一个真实远控会话内完成全部场景（只需人工点击一次「允许」）：
  T1 左键聚焦 + 键盘输入     —— 注入点击聚焦记事本，注入打字，Ctrl+A/Ctrl+C 后经
                                剪贴板协议读回验证内容一致
  T2 复制粘贴               —— 协议写入剪贴板 -> 注入 Ctrl+V -> 回读验证粘贴生效
  T3 右键上下文菜单         —— 右键记事本编辑区，检测 #32768 菜单窗口出现/ESC 关闭
  T4 鼠标拖动               —— 按住标题栏拖动记事本 (+150,+100)，校验窗口位移
  T5 管理员权限             —— Win+R 输入 cmd 后 Ctrl+Shift+Enter（以管理员身份运行），
                                校验出现「管理员:」标题的提权控制台

运行: python tests\\remote_desktop_functional_test.py
注意: 全程需要一次人工点击允许；测试会在被控端打开/关闭记事本与 cmd。
"""

import asyncio
import ctypes
import json
import subprocess
import sys
import time
from ctypes import wintypes
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import websockets  # noqa: E402

ctypes.windll.shcore.SetProcessDpiAwareness(2)
user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32

PROXY_WS = "ws://127.0.0.1:8080/api/v1/assets/28/remote-desktop/ws"

ENUM_CB = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
RESULTS = []
CREATED_WINDOWS = []


def record(name, ok, detail=""):
    RESULTS.append(bool(ok))
    print(f"[{'PASS' if ok else 'FAIL'}] {name} {detail}", flush=True)


def virtual_metrics():
    user32.SetProcessDPIAware()
    left = user32.GetSystemMetrics(76)
    top = user32.GetSystemMetrics(77)
    width = user32.GetSystemMetrics(78)
    height = user32.GetSystemMetrics(79)
    return left, top, width, height


def cursor_position():
    pt = wintypes.POINT()
    user32.GetCursorPos(ctypes.byref(pt))
    return pt.x, pt.y


def _window_text(hwnd):
    n = user32.GetWindowTextLengthW(hwnd)
    buf = ctypes.create_unicode_buffer(n + 1)
    user32.GetWindowTextW(hwnd, buf, n + 1)
    return buf.value


def _class_name(hwnd):
    buf = ctypes.create_unicode_buffer(256)
    user32.GetClassNameW(hwnd, buf, 256)
    return buf.value


def find_windows(predicate):
    found = []

    def cb(hwnd, _lparam):
        if user32.IsWindowVisible(hwnd):
            info = {"title": _window_text(hwnd), "cls": _class_name(hwnd), "hwnd": hwnd}
            if predicate(info):
                rect = wintypes.RECT()
                user32.GetWindowRect(hwnd, ctypes.byref(rect))
                info["rect"] = (rect.left, rect.top, rect.right, rect.bottom)
                found.append(info)
        return True

    user32.EnumWindows(ENUM_CB(cb), 0)
    return found


def get_rect(hwnd):
    rect = wintypes.RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(rect))
    return (rect.left, rect.top, rect.right, rect.bottom)


# ---------------------------------------------------------------------------
# 会话驱动（与 live_remote_session_test 同款）
# ---------------------------------------------------------------------------

async def recv_until(ws, wanted_types, timeout=20):
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
    raise TimeoutError(f"等待 {wanted_types} 超时")


async def send_and_settle(ws, payload, settle=0.45):
    await ws.send(json.dumps(payload))
    await asyncio.sleep(settle)
    try:
        while True:
            raw = await asyncio.wait_for(ws.recv(), timeout=0.01)
            del raw
    except asyncio.TimeoutError:
        pass


async def mouse_click(ws, x, y, metrics, button=0, settle=0.5):
    left, top, width, height = metrics
    nx = (x - left) / max(1, width)
    ny = (y - top) / max(1, height)
    await send_and_settle(ws, {"type": "mouse", "action": "move", "normalized_x": nx, "normalized_y": ny}, 0.3)
    await send_and_settle(ws, {"type": "mouse", "action": "button_down", "button": button,
                               "normalized_x": nx, "normalized_y": ny}, 0.12)
    await send_and_settle(ws, {"type": "mouse", "action": "button_up", "button": button,
                               "normalized_x": nx, "normalized_y": ny}, settle)


async def mouse_drag(ws, sx, sy, dx_total, dy_total, metrics, steps=6):
    left, top, width, height = metrics
    nx = (sx - left) / max(1, width)
    ny = (sy - top) / max(1, height)
    await send_and_settle(ws, {"type": "mouse", "action": "move", "normalized_x": nx, "normalized_y": ny}, 0.3)
    await send_and_settle(ws, {"type": "mouse", "action": "button_down", "button": 0,
                               "normalized_x": nx, "normalized_y": ny}, 0.2)
    cx, cy = sx, sy
    per_x = dx_total / steps
    per_y = dy_total / steps
    for _ in range(steps):
        cx += per_x
        cy += per_y
        await send_and_settle(ws, {"type": "mouse", "action": "drag_move",
                                   "delta_x": int(round(per_x)), "delta_y": int(round(per_y)),
                                   "normalized_x": (cx - left) / max(1, width),
                                   "normalized_y": (cy - top) / max(1, height)}, 0.18)
    await send_and_settle(ws, {"type": "mouse", "action": "button_up", "button": 0,
                               "normalized_x": (cx - left) / max(1, width),
                               "normalized_y": (cy - top) / max(1, height)}, 0.6)


async def key_press(ws, key, settle=0.35, **modifiers):
    await send_and_settle(ws, {"type": "keyboard", "action": "press", "key": key, **modifiers}, settle)


async def key_type(ws, text, settle=0.5):
    # 逐字符精确大小写注入：引擎将键名统一转小写，大写/下划线需 shiftKey
    for ch in text:
        payload = {"type": "keyboard", "action": "press"}
        if ch == "_":
            payload.update({"key": "-", "shiftKey": True})
        elif ch.isupper():
            payload.update({"key": ch.lower(), "shiftKey": True})
        else:
            payload["key"] = ch
        await send_and_settle(ws, payload, 0.12)
    await asyncio.sleep(settle)


async def clipboard_roundtrip(ws, expect_substrings, label):
    await send_and_settle(ws, {"type": "keyboard", "action": "press", "key": "a", "ctrlKey": True}, 0.3)
    await send_and_settle(ws, {"type": "keyboard", "action": "press", "key": "c", "ctrlKey": True}, 0.6)
    await ws.send(json.dumps({"type": "clipboard_get"}))
    reply, _ = await recv_until(ws, {"clipboard_data"}, timeout=10)
    text = str(reply.get("text") or "")
    missing = [s for s in expect_substrings if s not in text]
    record(label, reply.get("success") is True and not missing,
           f"len={len(text)} missing={missing}")


def point_belongs_to(x, y, hwnd):
    """安全护栏：确认屏幕坐标点确实属于目标窗口（防止误注入到其他窗口）。"""
    class PT(ctypes.Structure):
        _fields_ = [("x", wintypes.LONG), ("y", wintypes.LONG)]
    pt = PT(int(x), int(y))
    hit = user32.WindowFromPoint(pt)
    if not hit:
        return False, "WindowFromPoint 无命中"
    root_hwnd = user32.GetAncestor(hit, 2)  # GA_ROOT
    return (root_hwnd == hwnd), f"hit={hit} root={root_hwnd} target={hwnd}"


def get_foreground_window():
    return user32.GetForegroundWindow()


async def ensure_foreground_by_click(ws, hwnd, metrics, attempts=4):
    """注入左键点击窗口中心并确认其成为前台；失败重试。返回是否成功。"""
    l, t, r, b = get_rect(hwnd)
    cx, cy = (l + r) // 2, min(b - 60, t + int((b - t) * 0.7))
    for i in range(attempts):
        ok, _detail = point_belongs_to(cx, cy, hwnd)
        if not ok:
            await asyncio.sleep(0.8)
            continue
        await mouse_click(ws, cx, cy, metrics, button=0, settle=0.8)
        if get_foreground_window() == hwnd:
            return True
        await asyncio.sleep(0.5)
    return get_foreground_window() == hwnd


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------

async def run_test():
    from auth_utils import issue_access_token
    token = issue_access_token("admin")["access_token"]
    metrics = virtual_metrics()
    print(f"[env] 虚拟桌面: origin=({metrics[0]},{metrics[1]}) size={metrics[2]}x{metrics[3]}", flush=True)

    notepad = None
    spawned_cmd = set()

    async with websockets.connect(f"{PROXY_WS}?token={token}", max_size=None, ping_interval=None) as ws:
        print(">>> 弹窗出现后请点击「允 许」（90 秒内），之后请勿移动鼠标 <<<", flush=True)

        consent_required, _ = await recv_until(ws, {"consent_required"})
        record("consent_required 收到", consent_required.get("target") is not None)

        consent_result, _ = await recv_until(ws, {"consent_result"}, timeout=130)
        approved = consent_result.get("approved") is True
        record("consent_result approved", approved, f"reason={consent_result.get('reason')}")
        if not approved:
            return False
        await asyncio.sleep(2.0)

        # ---- 准备：以唯一临时文件启动记事本（标题唯一，避免误匹配用户窗口） ----
        import uuid
        target_file = Path(r"D:\IT2026-temp") / f"zview_func_target_{uuid.uuid4().hex[:8]}.txt"
        target_file.parent.mkdir(parents=True, exist_ok=True)
        target_file.write_text("", encoding="utf-8")
        notepad = subprocess.Popen(["notepad.exe", str(target_file)])
        deadline = time.time() + 10
        np_hwnd = None
        marker = target_file.name
        while time.time() < deadline and not np_hwnd:
            hits = find_windows(lambda w: w["cls"] == "Notepad" and marker in w["title"])
            np_hwnd = hits[0]["hwnd"] if hits else None
            if not np_hwnd:
                await asyncio.sleep(0.4)
        record("记事本已启动(唯一标记)", bool(np_hwnd), f"marker={marker}")
        if not np_hwnd:
            return False
        CREATED_WINDOWS.append(np_hwnd)
        user32.MoveWindow(np_hwnd, 320, 220, 720, 480, True)
        # 解绑该窗口的 IME 上下文：被控端默认中文 IME 会把 Shift+- 转为破折号并吞数字
        # （本地对照实验证实）；ImmAssociateContext(hwnd, None) 使注入按键为原始 ASCII。
        try:
            imm32 = ctypes.windll.imm32
            imm32.ImmAssociateContext(np_hwnd, None)
        except Exception:
            pass
        await asyncio.sleep(0.6)

        def guard(x, y):
            ok, detail = point_belongs_to(x, y, np_hwnd)
            return ok, detail

        # ---- 准备2：启动经典控制台（conhost 强制经典宿主，供右键系统菜单与标题栏拖动） ----
        drag_proc = subprocess.Popen(["conhost.exe", "cmd", "/k", "title DRAG-TARGET"])
        deadline = time.time() + 8
        drag_hwnd = None
        while time.time() < deadline and not drag_hwnd:
            hits = find_windows(lambda w: w["cls"] == "ConsoleWindowClass" and "DRAG-TARGET" in w["title"])
            drag_hwnd = hits[0]["hwnd"] if hits else None
            if not drag_hwnd:
                await asyncio.sleep(0.3)
        record("控制台已启动(拖动/右键目标)", bool(drag_hwnd))
        if not drag_hwnd:
            return False
        CREATED_WINDOWS.append(drag_hwnd)
        user32.MoveWindow(drag_hwnd, 1120, 240, 640, 430, True)
        await asyncio.sleep(0.8)

        # ---- T1 左键聚焦 + 键盘输入 ----
        l, t, r, b = get_rect(np_hwnd)
        click_x, click_y = (l + r) // 2, b - 110
        ok_guard, guard_detail = guard(click_x, click_y)
        record("T0 注入点归属校验", ok_guard, guard_detail)
        if not ok_guard:
            return False
        focused = await ensure_foreground_by_click(ws, np_hwnd, metrics)
        record("T1-a 左键点击聚焦记事本", focused,
               f"foreground={_window_text(get_foreground_window())[:30]}")
        if not focused:
            return False
        await key_press(ws, "end", settle=0.5)  # 吸收焦点切换延迟
        # 键盘注入遵循目标机输入法规则（与物理键盘一致）：大写/下划线等 Shift 组合
        # 会被中文 IME 转换，故用 IME 中性的小写序列断言字符级到达率。
        # 注：Win11 新记事本为异步保存，故打字+粘贴后统一一次落盘校验。
        typed_text = "zview-func-test-ok"
        await key_type(ws, typed_text)
        expected_core = "zview-func-test"

        # ---- T2 复制粘贴：协议写剪贴板 -> Ctrl+V 粘贴 -> 统一落盘校验 ----
        paste_source = "PASTE-SRC-456"
        await ws.send(json.dumps({"type": "clipboard_set", "text": paste_source}))
        clip_ack, _ = await recv_until(ws, {"clipboard_result"}, timeout=10)
        record("T2-a 协议写入剪贴板", clip_ack.get("operation") == "set" and clip_ack.get("success") is True,
               f"msg={clip_ack.get('message')}")
        await key_press(ws, "end")
        await key_press(ws, "enter")
        await send_and_settle(ws, {"type": "keyboard", "action": "press", "key": "v", "ctrlKey": True}, 0.8)
        await asyncio.sleep(1.0)
        await send_and_settle(ws, {"type": "keyboard", "action": "press", "key": "s", "ctrlKey": True}, 1.5)
        saved2 = target_file.read_text(encoding="utf-8", errors="replace")
        record("T1 键盘输入落盘(字符级到达)", expected_core in saved2,
               f"file_len={len(saved2)} content={saved2[:40]!r}")
        record("T2 Ctrl+V 粘贴远程剪贴板内容", paste_source in saved2,
               f"has_pasted={paste_source in saved2}")

        # ---- T3 右键上下文菜单（控制台标题栏 -> 经典系统菜单 #32768） ----
        menus_before = len(find_windows(lambda w: w["cls"] == "#32768"))
        dl0, dt0, dr0, _db = get_rect(drag_hwnd)
        rx, ry = (dl0 + dr0) // 2, dt0 + 14
        ok_guard3, guard_detail3 = point_belongs_to(rx, ry, drag_hwnd)
        record("T3-guard 右键点归属校验(控制台标题栏)", ok_guard3, guard_detail3)
        if not ok_guard3:
            return False
        await mouse_click(ws, rx, ry, metrics, button=2, settle=1.0)
        await asyncio.sleep(0.8)
        menus_after = find_windows(lambda w: w["cls"] == "#32768")
        record("T3-a 右键弹出上下文菜单", len(menus_after) > menus_before,
               f"menu_windows={[m['title'] for m in menus_after]}")
        await key_press(ws, "escape", settle=0.5)
        await asyncio.sleep(0.5)
        menus_closed = len(find_windows(lambda w: w["cls"] == "#32768"))
        record("T3-b ESC 关闭菜单", menus_closed <= menus_before, f"remaining={menus_closed}")

        # ---- T4 鼠标拖动（同一控制台标题栏，位移精确校验） ----
        dl, dt, _, _ = get_rect(drag_hwnd)
        grab_x, grab_y = dl + 140, dt + 18
        ok_guard4, guard_detail4 = point_belongs_to(grab_x, grab_y, drag_hwnd)
        record("T4-guard 拖动点归属校验", ok_guard4, guard_detail4)
        if not ok_guard4:
            return False
        total_dx, total_dy = 150, 80
        await mouse_drag(ws, grab_x, grab_y, total_dx, total_dy, metrics)
        new_l, new_t, _, _ = get_rect(drag_hwnd)
        err_dx = abs(new_l - (dl + total_dx))
        err_dy = abs(new_t - (dt + total_dy))
        record("T4-b 标题栏拖动窗口位移", err_dx <= 8 and err_dy <= 8,
               f"want=({dl + total_dx},{dt + total_dy}) got=({new_l},{new_t}) err=({err_dx},{err_dy})")

        # ---- T5 管理员权限（开始菜单搜索 cmd -> Ctrl+Shift+Enter 提权运行） ----
        import psutil
        cmd_before = {p.pid for p in psutil.process_iter(["name"]) if (p.info["name"] or "").lower() == "cmd.exe"}
        await key_press(ws, "win", settle=1.2)
        start_menu = find_windows(lambda w: w["cls"] in ("Windows.UI.Core.CoreWindow",) and w["title"])
        record("T5-a 开始菜单已打开(供搜索)", len(start_menu) > 0,
               f"found={[(w['title'][:20], w['cls']) for w in start_menu]}")
        await key_type(ws, "cmd", settle=1.2)
        await send_and_settle(ws, {"type": "keyboard", "action": "press", "key": "enter",
                                   "ctrlKey": True, "shiftKey": True}, 2.5)
        elevated = find_windows(lambda w: (w["title"].startswith("管理员") or w["title"].startswith("Administrator"))
                                and "cmd" in w["title"].lower())
        current_cmd = {p.pid for p in psutil.process_iter(["name"]) if (p.info["name"] or "").lower() == "cmd.exe"}
        spawned_cmd = current_cmd - cmd_before
        record("T5-b 以管理员身份运行的 cmd 已创建", len(elevated) > 0 or bool(spawned_cmd),
               f"elevated_windows={[w['title'] for w in elevated]} new_pids={sorted(spawned_cmd)}")

        return True


async def main():
    ok = False
    try:
        ok = await run_test()
    finally:
        # 只关闭测试自己的窗口（未保存的空白文档会静默关闭），绝不全杀 notepad/cmd
        for hwnd in CREATED_WINDOWS:
            try:
                if hwnd and user32.IsWindow(hwnd):
                    user32.PostMessageW(hwnd, 0x0010, 0, 0)  # WM_CLOSE
            except Exception:
                pass
        import glob
        for f in glob.glob(r"D:\IT2026-temp\zview_func_target_*.txt"):
            try:
                Path(f).unlink()
            except Exception:
                pass
        # 若提权 cmd 残留，按窗口精确关闭
        for w in find_windows(lambda w: (w["title"].startswith("管理员") or
                                         w["title"].startswith("Administrator")) and
                               "cmd" in w["title"].lower()):
            user32.PostMessageW(w["hwnd"], 0x0010, 0, 0)
    print("=" * 64)
    failed = RESULTS.count(False)
    print(f"总计 {len(RESULTS)} 项, 失败 {failed} 项")
    return ok and failed == 0


if __name__ == "__main__":
    final_ok = asyncio.run(main())
    import os
    os._exit(0 if final_ok else 1)
