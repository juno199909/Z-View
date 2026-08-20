"""
Compatibility wrapper for the user-session consent UI.

The original source file was lost, but the compiled module is still available
under ``__pycache__``. This wrapper restores the existing tray/consent logic
from the cached bytecode and applies small runtime patches that are easier to
maintain in source control.
"""

from __future__ import annotations

import ctypes
import importlib.machinery
import importlib.util
import os
import sys
import threading
import time
from ctypes import wintypes
from multiprocessing.connection import Listener
from pathlib import Path
from types import SimpleNamespace

from agent_consent_ipc import (
    build_consent_authkey,
    build_consent_pipe_name,
    get_current_process_session_id,
    get_current_username,
    load_agent_config,
    load_tray_settings,
    resolve_runtime_log_path,
    save_tray_settings,
)


WM_USER = 0x0400
LRESULT = getattr(wintypes, "LRESULT", ctypes.c_ssize_t)
TDF_ALLOW_DIALOG_CANCELLATION = 0x0008
TDF_POSITION_RELATIVE_TO_WINDOW = 0x1000
TDF_CALLBACK_TIMER = 0x0800
TDN_CREATED = 0
TDN_BUTTON_CLICKED = 2
TDN_TIMER = 4
TDE_CONTENT = 0
IDYES = 6
IDNO = 7
IDTIMEOUT = 32000
MB_YESNO = 0x00000004
MB_ICONQUESTION = 0x00000020
MB_TOPMOST = 0x00040000
MB_SETFOREGROUND = 0x00010000
MB_SYSTEMMODAL = 0x00001000
TDM_CLICK_BUTTON = WM_USER + 102
TDM_SET_ELEMENT_TEXT = WM_USER + 108
HWND_TOPMOST = -1
SWP_NOMOVE = 0x0002
SWP_NOSIZE = 0x0001
SWP_NOACTIVATE = 0x0010

# 托盘右键菜单命令 ID（火绒风格：入口 + 状态开关 + 关于/退出）
IDM_MACHINE_INFO = 2001
IDM_TOGGLE_ALLOW_REQUESTS = 2002
IDM_TOGGLE_SKIP_CONSENT = 2003
IDM_TOGGLE_BALLOON = 2004
IDM_TOGGLE_UAC_INPUT = 2007
IDM_ABOUT = 2005
IDM_EXIT = 2006


class TASKDIALOG_BUTTON(ctypes.Structure):
    _fields_ = [
        ("nButtonID", ctypes.c_int),
        ("pszButtonText", wintypes.LPCWSTR),
    ]


PFTASKDIALOGCALLBACK = ctypes.WINFUNCTYPE(
    ctypes.c_int,
    wintypes.HWND,
    wintypes.UINT,
    wintypes.WPARAM,
    wintypes.LPARAM,
    ctypes.c_void_p,
)


class TASKDIALOGCONFIG(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.UINT),
        ("hwndParent", wintypes.HWND),
        ("hInstance", wintypes.HINSTANCE),
        ("dwFlags", wintypes.UINT),
        ("dwCommonButtons", wintypes.UINT),
        ("pszWindowTitle", wintypes.LPCWSTR),
        ("MainIcon", wintypes.LPCWSTR),
        ("pszMainInstruction", wintypes.LPCWSTR),
        ("pszContent", wintypes.LPCWSTR),
        ("cButtons", wintypes.UINT),
        ("pButtons", ctypes.POINTER(TASKDIALOG_BUTTON)),
        ("nDefaultButton", ctypes.c_int),
        ("cRadioButtons", wintypes.UINT),
        ("pRadioButtons", ctypes.c_void_p),
        ("nDefaultRadioButton", ctypes.c_int),
        ("pszVerificationText", wintypes.LPCWSTR),
        ("pszExpandedInformation", wintypes.LPCWSTR),
        ("pszExpandedControlText", wintypes.LPCWSTR),
        ("pszCollapsedControlText", wintypes.LPCWSTR),
        ("FooterIcon", wintypes.LPCWSTR),
        ("pszFooter", wintypes.LPCWSTR),
        ("pfCallback", PFTASKDIALOGCALLBACK),
        ("lpCallbackData", ctypes.c_void_p),
        ("cxWidth", wintypes.UINT),
    ]


def _candidate_legacy_paths() -> list[Path]:
    base_dir = Path(__file__).resolve().parent
    candidates = [
        base_dir / "cmdb_agent_consent_ui.legacy.pyc",
        base_dir / "__pycache__" / f"cmdb_agent_consent_ui.cpython-{os.sys.version_info.major}{os.sys.version_info.minor}.pyc",
        base_dir / "__pycache__" / "cmdb_agent_consent_ui.cpython-313.pyc",
    ]

    bundled_root = getattr(os.sys, "_MEIPASS", None)
    if bundled_root:
        bundled = Path(bundled_root)
        candidates.extend(
            [
                bundled / "cmdb_agent_consent_ui.legacy.pyc",
                bundled / "__pycache__" / f"cmdb_agent_consent_ui.cpython-{os.sys.version_info.major}{os.sys.version_info.minor}.pyc",
                bundled / "__pycache__" / "cmdb_agent_consent_ui.cpython-313.pyc",
            ]
        )

    return candidates


def _candidate_icon_paths() -> list[Path]:
    base_dir = Path(__file__).resolve().parent
    candidates = [
        base_dir / "favicon.ico",
        base_dir / "frontend" / "public" / "favicon.ico",
    ]
    bundled_root = getattr(os.sys, "_MEIPASS", None)
    if bundled_root:
        bundled = Path(bundled_root)
        candidates.extend(
            [
                bundled / "favicon.ico",
                bundled / "frontend" / "public" / "favicon.ico",
            ]
        )
    return candidates


def _load_legacy_module():
    for candidate in _candidate_legacy_paths():
        if not candidate.exists():
            continue
        loader = importlib.machinery.SourcelessFileLoader(
            "_cmdb_agent_consent_ui_legacy",
            str(candidate),
        )
        spec = importlib.util.spec_from_loader(loader.name, loader)
        if spec is None:
            continue
        module = importlib.util.module_from_spec(spec)
        loader.exec_module(module)
        return module
    return _build_fallback_module()


def _build_fallback_module():
    class ConsentTrayApp:
        def __init__(self):
            self.hwnd = None
            self.hinst = None
            self.username = get_current_username()
            self.dialog_lock = threading.Lock()
            self.last_dialog_mode = "fallback"
            self.last_dialog_error = ""
            self.last_dialog_attempts = []
            self._stop_event = threading.Event()
            self._listener_thread = None
            self._tray_wnd_proc = None

        def _invoke_messagebox(self, title: str, message: str, _style: int, timeout_seconds: int) -> int:
            """带超时回收的原生 MessageBox 弹窗。

            旧实现超时后仅 abandon 等待线程，原生对话框窗口会永久残留在桌面
            上（多次超时后层层堆叠，操作者无法分辨应点击哪个）。现通过 CBT
            钩子捕获对话框句柄，超时后对其调用 EndDialog 以 IDTIMEOUT 结束，
            窗口随之销毁、线程干净退出。
            """
            user32 = ctypes.windll.user32
            kernel32 = ctypes.windll.kernel32
            box_style = MB_YESNO | MB_ICONQUESTION | MB_TOPMOST | MB_SETFOREGROUND | MB_SYSTEMMODAL

            WH_CBT = 5
            HCBT_ACTIVATE = 5

            state = {"hwnd": None}
            hook_proc_type = ctypes.WINFUNCTYPE(
                ctypes.c_ssize_t, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM
            )

            def _cbt_proc(n_code, w_param, l_param):
                if n_code == HCBT_ACTIVATE and state["hwnd"] is None:
                    state["hwnd"] = w_param
                return user32.CallNextHookEx(None, n_code, w_param, l_param)

            cbt_ref = hook_proc_type(_cbt_proc)

            class _MessageBoxThread(threading.Thread):
                def __init__(self):
                    super().__init__(daemon=True)
                    self.result = IDNO

                def run(self):
                    hook = None
                    try:
                        user32.MessageBoxW.argtypes = [
                            wintypes.HWND,
                            wintypes.LPCWSTR,
                            wintypes.LPCWSTR,
                            wintypes.UINT,
                        ]
                        user32.MessageBoxW.restype = ctypes.c_int
                        user32.SetWindowsHookExW.restype = wintypes.HHOOK
                        thread_id = kernel32.GetCurrentThreadId()
                        hook = user32.SetWindowsHookExW(WH_CBT, cbt_ref, None, thread_id)
                        self.result = int(
                            user32.MessageBoxW(None, message, title, box_style)
                        )
                    except Exception:
                        self.result = IDNO
                    finally:
                        if hook:
                            try:
                                user32.UnhookWindowsHookEx(hook)
                            except Exception:
                                pass

            worker = _MessageBoxThread()
            worker.start()

            deadline = time.time() + max(5, int(timeout_seconds))
            while worker.is_alive() and time.time() < deadline:
                time.sleep(0.05)

            if worker.is_alive():
                hwnd = state.get("hwnd")
                if hwnd:
                    # 超时：以 IDTIMEOUT 结束对话框，避免窗口残留堆积。
                    try:
                        user32.EndDialog.argtypes = [wintypes.HWND, ctypes.c_ssize_t]
                        user32.EndDialog(wintypes.HWND(hwnd), IDTIMEOUT)
                    except Exception:
                        pass
                    worker.join(timeout=3)
                if worker.is_alive():
                    return IDTIMEOUT
            return worker.result

        def _serve_consent_pipe(self, session_id: int):
            pipe_name = build_consent_pipe_name(session_id)
            authkey = build_consent_authkey()
            _append_consent_runtime_log(f"fallback consent helper listening: pipe={pipe_name} session={session_id}")
            while True:
                try:
                    with Listener(pipe_name, family="AF_PIPE", authkey=authkey) as listener:
                        while True:
                            connection = listener.accept()
                            try:
                                payload = connection.recv()
                                if not isinstance(payload, dict) or payload.get("type") != "consent_request":
                                    connection.send({"approved": False, "reason": "unsupported_request"})
                                    continue

                                settings = load_tray_settings()
                                if not settings.get("allow_remote_requests", True):
                                    connection.send({"approved": False, "reason": "disabled_by_user"})
                                    continue
                                if settings.get("skip_consent_for_session", False):
                                    connection.send({"approved": True, "reason": "session_skip_enabled"})
                                    continue

                                approved, reason = self._show_consent_dialog(payload)
                                connection.send(
                                    {
                                        "approved": bool(approved),
                                        "reason": str(reason or ("approved" if approved else "rejected")),
                                        "dialog_mode": self.last_dialog_mode,
                                    }
                                )
                            except Exception as exc:
                                # 对端可能在等待回包前断开（如引擎侧超时放弃）；
                                # 回包失败时保持监听循环存活，而不是抛到外层
                                # 触发整条管道销毁重建。
                                try:
                                    connection.send({"approved": False, "reason": f"helper_error:{type(exc).__name__}"})
                                except Exception:
                                    pass
                            finally:
                                try:
                                    connection.close()
                                except Exception:
                                    pass
                except Exception as exc:
                    _append_consent_runtime_log(
                        f"fallback consent helper listener error: type={type(exc).__name__} error={exc}"
                    )
                    time.sleep(2)

        def _run_native_tray_icon(self):
            if os.name != "nt":
                return False

            user32 = ctypes.windll.user32
            shell32 = ctypes.windll.shell32
            kernel32 = ctypes.windll.kernel32
            hinstance = kernel32.GetModuleHandleW(None)
            class_name = f"ZViewConsentTray_{os.getpid()}"

            wnd_proc_type = ctypes.WINFUNCTYPE(
                LRESULT,
                wintypes.HWND,
                wintypes.UINT,
                wintypes.WPARAM,
                wintypes.LPARAM,
            )

            class WNDCLASSW(ctypes.Structure):
                _fields_ = [
                    ("style", wintypes.UINT),
                    ("lpfnWndProc", wnd_proc_type),
                    ("cbClsExtra", ctypes.c_int),
                    ("cbWndExtra", ctypes.c_int),
                    ("hInstance", wintypes.HINSTANCE),
                    ("hIcon", wintypes.HANDLE),
                    ("hCursor", wintypes.HANDLE),
                    ("hbrBackground", wintypes.HANDLE),
                    ("lpszMenuName", wintypes.LPCWSTR),
                    ("lpszClassName", wintypes.LPCWSTR),
                ]

            class NOTIFYICONDATAW(ctypes.Structure):
                _fields_ = [
                    ("cbSize", wintypes.DWORD),
                    ("hWnd", wintypes.HWND),
                    ("uID", wintypes.UINT),
                    ("uFlags", wintypes.UINT),
                    ("uCallbackMessage", wintypes.UINT),
                    ("hIcon", wintypes.HANDLE),
                    ("szTip", wintypes.WCHAR * 128),
                    ("dwState", wintypes.DWORD),
                    ("dwStateMask", wintypes.DWORD),
                    ("szInfo", wintypes.WCHAR * 256),
                    ("uTimeoutOrVersion", wintypes.UINT),
                    ("szInfoTitle", wintypes.WCHAR * 64),
                    ("dwInfoFlags", wintypes.DWORD),
                    ("guidItem", ctypes.c_byte * 16),
                    ("hBalloonIcon", wintypes.HANDLE),
                ]

            class MSG(ctypes.Structure):
                _fields_ = [
                    ("hwnd", wintypes.HWND),
                    ("message", wintypes.UINT),
                    ("wParam", wintypes.WPARAM),
                    ("lParam", wintypes.LPARAM),
                    ("time", wintypes.DWORD),
                    ("pt_x", ctypes.c_long),
                    ("pt_y", ctypes.c_long),
                ]

            class POINT(ctypes.Structure):
                _fields_ = [("x", wintypes.LONG), ("y", wintypes.LONG)]

            NIM_ADD = 0x00000000
            NIM_MODIFY = 0x00000001
            NIM_DELETE = 0x00000002
            NIF_MESSAGE = 0x00000001
            NIF_ICON = 0x00000002
            NIF_TIP = 0x00000004
            NIF_INFO = 0x00000010
            NIIF_INFO = 0x00000001
            WM_DESTROY = 0x0002
            WM_CLOSE = 0x0010
            WM_NULL = 0x0000
            WM_CONTEXTMENU = 0x007B
            WM_LBUTTONUP = 0x0202
            WM_RBUTTONUP = 0x0205
            WM_APP = 0x8000
            IMAGE_ICON = 1
            LR_LOADFROMFILE = 0x00000010
            MF_STRING = 0x00000000
            MF_SEPARATOR = 0x00000800
            MF_CHECKED = 0x00000008
            TPM_RIGHTBUTTON = 0x0002
            TPM_NONOTIFY = 0x0080
            TPM_RETURNCMD = 0x0100
            TPM_BOTTOMALIGN = 0x0020
            TPM_RIGHTALIGN = 0x0008

            # 托盘右键菜单命令 ID 使用模块级常量 IDM_*（见文件头部）。

            kernel32.GetModuleHandleW.restype = wintypes.HANDLE
            user32.RegisterClassW.argtypes = [ctypes.POINTER(WNDCLASSW)]
            user32.RegisterClassW.restype = wintypes.ATOM
            user32.CreateWindowExW.argtypes = [
                wintypes.DWORD,
                wintypes.LPCWSTR,
                wintypes.LPCWSTR,
                wintypes.DWORD,
                ctypes.c_int,
                ctypes.c_int,
                ctypes.c_int,
                ctypes.c_int,
                wintypes.HWND,
                wintypes.HANDLE,
                wintypes.HANDLE,
                ctypes.c_void_p,
            ]
            user32.CreateWindowExW.restype = wintypes.HWND
            user32.DefWindowProcW.restype = LRESULT
            user32.GetMessageW.argtypes = [ctypes.POINTER(MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT]
            user32.GetMessageW.restype = ctypes.c_int
            shell32.Shell_NotifyIconW.argtypes = [wintypes.DWORD, ctypes.POINTER(NOTIFYICONDATAW)]
            shell32.Shell_NotifyIconW.restype = wintypes.BOOL
            user32.LoadImageW.argtypes = [
                wintypes.HINSTANCE,
                wintypes.LPCWSTR,
                wintypes.UINT,
                ctypes.c_int,
                ctypes.c_int,
                wintypes.UINT,
            ]
            user32.LoadImageW.restype = wintypes.HANDLE
            user32.LoadIconW.argtypes = [wintypes.HINSTANCE, ctypes.c_void_p]
            user32.LoadIconW.restype = wintypes.HANDLE
            user32.DestroyIcon.argtypes = [wintypes.HANDLE]
            user32.CreatePopupMenu.restype = wintypes.HANDLE
            user32.AppendMenuW.argtypes = [
                wintypes.HANDLE,
                wintypes.UINT,
                ctypes.c_size_t,
                wintypes.LPCWSTR,
            ]
            user32.TrackPopupMenu.argtypes = [
                wintypes.HANDLE,
                wintypes.UINT,
                ctypes.c_int,
                ctypes.c_int,
                ctypes.c_int,
                wintypes.HWND,
                ctypes.c_void_p,
            ]
            user32.TrackPopupMenu.restype = wintypes.BOOL
            user32.SetForegroundWindow.argtypes = [wintypes.HWND]
            user32.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
            user32.DestroyWindow.argtypes = [wintypes.HWND]
            user32.GetCursorPos.argtypes = [ctypes.POINTER(POINT)]
            shell32.ShellExecuteW.argtypes = [
                wintypes.HWND,
                wintypes.LPCWSTR,
                wintypes.LPCWSTR,
                wintypes.LPCWSTR,
                wintypes.LPCWSTR,
                ctypes.c_int,
            ]

            def load_brand_icon():
                """优先加载打包的 Z-View 图标；失败时退回 EXE 内嵌图标/系统默认。"""
                for icon_path in _candidate_icon_paths():
                    if not icon_path.exists() or icon_path.stat().st_size <= 0:
                        continue
                    handle = user32.LoadImageW(
                        None,
                        str(icon_path),
                        IMAGE_ICON,
                        0,
                        0,
                        LR_LOADFROMFILE,
                    )
                    if handle:
                        return handle
                embedded = user32.LoadIconW(hinstance, ctypes.c_void_p(0))
                if embedded:
                    return embedded
                return user32.LoadIconW(None, ctypes.c_void_p(32512))  # IDI_APPLICATION

            icon_data = NOTIFYICONDATAW()

            def show_machine_info():
                return _tray_show_machine_info(self)

            def apply_toggle(menu_id):
                return _tray_apply_toggle(self, menu_id)

            def build_context_menu(hwnd):
                menu = user32.CreatePopupMenu()
                if not menu:
                    return None
                settings = load_tray_settings()
                allow_checked = MF_CHECKED if settings.get("allow_remote_requests", True) else MF_STRING
                skip_checked = MF_CHECKED if settings.get("skip_consent_for_session") else MF_STRING
                balloon_checked = MF_CHECKED if settings.get("show_balloon_notifications", True) else MF_STRING
                uac_checked = MF_CHECKED if settings.get("allow_secure_desktop_input", True) else MF_STRING
                user32.AppendMenuW(menu, MF_STRING, IDM_MACHINE_INFO, "查看本机信息(&I)")
                user32.AppendMenuW(menu, MF_SEPARATOR, 0, None)
                user32.AppendMenuW(menu, MF_STRING | allow_checked, IDM_TOGGLE_ALLOW_REQUESTS, "允许远程控制请求")
                user32.AppendMenuW(menu, MF_STRING | skip_checked, IDM_TOGGLE_SKIP_CONSENT, "本机免确认（自动允许）")
                user32.AppendMenuW(
                    menu, MF_STRING | balloon_checked, IDM_TOGGLE_BALLOON, "请求到达时弹出气泡提醒"
                )
                user32.AppendMenuW(
                    menu, MF_STRING | uac_checked, IDM_TOGGLE_UAC_INPUT, "允许远程操作 UAC 提示"
                )
                user32.AppendMenuW(menu, MF_SEPARATOR, 0, None)
                user32.AppendMenuW(menu, MF_STRING, IDM_ABOUT, "关于 Z-View…")
                user32.AppendMenuW(menu, MF_STRING, IDM_EXIT, "退出代理(&X)")
                return menu

            def show_context_menu(hwnd):
                menu = build_context_menu(hwnd)
                if not menu:
                    return
                point = POINT()
                user32.GetCursorPos(ctypes.byref(point))
                # 前台激活是托盘菜单能正常消失的标准前置步骤。
                user32.SetForegroundWindow(hwnd)
                chosen = user32.TrackPopupMenu(
                    menu,
                    # 右下角托盘图标：菜单右下角对齐光标，向左上展开，避免被屏幕右/下缘裁剪
                    TPM_RIGHTBUTTON | TPM_NONOTIFY | TPM_RETURNCMD | TPM_BOTTOMALIGN | TPM_RIGHTALIGN,
                    point.x,
                    point.y,
                    0,
                    hwnd,
                    None,
                )
                user32.PostMessageW(hwnd, WM_NULL, 0, 0)
                try:
                    user32.DestroyMenu(menu)
                except Exception:
                    pass

                if chosen == IDM_MACHINE_INFO:
                    show_machine_info()
                elif chosen in (IDM_TOGGLE_ALLOW_REQUESTS, IDM_TOGGLE_SKIP_CONSENT,
                                IDM_TOGGLE_BALLOON, IDM_TOGGLE_UAC_INPUT):
                    apply_toggle(chosen)
                elif chosen == IDM_ABOUT:
                    user32.MessageBoxW(
                        None,
                        "Z-View 终端管理代理\n\n远程控制同意助手与托盘常驻程序。\n本图标提供远程控制开关与本机免确认设置。",
                        "关于 Z-View",
                        MB_TOPMOST | MB_SETFOREGROUND | 0x40,  # MB_ICONINFORMATION
                    )
                elif chosen == IDM_EXIT:
                    quit_confirm = user32.MessageBoxW(
                        None,
                        "退出后管理台将无法对本机发起远程控制，直到代理重新启动。\n\n确定退出 Z-View 代理？",
                        "退出确认",
                        MB_YESNO | MB_ICONQUESTION | MB_TOPMOST | MB_SETFOREGROUND | MB_SYSTEMMODAL,
                    )
                    if quit_confirm == IDYES:
                        _append_consent_runtime_log("tray exit requested by operator")
                        user32.DestroyWindow(hwnd)

            def window_proc(hwnd, message, wparam, lparam):
                if message == WM_APP + 1:
                    if lparam in (WM_RBUTTONUP, WM_CONTEXTMENU):
                        show_context_menu(hwnd)
                        return 0
                    if lparam == WM_LBUTTONUP:
                        show_machine_info()
                        return 0
                    return 0
                if message == WM_CLOSE:
                    user32.DestroyWindow(hwnd)
                    return 0
                if message == WM_DESTROY:
                    shell32.Shell_NotifyIconW(NIM_DELETE, ctypes.byref(icon_data))
                    user32.PostQuitMessage(0)
                    return 0
                return user32.DefWindowProcW(hwnd, message, wparam, lparam)

            self._tray_wnd_proc = wnd_proc_type(window_proc)
            window_class = WNDCLASSW()
            window_class.lpfnWndProc = self._tray_wnd_proc
            window_class.hInstance = hinstance
            window_class.lpszClassName = class_name
            brand_icon = load_brand_icon()
            window_class.hIcon = brand_icon
            if not user32.RegisterClassW(ctypes.byref(window_class)):
                raise ctypes.WinError(ctypes.get_last_error())

            hwnd = user32.CreateWindowExW(
                0,
                class_name,
                "Z-View",
                0,
                0,
                0,
                0,
                0,
                None,
                None,
                hinstance,
                None,
            )
            if not hwnd:
                raise ctypes.WinError(ctypes.get_last_error())

            icon_data.cbSize = ctypes.sizeof(NOTIFYICONDATAW)
            icon_data.hWnd = hwnd
            icon_data.uID = 1
            icon_data.uFlags = NIF_MESSAGE | NIF_ICON | NIF_TIP
            icon_data.uCallbackMessage = WM_APP + 1
            icon_data.hIcon = brand_icon
            icon_data.szTip = "Z-View 终端管理代理\n远程控制请求：弹窗询问"
            if not shell32.Shell_NotifyIconW(NIM_ADD, ctypes.byref(icon_data)):
                raise ctypes.WinError(ctypes.get_last_error())

            self.hwnd = hwnd
            self.hinst = hinstance
            self._tray_hwnd = hwnd
            self._tray_notify_icon = icon_data
            _append_consent_runtime_log("native tray icon added")
            message = MSG()
            while user32.GetMessageW(ctypes.byref(message), None, 0, 0) > 0:
                user32.TranslateMessage(ctypes.byref(message))
                user32.DispatchMessageW(ctypes.byref(message))
            return True

        def serve_forever(self):
            session_id = get_current_process_session_id()
            if session_id is None:
                _append_consent_runtime_log("fallback consent helper started without a session; waiting idle")
                while True:
                    time.sleep(60)

            self._listener_thread = threading.Thread(
                target=self._serve_consent_pipe,
                args=(session_id,),
                name="zview-consent-pipe",
                daemon=True,
            )
            self._listener_thread.start()
            try:
                self._run_native_tray_icon()
            except Exception as exc:
                _append_consent_runtime_log(
                    f"native tray icon unavailable: type={type(exc).__name__} error={exc}"
                )
                while True:
                    time.sleep(60)

    def main():
        try:
            ConsentTrayApp().serve_forever()
        finally:
            # 跳过解释器收尾：常驻 Tk 根窗口若在任意线程被 GC 清理会触发
            # "Tcl_AsyncDelete ... wrong thread" 并 abort 进程。助手无需要刷
            # 写的持久缓冲（运行日志逐条 open/close 落盘），硬退出是安全的。
            os._exit(0)

    return SimpleNamespace(
        ConsentTrayApp=ConsentTrayApp,
        main=main,
        IDYES=IDYES,
        IDNO=IDNO,
        IDTIMEOUT=IDTIMEOUT,
        MB_YESNO=MB_YESNO,
        MB_ICONQUESTION=MB_ICONQUESTION,
        MB_TOPMOST=MB_TOPMOST,
        MB_SETFOREGROUND=MB_SETFOREGROUND,
        MB_SYSTEMMODAL=MB_SYSTEMMODAL,
    )


_legacy = _load_legacy_module()
globals().update(
    {
        name: getattr(_legacy, name)
        for name in dir(_legacy)
        if not name.startswith("__")
    }
)


def _patched_init(self):
    _legacy_init(self)
    try:
        self._task_dialog_indirect = getattr(ctypes.windll.comctl32, "TaskDialogIndirect", None)
    except Exception:
        self._task_dialog_indirect = None
    self.last_dialog_mode = "unknown"
    self.last_dialog_error = ""
    self.last_dialog_attempts = []


def _append_consent_runtime_log(message: str):
    try:
        log_path = resolve_runtime_log_path()
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with open(log_path, "a", encoding="utf-8") as file:
            file.write(f"[ConsentUI] {message}\n")
    except Exception:
        pass


def _get_windows_version() -> tuple[int, int]:
    try:
        version = os.sys.getwindowsversion()
        return int(version.major), int(version.minor)
    except Exception:
        return 0, 0


def _safe_dialog_username(self) -> str:
    username = getattr(self, "username", None)
    return str(username or os.environ.get("USERNAME") or "未知用户")


def _record_dialog_attempt(self, backend: str, status: str, detail: str = ""):
    history = list(getattr(self, "last_dialog_attempts", []) or [])
    entry = backend if not detail else f"{backend}:{status}:{detail}"
    history.append(entry)
    self.last_dialog_attempts = history[-6:]


def _resolve_dialog_backend_preference() -> str:
    raw_value = str(os.environ.get("ZVIEW_CONSENT_UI_BACKEND") or "").strip().lower()
    aliases = {
        "": "auto",
        "auto": "auto",
        "default": "auto",
        "tk": "tkinter",
        "tkinter": "tkinter",
        "native": "taskdialog",
        "taskdialog": "taskdialog",
        "messagebox": "messagebox",
    }
    return aliases.get(raw_value, "auto")


def _determine_dialog_backends(self) -> list[str]:
    preferred_backend = _resolve_dialog_backend_preference()
    backends: list[str] = []

    # TaskDialogIndirect 在当前运行环境（含源码与打包）一律返回 E_INVALIDARG
    # （独立最小调用亦复现，与结构体定义无关），故 auto 链不再包含它；
    # 仅保留 ZVIEW_CONSENT_UI_BACKEND=taskdialog 显式覆盖用于将来排查。
    if preferred_backend == "taskdialog":
        backends.append("taskdialog")
    elif preferred_backend == "messagebox":
        backends.append("messagebox")
        backends.append("tkinter")
    elif preferred_backend == "tkinter":
        backends.append("tkinter")
        backends.append("messagebox")
    else:
        # auto：源码与打包统一优先品牌化 tkinter 弹窗（常驻 UI 线程承载，
        # 已验证可见可点击），原生 MessageBox 作为兜底（超时回收可靠）。
        backends.append("tkinter")
        backends.append("messagebox")

    ordered_backends: list[str] = []
    for backend in backends:
        if backend not in ordered_backends:
            ordered_backends.append(backend)
    return ordered_backends


def _safe_focus_window(root):
    try:
        root.deiconify()
    except Exception:
        pass

    for action_name in ("lift", "focus_force"):
        try:
            getattr(root, action_name)()
        except Exception:
            continue


def _build_consent_dialog_content(self, requester: str, origin: str, target: str, remaining_seconds: int) -> str:
    remaining_seconds = max(0, int(remaining_seconds))
    return (
        f"{requester} 正在请求远程控制这台终端。\n\n"
        f"目标终端: {target}\n"
        f"来源地址: {origin}\n"
        f"当前登录用户: {_safe_dialog_username(self)}\n\n"
        "是否允许本次远程控制？\n"
        f"{remaining_seconds} 秒内未处理将自动拒绝。"
    )


def _invoke_consent_prompt(self, title: str, requester: str, origin: str, target: str, timeout_seconds: int, style: int) -> int:
    self.last_dialog_mode = "unknown"
    self.last_dialog_error = ""
    self.last_dialog_attempts = []
    backend_errors: list[str] = []
    backend_order = _determine_dialog_backends(self)
    _append_consent_runtime_log(
        f"dialog backend order: preferred={_resolve_dialog_backend_preference()} order={backend_order}"
    )

    for backend in backend_order:
        try:
            if backend == "taskdialog":
                response = _invoke_task_dialog(self, title, requester, origin, target, timeout_seconds)
            elif backend == "tkinter":
                response = _invoke_tk_consent_dialog(self, title, requester, origin, target, timeout_seconds)
            else:
                message = _build_consent_dialog_content(self, requester, origin, target, timeout_seconds)
                response = self._invoke_messagebox(title, message, style, timeout_seconds)

            self.last_dialog_mode = backend
            _record_dialog_attempt(self, backend, "ok")
            _append_consent_runtime_log(
                f"dialog backend selected: backend={backend} timeout={timeout_seconds}"
            )
            return response
        except Exception as exc:
            error_text = f"{backend}_failed:{type(exc).__name__}:{exc}"
            backend_errors.append(error_text)
            self.last_dialog_error = error_text
            _record_dialog_attempt(self, backend, "failed", error_text)
            _append_consent_runtime_log(error_text)

    combined_error = " | ".join(backend_errors) if backend_errors else "dialog_backend_unavailable"
    self.last_dialog_error = combined_error
    self.last_dialog_mode = "failed"
    _append_consent_runtime_log(f"dialog fallback exhausted, rejecting request: {combined_error}")
    return IDNO


def _invoke_task_dialog(self, title: str, requester: str, origin: str, target: str, timeout_seconds: int) -> int:
    task_dialog_indirect = getattr(self, "_task_dialog_indirect", None)
    if task_dialog_indirect is None:
        raise RuntimeError("TaskDialogIndirect unavailable")

    user32 = ctypes.windll.user32
    task_dialog_indirect.argtypes = [
        ctypes.POINTER(TASKDIALOGCONFIG),
        ctypes.POINTER(ctypes.c_int),
        ctypes.POINTER(ctypes.c_int),
        ctypes.POINTER(ctypes.c_int),
    ]
    task_dialog_indirect.restype = ctypes.c_long

    user32.SendMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
    user32.SendMessageW.restype = LRESULT
    user32.SetForegroundWindow.argtypes = [wintypes.HWND]
    user32.SetForegroundWindow.restype = wintypes.BOOL
    user32.SetWindowPos.argtypes = [
        wintypes.HWND,
        wintypes.HWND,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_int,
        wintypes.UINT,
    ]
    user32.SetWindowPos.restype = wintypes.BOOL
    user32.EndDialog.argtypes = [wintypes.HWND, ctypes.c_int]
    user32.EndDialog.restype = wintypes.BOOL

    state = {
        "timed_out": False,
        "remaining": max(0, int(timeout_seconds)),
        "content_buffer": ctypes.create_unicode_buffer(
            _build_consent_dialog_content(self, requester, origin, target, timeout_seconds)
        ),
    }

    buttons = (TASKDIALOG_BUTTON * 2)(
        TASKDIALOG_BUTTON(IDYES, "允许"),
        TASKDIALOG_BUTTON(IDNO, "拒绝"),
    )

    def callback(hwnd, notification, wparam, lparam, _ref_data):
        if notification == TDN_CREATED:
            try:
                user32.SetWindowPos(
                    hwnd,
                    HWND_TOPMOST,
                    0,
                    0,
                    0,
                    0,
                    SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE,
                )
                user32.SetForegroundWindow(hwnd)
            except Exception:
                pass
            return 0

        if notification == TDN_TIMER:
            elapsed_seconds = max(0, int(wparam) // 1000)
            remaining = max(0, timeout_seconds - elapsed_seconds)
            if remaining != state["remaining"]:
                state["remaining"] = remaining
                state["content_buffer"] = ctypes.create_unicode_buffer(
                    _build_consent_dialog_content(self, requester, origin, target, remaining)
                )
                user32.SendMessageW(
                    hwnd,
                    TDM_SET_ELEMENT_TEXT,
                    TDE_CONTENT,
                    ctypes.cast(state["content_buffer"], ctypes.c_void_p).value,
                )
            if remaining <= 0 and not state["timed_out"]:
                state["timed_out"] = True
                if not user32.EndDialog(hwnd, IDTIMEOUT):
                    user32.SendMessageW(hwnd, TDM_CLICK_BUTTON, IDNO, 0)
            return 0

        if notification == TDN_BUTTON_CLICKED:
            return 0
        return 0

    callback_ref = PFTASKDIALOGCALLBACK(callback)
    config = TASKDIALOGCONFIG()
    config.cbSize = ctypes.sizeof(TASKDIALOGCONFIG)
    config.hwndParent = getattr(self, "hwnd", None)
    config.hInstance = getattr(self, "hinst", None)
    config.dwFlags = TDF_ALLOW_DIALOG_CANCELLATION | TDF_POSITION_RELATIVE_TO_WINDOW | TDF_CALLBACK_TIMER
    config.dwCommonButtons = 0
    config.pszWindowTitle = title
    config.pszMainInstruction = "Z-View 远程控制确认"
    config.pszContent = ctypes.cast(state["content_buffer"], wintypes.LPCWSTR)
    config.cButtons = len(buttons)
    config.pButtons = buttons
    config.nDefaultButton = IDNO
    config.pfCallback = callback_ref
    config.cxWidth = 260

    pressed_button = ctypes.c_int(IDNO)
    radio_button = ctypes.c_int(0)
    verification_flag = ctypes.c_int(0)
    result = task_dialog_indirect(
        ctypes.byref(config),
        ctypes.byref(pressed_button),
        ctypes.byref(radio_button),
        ctypes.byref(verification_flag),
    )
    if result != 0:
        raise ctypes.WinError(result)
    if state["timed_out"]:
        return IDTIMEOUT
    return int(pressed_button.value)


def _ensure_tk_ui_thread(self) -> bool:
    """确保存在唯一的常驻 Tk UI 线程，返回其是否就绪。

    历史上每次弹窗都在管道线程新建 Tk() 并在结束后销毁；Tcl 解释器被与创建
    线程不同的线程清理时会以 "Tcl_AsyncDelete: async handler deleted by the
    wrong thread" 直接 abort 进程——这正是同意助手静默退出的根源（进程无任
    何日志消失）。现在由单一 UI 线程持有常驻隐藏根窗口，所有对话框以
    Toplevel 形式在该线程上展示；根窗口在进程存活期间永不销毁。
    """
    if getattr(self, "_tk_ui_ready", False):
        return True
    existing = getattr(self, "_tk_ui_thread", None)
    if existing is not None and existing.is_alive():
        return bool(getattr(self, "_tk_ui_ready", False))

    import queue

    job_queue: "queue.Queue[dict]" = queue.Queue()

    def _ui_main():
        import tkinter as tk

        try:
            root = tk.Tk()
            root.withdraw()
        except Exception as exc:
            self._tk_ui_root = None
            _append_consent_runtime_log(f"persistent consent ui root unavailable: {exc}")
            while True:
                job = job_queue.get()
                job["error"] = exc
                job["done"].set()
            return

        self._tk_ui_root = root
        self._tk_ui_ready = True
        _append_consent_runtime_log("persistent consent ui thread ready")

        def _pump():
            while True:
                job = job_queue.get()
                try:
                    job["result"] = job["fn"]()
                except BaseException as exc:
                    job["error"] = exc
                finally:
                    job["done"].set()

        root.after(50, _pump)
        root.mainloop()

    thread = threading.Thread(target=_ui_main, name="zview-consent-ui", daemon=True)
    self._tk_ui_queue = job_queue
    self._tk_ui_thread = thread
    thread.start()

    deadline = time.time() + 10
    while not getattr(self, "_tk_ui_ready", False) and time.time() < deadline:
        time.sleep(0.05)
    return bool(getattr(self, "_tk_ui_ready", False))


def _show_tk_consent_toplevel(self, requester: str, origin: str, target: str, timeout_seconds: int) -> int:
    """在 Tk UI 线程上构建品牌化的安全确认弹窗（自绘专业风格），返回按钮结果值。

    布局：品牌色头部（可拖动/关闭）+ 警示图标与主说明 + 请求详情卡片 +
    自绘倒计时进度条 + 主次按钮（允许=绿色主按钮，拒绝=灰色次按钮）。
    键盘：Enter=允许，Esc=拒绝。
    """
    import tkinter as tk

    root = getattr(self, "_tk_ui_root", None)
    if root is None:
        raise RuntimeError("persistent consent ui root unavailable")
    timeout_seconds = max(5, int(timeout_seconds))
    username = _safe_dialog_username(self)

    # ---- 视觉规范（按系统 DPI 自动缩放，避免高缩放下控件被裁切） ----
    try:
        _dpi = float(root.winfo_fpixels("1i"))
    except Exception:
        _dpi = 96.0
    S = max(1.0, min(2.0, _dpi / 96.0))

    def S_px(value: int) -> int:
        return max(1, int(round(value * S)))

    WIDTH = S_px(560)
    HEIGHT = S_px(412)
    HEADER_H = S_px(54)
    PAD_X = S_px(30)
    BRAND_DARK = "#0E3358"
    BRAND = "#16497E"
    TEXT_MAIN = "#1F2733"
    TEXT_SUB = "#64707F"
    CARD_BG = "#F4F7FA"
    CARD_BORDER = "#DCE4EC"
    GREEN = "#1E8E4E"
    GREEN_DARK = "#166B3B"
    GRAY_BTN = "#EDF1F6"
    GRAY_BTN_ACTIVE = "#DFE6EE"
    AMBER = "#E8A23C"
    BAR_BG = "#E6EBF1"
    DANGER_HOVER = "#C0392B"

    state = {"finished": False, "value": IDNO}
    countdown = {"remaining": timeout_seconds}

    top = tk.Toplevel(root)
    top.withdraw()
    top.title("Z-View 远程控制确认")
    top.resizable(False, False)
    top.configure(bg="white")
    try:
        top.attributes("-topmost", True)
    except Exception:
        pass
    # 无系统边框，完全自绘（现代安全软件风格）
    try:
        top.overrideredirect(True)
    except Exception:
        pass

    screen_width = top.winfo_screenwidth()
    screen_height = top.winfo_screenheight()
    pos_x = max(0, int((screen_width - WIDTH) / 2))
    pos_y = max(0, int((screen_height - HEIGHT) / 3))

    def _finish(value: int):
        if state["finished"]:
            return
        state["finished"] = True
        state["value"] = value
        try:
            top.destroy()
        except Exception:
            pass

    def _make_hover(widget, normal_bg, hover_bg):
        widget.bind("<Enter>", lambda _e: widget.configure(bg=hover_bg))
        widget.bind("<Leave>", lambda _e: widget.configure(bg=normal_bg))

    # ---- 头部（品牌条：盾牌 + 标题 + 关闭；支持拖动） ----
    header = tk.Frame(top, bg=BRAND, height=HEADER_H)
    header.pack(fill="x", side="top")
    header.pack_propagate(False)

    shield = tk.Canvas(header, width=S_px(26), height=S_px(30), bg=BRAND, highlightthickness=0)
    shield.create_polygon(
        13, 1, 24, 5, 24, 14, 24, 19, 13, 29, 2, 19, 2, 5,
        fill="", outline="white", width=2,
        joinstyle=tk.ROUND,
    )
    shield.create_line(13, 9, 13, 16, fill="white", width=2, capstyle=tk.ROUND)
    shield.create_oval(12, 18, 14, 20, fill="white", outline="")
    shield.place(x=S_px(20), y=max(0, (HEADER_H - S_px(30)) // 2))

    tk.Label(
        header,
        text="Z-View 安全中心",
        font=("Microsoft YaHei UI", 11, "bold"),
        bg=BRAND,
        fg="white",
    ).place(x=S_px(54), y=S_px(8))
    tk.Label(
        header,
        text="远程控制确认",
        font=("Microsoft YaHei UI", 9),
        bg=BRAND,
        fg="#B9CBE0",
    ).place(x=S_px(54), y=S_px(30))

    close_label = tk.Label(
        header,
        text="✕",
        font=("Microsoft YaHei UI", 11),
        bg=BRAND,
        fg="#C7D6E8",
        cursor="hand2",
        width=3,
    )
    close_label.place(x=WIDTH - S_px(46), y=0, height=HEADER_H)
    close_label.bind("<Button-1>", lambda _e: _finish(IDNO))
    _make_hover(close_label, BRAND, DANGER_HOVER)

    drag_state = {"x": 0, "y": 0}

    def _drag_start(event):
        drag_state["x"] = event.x
        drag_state["y"] = event.y

    def _drag_move(event):
        if state["finished"]:
            return
        new_x = top.winfo_x() + event.x - drag_state["x"]
        new_y = top.winfo_y() + event.y - drag_state["y"]
        top.geometry(f"+{max(0, new_x)}+{max(0, new_y)}")

    for widget in (header, shield):
        widget.bind("<ButtonPress-1>", _drag_start)
        widget.bind("<B1-Motion>", _drag_move)

    # ---- 主体 ----
    body = tk.Frame(top, bg="white")
    body.pack(fill="both", expand=True)
    pad_x = PAD_X
    inner_width = WIDTH - pad_x * 2

    warn = tk.Canvas(body, width=46, height=46, bg="white", highlightthickness=0)
    warn.create_polygon(
        23, 3, 43, 40, 3, 40,
        fill=AMBER, outline="#D18E2F", width=1,
        joinstyle=tk.ROUND,
    )
    warn.create_rectangle(21, 15, 25, 27, fill="white", outline="")
    warn.create_oval(21, 31, 25, 35, fill="white", outline="")
    warn.grid(row=0, column=0, rowspan=2, sticky="n", padx=(pad_x, 16), pady=(22, 0))

    tk.Label(
        body,
        text=f"「{requester}」请求远程控制这台设备",
        font=("Microsoft YaHei UI", 13, "bold"),
        bg="white",
        fg=TEXT_MAIN,
        anchor="w",
    ).grid(row=0, column=1, sticky="w", padx=(0, pad_x), pady=(24, 2))
    tk.Label(
        body,
        text="允许后，对方将可以查看屏幕并操作鼠标键盘。请确认对方身份。",
        font=("Microsoft YaHei UI", 9),
        bg="white",
        fg=TEXT_SUB,
        anchor="w",
    ).grid(row=1, column=1, sticky="w", padx=(0, pad_x), pady=(0, 14))

    # ---- 请求详情卡片 ----
    card = tk.Frame(
        body,
        bg=CARD_BG,
        highlightbackground=CARD_BORDER,
        highlightthickness=1,
    )
    card.grid(row=2, column=0, columnspan=2, sticky="ew", padx=pad_x)

    details = (
        ("请求方", requester),
        ("来源地址", origin or "未知"),
        ("目标终端", target or "本机"),
        ("本机用户", username),
    )
    for i, (key, value) in enumerate(details):
        tk.Label(
            card,
            text=key,
            font=("Microsoft YaHei UI", 9),
            bg=CARD_BG,
            fg=TEXT_SUB,
            width=8,
            anchor="e",
        ).grid(row=i, column=0, sticky="w", padx=(14, 10), pady=(6 if i == 0 else 2, 2))
        tk.Label(
            card,
            text=str(value),
            font=("Microsoft YaHei UI", 9, "bold"),
            bg=CARD_BG,
            fg=TEXT_MAIN,
            anchor="w",
        ).grid(row=i, column=1, sticky="w", padx=(0, 14), pady=(6 if i == 0 else 2, 2))
    remaining_var = tk.StringVar()
    tk.Label(
        card,
        text="剩余时间",
        font=("Microsoft YaHei UI", 9),
        bg=CARD_BG,
        fg=TEXT_SUB,
        width=8,
        anchor="e",
    ).grid(row=len(details), column=0, sticky="w", padx=(14, 10), pady=(2, 12))
    remaining_value = tk.Label(
        card,
        textvariable=remaining_var,
        font=("Microsoft YaHei UI", 9, "bold"),
        bg=CARD_BG,
        fg=AMBER,
        anchor="w",
    )
    remaining_value.grid(row=len(details), column=1, sticky="w", padx=(0, 14), pady=(2, 12))

    # ---- 倒计时进度条（自绘，颜色随剩余时间变化） ----
    bar_canvas = tk.Canvas(
        body,
        width=inner_width,
        height=6,
        bg="white",
        highlightthickness=0,
    )
    bar_canvas.grid(row=3, column=0, columnspan=2, sticky="ew", padx=pad_x, pady=(16, 0))

    timeout_note = tk.Label(
        body,
        textvariable=remaining_var,
        font=("Microsoft YaHei UI", 8),
        bg="white",
        fg=TEXT_SUB,
        anchor="e",
    )
    timeout_note.grid(row=4, column=0, columnspan=2, sticky="e", padx=pad_x, pady=(6, 0))

    # ---- 底部按钮区 ----
    footer_h = S_px(84)
    btn_h = S_px(44)
    btn_y = (footer_h - btn_h) // 2
    deny_w = S_px(110)
    allow_w = S_px(150)
    footer = tk.Frame(top, bg="#FAFBFD")
    footer.pack(fill="x", side="bottom")
    footer.configure(height=footer_h)
    footer.pack_propagate(False)

    deny_button = tk.Button(
        footer,
        text="拒 绝",
        font=("Microsoft YaHei UI", 10, "bold"),
        bg=GRAY_BTN,
        fg=TEXT_MAIN,
        activebackground=GRAY_BTN_ACTIVE,
        activeforeground=TEXT_MAIN,
        relief=tk.FLAT,
        bd=0,
        cursor="hand2",
        command=lambda: _finish(IDNO),
    )
    deny_button.place(x=WIDTH - pad_x - allow_w - S_px(14) - deny_w, y=btn_y, width=deny_w, height=btn_h)

    allow_button = tk.Button(
        footer,
        text="允 许",
        font=("Microsoft YaHei UI", 10, "bold"),
        bg=GREEN,
        fg="white",
        activebackground=GREEN_DARK,
        activeforeground="white",
        relief=tk.FLAT,
        bd=0,
        cursor="hand2",
        command=lambda: _finish(IDYES),
    )
    allow_button.place(x=WIDTH - pad_x - allow_w, y=btn_y, width=allow_w, height=btn_h)
    _make_hover(deny_button, GRAY_BTN, GRAY_BTN_ACTIVE)
    _make_hover(allow_button, GREEN, GREEN_DARK)

    def _tick():
        if state["finished"]:
            return
        remaining = countdown["remaining"]
        if remaining <= 0:
            _finish(IDTIMEOUT)
            return
        remaining_var.set(f"{remaining} 秒后未处理将自动拒绝")
        ratio = max(0.0, min(1.0, remaining / float(timeout_seconds)))
        bar_canvas.delete("all")
        bar_canvas.create_rectangle(0, 0, inner_width, 6, fill=BAR_BG, outline="")
        fill_color = GREEN if ratio > 0.34 else AMBER
        bar_canvas.create_rectangle(0, 0, max(2, int(inner_width * ratio)), 6, fill=fill_color, outline="")
        countdown["remaining"] = remaining - 1
        top.after(1000, _tick)

    top.bind("<Return>", lambda _e: _finish(IDYES))
    top.bind("<Escape>", lambda _e: _finish(IDNO))
    top.protocol("WM_DELETE_WINDOW", lambda: _finish(IDNO))

    top.geometry(f"{WIDTH}x{HEIGHT}+{pos_x}+{pos_y}")

    for icon_path in _candidate_icon_paths():
        if not icon_path.exists():
            continue
        try:
            top.iconbitmap(default=str(icon_path))
            break
        except Exception:
            continue

    top.deiconify()
    top.update_idletasks()
    try:
        top.focus_set()
    except Exception:
        pass
    top.after(200, _tick)
    top.after(50, lambda: _safe_focus_window(top))
    try:
        top.grab_set()
    except Exception:
        pass

    try:
        # wait_window 进入局部事件循环，倒计时 after 回调仍会被正常调度。
        root.wait_window(top)
    finally:
        try:
            if top.winfo_exists():
                top.destroy()
        except Exception:
            pass

    return int(state["value"])


def _invoke_tk_consent_dialog(self, title: str, requester: str, origin: str, target: str, timeout_seconds: int) -> int:
    """把弹窗请求编组到常驻 Tk UI 线程执行（title 参数仅为兼容旧签名保留）。"""
    del title
    if not _ensure_tk_ui_thread(self):
        raise RuntimeError("consent tk ui thread unavailable")

    job = {
        "fn": lambda: _show_tk_consent_toplevel(self, requester, origin, target, timeout_seconds),
        "done": threading.Event(),
    }
    self._tk_ui_queue.put(job)

    wait_seconds = max(5, int(timeout_seconds)) + 10
    if not job["done"].wait(wait_seconds):
        raise TimeoutError("consent dialog did not finish within the expected window")
    if "error" in job:
        raise job["error"]
    return int(job.get("result") or IDNO)


def _tray_collect_machine_info(self) -> dict:
    """采集本机概要信息：主机名/用户/系统 + 在线网卡的 IP 与 MAC。"""
    import os as _os
    import platform
    import socket

    info = {
        "hostname": socket.gethostname() or "未知",
        "user": get_current_username() or _os.environ.get("USERNAME") or "未知",
        "system": f"{platform.system()} {platform.release()} (build {platform.version()})",
        "arch": platform.machine(),
        "adapters": [],
    }
    try:
        import psutil

        addrs_by_if = psutil.net_if_addrs()
        stats_by_if = psutil.net_if_stats()
        for name, addr_list in addrs_by_if.items():
            if name.lower().startswith(("lo", "loopback")):
                continue
            stats = stats_by_if.get(name)
            is_up = bool(stats and getattr(stats, "isup", False))
            ipv4, ipv6, mac = [], [], ""
            for addr in addr_list:
                family = getattr(addr, "family", None)
                address = str(getattr(addr, "address", "") or "")
                if not address:
                    continue
                name_of = getattr(family, "name", "")
                if name_of == "AF_INET":
                    ipv4.append(address.split("%")[0])
                elif name_of == "AF_INET6":
                    ipv6.append(address.split("%")[0])
                elif name_of == "AF_LINK":
                    mac = address.replace("-", ":").upper()
                    if set(mac) <= {":"} :
                        mac = ""
            if not (ipv4 or ipv6 or mac):
                continue
            info["adapters"].append(
                {
                    "name": name,
                    "up": is_up,
                    "ipv4": ipv4,
                    "ipv6": ipv6,
                    "mac": mac,
                    "speed_mbps": int(getattr(stats, "speed", 0) or 0),
                }
            )
        # 在线网卡排前，其余按名称稳定排序
        info["adapters"].sort(key=lambda a: (not a["up"], a["name"]))
    except Exception as exc:
        _append_consent_runtime_log(f"machine info collect failed: {type(exc).__name__}: {exc}")
        info["collect_error"] = f"网络适配器信息采集失败: {type(exc).__name__}: {exc}"
    return info


def _tray_render_machine_info(info: dict) -> str:
    """把本机信息字典渲染为托盘对话框展示文本。"""
    lines = [
        f"主机名: {info.get('hostname', '未知')}",
        f"当前用户: {info.get('user', '未知')}",
        f"操作系统: {info.get('system', '未知')} [{info.get('arch', '')}]".rstrip(),
    ]
    adapters = info.get("adapters") or []
    if adapters:
        lines.append("")
        for adapter in adapters:
            state = "在线" if adapter.get("up") else "离线"
            speed = adapter.get("speed_mbps") or 0
            speed_text = f"{speed}Mbps" if speed > 0 else "速率未知"
            header = f"[{adapter.get('name', '未命名')}] {state} · {speed_text}"
            lines.append(header)
            for ip in adapter.get("ipv4") or []:
                lines.append(f"  IPv4: {ip}")
            for ip in (adapter.get("ipv6") or [])[:2]:
                lines.append(f"  IPv6: {ip}")
            if adapter.get("mac"):
                lines.append(f"  MAC: {adapter['mac']}")
    else:
        lines.append("")
        lines.append("网络适配器: 无可用数据")
    if info.get("collect_error"):
        lines.append(info["collect_error"])
    return "\n".join(lines)


def _tray_show_machine_info(self) -> None:
    """弹出「查看本机信息」对话框（IP/MAC 等）。"""
    try:
        info = _tray_collect_machine_info(self)
        text = _tray_render_machine_info(info)
    except Exception as exc:
        text = f"本机信息采集失败: {type(exc).__name__}: {exc}"
    _append_consent_runtime_log("tray action: view machine info")
    ctypes.windll.user32.MessageBoxW(
        None,
        text,
        "Z-View 本机信息",
        MB_TOPMOST | MB_SETFOREGROUND | MB_SYSTEMMODAL | 0x40,  # MB_ICONINFORMATION
    )


def _tray_set_skip_consent(self, enabled: bool) -> None:
    settings = load_tray_settings()
    settings["skip_consent_for_session"] = bool(enabled)
    save_tray_settings(settings)
    state = "ENABLED" if enabled else "disabled"
    _append_consent_runtime_log(f"tray toggle: skip_consent {state} by operator")


def _tray_apply_toggle(self, menu_id: int) -> None:
    """托盘菜单开关动作；skip 开关启用前需操作者二次确认。"""
    user32 = ctypes.windll.user32
    if menu_id == IDM_TOGGLE_ALLOW_REQUESTS:
        settings = load_tray_settings()
        settings["allow_remote_requests"] = not bool(settings.get("allow_remote_requests", True))
        state = "enabled" if settings["allow_remote_requests"] else "disabled"
        _append_consent_runtime_log(f"tray toggle: allow_remote_requests {state} by operator")
        save_tray_settings(settings)
    elif menu_id == IDM_TOGGLE_SKIP_CONSENT:
        settings = load_tray_settings()
        if settings.get("skip_consent_for_session"):
            _tray_set_skip_consent(self, False)
            return
        confirm = user32.MessageBoxW(
            None,
            "启用后，所有远程控制请求将不再弹出确认窗口、直接自动允许。\n\n确定要启用「本机免确认」吗？",
            "Z-View 安全提示",
            MB_YESNO | MB_ICONQUESTION | MB_TOPMOST | MB_SETFOREGROUND | MB_SYSTEMMODAL,
        )
        if confirm == IDYES:
            _tray_set_skip_consent(self, True)
    elif menu_id == IDM_TOGGLE_BALLOON:
        settings = load_tray_settings()
        settings["show_balloon_notifications"] = not bool(
            settings.get("show_balloon_notifications", True)
        )
        save_tray_settings(settings)
    elif menu_id == IDM_TOGGLE_UAC_INPUT:
        settings = load_tray_settings()
        settings["allow_secure_desktop_input"] = not bool(
            settings.get("allow_secure_desktop_input", True)
        )
        state = "enabled" if settings["allow_secure_desktop_input"] else "disabled"
        _append_consent_runtime_log(f"tray toggle: allow_secure_desktop_input {state} by operator")
        save_tray_settings(settings)


def _show_tray_balloon(self, title: str, message: str) -> None:
    """从任意线程弹出托盘气泡提醒；托盘未就绪或失败时静默跳过。

    使用独立的 NOTIFYICONDATAW 副本，避免与主线程消息循环共享可变状态。
    """
    template = getattr(self, "_tray_notify_icon", None)
    hwnd = getattr(self, "_tray_hwnd", None)
    if not template or not hwnd:
        return
    try:
        shell32 = ctypes.windll.shell32

        class _NID(ctypes.Structure):
            _fields_ = [
                ("cbSize", wintypes.DWORD),
                ("hWnd", wintypes.HWND),
                ("uID", wintypes.UINT),
                ("uFlags", wintypes.UINT),
                ("uCallbackMessage", wintypes.UINT),
                ("hIcon", wintypes.HANDLE),
                ("szTip", wintypes.WCHAR * 128),
                ("dwState", wintypes.DWORD),
                ("dwStateMask", wintypes.DWORD),
                ("szInfo", wintypes.WCHAR * 256),
                ("uTimeoutOrVersion", wintypes.UINT),
                ("szInfoTitle", wintypes.WCHAR * 64),
                ("dwInfoFlags", wintypes.DWORD),
                ("guidItem", ctypes.c_byte * 16),
                ("hBalloonIcon", wintypes.HANDLE),
            ]

        data = _NID()
        data.cbSize = ctypes.sizeof(data)
        data.hWnd = hwnd
        data.uID = template.uID
        data.uFlags = 0x00000010  # NIF_INFO
        data.szInfo = str(message or "").strip()[:255] or " "
        data.szInfoTitle = str(title or "Z-View").strip()[:63] or "Z-View"
        data.dwInfoFlags = 0x00000001  # NIIF_INFO
        shell32.Shell_NotifyIconW(0x00000001, ctypes.byref(data))  # NIM_MODIFY
    except Exception:
        pass


def _patched_show_consent_dialog(self, request: dict) -> tuple[bool, str]:
    requester = str(request.get("requester") or "unknown-operator")
    origin = str(request.get("origin") or "未知来源")
    target = str(request.get("target") or os.environ.get("COMPUTERNAME") or "当前终端")
    timeout_seconds = max(5, int(request.get("timeout_seconds") or 30))
    title = "Z-View 远程控制确认"
    style = MB_YESNO | MB_ICONQUESTION | MB_TOPMOST | MB_SETFOREGROUND | MB_SYSTEMMODAL

    if load_tray_settings().get("show_balloon_notifications", True):
        _show_tray_balloon(
            self,
            "远程控制请求",
            f"{requester} ({origin}) 请求远程控制本机，请在确认窗口中选择允许或拒绝。",
        )

    with self.dialog_lock:
        response = _invoke_consent_prompt(self, title, requester, origin, target, timeout_seconds, style)

    _append_consent_runtime_log(
        f"dialog result: backend={self.last_dialog_mode} response={response} attempts={getattr(self, 'last_dialog_attempts', [])}"
    )
    if response == IDYES:
        return True, "approved"
    if response == IDNO:
        return False, "rejected"
    if response == IDTIMEOUT:
        return False, "timeout"
    return False, f"unknown_response:{response}"


_legacy_init = ConsentTrayApp.__init__
ConsentTrayApp.__init__ = _patched_init
ConsentTrayApp._build_consent_dialog_content = _build_consent_dialog_content
ConsentTrayApp._invoke_consent_prompt = _invoke_consent_prompt
ConsentTrayApp._invoke_task_dialog = _invoke_task_dialog
ConsentTrayApp._show_consent_dialog = _patched_show_consent_dialog
ConsentTrayApp._show_tray_balloon = _show_tray_balloon
ConsentTrayApp._tray_show_machine_info = _tray_show_machine_info
ConsentTrayApp._tray_collect_machine_info = _tray_collect_machine_info
ConsentTrayApp._tray_apply_toggle = _tray_apply_toggle

