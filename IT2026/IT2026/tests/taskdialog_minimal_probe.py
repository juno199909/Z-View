# -*- coding: utf-8 -*-
"""独立最小 TASKDIALOGCONFIG 调用测试（不依赖 cui 的定义）。"""

import ctypes
import threading
from ctypes import wintypes

user32 = ctypes.windll.user32

TDN_CREATED = 0
TDM_CLICK_BUTTON = 0x0466  # WM_USER+102
IDNO = 7


class TDBUTTON(ctypes.Structure):
    _fields_ = [("id", ctypes.c_int), ("text", wintypes.LPCWSTR)]


CALLBACK = ctypes.WINFUNCTYPE(
    ctypes.c_long, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM, ctypes.c_void_p
)


class CONFIG(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.UINT),
        ("hwndParent", wintypes.HWND),
        ("hInstance", wintypes.HINSTANCE),
        ("dwFlags", wintypes.UINT),
        ("dwCommonButtons", wintypes.UINT),
        ("pszWindowTitle", wintypes.LPCWSTR),
        ("hMainIcon", wintypes.HANDLE),
        ("pszMainInstruction", wintypes.LPCWSTR),
        ("pszContent", wintypes.LPCWSTR),
        ("cButtons", wintypes.UINT),
        ("pButtons", ctypes.POINTER(TDBUTTON)),
        ("nDefaultButton", ctypes.c_int),
        ("cRadioButtons", wintypes.UINT),
        ("pRadioButtons", ctypes.POINTER(TDBUTTON)),
        ("nDefaultRadioButton", ctypes.c_int),
        ("pszVerificationText", wintypes.LPCWSTR),
        ("pszExpandedInformation", wintypes.LPCWSTR),
        ("pszExpandedControlText", wintypes.LPCWSTR),
        ("pszCollapsedControlText", wintypes.LPCWSTR),
        ("hFooterIcon", wintypes.HANDLE),
        ("pszFooter", wintypes.LPCWSTR),
        ("pfCallback", CALLBACK),
        ("lpCallbackData", ctypes.c_void_p),
        ("cxWidth", wintypes.UINT),
    ]


td = ctypes.windll.comctl32.TaskDialogIndirect
td.argtypes = [
    ctypes.POINTER(CONFIG),
    ctypes.POINTER(ctypes.c_int),
    ctypes.POINTER(ctypes.c_int),
    ctypes.POINTER(ctypes.c_int),
]
td.restype = ctypes.c_long


def run_variant(name, use_buttons, use_cb):
    cfg = CONFIG()
    cfg.cbSize = ctypes.sizeof(CONFIG)
    cfg.dwFlags = 0x0800  # TDF_CALLBACK_TIMER
    cfg.pszWindowTitle = "probe"
    cfg.pszMainInstruction = "main"
    cfg.pszContent = "content"

    cb_ref = None
    if use_cb:
        def cb(hwnd, notification, wparam, lparam, ref):
            if notification == TDN_CREATED:
                user32.SendMessageW(hwnd, TDM_CLICK_BUTTON, IDNO, 0)
            return 0
        cb_ref = CALLBACK(cb)
        cfg.pfCallback = cb_ref

    pressed = ctypes.c_int(0)

    def go():
        result["code"] = td(
            ctypes.byref(cfg),
            ctypes.byref(pressed),
            ctypes.byref(ctypes.c_int(0)),
            ctypes.byref(ctypes.c_int(0)),
        )

    result = {}
    t = threading.Thread(target=go)
    t.start()
    t.join(10)
    print(f"[{name}] code={result.get('code')} hex={result.get('code', 0) & 0xFFFFFFFF:#x}", flush=True)


run_variant("plain", False, False)
run_variant("with-callback", False, True)
print("done", flush=True)
import os

os._exit(0)
