# -*- coding: utf-8 -*-
"""取证：通过引擎真实路径触发同意请求，同时监控桌面窗口并截图。

用于判定「实测会话超时」期间弹窗是否真的显示在被控端桌面上。
运行约 25 秒；若看到弹窗请勿点击，让其保持以便取证。
"""

import asyncio
import ctypes
import json
import sys
import time
from ctypes import wintypes
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import websockets  # noqa: E402

ctypes.windll.shcore.SetProcessDpiAwareness(2)
user32 = ctypes.windll.user32

PROXY_WS = "ws://127.0.0.1:8080/api/v1/assets/28/remote-desktop/ws"

ENUM_CB = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)


def _text(hwnd):
    n = user32.GetWindowTextLengthW(hwnd)
    buf = ctypes.create_unicode_buffer(n + 1)
    user32.GetWindowTextW(hwnd, buf, n + 1)
    return buf.value


def list_dialog_windows():
    found = []
    pid_map = {}

    def cb(hwnd, _):
        if user32.IsWindowVisible(hwnd):
            t = _text(hwnd)
            if ("远程控制" in t) or ("确认" in t) or ("Z-View" in t):
                r = wintypes.RECT()
                user32.GetWindowRect(hwnd, ctypes.byref(r))
                pid = wintypes.DWORD(0)
                user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
                found.append((t.strip(), r.left, r.top, r.right, r.bottom, pid.value))
        return True

    user32.EnumWindows(ENUM_CB(cb), 0)
    return found


async def main():
    from auth_utils import issue_access_token

    token = issue_access_token("admin")["access_token"]
    uri = f"{PROXY_WS}?token={token}"
    async with websockets.connect(uri, max_size=None, ping_interval=None) as ws:
        print("[watch] 已发起远控请求，等待 consent_required ...", flush=True)
        deadline = time.time() + 15
        got = None
        while time.time() < deadline:
            try:
                msg = json.loads(await asyncio.wait_for(ws.recv(), timeout=1))
            except asyncio.TimeoutError:
                continue
            if msg.get("type") == "consent_required":
                got = msg
                break
        print(f"[watch] consent_required: {'收到' if got else '未收到'}", flush=True)

        shot_at = time.time() + 6
        end = time.time() + 22
        last_titles = None
        while time.time() < end:
            wins = list_dialog_windows()
            titles = [w[0] for w in wins]
            if titles != last_titles:
                procs = {}
                for w in wins:
                    try:
                        import psutil

                        procs[w[5]] = psutil.Process(w[5]).name()
                    except Exception:
                        procs[w[5]] = "?"
                print(
                    f"[watch] t=+{22 - (end - time.time()):04.1f}s 窗口(标题,x,y,x2,y2,pid/进程名): "
                    f"{[(w[0][:14], w[5], procs.get(w[5])) for w in wins]}",
                    flush=True,
                )
                last_titles = titles
            if time.time() >= shot_at:
                try:
                    from PIL import ImageGrab

                    img = ImageGrab.grab(all_screens=True)
                    out = Path(PROJECT_ROOT) / "tests" / "consent_forensic_shot.png"
                    img.save(out)
                    print(f"[watch] 截图已保存: {out}", flush=True)
                except Exception as exc:
                    print(f"[watch] 截图失败: {exc}", flush=True)
                shot_at = float("inf")
            await asyncio.sleep(0.4)

    print("[watch] 结束（不点击，等待其自动超时）", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
