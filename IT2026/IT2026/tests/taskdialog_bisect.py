# -*- coding: utf-8 -*-
"""TaskDialogIndirect E_INVALIDARG 二分定位。"""

import ctypes
import sys
import threading
from ctypes import wintypes

sys.path.insert(0, ".")
import cmdb_agent_consent_ui as cui  # noqa: E402

TD_WARNINGIFUSED = None


def try_variant(name, mutate):
    app = cui.ConsentTrayApp()
    fn = app._task_dialog_indirect
    fn.argtypes = [
        ctypes.POINTER(cui.TASKDIALOGCONFIG),
        ctypes.POINTER(ctypes.c_int),
        ctypes.POINTER(ctypes.c_int),
        ctypes.POINTER(ctypes.c_int),
    ]
    fn.restype = ctypes.c_long

    pressed = ctypes.c_int(0)
    radio = ctypes.c_int(0)
    checked = ctypes.c_int(0)

    def close_on_create(hwnd, notification, wparam, lparam, _ref):
        if notification == cui.TDN_CREATED:
            ctypes.windll.user32.SendMessageW(hwnd, cui.TDM_CLICK_BUTTON, cui.IDNO, 0)
        return 0

    cb_ref = cui.PFTASKDIALOGCALLBACK(close_on_create)

    cfg = cui.TASKDIALOGCONFIG()
    cfg.cbSize = ctypes.sizeof(cui.TASKDIALOGCONFIG)
    cfg.hwndParent = None
    cfg.dwFlags = cui.TDF_ALLOW_DIALOG_CANCELLATION | cui.TDF_CALLBACK_TIMER
    cfg.pszWindowTitle = "probe"
    cfg.pszMainInstruction = "main"
    cfg.pszContent = "content"
    cfg.nDefaultButton = cui.IDNO
    cfg.cxWidth = 260
    mutate(cfg, cb_ref)

    def run():
        result["code"] = fn(
            ctypes.byref(cfg),
            ctypes.byref(pressed),
            ctypes.byref(radio),
            ctypes.byref(checked),
        )
        result["pressed"] = pressed.value

    result = {}
    t = threading.Thread(target=run)
    t.start()
    t.join(10)
    print(f"[{name}] code={result.get('code')} pressed={result.get('pressed')} "
          f"sizeof={ctypes.sizeof(cui.TASKDIALOGCONFIG)}", flush=True)
    return result


def m_base(cfg, cb):
    pass


def m_buttons(cfg, cb):
    buttons = (cui.TASKDIALOG_BUTTON * 2)(
        cui.TASKDIALOG_BUTTON(cui.IDYES, "允许"),
        cui.TASKDIALOG_BUTTON(cui.IDNO, "拒绝"),
    )
    cfg.cButtons = 2
    cfg.pButtons = buttons


def m_callback(cfg, cb):
    cfg.pfCallback = cb


def m_parent(cfg, cb):
    cfg.hwndParent = getattr(app_ref[0], "hwnd", None)


app_ref = [None]

try_variant("base(minimal)", m_base)
try_variant("base+buttons", m_buttons)
try_variant("base+buttons+callback", lambda c, b: (m_buttons(c, b), m_callback(c, b)))

# 带托盘窗口句柄的变体（模拟真实调用）
app = cui.ConsentTrayApp()
app_ref[0] = app
threading.Thread(target=lambda: setattr(app, "_tray_probe", True)).start()

print("done", flush=True)
import os
os._exit(0)
