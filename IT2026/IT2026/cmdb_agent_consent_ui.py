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
    load_tray_settings,
    resolve_runtime_log_path,
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

        def _invoke_messagebox(self, title: str, message: str, _style: int, timeout_seconds: int) -> int:
            class _MessageBoxThread(threading.Thread):
                def __init__(self):
                    super().__init__(daemon=True)
                    self.result = IDNO

                def run(self):
                    try:
                        user32 = ctypes.windll.user32
                        user32.MessageBoxW.argtypes = [
                            wintypes.HWND,
                            wintypes.LPCWSTR,
                            wintypes.LPCWSTR,
                            wintypes.UINT,
                        ]
                        user32.MessageBoxW.restype = ctypes.c_int
                        self.result = int(
                            user32.MessageBoxW(None, message, title, MB_YESNO | MB_ICONQUESTION | MB_TOPMOST)
                        )
                    except Exception:
                        self.result = IDNO

            worker = _MessageBoxThread()
            worker.start()
            worker.join(timeout=max(5, int(timeout_seconds)))
            if worker.is_alive():
                return IDTIMEOUT
            return worker.result

        def serve_forever(self):
            session_id = get_current_process_session_id()
            if session_id is None:
                _append_consent_runtime_log("fallback consent helper started without a session; waiting idle")
                while True:
                    time.sleep(60)

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
                                connection.send({"approved": False, "reason": f"helper_error:{type(exc).__name__}"})
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

    def main():
        ConsentTrayApp().serve_forever()

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
    return str(username or os.environ.get("USERNAME") or "鏈煡鐢ㄦ埛")


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
    native_available = bool(getattr(self, "_task_dialog_indirect", None))
    backends: list[str] = []

    # Default to tkinter so the operator always gets the live countdown UI.
    if preferred_backend in ("auto", "tkinter"):
        backends.append("tkinter")
        if native_available:
            backends.append("taskdialog")
    elif preferred_backend == "taskdialog":
        if native_available:
            backends.append("taskdialog")
        backends.append("tkinter")
    elif preferred_backend == "messagebox":
        backends.append("messagebox")
        backends.append("tkinter")
        if native_available:
            backends.append("taskdialog")
    else:
        backends.append("tkinter")
        if native_available:
            backends.append("taskdialog")

    if "messagebox" not in backends:
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
        f"{requester} 姝ｅ湪璇锋眰杩滅▼鎺у埗杩欏彴缁堢銆俓n\n"
        f"鐩爣缁堢: {target}\n"
        f"鏉ユ簮鍦板潃: {origin}\n"
        f"褰撳墠鐧诲綍鐢ㄦ埛: {_safe_dialog_username(self)}\n\n"
        "鏄惁鍏佽鏈杩滅▼鎺у埗锛焅n"
        f"{remaining_seconds} 绉掑唴鏈鐞嗗皢鑷姩鎷掔粷銆?"
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
        TASKDIALOG_BUTTON(IDYES, "鍏佽"),
        TASKDIALOG_BUTTON(IDNO, "鎷掔粷"),
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
    config.pszMainInstruction = "Z-View 杩滅▼鎺у埗纭"
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


def _invoke_tk_consent_dialog(self, title: str, requester: str, origin: str, target: str, timeout_seconds: int) -> int:
    import tkinter as tk
    from tkinter import ttk

    timeout_seconds = max(5, int(timeout_seconds))
    state = {"finished": False, "value": IDNO}
    countdown = {"remaining": timeout_seconds}

    root = tk.Tk()
    root.withdraw()
    root.title(title)
    root.resizable(False, False)
    try:
        root.attributes("-topmost", True)
    except Exception:
        pass

    content_var = tk.StringVar(
        value=_build_consent_dialog_content(self, requester, origin, target, countdown["remaining"])
    )

    container = ttk.Frame(root, padding=16)
    container.grid(row=0, column=0, sticky="nsew")

    header = ttk.Label(container, text="Z-View 杩滅▼鎺у埗纭", font=("Microsoft YaHei UI", 12, "bold"))
    header.grid(row=0, column=0, sticky="w")

    content = ttk.Label(
        container,
        textvariable=content_var,
        justify="left",
        wraplength=420,
    )
    content.grid(row=1, column=0, pady=(12, 16), sticky="w")

    button_row = ttk.Frame(container)
    button_row.grid(row=2, column=0, sticky="e")

    def _finish(value: int):
        if state["finished"]:
            return
        state["finished"] = True
        state["value"] = value
        try:
            root.quit()
        except Exception:
            pass

    root.protocol("WM_DELETE_WINDOW", lambda: _finish(IDNO))

    allow_button = ttk.Button(button_row, text="鍏佽", command=lambda: _finish(IDYES))
    allow_button.grid(row=0, column=0, padx=(0, 8))

    reject_button = ttk.Button(button_row, text="鎷掔粷", command=lambda: _finish(IDNO))
    reject_button.grid(row=0, column=1)

    def _tick():
        if state["finished"]:
            return
        if countdown["remaining"] <= 0:
            _finish(IDTIMEOUT)
            return
        countdown["remaining"] -= 1
        content_var.set(
            _build_consent_dialog_content(self, requester, origin, target, countdown["remaining"])
        )
        root.after(1000, _tick)

    root.update_idletasks()
    width = max(root.winfo_reqwidth(), 470)
    height = max(root.winfo_reqheight(), 230)
    screen_width = root.winfo_screenwidth()
    screen_height = root.winfo_screenheight()
    pos_x = max(0, int((screen_width - width) / 2))
    pos_y = max(0, int((screen_height - height) / 3))
    root.geometry(f"{width}x{height}+{pos_x}+{pos_y}")

    for icon_path in _candidate_icon_paths():
        if not icon_path.exists():
            continue
        try:
            root.iconbitmap(default=str(icon_path))
            break
        except Exception:
            continue

    root.deiconify()
    root.after(1000, _tick)
    root.after(50, lambda: _safe_focus_window(root))
    root.after(300, lambda: _safe_focus_window(root))
    try:
        root.grab_set()
    except Exception:
        pass

    try:
        root.mainloop()
    finally:
        try:
            if root.winfo_exists():
                root.destroy()
        except Exception:
            pass

    return int(state["value"])


def _patched_show_consent_dialog(self, request: dict) -> tuple[bool, str]:
    requester = str(request.get("requester") or "unknown-operator")
    origin = str(request.get("origin") or "鏈煡鏉ユ簮")
    target = str(request.get("target") or os.environ.get("COMPUTERNAME") or "褰撳墠缁堢")
    timeout_seconds = max(5, int(request.get("timeout_seconds") or 30))
    title = "Z-View 杩滅▼鎺у埗纭"
    style = MB_YESNO | MB_ICONQUESTION | MB_TOPMOST | MB_SETFOREGROUND | MB_SYSTEMMODAL

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

