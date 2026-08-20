# -*- coding: utf-8 -*-
"""messagebox 后端超时回收回归测试。

旧实现超时后 abandon 等待线程，原生 MessageBox 窗口永久残留桌面并随失败
尝试不断堆积（取证发现同屏三个叠放）。修复后超时经 EndDialog 干净关闭。

运行: python tests\\test_messagebox_timeout_recycle.py
注意: 屏幕会弹出一个约 8 秒的确认框，请勿点击，让其自动超时。
"""

import ctypes
import sys
import threading
import time
from ctypes import wintypes
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import cmdb_agent_consent_ui as cui  # noqa: E402

user32 = ctypes.windll.user32
ENUM_CB = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

RESULTS = []


def record(name, ok, detail=""):
    RESULTS.append(bool(ok))
    print(f"[{'PASS' if ok else 'FAIL'}] {name} {detail}", flush=True)


def dialog_windows():
    found = []

    def cb(hwnd, _):
        if user32.IsWindowVisible(hwnd):
            n = user32.GetWindowTextLengthW(hwnd)
            buf = ctypes.create_unicode_buffer(n + 1)
            user32.GetWindowTextW(hwnd, buf, n + 1)
            if "远程控制确认" in buf.value:
                r = wintypes.RECT()
                user32.GetWindowRect(hwnd, ctypes.byref(r))
                found.append((buf.value.strip(), r.left, r.top))
        return True

    user32.EnumWindows(ENUM_CB(cb), 0)
    return found


def main():
    app = cui.ConsentTrayApp()

    before = dialog_windows()
    print(f"[env] 测试前残留弹窗: {len(before)} -> {before}", flush=True)

    box = {"result": None}

    def invoke():
        box["result"] = cui.ConsentTrayApp._invoke_messagebox(
            app,
            "Z-View 远程控制确认",
            "超时回收测试：请勿点击，等待自动超时。",
            cui.MB_YESNO | cui.MB_ICONQUESTION | cui.MB_TOPMOST,
            8,
        )

    thread = threading.Thread(target=invoke)
    started = time.time()
    thread.start()
    time.sleep(2.5)
    during = dialog_windows()
    record("显示期间对话框存在", len(during) >= len(before) + 1, f"窗口={during}")

    thread.join(timeout=15)
    elapsed = time.time() - started

    valid_result = box["result"] in (cui.IDYES, cui.IDNO, cui.IDTIMEOUT)
    record("调用按期完成", not thread.is_alive() and valid_result, f"result={box['result']} elapsed={elapsed:.1f}s")

    time.sleep(1.0)
    after = dialog_windows()
    record(
        "超时后无窗口残留",
        len(after) <= len(before),
        f"before={len(before)} during={len(during)} after={len(after)} -> {after}",
    )

    print("=" * 60)
    failed = RESULTS.count(False)
    print(f"总计 {len(RESULTS)} 项, 失败 {failed} 项")
    import os

    os._exit(0 if failed == 0 else 1)


if __name__ == "__main__":
    main()
