"""
远程桌面引擎 v2.0 - 企业级实现
基于火绒/360 EDR远控架构设计
"""

import asyncio
import ctypes
import json
import base64
import io
import os
import subprocess
import time
import contextlib
import zlib
import threading
import traceback
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from multiprocessing.connection import Client
from ctypes import wintypes
from PIL import Image
import pyautogui
from fastapi import WebSocket

from Capture.desktop_capture import DesktopFrameCapturer, create_capture_stack
from Codec.video_encoder import VideoEncoderProfile
from agent_consent_ipc import (
    APP_BASE_DIR,
    build_consent_authkey,
    build_consent_pipe_name,
    get_current_process_session_id,
    build_ui_launch_command,
)
from console_utils import enable_utf8_stdio, safe_console_print
from coordinate_mapper import DPIAwareMapper
from Input.input_controller import InputInjector, MouseEvent, MouseEventType, MouseButton
from Network.transport import TransportProfile
from RemoteAgent.privileged_client import PrivilegedServiceClient
from remote_desktop_protocol import (
    RemoteMouseAction,
    RemoteMouseState,
    parse_mouse_message,
)

enable_utf8_stdio()
print = safe_console_print

if os.name == "nt":
    _PROGRAM_DATA_DIR = Path(os.environ.get("ProgramData", r"C:\ProgramData"))
    _REMOTE_RUNTIME_LOG_FILE = _PROGRAM_DATA_DIR / "CMDB-Agent" / "logs" / "agent-runtime.log"
else:
    _REMOTE_RUNTIME_LOG_FILE = Path(__file__).resolve().parent / "logs" / "agent-runtime.log"


def append_remote_runtime_log(component: str, message: str) -> None:
    timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{timestamp}] [{component}] {message}"
    try:
        _REMOTE_RUNTIME_LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(_REMOTE_RUNTIME_LOG_FILE, "a", encoding="utf-8") as file:
            file.write(line + "\n")
    except Exception:
        pass


def log_remote_desktop_flow(session_id: int | str, stage: str, message: str) -> None:
    line = f"[{session_id}] {stage}: {message}"
    print(f"[RemoteDesktop] {line}")
    append_remote_runtime_log("RemoteDesktop", line)

try:
    import win32clipboard
    import win32con
except ImportError:
    win32clipboard = None
    win32con = None


# 禁用pyautogui安全检查
pyautogui.FAILSAFE = False


class RemoteAccessConsentManager:
    """被控端远程控制确认管理器，适配后台常驻 Agent。"""

    MB_ICONQUESTION = 0x00000020
    MB_SETFOREGROUND = 0x00010000
    MB_SYSTEMMODAL = 0x00001000
    MB_TOPMOST = 0x00040000
    MB_YESNO = 0x00000004
    IDYES = 6
    IDNO = 7
    IDTIMEOUT = 32000
    INVALID_SESSION_ID = 0xFFFFFFFF
    WTS_CURRENT_SERVER_HANDLE = wintypes.HANDLE(0)
    WTSActive = 0
    WTSConnected = 1
    WTSUserName = 5
    WTSDomainName = 7

    class WTS_SESSION_INFO(ctypes.Structure):
        _fields_ = [
            ("SessionId", wintypes.DWORD),
            ("pWinStationName", wintypes.LPWSTR),
            ("State", wintypes.DWORD),
        ]

    def __init__(self):
        self._lock = threading.Lock()
        self.enabled = True
        self.timeout_seconds = 30
        self.allow_if_no_user = False
        self.helper_enabled = True
        self.helper_connect_timeout_seconds = 4
        self.helper_launch_cooldown_seconds = 15
        self._last_helper_launch_by_session: dict[int, float] = {}
        self._wtsapi32 = getattr(ctypes.windll, "wtsapi32", None)
        self._kernel32 = getattr(ctypes.windll, "kernel32", None)
        self._wts_available = False
        self._setup_windows_apis()

    def _setup_windows_apis(self):
        if self._wtsapi32 is None or self._kernel32 is None:
            return

        self._kernel32.WTSGetActiveConsoleSessionId.restype = wintypes.DWORD

        self._wtsapi32.WTSSendMessageW.argtypes = [
            wintypes.HANDLE,
            wintypes.DWORD,
            wintypes.LPWSTR,
            wintypes.DWORD,
            wintypes.LPWSTR,
            wintypes.DWORD,
            wintypes.DWORD,
            wintypes.DWORD,
            ctypes.POINTER(wintypes.DWORD),
            wintypes.BOOL,
        ]
        self._wtsapi32.WTSSendMessageW.restype = wintypes.BOOL

        self._wtsapi32.WTSEnumerateSessionsW.argtypes = [
            wintypes.HANDLE,
            wintypes.DWORD,
            wintypes.DWORD,
            ctypes.POINTER(ctypes.POINTER(self.WTS_SESSION_INFO)),
            ctypes.POINTER(wintypes.DWORD),
        ]
        self._wtsapi32.WTSEnumerateSessionsW.restype = wintypes.BOOL

        self._wtsapi32.WTSFreeMemory.argtypes = [wintypes.LPVOID]
        self._wtsapi32.WTSFreeMemory.restype = None

        self._wtsapi32.WTSQuerySessionInformationW.argtypes = [
            wintypes.HANDLE,
            wintypes.DWORD,
            wintypes.DWORD,
            ctypes.POINTER(wintypes.LPWSTR),
            ctypes.POINTER(wintypes.DWORD),
        ]
        self._wtsapi32.WTSQuerySessionInformationW.restype = wintypes.BOOL
        self._wts_available = True

    def configure(self, settings: dict | None = None):
        remote_settings = settings or {}
        consent_settings = remote_settings.get("consent", {})

        if "require_consent" in remote_settings:
            self.enabled = bool(remote_settings.get("require_consent"))
        elif "enabled" in consent_settings:
            self.enabled = bool(consent_settings.get("enabled"))

        timeout_value = (
            consent_settings.get("timeout_seconds")
            if "timeout_seconds" in consent_settings
            else remote_settings.get("consent_timeout_seconds")
        )
        if timeout_value is not None:
            try:
                self.timeout_seconds = max(5, int(timeout_value))
            except (TypeError, ValueError):
                pass

        allow_if_no_user = (
            consent_settings.get("allow_if_no_user")
            if "allow_if_no_user" in consent_settings
            else remote_settings.get("allow_if_no_user")
        )
        if allow_if_no_user is not None:
            self.allow_if_no_user = bool(allow_if_no_user)

        helper_enabled = (
            consent_settings.get("helper_enabled")
            if "helper_enabled" in consent_settings
            else remote_settings.get("consent_helper_enabled")
        )
        if helper_enabled is not None:
            self.helper_enabled = bool(helper_enabled)

        helper_timeout = (
            consent_settings.get("helper_connect_timeout_seconds")
            if "helper_connect_timeout_seconds" in consent_settings
            else remote_settings.get("consent_helper_connect_timeout_seconds")
        )
        if helper_timeout is not None:
            try:
                self.helper_connect_timeout_seconds = max(1, int(helper_timeout))
            except (TypeError, ValueError):
                pass

    async def request_permission(self, request_context: dict | None = None) -> tuple[bool, str]:
        if not self.enabled:
            return True, "disabled"

        context = request_context or {}
        loop = asyncio.get_running_loop()

        with self._lock:
            return await loop.run_in_executor(None, self._show_consent_dialog, context)

    def _show_consent_dialog(self, request_context: dict) -> tuple[bool, str]:
        session_id = self._resolve_target_session_id()

        if session_id is None:
            if self.allow_if_no_user:
                print("[Consent] No active user session; request auto-approved by policy")
                return True, "no_active_session_auto_allowed"
            print("[Consent] No active user session; request rejected by policy")
            return False, "no_active_session"

        if self.helper_enabled:
            helper_result = self._show_helper_dialog(request_context, session_id)
            if helper_result is not None:
                return helper_result

        if self._wts_available:
            return self._show_wts_dialog(request_context, session_id)

        print("[Consent] No consent UI helper and WTS dialog unavailable; remote control request rejected")
        return False, "wts_unavailable"

    def _show_helper_dialog(self, request_context: dict, session_id: int) -> tuple[bool, str] | None:
        deadline = time.time() + max(1, self.helper_connect_timeout_seconds)
        launched = False

        while time.time() <= deadline:
            result = self._request_helper_response(session_id, request_context)
            if result is not None:
                return result

            if not launched:
                launched = self._launch_helper_for_session(session_id)

            time.sleep(0.5)

        print(
            f"[Consent] UI helper unavailable for session={session_id}; "
            "falling back to WTS confirmation"
        )
        return None

    def _request_helper_response(self, session_id: int, request_context: dict) -> tuple[bool, str] | None:
        pipe_name = build_consent_pipe_name(session_id)
        payload = {
            "type": "consent_request",
            "session_id": session_id,
            "requester": str(request_context.get("requester") or "未知管理员"),
            "origin": str(request_context.get("origin") or "未知来源"),
            "target": str(request_context.get("target") or os.environ.get("COMPUTERNAME") or "当前终端"),
            "timeout_seconds": self.timeout_seconds,
        }

        try:
            with Client(pipe_name, family="AF_PIPE", authkey=build_consent_authkey()) as connection:
                connection.send(payload)
                if connection.poll(self.timeout_seconds + 2):
                    response = connection.recv()
                    approved = bool(response.get("approved"))
                    reason = str(response.get("reason") or ("accepted" if approved else "rejected"))
                    return approved, reason
                return False, "timeout"
        except (FileNotFoundError, OSError, EOFError):
            return None
        except Exception as exc:
            print(f"[Consent] Helper IPC failed: session={session_id} error={exc}")
            return None

    def _launch_helper_for_session(self, session_id: int) -> bool:
        now = time.time()
        last_attempt_at = self._last_helper_launch_by_session.get(session_id, 0.0)
        if now - last_attempt_at < self.helper_launch_cooldown_seconds:
            return False

        self._last_helper_launch_by_session[session_id] = now
        command = build_ui_launch_command()
        if not command:
            print("[Consent] Consent UI executable not found")
            return False

        try:
            import win32api
            import win32con
            import win32process
            import win32profile
            import win32security
            import win32ts

            user_token = win32ts.WTSQueryUserToken(session_id)
            primary_token = win32security.DuplicateTokenEx(
                user_token,
                win32security.SecurityImpersonation,
                win32con.MAXIMUM_ALLOWED,
                win32security.TokenPrimary,
            )
            environment = win32profile.CreateEnvironmentBlock(primary_token, False)
            startup = win32process.STARTUPINFO()
            startup.lpDesktop = "winsta0\\default"
            creation_flags = (
                win32con.CREATE_NEW_PROCESS_GROUP
                | win32con.CREATE_UNICODE_ENVIRONMENT
            )
            command_line = subprocess.list2cmdline(command)
            process_handle, thread_handle, process_id, _ = win32process.CreateProcessAsUser(
                primary_token,
                None,
                command_line,
                None,
                None,
                False,
                creation_flags,
                environment,
                str(APP_BASE_DIR),
                startup,
            )
            win32api.CloseHandle(thread_handle)
            win32api.CloseHandle(process_handle)
            win32api.CloseHandle(primary_token)
            win32api.CloseHandle(user_token)
            print(f"[Consent] Launched UI helper: session={session_id} pid={process_id}")
            return True
        except Exception as exc:
            print(f"[Consent] Failed to launch UI helper: session={session_id} error={exc}")
            return False

    def _show_wts_dialog(self, request_context: dict, session_id: int) -> tuple[bool, str]:
        requester = str(request_context.get("requester") or "未知管理员")
        origin = str(request_context.get("origin") or "未知来源")
        target = str(request_context.get("target") or os.environ.get("COMPUTERNAME") or "当前终端")

        title = "CMDB 远程控制确认"
        message = (
            f"{requester} 正在请求远程控制这台终端。\n\n"
            f"目标终端: {target}\n"
            f"来源地址: {origin}\n\n"
            f"是否允许本次远程控制？\n"
            f"{self.timeout_seconds} 秒内未处理将自动拒绝。"
        )

        try:
            response = wintypes.DWORD(0)
            style = (
                self.MB_YESNO
                | self.MB_ICONQUESTION
                | self.MB_TOPMOST
                | self.MB_SETFOREGROUND
                | self.MB_SYSTEMMODAL
            )

            result = self._wtsapi32.WTSSendMessageW(
                self.WTS_CURRENT_SERVER_HANDLE,
                session_id,
                title,
                len(title.encode("utf-16-le")),
                message,
                len(message.encode("utf-16-le")),
                style,
                self.timeout_seconds,
                ctypes.byref(response),
                True,
            )

            if not result:
                error_code = self._kernel32.GetLastError()
                print(f"[Consent] WTS confirmation send failed: session={session_id} error={error_code}")
                return False, f"wts_send_failed:{error_code}"

            if response.value == self.IDYES:
                return True, "accepted"
            if response.value == self.IDNO:
                return False, "rejected"
            if response.value == self.IDTIMEOUT:
                return False, "timeout"
            if response.value == 0:
                # 部分 Windows 版本（含本机 RDP 会话）在等待超时或对话框未能展示时
                # 返回 TRUE 且 response=0，而非 IDTIMEOUT；按超时拒绝并保留原始值供排查。
                print(f"[Consent] WTS dialog returned response=0 after {self.timeout_seconds}s; treating as timeout")
                return False, "timeout"

            return False, f"unknown_response:{response.value}"

        except Exception as exc:
            print(f"[Consent] Confirmation dialog failed; rejecting request: {exc}")
            return False, f"dialog_error:{exc}"

    def _resolve_target_session_id(self) -> int | None:
        if not self._wts_available:
            return None

        session_pointer = ctypes.POINTER(self.WTS_SESSION_INFO)()
        session_count = wintypes.DWORD(0)
        active_console_session = self._kernel32.WTSGetActiveConsoleSessionId()
        preferred_console = (
            int(active_console_session)
            if active_console_session != self.INVALID_SESSION_ID
            else None
        )
        active_sessions_with_user: list[int] = []
        active_sessions_without_user: list[int] = []
        connected_sessions_with_user: list[int] = []
        fallback_connected = None

        try:
            success = self._wtsapi32.WTSEnumerateSessionsW(
                self.WTS_CURRENT_SERVER_HANDLE,
                0,
                1,
                ctypes.byref(session_pointer),
                ctypes.byref(session_count),
            )
            if not success:
                return None

            for index in range(session_count.value):
                session_info = session_pointer[index]
                station_name = (session_info.pWinStationName or "").lower()
                if session_info.State == self.WTSActive:
                    if station_name in {"services", "rdp-tcp"}:
                        continue
                    session_id = int(session_info.SessionId)
                    username = self._query_session_identity(session_id)
                    if username:
                        active_sessions_with_user.append(session_id)
                    else:
                        active_sessions_without_user.append(session_id)
                if session_info.State == self.WTSConnected:
                    session_id = int(session_info.SessionId)
                    if fallback_connected is None:
                        fallback_connected = session_id
                    if self._query_session_identity(session_id):
                        connected_sessions_with_user.append(session_id)

            if preferred_console in active_sessions_with_user:
                return preferred_console
            if active_sessions_with_user:
                return active_sessions_with_user[0]
            if preferred_console in active_sessions_without_user:
                return preferred_console
            if active_sessions_without_user:
                return active_sessions_without_user[0]
            if connected_sessions_with_user:
                return connected_sessions_with_user[0]
            if preferred_console is not None:
                return preferred_console
            return fallback_connected
        finally:
            if session_pointer:
                self._wtsapi32.WTSFreeMemory(session_pointer)

    def _query_session_identity(self, session_id: int) -> str:
        username = self._query_session_text(session_id, self.WTSUserName)
        if not username:
            return ""

        domain = self._query_session_text(session_id, self.WTSDomainName)
        if domain:
            return f"{domain}\\{username}"
        return username

    def _query_session_text(self, session_id: int, info_class: int) -> str:
        if not self._wts_available:
            return ""

        buffer = wintypes.LPWSTR()
        bytes_returned = wintypes.DWORD(0)
        try:
            success = self._wtsapi32.WTSQuerySessionInformationW(
                self.WTS_CURRENT_SERVER_HANDLE,
                int(session_id),
                int(info_class),
                ctypes.byref(buffer),
                ctypes.byref(bytes_returned),
            )
            if not success or not buffer:
                return ""

            value = ctypes.wstring_at(buffer) if buffer else ""
            return str(value or "").replace("\x00", "").strip()
        except Exception:
            return ""
        finally:
            if buffer:
                with contextlib.suppress(Exception):
                    self._wtsapi32.WTSFreeMemory(buffer)


CONSENT_MANAGER = RemoteAccessConsentManager()

# 启动时用 agent 配置初始化同意策略；此前 configure() 从未被调用，
# config.json 的 require_consent/consent_timeout_seconds 等键实际不生效。
try:
    from agent_consent_ipc import load_agent_config

    CONSENT_MANAGER.configure((load_agent_config() or {}).get("remote_desktop") or {})
except Exception as _consent_config_exc:  # pragma: no cover - 配置缺失时保持默认
    print(f"[Consent] Failed to apply consent settings from config: {_consent_config_exc}")


class RemoteClipboardManager:
    """远程剪贴板管理，仅同步文本内容。"""

    MAX_TEXT_BYTES = 1024 * 1024

    def __init__(self):
        self.available = win32clipboard is not None and win32con is not None
        self._lock = threading.Lock()

    def get_text(self) -> tuple[bool, str, str]:
        if not self.available:
            return False, "", "当前环境不支持剪贴板访问"

        with self._lock:
            try:
                self._open_clipboard()
                if not win32clipboard.IsClipboardFormatAvailable(win32con.CF_UNICODETEXT):
                    return True, "", "剪贴板中没有文本内容"
                text = win32clipboard.GetClipboardData(win32con.CF_UNICODETEXT) or ""
                return True, str(text), "已读取远端剪贴板"
            except Exception as exc:
                return False, "", f"读取远端剪贴板失败: {exc}"
            finally:
                self._close_clipboard()

    def set_text(self, text: str) -> tuple[bool, str]:
        if not self.available:
            return False, "当前环境不支持剪贴板访问"

        if len(text.encode("utf-8")) > self.MAX_TEXT_BYTES:
            return False, "剪贴板文本过大，已超过 1 MB 限制"

        with self._lock:
            try:
                self._open_clipboard()
                win32clipboard.EmptyClipboard()
                win32clipboard.SetClipboardData(win32con.CF_UNICODETEXT, text)
                return True, "已写入远端剪贴板"
            except Exception as exc:
                return False, f"写入远端剪贴板失败: {exc}"
            finally:
                self._close_clipboard()

    def _open_clipboard(self):
        last_error = None
        for _ in range(10):
            try:
                win32clipboard.OpenClipboard()
                return
            except Exception as exc:
                last_error = exc
                time.sleep(0.05)
        raise RuntimeError(last_error or "OpenClipboard failed")

    def _close_clipboard(self):
        if not self.available:
            return
        with contextlib.suppress(Exception):
            win32clipboard.CloseClipboard()


class RemoteFileTransferManager:
    """远程文件传输管理。"""

    CHUNK_SIZE = 96 * 1024
    MAX_FILE_SIZE = 0
    MAX_LIST_ITEMS = 200

    def __init__(self):
        self.base_dir = self._resolve_base_dir()
        self.base_dir.mkdir(parents=True, exist_ok=True)
        self.MAX_FILE_SIZE = self._load_max_file_size()
        self._lock = threading.Lock()
        self._incoming_transfers: dict[str, dict] = {}

    @staticmethod
    def _load_max_file_size() -> int:
        """读取上传大小限制，单位 MB；0 表示不限。"""
        raw_value = str(os.getenv("CMDB_REMOTE_MAX_FILE_SIZE_MB", "0") or "0").strip()
        try:
            limit_mb = int(raw_value)
        except ValueError:
            return 0

        if limit_mb <= 0:
            return 0
        return limit_mb * 1024 * 1024

    def get_transfer_directory(self) -> str:
        return str(self.base_dir)

    def list_files(self, limit: int | None = None) -> list[dict]:
        self.base_dir.mkdir(parents=True, exist_ok=True)
        files: list[dict] = []
        max_items = max(1, min(limit or self.MAX_LIST_ITEMS, self.MAX_LIST_ITEMS))

        for path in sorted(
            self.base_dir.rglob("*"),
            key=lambda entry: entry.stat().st_mtime if entry.exists() else 0,
            reverse=True,
        ):
            if not path.is_file():
                continue
            files.append(self._build_file_metadata(path))
            if len(files) >= max_items:
                break
        return files

    def start_upload(
        self,
        transfer_id: str,
        file_name: str,
        file_size: int,
        relative_path: str | None = None,
    ) -> tuple[bool, dict | None, str]:
        if not transfer_id:
            return False, None, "缺少传输标识"

        file_size = max(0, int(file_size or 0))
        if self.MAX_FILE_SIZE > 0 and file_size > self.MAX_FILE_SIZE:
            return False, None, f"文件超过 {self.MAX_FILE_SIZE // (1024 * 1024)} MB 限制"

        try:
            safe_relative_path = self._sanitize_relative_upload_path(
                relative_path or file_name or f"upload-{int(time.time())}.bin"
            )
        except ValueError as exc:
            return False, None, str(exc)
        target_path = self._allocate_target_path(safe_relative_path)

        try:
            target_path.parent.mkdir(parents=True, exist_ok=True)
            handle = open(target_path, "wb")
        except Exception as exc:
            return False, None, f"创建目标文件失败: {exc}"

        state = {
            "transfer_id": transfer_id,
            "name": target_path.name,
            "path": target_path,
            "handle": handle,
            "expected_size": file_size,
            "bytes_received": 0,
            "started_at": time.time(),
        }

        with self._lock:
            previous = self._incoming_transfers.pop(transfer_id, None)
            if previous:
                self._dispose_transfer(previous, delete_partial=True)
            self._incoming_transfers[transfer_id] = state

        return True, self._build_file_metadata(target_path), "已开始接收文件"

    def append_upload_chunk(self, transfer_id: str, chunk_index: int, chunk_data: str) -> tuple[bool, dict, str]:
        with self._lock:
            state = self._incoming_transfers.get(transfer_id)

        if not state:
            return False, {}, "未找到待接收的上传任务"

        try:
            decoded = base64.b64decode(chunk_data.encode("ascii"), validate=True)
        except Exception as exc:
            self.cancel_upload(transfer_id)
            return False, {}, f"文件分块解码失败: {exc}"

        expected_size = int(state["expected_size"])
        next_size = int(state["bytes_received"]) + len(decoded)
        if expected_size and next_size > expected_size:
            self.cancel_upload(transfer_id)
            return False, {}, "接收的文件内容超过声明大小，已终止上传"

        try:
            state["handle"].write(decoded)
            state["bytes_received"] = next_size
        except Exception as exc:
            self.cancel_upload(transfer_id)
            return False, {}, f"写入文件失败: {exc}"

        progress = 100.0 if expected_size <= 0 else min(100.0, (next_size / expected_size) * 100.0)
        return True, {
            "chunk_index": int(chunk_index or 0),
            "bytes_received": next_size,
            "total_bytes": expected_size,
            "progress": round(progress, 2),
        }, "文件分块接收成功"

    def finish_upload(self, transfer_id: str) -> tuple[bool, dict | None, str]:
        with self._lock:
            state = self._incoming_transfers.pop(transfer_id, None)

        if not state:
            return False, None, "未找到待完成的上传任务"

        try:
            state["handle"].flush()
            with contextlib.suppress(Exception):
                os.fsync(state["handle"].fileno())
        except Exception:
            pass
        finally:
            with contextlib.suppress(Exception):
                state["handle"].close()

        expected_size = int(state["expected_size"])
        actual_size = int(state["bytes_received"])
        if expected_size and actual_size != expected_size:
            with contextlib.suppress(Exception):
                state["path"].unlink(missing_ok=True)
            return False, None, f"文件大小校验失败，期望 {expected_size} 字节，实际 {actual_size} 字节"

        return True, self._build_file_metadata(state["path"]), "文件上传完成"

    def cancel_upload(self, transfer_id: str):
        with self._lock:
            state = self._incoming_transfers.pop(transfer_id, None)

        if state:
            self._dispose_transfer(state, delete_partial=True)

    def resolve_download_file(self, relative_path: str) -> tuple[Path | None, dict | None, str]:
        if not relative_path:
            return None, None, "缺少下载文件标识"

        try:
            target_path = self._resolve_relative_path(relative_path)
        except ValueError as exc:
            return None, None, str(exc)

        if not target_path.exists() or not target_path.is_file():
            return None, None, "请求下载的文件不存在"

        return target_path, self._build_file_metadata(target_path), "已找到文件"

    def iter_download_chunks(self, path: Path):
        with open(path, "rb") as handle:
            chunk_index = 0
            while True:
                chunk = handle.read(self.CHUNK_SIZE)
                if not chunk:
                    break
                yield chunk_index, base64.b64encode(chunk).decode("ascii"), len(chunk)
                chunk_index += 1

    def _resolve_base_dir(self) -> Path:
        candidates = [
            Path.home() / "Downloads" / "CMDB-Agent" / "RemoteDesktopTransfers",
            APP_BASE_DIR / "RemoteDesktopTransfers",
        ]
        for candidate in candidates:
            try:
                candidate.mkdir(parents=True, exist_ok=True)
                return candidate
            except Exception:
                continue
        return Path.cwd() / "RemoteDesktopTransfers"

    def _sanitize_filename(self, file_name: str) -> str:
        cleaned = "".join(
            character if character not in '<>:"/\\|?*\r\n\t' else "_"
            for character in os.path.basename(file_name).strip()
        )
        return cleaned or f"transfer-{int(time.time())}.bin"

    def _sanitize_path_segment(self, segment: str) -> str:
        cleaned = "".join(
            character if character not in '<>:"/\\|?*\r\n\t' else "_"
            for character in str(segment or "").strip()
        ).strip(". ")
        return cleaned

    def _sanitize_relative_upload_path(self, relative_path: str) -> Path:
        normalized = str(relative_path or "").replace("\\", "/").strip("/")
        segments = [segment for segment in normalized.split("/") if segment and segment != "."]
        safe_segments: list[str] = []

        for index, segment in enumerate(segments):
            if segment == "..":
                raise ValueError("上传路径不能包含上级目录")

            if index == len(segments) - 1:
                cleaned = self._sanitize_filename(segment)
            else:
                cleaned = self._sanitize_path_segment(segment)

            if cleaned:
                safe_segments.append(cleaned)

        if not safe_segments:
            safe_segments = [self._sanitize_filename(f"upload-{int(time.time())}.bin")]

        return Path(*safe_segments)

    def _allocate_target_path(self, relative_path: Path) -> Path:
        target_path = self.base_dir / relative_path
        if not target_path.exists():
            return target_path

        stem = target_path.stem
        suffix = target_path.suffix
        parent = target_path.parent
        for index in range(1, 1000):
            candidate = parent / f"{stem}-{index}{suffix}"
            if not candidate.exists():
                return candidate
        return parent / f"{stem}-{int(time.time())}{suffix}"

    def _resolve_relative_path(self, relative_path: str) -> Path:
        normalized = relative_path.replace("\\", "/").lstrip("/")
        candidate = (self.base_dir / normalized).resolve()
        base_resolved = self.base_dir.resolve()
        try:
            candidate.relative_to(base_resolved)
        except ValueError as exc:
            raise ValueError("非法的文件路径") from exc
        return candidate

    def _build_file_metadata(self, path: Path) -> dict:
        stat = path.stat()
        return {
            "name": path.name,
            "relative_path": path.relative_to(self.base_dir).as_posix(),
            "size": int(stat.st_size),
            "modified_at": int(stat.st_mtime),
        }

    def _dispose_transfer(self, state: dict, delete_partial: bool):
        with contextlib.suppress(Exception):
            state["handle"].close()
        if delete_partial:
            with contextlib.suppress(Exception):
                state["path"].unlink(missing_ok=True)


class DisabledRemoteClipboardManager:
    """剪贴板能力降级保护，避免非核心能力拖垮整条远控会话。"""

    def __init__(self, reason: str):
        self.available = False
        self.reason = reason

    def get_text(self) -> tuple[bool, str, str]:
        return False, "", self.reason

    def set_text(self, text: str) -> tuple[bool, str]:
        return False, self.reason


class DisabledRemoteFileTransferManager:
    """文件传输能力降级保护。"""

    CHUNK_SIZE = 96 * 1024
    MAX_FILE_SIZE = 0
    MAX_LIST_ITEMS = 1

    def __init__(self, reason: str):
        self.reason = reason
        self._incoming_transfers: dict[str, dict] = {}

    def get_transfer_directory(self) -> str:
        return ""

    def list_files(self, limit: int | None = None) -> list[dict]:
        return []

    def start_upload(
        self,
        transfer_id: str,
        file_name: str,
        file_size: int,
        relative_path: str | None = None,
    ) -> tuple[bool, dict | None, str]:
        return False, None, self.reason

    def append_upload_chunk(self, transfer_id: str, chunk_index: int, chunk_data: str) -> tuple[bool, dict, str]:
        return False, {
            "progress": 0,
            "bytes_received": 0,
            "total_bytes": 0,
            "chunk_index": chunk_index,
        }, self.reason

    def finish_upload(self, transfer_id: str) -> tuple[bool, dict | None, str]:
        return False, None, self.reason

    def cancel_upload(self, transfer_id: str):
        return None

    def resolve_download_file(self, relative_path: str):
        return None, None, self.reason

    def iter_download_chunks(self, target_path: Path):
        return iter(())


class ScreenCapturer(DesktopFrameCapturer):
    """远程桌面本地兜底抓屏器，统一复用共享抓屏后端。

    后端优先级：mss(GDI) 优先——它在 headless/VMware/无显示基底场景下仍能稳定抓到
    桌面最后合成图（兼容 Win9x 时代的 GDI BitBlt 路径），不像 dxgi 那样依赖 DWM
    持续产出新帧。dxgi 仅在物理显示器正常附着时作为备选（更高刷新率时质量更好）。
    """

    def __init__(self):
        super().__init__(backend_order=("mss", "gdi", "dxgi", "wgc", "dwm", "imagegrab", "pyautogui"))
        try:
            # CAPTUREBLT 标志为捕获分层窗口而设，BitBlt 慢 3-5 倍；
            # 远控 60fps 场景去掉它（分层窗口脉冲窗口 1px 透明，无需捕获）
            import mss.windows as _mss_win
            _mss_win.CAPTUREBLT = 0
        except Exception:
            pass
        print("[RemoteDesktop] Screen capturer initialized (headless-safe: mss first, fast-bitblt)")


class DisplayResolutionManager:
    """管理 Windows 主显示器分辨率切换。"""

    ENUM_CURRENT_SETTINGS = 0xFFFFFFFF
    DISP_CHANGE_SUCCESSFUL = 0
    DM_BITSPERPEL = 0x00040000
    DM_PELSWIDTH = 0x00080000
    DM_PELSHEIGHT = 0x00100000
    DM_DISPLAYFREQUENCY = 0x00400000

    class DEVMODEW(ctypes.Structure):
        _fields_ = [
            ("dmDeviceName", wintypes.WCHAR * 32),
            ("dmSpecVersion", wintypes.WORD),
            ("dmDriverVersion", wintypes.WORD),
            ("dmSize", wintypes.WORD),
            ("dmDriverExtra", wintypes.WORD),
            ("dmFields", wintypes.DWORD),
            ("dmOrientation", ctypes.c_short),
            ("dmPaperSize", ctypes.c_short),
            ("dmPaperLength", ctypes.c_short),
            ("dmPaperWidth", ctypes.c_short),
            ("dmScale", ctypes.c_short),
            ("dmCopies", ctypes.c_short),
            ("dmDefaultSource", ctypes.c_short),
            ("dmPrintQuality", ctypes.c_short),
            ("dmColor", ctypes.c_short),
            ("dmDuplex", ctypes.c_short),
            ("dmYResolution", ctypes.c_short),
            ("dmTTOption", ctypes.c_short),
            ("dmCollate", ctypes.c_short),
            ("dmFormName", wintypes.WCHAR * 32),
            ("dmLogPixels", wintypes.WORD),
            ("dmBitsPerPel", wintypes.DWORD),
            ("dmPelsWidth", wintypes.DWORD),
            ("dmPelsHeight", wintypes.DWORD),
            ("dmDisplayFlags", wintypes.DWORD),
            ("dmDisplayFrequency", wintypes.DWORD),
            ("dmICMMethod", wintypes.DWORD),
            ("dmICMIntent", wintypes.DWORD),
            ("dmMediaType", wintypes.DWORD),
            ("dmDitherType", wintypes.DWORD),
            ("dmReserved1", wintypes.DWORD),
            ("dmReserved2", wintypes.DWORD),
            ("dmPanningWidth", wintypes.DWORD),
            ("dmPanningHeight", wintypes.DWORD),
        ]

    def __init__(self):
        self.user32 = ctypes.windll.user32
        self.available = False
        self.original_mode = None
        self._setup_windows_apis()

    def _setup_windows_apis(self):
        try:
            lpcwstr = getattr(wintypes, "LPCWSTR", wintypes.LPWSTR)
            self.user32.EnumDisplaySettingsW.argtypes = [
                lpcwstr,
                wintypes.DWORD,
                ctypes.POINTER(self.DEVMODEW),
            ]
            self.user32.EnumDisplaySettingsW.restype = wintypes.BOOL
            self.user32.ChangeDisplaySettingsW.argtypes = [
                ctypes.POINTER(self.DEVMODEW),
                wintypes.DWORD,
            ]
            self.user32.ChangeDisplaySettingsW.restype = wintypes.LONG
            self.available = True
        except Exception as exc:
            print(f"[Display] Windows display APIs unavailable: {exc}")
            self.available = False

    def _new_devmode(self):
        devmode = self.DEVMODEW()
        devmode.dmSize = ctypes.sizeof(self.DEVMODEW)
        return devmode

    def _mode_to_dict(self, mode: DEVMODEW | None):
        if mode is None:
            return None
        return {
            "width": int(mode.dmPelsWidth or 0),
            "height": int(mode.dmPelsHeight or 0),
            "bits_per_pixel": int(mode.dmBitsPerPel or 0),
            "display_frequency": int(mode.dmDisplayFrequency or 0),
        }

    def _format_resolution(self, width: int, height: int) -> str:
        return f"{int(width)}x{int(height)}"

    def get_current_mode(self):
        if not self.available:
            return None

        devmode = self._new_devmode()
        success = self.user32.EnumDisplaySettingsW(
            None,
            self.ENUM_CURRENT_SETTINGS,
            ctypes.byref(devmode),
        )
        if not success:
            return None
        return self._mode_to_dict(devmode)

    def capture_original_mode(self):
        if self.original_mode is None:
            self.original_mode = self.get_current_mode()
            if self.original_mode:
                print(
                    "[Display] Captured original resolution: "
                    f"{self._format_resolution(self.original_mode['width'], self.original_mode['height'])}"
                )
        return self.original_mode

    def list_supported_resolutions(self):
        if not self.available:
            return []

        supported = set()
        index = 0
        while True:
            devmode = self._new_devmode()
            success = self.user32.EnumDisplaySettingsW(None, index, ctypes.byref(devmode))
            if not success:
                break

            width = int(devmode.dmPelsWidth or 0)
            height = int(devmode.dmPelsHeight or 0)
            if width >= 800 and height >= 600:
                supported.add((width, height))
            index += 1

        current_mode = self.get_current_mode()
        if current_mode and current_mode["width"] > 0 and current_mode["height"] > 0:
            supported.add((current_mode["width"], current_mode["height"]))

        return [
            {"width": width, "height": height, "label": self._format_resolution(width, height)}
            for width, height in sorted(supported, key=lambda item: (item[0], item[1]))
        ]

    def _find_best_mode(self, width: int, height: int):
        candidates = []
        current_mode = self.get_current_mode() or {}
        index = 0

        while True:
            devmode = self._new_devmode()
            success = self.user32.EnumDisplaySettingsW(None, index, ctypes.byref(devmode))
            if not success:
                break

            if int(devmode.dmPelsWidth or 0) == width and int(devmode.dmPelsHeight or 0) == height:
                candidates.append(devmode)
            index += 1

        if not candidates:
            return None

        candidates.sort(
            key=lambda mode: (
                1 if int(mode.dmBitsPerPel or 0) == int(current_mode.get("bits_per_pixel") or 0) else 0,
                1 if int(mode.dmDisplayFrequency or 0) == int(current_mode.get("display_frequency") or 0) else 0,
                int(mode.dmBitsPerPel or 0),
                int(mode.dmDisplayFrequency or 0),
            ),
            reverse=True,
        )
        return candidates[0]

    def _apply_mode(self, devmode: DEVMODEW):
        devmode.dmFields = self.DM_PELSWIDTH | self.DM_PELSHEIGHT
        if int(devmode.dmBitsPerPel or 0) > 0:
            devmode.dmFields |= self.DM_BITSPERPEL
        if int(devmode.dmDisplayFrequency or 0) > 0:
            devmode.dmFields |= self.DM_DISPLAYFREQUENCY

        result = self.user32.ChangeDisplaySettingsW(ctypes.byref(devmode), 0)
        if result != self.DISP_CHANGE_SUCCESSFUL:
            return False, result
        return True, result

    def apply_resolution(self, width: int, height: int):
        if not self.available:
            return False, "当前系统不支持桌面分辨率控制", self.get_current_mode()

        target_width = max(0, int(width or 0))
        target_height = max(0, int(height or 0))
        if target_width <= 0 or target_height <= 0:
            return False, "无效的桌面分辨率参数", self.get_current_mode()

        current_mode = self.get_current_mode()
        if current_mode and current_mode["width"] == target_width and current_mode["height"] == target_height:
            return True, f"桌面分辨率已是 {self._format_resolution(target_width, target_height)}", current_mode

        target_mode = self._find_best_mode(target_width, target_height)
        if target_mode is None:
            return False, f"当前终端不支持 {self._format_resolution(target_width, target_height)}", current_mode

        success, result = self._apply_mode(target_mode)
        if not success:
            return False, f"切换桌面分辨率失败，系统返回码 {result}", current_mode

        time.sleep(0.6)
        applied_mode = self.get_current_mode() or self._mode_to_dict(target_mode)
        if (
            applied_mode
            and applied_mode["width"] == target_width
            and applied_mode["height"] == target_height
        ):
            return True, f"桌面分辨率已切换为 {self._format_resolution(target_width, target_height)}", applied_mode

        return False, "桌面分辨率切换后校验失败", applied_mode

    def restore_original_mode(self):
        original_mode = self.capture_original_mode()
        if not original_mode:
            return False, "未记录原始桌面分辨率", self.get_current_mode()

        current_mode = self.get_current_mode()
        if (
            current_mode
            and current_mode["width"] == original_mode["width"]
            and current_mode["height"] == original_mode["height"]
        ):
            return True, "桌面分辨率已恢复为原始值", current_mode

        return self.apply_resolution(original_mode["width"], original_mode["height"])


class RemoteDesktopSession:
    """远程桌面会话 - 企业级实现"""

    MODIFIER_KEYS = {'ctrl', 'shift', 'alt'}

    def __init__(self, websocket: WebSocket, session_id: str):
        self.websocket = websocket
        self.session_id = session_id
        self.windows_session_id = get_current_process_session_id()
        self.running = False
        self._resolution_restored = False
        self._initialization_errors: list[dict[str, str]] = []
        self._last_keyboard_delegate_error: str | None = None
        self.service_client = self._initialize_service_client()
        self.service_runtime_profile: dict = {}
        self.capture_runtime_mode = (
            "service_capture_pending"
            if self.service_client is not None
            else "legacy_local_capture"
        )
        self.capture_host_backend = ""
        self.capture_host_session_id: int | None = None
        self.service_managed_session_routing = self.service_client is not None
        self.capture_stack = create_capture_stack(
            runtime_mode=self.capture_runtime_mode,
            helper_available=bool(self.service_client),
        )
        self.transport_profile = TransportProfile()
        self.video_encoder_profile = VideoEncoderProfile()
        self.runtime_stack: dict = {}
        self._last_capture_runtime_signature: tuple[str, str, int | None] | None = None

        # 配置：内网环境不限帧率，60fps 流畅优先
        self.quality = 60
        self.fps = 60
        self.scale = 0.6
        self.adaptive_streaming = True
        self.wheel_speed = 1.0
        self.mouse_sensitivity = 1.0
        self.color_preset = "balanced"
        # 轻量 DWM 唤醒窗口（替代重绘全桌面的 RedrawWindow）
        self._redraw_hwnd = None
        self._redraw_thread_lock = threading.Lock()
        self._redraw_executor = None

        # 初始化模块
        self.display_manager = self._initialize_required_component(
            "display_manager",
            DisplayResolutionManager,
        )
        self._capture_original_resolution()
        self.coordinate_mapper = self._initialize_required_component(
            "coordinate_mapper",
            DPIAwareMapper,
        )
        self.input_injector = self._initialize_required_component(
            "input_injector",
            lambda: InputInjector(
                privileged_client=self.service_client,
                session_id=self.windows_session_id,
                follow_service_session=self.service_managed_session_routing,
            ),
        )
        # 输入处理专用单线程执行器：委托 helper 的命名管道调用可能阻塞，
        # 必须移出 asyncio 事件循环，否则会冻结帧推送并造成"无法操作"；
        # max_workers=1 同时保证键盘/鼠标事件按到达顺序执行。
        self._input_executor = ThreadPoolExecutor(
            max_workers=1, thread_name_prefix=f"rd-input-{self.session_id}"
        )
        # move 合并状态：队列中至多一个待处理 move（始终最新位置）
        self._pending_move_message: dict | None = None
        self._move_task_scheduled = False
        self._pending_move_lock = threading.Lock()
        self.capturer = self._initialize_required_component(
            "screen_capturer",
            ScreenCapturer,
        )
        self.capture_stack = create_capture_stack(
            self.capturer,
            runtime_mode=self.capture_runtime_mode,
            helper_backend=self.capture_host_backend if self.capture_runtime_mode.startswith("service_") else None,
            helper_session_id=self.capture_host_session_id if self.capture_runtime_mode.startswith("service_") else None,
            helper_available=bool(self.service_client),
        )
        self.clipboard_manager = self._initialize_optional_component(
            "clipboard_manager",
            RemoteClipboardManager,
            lambda exc: DisabledRemoteClipboardManager(f"远程剪贴板不可用: {exc}"),
        )
        self.file_transfer_manager = self._initialize_optional_component(
            "file_transfer_manager",
            RemoteFileTransferManager,
            lambda exc: DisabledRemoteFileTransferManager(f"远程文件传输不可用: {exc}"),
        )
        self._download_tasks: dict[str, asyncio.Task] = {}
        self._canceled_downloads: set[str] = set()

        # 统计
        self.frame_count = 0
        self.start_time = time.time()
        self.active_keys = set()
        self.modifier_states = {key: False for key in self.MODIFIER_KEYS}
        self.mouse_state = RemoteMouseState()
        self.capture_task = None
        self.send_lock = asyncio.Lock()
        self.last_input_at = time.time()
        self.last_frame_signature = None
        self.last_frame_profile_key = None
        self.last_frame_sent_at = 0.0
        self.last_visual_change_at = time.time()
        self.last_backend_rotation_at = 0.0
        self.last_skip_log_at = 0.0
        self.skipped_frame_count = 0
        self.capture_pressure = 0.0
        self.capture_profile_name = "interactive"
        self._last_sent_frame: dict | None = None  # 用于 unchanged 期间的心跳重发
        self.force_keyframe_interval = 1.0
        self.stale_frame_after_input_threshold = 3.0
        self.stale_frame_rotation_cooldown = 5.0
        self.capture_empty_count = 0
        self.last_capture_empty_at = 0.0
        self.last_capture_recreate_at = 0.0
        self.last_warning_emitted_at = 0.0
        self.last_warning_code = ""
        self.session_warning_cooldown = 8.0
        self.capture_recreate_cooldown = 5.0
        self.console_handoff_cooldown = 12.0
        self.last_console_handoff_request_at = 0.0
        self.last_console_handoff_reason = ""
        self.persistent_substrate_recovery_cooldown = 15.0
        self.last_persistent_substrate_recovery_at = 0.0
        self.last_persistent_substrate_recovery_signature = ""

        # 获取屏幕信息
        self.screen_info = self._load_screen_info()
        current_mouse_x, current_mouse_y = self._load_mouse_position()
        self.mouse_state.remember_screen_position(current_mouse_x, current_mouse_y)
        self._refresh_runtime_stack(refresh_service=True)

        self._log_session_event("session-created", "RemoteDesktopSession initialized")
        self._log_session_event("runtime-stack", json.dumps(self.runtime_stack, ensure_ascii=False, default=str))
        print(f"[RemoteDesktop] Session profile: quality={self.quality} fps={self.fps} scale={self.scale}")

    def _initialize_service_client(self):
        try:
            client = PrivilegedServiceClient()
            payload = client.ping()
            self._log_session_event("service_client", f"ready pipe={payload.get('pipe_name')}")
            return client
        except Exception as exc:
            self._log_session_event("service_client", f"degraded error={exc}")
            return None

    def _refresh_runtime_stack(self, refresh_service: bool = False):
        helper_mode = self.capture_runtime_mode.startswith("service_")
        self.capture_stack = create_capture_stack(
            getattr(self, "capturer", None),
            runtime_mode=self.capture_runtime_mode,
            helper_backend=self.capture_host_backend if helper_mode else None,
            helper_session_id=self.capture_host_session_id if helper_mode else None,
            helper_available=helper_mode or bool(self.service_client),
        )
        input_stack = self.input_injector.describe_stack() if getattr(self, "input_injector", None) else {}
        if refresh_service and self.service_client is not None:
            try:
                self.service_runtime_profile = self.service_client.get_capabilities()
            except Exception as exc:
                self.service_runtime_profile = {
                    "available": False,
                    "error": str(exc),
                }
        self.runtime_stack = {
            "architecture": "service_user_agent_split_phase2",
            "capture": self.capture_stack.to_dict(),
            "transport": self.transport_profile.to_dict(),
            "codec": self.video_encoder_profile.to_dict(),
            "input": input_stack,
            "service_runtime": self.service_runtime_profile,
            "windows_session_id": self.windows_session_id,
        }

    def _sync_capture_runtime_state(self):
        current_backend = (
            self.capture_host_backend
            if self.capture_runtime_mode.startswith("service_")
            else str(getattr(self.capturer, "capture_backend", "") or "")
        )
        runtime_signature = (
            self.capture_runtime_mode,
            current_backend,
            self.capture_host_session_id,
        )
        if runtime_signature == self._last_capture_runtime_signature:
            return
        self._last_capture_runtime_signature = runtime_signature
        self._refresh_runtime_stack(refresh_service=False)
        self._log_session_event(
            "capture-backend",
            json.dumps(
                {
                    "mode": self.capture_runtime_mode,
                    "backend": current_backend or "pending",
                    "session_id": self.capture_host_session_id,
                },
                ensure_ascii=False,
            ),
        )

    def _set_active_capture_source(
        self,
        runtime_mode: str,
        backend: str | None = None,
        session_id: int | None = None,
    ):
        self.capture_runtime_mode = str(runtime_mode or "service_capture_pending")
        self.capture_host_backend = str(backend or "")
        self.capture_host_session_id = int(session_id) if session_id is not None else None
        self._sync_capture_runtime_state()

    async def _capture_frame_via_service(self, profile: dict) -> dict | None:
        if self.service_client is None:
            return None

        try:
            # 深度诊断仅按需采集：每帧携带 desktop_state/backend_diagnostics 会让
            # 响应膨胀数十 KB，经两跳命名管道传输后把帧率拖到亚秒级。
            include_diagnostics = self.capture_empty_count >= 3
            call_started_at = time.perf_counter()
            response = await asyncio.to_thread(
                self.service_client.capture_frame,
                {
                    "quality": profile["quality"],
                    "scale": profile["scale"],
                    "previous_signature": self.last_frame_signature,
                    "include_desktop_state": include_diagnostics,
                    "include_backend_diagnostics": include_diagnostics,
                },
            )
            call_elapsed = time.perf_counter() - call_started_at
            if call_elapsed > 0.25:
                self._log_session_event(
                    "capture_service_call",
                    f"slow round-trip elapsed={call_elapsed:.3f}s "
                    f"mode={self.capture_runtime_mode}",
                )
        except Exception as exc:
            self._log_session_event("capture_service_helper", f"unavailable error={exc}")
            return None

        helper_response = response.get("helper_response") or {}
        target_session = response.get("target_session") or {}
        helper_backend = str(helper_response.get("backend") or "")
        helper_session_id = helper_response.get("session_id", target_session.get("session_id"))
        capture_context = {
            "requested_session_id": response.get("requested_session_id"),
            "target_session": target_session,
            "helper_session_id": helper_session_id,
            "helper_backend": helper_backend,
            "helper_blocker": helper_response.get("blocker"),
            "helper_error": helper_response.get("error"),
            "helper_empty": bool(helper_response.get("empty", False)),
            "display_presence": helper_response.get("display_presence") or {},
            "desktop_context": helper_response.get("desktop_context") or {},
            "backend_diagnostics": helper_response.get("backend_diagnostics") or {},
            "attempted_sessions": response.get("attempted_sessions") or [],
            "console_handoff_attempts": response.get("console_handoff_attempts") or [],
            "display_substrate_recovery_attempts": (
                response.get("display_substrate_recovery_attempts") or []
            ),
            "remote_desktop_readiness": response.get("remote_desktop_readiness") or {},
        }
        self._set_active_capture_source(
            "service_helper_session_capture",
            backend=helper_backend,
            session_id=helper_session_id,
        )

        if not helper_response.get("captured", False):
            return {
                "transport": "service_helper",
                "empty": True,
                "captured_at": float(helper_response.get("captured_at") or time.time()),
                "capture_context": capture_context,
            }

        return {
            "transport": "service_helper",
            "empty": False,
            "unchanged": bool(helper_response.get("unchanged", False)),
            "captured_at": float(helper_response.get("captured_at") or time.time()),
            "signature": helper_response.get("signature"),
            "frame": helper_response.get("frame") or {},
            "session_id": helper_session_id,
            "backend": helper_backend,
            "capture_context": capture_context,
        }

    def _ensure_redraw_executor(self):
        """重绘/脉冲专用单线程 executor，与捕获线程池隔离。"""
        if self._redraw_executor is None:
            with self._redraw_thread_lock:
                if self._redraw_executor is None:
                    self._redraw_executor = ThreadPoolExecutor(
                        max_workers=1, thread_name_prefix=f"rd-dwm-{self.session_id}"
                    )

    def _trigger_dwm_redraw(self) -> None:
        """强制应用重绘产出新像素：RedrawWindow 全桌面（后台线程调用，勿阻塞事件循环）。

        headless/VM 场景下 DWM 不主动合成新像素，RedrawWindow 让所有窗口
        立即重绘（时钟/动画/悬停效果等产生真实像素变化）。耗时 70-100ms，
        必须由 capture_loop 通过 run_in_executor 后台调用。
        """
        try:
            import ctypes

            user32 = ctypes.WinDLL("user32", use_last_error=True)
            HWND_DESKTOP = 0
            RDW_INVALIDATE = 0x0001
            RDW_UPDATENOW = 0x0100
            RDW_ALLCHILDREN = 0x0080
            user32.RedrawWindow(
                HWND_DESKTOP, None, None,
                RDW_INVALIDATE | RDW_UPDATENOW | RDW_ALLCHILDREN,
            )
        except Exception:
            pass

    def _pulse_layered_window(self) -> None:
        """轻量 DWM 合成脉冲：1px 透明分层窗口 alpha 微调（微秒级），强制 DWM 重新合成。

        不产出新像素，但让 DWM 尽快把已变化的内容合成到屏幕（配合 RedrawWindow 用）。
        """
        try:
            import ctypes

            user32 = ctypes.WinDLL("user32", use_last_error=True)
            hwnd = self._redraw_hwnd
            if not hwnd:
                with self._redraw_thread_lock:
                    if self._redraw_hwnd:
                        hwnd = self._redraw_hwnd
                    else:
                        WS_POPUP = 0x80000000
                        WS_VISIBLE = 0x10000000
                        WS_EX_LAYERED = 0x00080000
                        WS_EX_TRANSPARENT = 0x00000020
                        WS_EX_NOACTIVATE = 0x08000000
                        WS_EX_TOOLWINDOW = 0x00000080
                        HWND_TOPMOST = -1
                        SWP_NOMOVE = 0x0002
                        SWP_NOSIZE = 0x0001
                        SWP_NOACTIVATE = 0x0010
                        SWP_SHOWWINDOW = 0x0040
                        LWA_ALPHA = 0x00000002
                        hwnd = user32.CreateWindowExW(
                            WS_EX_LAYERED | WS_EX_TRANSPARENT | WS_EX_NOACTIVATE | WS_EX_TOOLWINDOW,
                            "STATIC", None, WS_POPUP | WS_VISIBLE,
                            0, 0, 1, 1, None, None, None, None,
                        )
                        if hwnd:
                            user32.SetWindowPos(
                                hwnd, HWND_TOPMOST, 0, 0, 0, 0,
                                SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE | SWP_SHOWWINDOW,
                            )
                            user32.SetLayeredWindowAttributes(hwnd, 0, 1, LWA_ALPHA)
                            self._redraw_hwnd = hwnd
            if not hwnd:
                return
            LWA_ALPHA = 0x00000002
            # alpha 在 1/2 间微调：每次分层属性更新都强制 DWM 重新合成
            self._redraw_alpha = 2 if not getattr(self, "_redraw_alpha", 0) == 2 else 1
            user32.SetLayeredWindowAttributes(hwnd, 0, self._redraw_alpha, LWA_ALPHA)
        except Exception:
            pass

    async def _capture_frame_locally(self, profile: dict) -> dict:
        """Use the interactive Agent process when the optional service is absent."""
        self._set_active_capture_source("legacy_local_capture")
        local_started_at = time.perf_counter()
        frame = await asyncio.to_thread(
            self.capturer.capture,
            quality=profile["quality"],
            scale=profile["scale"],
        )
        local_elapsed = time.perf_counter() - local_started_at
        if local_elapsed > 0.25:
            self._log_session_event(
                "local_capture_timing",
                f"slow grab+encode elapsed={local_elapsed:.3f}s "
                f"backend={getattr(self.capturer, 'capture_backend', '')}",
            )
        if not frame:
            return {
                "empty": True,
                "captured_at": time.time(),
                "capture_context": {},
            }

        signature = zlib.adler32(frame["data"].encode("ascii"))
        profile_key = (
            profile["quality"],
            round(profile["scale"], 4),
            profile["fps"],
            self.capture_profile_name,
        )
        if signature == self.last_frame_signature and profile_key == self.last_frame_profile_key:
            return {
                "unchanged": True,
                "signature": signature,
                "captured_at": time.time(),
                "capture_context": {},
            }

        return {
            "frame": frame,
            "signature": signature,
            "captured_at": time.time(),
            "capture_context": {},
        }

    def _log_session_event(self, stage: str, detail: str):
        message = f"[{self.session_id}] {stage}: {detail}"
        print(f"[RemoteDesktop]{message}")
        append_remote_runtime_log("RemoteDesktop", message)

    def _record_initialization_issue(self, stage: str, exc: Exception):
        self._initialization_errors.append({
            "stage": stage,
            "error": str(exc),
        })
        self._log_session_event(stage, f"error={exc}")
        trace_text = traceback.format_exc().strip()
        if trace_text:
            print(trace_text)
            append_remote_runtime_log("RemoteDesktop", trace_text)

    def _initialize_required_component(self, name: str, factory):
        self._log_session_event(name, "start")
        try:
            component = factory()
        except Exception as exc:
            self._record_initialization_issue(name, exc)
            raise RuntimeError(f"初始化 {name} 失败: {exc}") from exc
        self._log_session_event(name, "ready")
        return component

    def _initialize_optional_component(self, name: str, factory, fallback_factory):
        self._log_session_event(name, "start")
        try:
            component = factory()
        except Exception as exc:
            self._record_initialization_issue(name, exc)
            component = fallback_factory(exc)
            self._log_session_event(name, "degraded")
            return component
        self._log_session_event(name, "ready")
        return component

    def _capture_original_resolution(self):
        self._log_session_event("display_original_mode", "capture-start")
        try:
            self.display_manager.capture_original_mode()
        except Exception as exc:
            self._record_initialization_issue("display_original_mode", exc)
        else:
            self._log_session_event("display_original_mode", "capture-ready")

    def _default_screen_info(self) -> dict:
        user32 = getattr(ctypes.windll, "user32", None)
        if user32 is not None:
            width = int(user32.GetSystemMetrics(78) or user32.GetSystemMetrics(0) or 1920)
            height = int(user32.GetSystemMetrics(79) or user32.GetSystemMetrics(1) or 1080)
            primary_width = int(user32.GetSystemMetrics(0) or width or 1920)
            primary_height = int(user32.GetSystemMetrics(1) or height or 1080)
        else:
            width = 1920
            height = 1080
            primary_width = 1920
            primary_height = 1080
        return {
            "virtual_x": 0,
            "virtual_y": 0,
            "virtual_width": max(1, width),
            "virtual_height": max(1, height),
            "primary_width": max(1, primary_width),
            "primary_height": max(1, primary_height),
        }

    def _load_screen_info(self) -> dict:
        self._log_session_event("screen_info", "load-start")
        try:
            screen_info = self.coordinate_mapper.get_screen_info()
        except Exception as exc:
            self._record_initialization_issue("screen_info", exc)
            screen_info = self._default_screen_info()
            self._log_session_event(
                "screen_info",
                f"fallback={screen_info['virtual_width']}x{screen_info['virtual_height']}",
            )
            return screen_info
        self._log_session_event(
            "screen_info",
            f"ready={screen_info['virtual_width']}x{screen_info['virtual_height']}",
        )
        return screen_info

    def _load_mouse_position(self) -> tuple[int, int]:
        self._log_session_event("mouse_position", "load-start")
        try:
            current_mouse_x, current_mouse_y = self.coordinate_mapper.get_current_mouse_position()
            current_mouse_x, current_mouse_y = self.coordinate_mapper.clamp_screen_coordinate(
                current_mouse_x,
                current_mouse_y,
            )
        except Exception as exc:
            self._record_initialization_issue("mouse_position", exc)
            current_mouse_x = 0
            current_mouse_y = 0
            self._log_session_event("mouse_position", "fallback=0,0")
            return current_mouse_x, current_mouse_y
        self._log_session_event("mouse_position", f"ready={current_mouse_x},{current_mouse_y}")
        return current_mouse_x, current_mouse_y

    async def start(self):
        """启动会话"""
        self.running = True

        try:
            requester = "未知管理员"
            remote_address = getattr(self.websocket, "remote_address", None)
            if remote_address:
                requester = str(remote_address)
            target = os.environ.get("COMPUTERNAME") or "当前终端"
            await self._send_json({
                "type": "consent_required",
                "target": target,
            })
            approved, reason = await CONSENT_MANAGER.request_permission({
                "requester": requester,
                "origin": requester,
                "target": target,
            })
            if not approved:
                self._log_session_event("consent", f"rejected reason={reason}")
                await self._send_json({
                    "type": "consent_result",
                    "approved": False,
                    "reason": reason,
                    "message": "被控端拒绝或未响应远程控制请求",
                })
                await self.websocket.close(code=4003, reason="consent_denied")
                return

            self._log_session_event("consent", f"approved reason={reason}")
            await self._send_json({
                "type": "consent_result",
                "approved": True,
                "reason": reason,
                "message": "被控端已允许远程控制",
            })

            # 发送屏幕信息给控制端
            self._log_session_event("start", "send_screen_info")
            await self._send_screen_info()
            self._log_session_event("start", "send_session_settings")
            await self._send_session_settings()
            self._log_session_event("start", "send_capabilities")
            await self._send_capabilities()

            # 启动屏幕捕获循环
            self._log_session_event("start", "capture_loop_create")
            self.capture_task = asyncio.create_task(self.capture_loop())

            # 处理控制消息
            self._log_session_event("start", "message_loop_enter")
            await self.message_loop()
        finally:
            self.running = False
            if self.capture_task:
                self.capture_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await self.capture_task
            await asyncio.to_thread(self._restore_original_resolution)

    async def _send_screen_info(self):
        """发送屏幕信息给控制端"""
        try:
            self.screen_info = self.coordinate_mapper.get_screen_info()
        except Exception as exc:
            self._record_initialization_issue("send_screen_info", exc)
            self.screen_info = self._default_screen_info()
        desktop_mode = self._get_current_desktop_mode()
        self._refresh_runtime_stack(refresh_service=False)
        await self._send_json({
            'type': 'screen_info',
            'width': self.screen_info['virtual_width'],
            'height': self.screen_info['virtual_height'],
            'primary_width': self.screen_info['primary_width'],
            'primary_height': self.screen_info['primary_height'],
            'desktop_width': desktop_mode['width'],
            'desktop_height': desktop_mode['height'],
            'runtime_stack': self.runtime_stack,
        })
        print(f"[RemoteDesktop] Screen info sent: {self.screen_info['virtual_width']}x{self.screen_info['virtual_height']}")

    async def _send_session_settings(self):
        """向控制端同步当前会话配置。"""
        desktop_mode = self._get_current_desktop_mode()
        self._refresh_runtime_stack(refresh_service=False)
        await self._send_json({
            'type': 'session_settings',
            'quality': self.quality,
            'fps': self.fps,
            'scale': self.scale,
            'scale_percent': int(round(self.scale * 100)),
            'adaptive': self.adaptive_streaming,
            'profile': self.capture_profile_name,
            'wheel_speed': self.wheel_speed,
            'mouse_sensitivity': self.mouse_sensitivity,
            'preset': self.color_preset,
            'desktop_width': desktop_mode['width'],
            'desktop_height': desktop_mode['height'],
            'runtime_stack': self.runtime_stack,
        })

    async def _send_capabilities(self):
        desktop_resolutions = []
        try:
            desktop_resolutions = self.display_manager.list_supported_resolutions()
        except Exception as exc:
            self._record_initialization_issue("send_capabilities.desktop_resolutions", exc)

        transfer_directory = ""
        try:
            transfer_directory = self.file_transfer_manager.get_transfer_directory()
        except Exception as exc:
            self._record_initialization_issue("send_capabilities.transfer_directory", exc)

        clipboard_available = bool(getattr(self.clipboard_manager, "available", False))
        file_transfer_enabled = not isinstance(self.file_transfer_manager, DisabledRemoteFileTransferManager)
        self._refresh_runtime_stack(refresh_service=True)
        await self._send_json({
            'type': 'remote_capabilities',
            'clipboard_text': clipboard_available,
            'file_transfer': file_transfer_enabled,
            'directory_upload': file_transfer_enabled,
            'cancel_transfer': file_transfer_enabled,
            'transfer_directory': transfer_directory,
            'max_file_size': int(getattr(self.file_transfer_manager, 'MAX_FILE_SIZE', 0) or 0),
            'chunk_size': int(getattr(self.file_transfer_manager, 'CHUNK_SIZE', 96 * 1024) or 96 * 1024),
            'desktop_resolution_control': bool(desktop_resolutions),
            'desktop_resolutions': desktop_resolutions,
            'runtime_stack': self.runtime_stack,
        })

    async def capture_loop(self):
        """屏幕捕获循环"""
        self._log_session_event("capture_loop", "started")
        while self.running:
            try:
                loop_started_at = time.perf_counter()
                profile = self._select_capture_profile()

                # 主路径：同会话进程内直抓（毫秒级），避免每帧两跳服务管道往返；
                # 本地抓不到（锁屏/桌面不可见）时降级 SYSTEM 助手处理特殊桌面。
                helper_result = await self._capture_frame_locally(profile)
                if helper_result.get("empty"):
                    service_fallback = await self._capture_frame_via_service(profile)
                    if service_fallback is not None:
                        helper_result = {**service_fallback, "via_service": True}

                if helper_result is None:
                    helper_result = {
                        "empty": True,
                        "captured_at": time.time(),
                        "capture_context": {},
                    }

                if helper_result is not None:
                    frame_observed_at = float(helper_result.get("captured_at") or time.time())

                    if not self.running:
                        break

                    if helper_result.get("empty"):
                        await self._handle_capture_empty(
                            profile,
                            capture_source=(
                                "legacy_local_capture_empty"
                                if self.service_client is None
                                else "service_helper_empty"
                            ),
                            capture_context=helper_result.get("capture_context") or {},
                        )
                    elif helper_result.get("unchanged"):
                        self.capture_empty_count = 0
                        self.last_capture_empty_at = 0.0
                        self.skipped_frame_count += 1
                        unchanged_for = max(0.0, frame_observed_at - self.last_visual_change_at)
                        input_after_visual = self.last_input_at > self.last_visual_change_at
                        if (
                            input_after_visual
                            and unchanged_for >= self.stale_frame_after_input_threshold
                            and frame_observed_at - self.last_input_at >= self.stale_frame_after_input_threshold
                        ):
                            await self._handle_stale_frame(unchanged_for)
                        # 主动 DWM 唤醒：mss/GDI 抓帧本身依赖桌面合成器持续渲染。
                        # headless 场景下 DWM 可能停止产出新像素 → 用 RedrawWindow 强制重绘。
                        # 这是向日葵/ToDesk 在无显示器/VM 场景下保证画面更新的关键 hack。
                        backend = str(getattr(self.capturer, "capture_backend", "") or "")
                        if backend in ("mss", "gdi", "imagegrab", "pyautogui"):
                            now_t = time.time()
                            # 双机制：
                            # 1) RedrawWindow 后台线程@80ms——强制应用重绘产出新像素（帧率上限12.5fps）
                            # 2) 分层窗口 pulse@33ms——强制 DWM 合成保鲜（微秒级，不产像素）
                            if (
                                not hasattr(self, "_last_dwm_wakeup_at")
                                or (now_t - self._last_dwm_wakeup_at) >= 0.08
                            ):
                                self._last_dwm_wakeup_at = now_t
                                # 专用单线程 executor：与捕获线程池隔离，避免重绘任务阻塞抓帧
                                self._ensure_redraw_executor()
                                self._redraw_executor.submit(self._trigger_dwm_redraw)
                            if (
                                not hasattr(self, "_last_dwm_pulse_at")
                                or (now_t - self._last_dwm_pulse_at) >= 0.033
                            ):
                                self._last_dwm_pulse_at = now_t
                                self._ensure_redraw_executor()
                                self._redraw_executor.submit(self._pulse_layered_window)
                        # 定期心跳重发：即使像素未变，每隔一定帧数强制推一帧，
                        # 避免前端画面"看起来卡死"（与鼠标操作无关的静止场景）。
                        heartbeat_interval = max(0.5, profile.get("fps_heartbeat_seconds", 2.0))
                        if (
                            self.last_frame_sent_at == 0.0
                            or (time.time() - self.last_frame_sent_at) >= heartbeat_interval
                        ):
                            stale_frame = (
                                self._last_sent_frame
                                if self._last_sent_frame
                                else (helper_result.get("frame") or {})
                            )
                            if stale_frame:
                                self._enqueue_frame({
                                    "type": "frame",
                                    "data": stale_frame["data"],
                                    "width": stale_frame["width"],
                                    "height": stale_frame["height"],
                                })
                                self.last_frame_sent_at = time.time()
                    else:
                        frame = helper_result.get("frame") or {}
                        self.capture_empty_count = 0
                        self.last_capture_empty_at = 0.0
                        self.last_frame_signature = helper_result.get("signature")
                        self.last_frame_profile_key = (
                            profile["quality"],
                            round(profile["scale"], 4),
                            profile["fps"],
                            self.capture_profile_name,
                        )
                        self.last_visual_change_at = frame_observed_at
                        self.skipped_frame_count = 0
                        self._last_sent_frame = frame

                        # 异步发送：不再 await（避免 110KB 帧/网络慢时卡循环）
                        self._enqueue_frame({
                            "type": "frame",
                            "data": frame["data"],
                            "width": frame["width"],
                            "height": frame["height"],
                        })
                        send_elapsed = 0.0
                        self._update_capture_pressure(send_elapsed, int(frame.get("size") or 0))

                        self.frame_count += 1
                        self.last_frame_sent_at = time.time()
                    elapsed = time.perf_counter() - loop_started_at
                    frame_interval = 1.0 / profile["fps"]
                    sleep_time = max(0, frame_interval - elapsed)
                    await asyncio.sleep(sleep_time)
                    continue

                self._set_active_capture_source(
                    "service_capture_unavailable",
                    backend=self.capture_host_backend,
                    session_id=self.capture_host_session_id,
                )
                await self._handle_capture_empty(
                    profile,
                    capture_source="service_capture_unavailable",
                    capture_context={},
                )

                elapsed = time.perf_counter() - loop_started_at
                frame_interval = 1.0 / profile['fps']
                sleep_time = max(0, frame_interval - elapsed)
                await asyncio.sleep(sleep_time)

            except asyncio.CancelledError:
                break
            except Exception as e:
                self._log_session_event(
                    "capture_loop",
                    f"fatal type={type(e).__name__} error={e}",
                )
                print(f"[RemoteDesktop] Capture loop error: {e}")
                break
        self._log_session_event("capture_loop", "stopped")

    def _select_capture_profile(self):
        if not self.adaptive_streaming:
            self.capture_profile_name = "manual"
            return {
                "fps": self.fps,
                "quality": self.quality,
                "scale": self.scale,
            }

        idle_seconds = time.time() - self.last_input_at
        profile_name = "interactive"
        profile = {
            "fps": self.fps,
            "quality": self.quality,
            "scale": self.scale,
        }

        if idle_seconds >= 15:
            profile_name = "idle"
            profile = {"fps": 15, "quality": 55, "scale": min(self.scale, 0.65)}
        elif idle_seconds >= 5 or self.capture_pressure > 0.8:
            profile_name = "balanced"
            profile = {"fps": min(self.fps, 30), "quality": 62, "scale": min(self.scale, 0.75)}
        elif self.capture_pressure > 0.45:
            profile_name = "interactive-lite"
            profile = {"fps": min(self.fps, 45), "quality": 68, "scale": min(self.scale, 0.85)}

        self.capture_profile_name = profile_name
        return profile

    def _update_capture_pressure(self, send_elapsed, frame_size):
        target_interval = 1.0 / max(self.fps, 1)
        relative_send = send_elapsed / max(target_interval, 0.001)
        size_pressure = frame_size / (220 * 1024)
        sample = max(relative_send, size_pressure)
        self.capture_pressure = (self.capture_pressure * 0.7) + (min(sample, 2.0) * 0.3)

    def _virtual_display_repairable_states(self) -> set[str]:
        return {
            "installed_detached",
            "installed_missing_enablement",
            "driver_package_ready_install_pending",
        }

    def _reset_capture_stream_state(self, reason: str) -> None:
        now = time.time()
        self._log_session_event("capture-stream-reset", reason)
        self.capture_empty_count = 0
        self.last_capture_empty_at = 0.0
        self.last_frame_signature = None
        self.last_frame_profile_key = None
        self.last_frame_sent_at = 0.0
        self.last_visual_change_at = now
        self.skipped_frame_count = 0
        self.capture_pressure = 0.0

    def _substrate_recovery_signature(self, diagnostics: dict, status: dict | None = None) -> str:
        display_substrate = diagnostics.get("display_substrate") or {}
        virtual_status = status or display_substrate.get("virtual_display_status") or {}
        return "|".join(
            [
                f"blocked={1 if diagnostics.get('continuity_blocked_by_missing_substrate') else 0}",
                f"provider={display_substrate.get('provider_state') or 'unknown'}",
                f"state={virtual_status.get('provisioning_state') or diagnostics.get('virtual_display_provisioning_state') or 'unknown'}",
                f"package={1 if virtual_status.get('driver_package_complete') else 0}",
                f"tools={json.dumps(virtual_status.get('available_tools') or {}, sort_keys=True, default=str)}",
            ]
        )

    async def _ensure_persistent_display_substrate(
        self,
        reason: str,
        diagnostics: dict | None = None,
    ) -> dict:
        if self.service_client is None:
            return {
                "attempted": False,
                "recovered": False,
                "reason": reason,
                "blocker": "service_client_unavailable",
            }

        diagnostics = diagnostics or self._get_desktop_diagnostics()
        display_substrate = diagnostics.get("display_substrate") or {}
        should_probe = bool(
            diagnostics.get("continuity_blocked_by_missing_substrate")
            or diagnostics.get("likely_rdp_session")
            or not diagnostics.get("has_input_desktop")
            or str(display_substrate.get("active_capture_substrate_class") or "").lower()
            in {"remote_session_surface", "disconnected_surface"}
        )
        if not should_probe:
            return {
                "attempted": False,
                "recovered": False,
                "reason": reason,
                "blocker": "persistent_display_substrate_already_available",
            }

        try:
            status = await asyncio.to_thread(
                self.service_client.get_virtual_display_status,
                force_refresh=True,
            )
        except Exception as exc:
            self._log_session_event(
                "display-substrate-recovery",
                f"status-query-failed reason={reason} error={exc}",
            )
            return {
                "attempted": False,
                "recovered": False,
                "reason": reason,
                "blocker": "virtual_display_status_query_failed",
                "error": str(exc),
            }

        signature = self._substrate_recovery_signature(diagnostics, status)
        now = time.time()
        if (
            signature == self.last_persistent_substrate_recovery_signature
            and now - self.last_persistent_substrate_recovery_at
            < self.persistent_substrate_recovery_cooldown
        ):
            return {
                "attempted": False,
                "recovered": False,
                "reason": reason,
                "blocker": "recovery_cooldown_active",
                "virtual_display_status": status,
            }

        self.last_persistent_substrate_recovery_signature = signature
        self.last_persistent_substrate_recovery_at = now

        can_provision = bool(
            status.get("can_provision_virtual_display")
            or diagnostics.get("can_provision_virtual_display")
            or display_substrate.get("can_provision_virtual_display")
        )
        provisioning_state = str(
            status.get("provisioning_state")
            or diagnostics.get("virtual_display_provisioning_state")
            or "unknown"
        )
        if bool(status.get("attached_virtual_display")) or provisioning_state == "attached":
            self._log_session_event(
                "display-substrate-recovery",
                f"already-attached reason={reason} state={provisioning_state}",
            )
            self._refresh_runtime_stack(refresh_service=True)
            await self._recreate_capturer(f"display_substrate_attached:{reason}")
            return {
                "attempted": True,
                "recovered": True,
                "reason": reason,
                "virtual_display_status": status,
            }

        if not can_provision:
            blocker = provisioning_state
            self._log_session_event(
                "display-substrate-recovery",
                (
                    f"blocked reason={reason} state={provisioning_state} "
                    f"package_complete={status.get('driver_package_complete')} "
                    f"package_root={status.get('package_root') or ''} "
                    f"tools={status.get('available_tools') or {}}"
                ),
            )
            return {
                "attempted": False,
                "recovered": False,
                "reason": reason,
                "blocker": blocker,
                "virtual_display_status": status,
            }

        ensure_status = {}
        repair_status = {}
        try:
            ensure_status = await asyncio.to_thread(self.service_client.ensure_virtual_display)
        except Exception as exc:
            self._log_session_event(
                "display-substrate-recovery",
                f"ensure-failed reason={reason} state={provisioning_state} error={exc}",
            )
            return {
                "attempted": True,
                "recovered": False,
                "reason": reason,
                "blocker": "ensure_virtual_display_failed",
                "error": str(exc),
                "virtual_display_status": status,
            }

        ensure_state = str(ensure_status.get("provisioning_state") or provisioning_state)
        ensure_attached = bool(
            ensure_status.get("attached_virtual_display")
            or ensure_state == "attached"
        )
        if not ensure_attached and ensure_state in self._virtual_display_repairable_states():
            try:
                repair_status = await asyncio.to_thread(self.service_client.repair_virtual_display)
            except Exception as exc:
                self._log_session_event(
                    "display-substrate-recovery",
                    f"repair-failed reason={reason} state={ensure_state} error={exc}",
                )
            else:
                ensure_state = str(repair_status.get("provisioning_state") or ensure_state)
                ensure_attached = bool(
                    repair_status.get("attached_virtual_display")
                    or ensure_state == "attached"
                )

        changed = bool(ensure_status.get("changed") or repair_status.get("changed"))
        recovered = bool(ensure_attached)
        self._log_session_event(
            "display-substrate-recovery",
            (
                f"result reason={reason} changed={changed} recovered={recovered} "
                f"ensure_state={ensure_status.get('provisioning_state') or 'unknown'} "
                f"repair_state={repair_status.get('provisioning_state') or 'none'}"
            ),
        )

        if changed or recovered:
            self._refresh_runtime_stack(refresh_service=True)
            self._reset_capture_stream_state(f"display_substrate_recovery:{reason}")
            await self._recreate_capturer(f"display_substrate_recovery:{reason}")

        return {
            "attempted": True,
            "recovered": recovered,
            "changed": changed,
            "reason": reason,
            "virtual_display_status": repair_status or ensure_status or status,
            "ensure_status": ensure_status,
            "repair_status": repair_status,
        }

    def _get_desktop_diagnostics(self) -> dict:
        current_session_id = (
            int(self.capture_host_session_id)
            if self.capture_host_session_id is not None
            else get_current_process_session_id()
        )
        active_console_session = -1
        has_input_desktop = False
        helper_context = None
        target_session = None
        preferred_capture_host_session = None
        display_substrate = dict(self.service_runtime_profile.get("display_substrate") or {})
        remote_desktop_readiness = dict(
            self.service_runtime_profile.get("remote_desktop_readiness") or {}
        )
        continuity_blocked_by_missing_substrate = bool(
            self.service_runtime_profile.get("continuity_blocked_by_missing_substrate", False)
        )

        with contextlib.suppress(Exception):
            active_console_session = int(self.capturer.kernel32.WTSGetActiveConsoleSessionId())
        with contextlib.suppress(Exception):
            has_input_desktop = bool(self.capturer._has_input_desktop())

        if self.capture_runtime_mode.startswith("service_") and self.service_client is not None:
            try:
                service_context = self.service_client.invoke_admin_action(
                    "describe_desktop_context",
                    {
                        "session_id": self.capture_host_session_id,
                        "reason": "remote_desktop_diagnostics",
                    },
                )
                helper_context = service_context.get("helper_context")
                target_session = service_context.get("target_session")
                preferred_capture_host_session = service_context.get("preferred_capture_host_session")
                remote_desktop_readiness = (
                    service_context.get("remote_desktop_readiness")
                    or remote_desktop_readiness
                )
                if not isinstance(remote_desktop_readiness, dict):
                    remote_desktop_readiness = {}
                display_substrate = service_context.get("display_substrate") or display_substrate
                service_capture_continuity = service_context.get("capture_continuity") or {}
                continuity_blocked_by_missing_substrate = bool(
                    service_capture_continuity.get("continuity_blocked_by_missing_substrate", False)
                    or display_substrate.get("continuity_blocked_by_missing_substrate", False)
                )
                console_session = service_context.get("console_session") or {}
                active_console_session = int(console_session.get("session_id") or active_console_session)
                if target_session and target_session.get("session_id") is not None:
                    current_session_id = int(target_session.get("session_id"))

                helper_desktop_context = (helper_context or {}).get("desktop_context") or {}
                if "input_desktop_available" in helper_desktop_context:
                    has_input_desktop = bool(helper_desktop_context.get("input_desktop_available"))
            except Exception as exc:
                self._log_session_event("desktop-diagnostics", f"service-query-failed error={exc}")

        likely_rdp_session = (
            current_session_id >= 0
            and active_console_session not in (-1, 0xFFFFFFFF, current_session_id)
        )
        return {
            "current_session_id": current_session_id,
            "active_console_session": active_console_session,
            "has_input_desktop": has_input_desktop,
            "likely_rdp_session": likely_rdp_session,
            "capture_backend": (
                self.capture_host_backend
                if self.capture_runtime_mode.startswith("service_")
                else str(getattr(self.capturer, "capture_backend", "") or "")
            ),
            "capture_runtime_mode": self.capture_runtime_mode,
            "target_session": target_session,
            "helper_context": helper_context,
            "preferred_capture_host_session": preferred_capture_host_session,
            "display_substrate": display_substrate,
            "remote_desktop_readiness": remote_desktop_readiness,
            "continuity_grade": str(
                remote_desktop_readiness.get("continuity_grade")
                or self.service_runtime_profile.get("continuity_grade")
                or "best_effort_rdp_only"
            ),
            "continuity_blockers": list(
                remote_desktop_readiness.get("continuity_blockers")
                or self.service_runtime_profile.get("continuity_blockers")
                or []
            ),
            "continuity_requirements": list(
                remote_desktop_readiness.get("continuity_requirements")
                or self.service_runtime_profile.get("continuity_requirements")
                or []
            ),
            "commercial_continuity_blocker": str(
                remote_desktop_readiness.get("commercial_continuity_blocker")
                or self.service_runtime_profile.get("commercial_continuity_blocker")
                or ""
            ),
            "continuity_blocked_by_missing_substrate": continuity_blocked_by_missing_substrate,
            "persistent_ready_for_unattended": bool(
                display_substrate.get("persistent_ready_for_unattended", False)
            ),
            "can_provision_virtual_display": bool(
                display_substrate.get("can_provision_virtual_display", False)
            ),
            "virtual_display_provisioning_state": str(
                display_substrate.get("virtual_display_provisioning_state")
                or "not_supported_in_current_build"
            ),
        }

    async def _emit_session_warning(self, code: str, message: str, **extra):
        now = time.time()
        if (
            code == self.last_warning_code
            and now - self.last_warning_emitted_at < self.session_warning_cooldown
        ):
            return

        self.last_warning_code = code
        self.last_warning_emitted_at = now
        payload = {
            "type": "session_warning",
            "code": code,
            "message": message,
            "timestamp": int(now * 1000),
        }
        payload.update(extra)
        self._log_session_event("warning", f"{code}: {message}")
        with contextlib.suppress(Exception):
            await self._send_json(payload)

    async def _request_console_capture_handoff(
        self,
        reason: str,
        diagnostics: dict,
    ) -> bool:
        if self.service_client is None:
            return False
        if diagnostics.get("continuity_blocked_by_missing_substrate"):
            return False

        now = time.time()
        normalized_reason = str(reason or "capture_continuity_recovery").strip() or "capture_continuity_recovery"
        if (
            normalized_reason == self.last_console_handoff_reason
            and now - self.last_console_handoff_request_at < self.console_handoff_cooldown
        ):
            return False

        target_session = diagnostics.get("target_session") or {}
        preferred_capture_host = diagnostics.get("preferred_capture_host_session") or {}
        target_session_id = target_session.get("session_id")
        if target_session_id in (None, ""):
            target_session_id = preferred_capture_host.get("session_id")
        if target_session_id in (None, ""):
            target_session_id = self.capture_host_session_id

        payload = {
            "reason": normalized_reason,
            "wait_seconds": 4.0,
        }
        if target_session_id not in (None, ""):
            payload["session_id"] = int(target_session_id)

        try:
            response = await asyncio.to_thread(
                self.service_client.invoke_admin_action,
                "handoff_session_to_console",
                payload,
            )
        except Exception as exc:
            self._log_session_event("console-handoff", f"request-failed reason={normalized_reason} error={exc}")
            return False

        result = response.get("result") or {}
        attempted = bool(result.get("attempted"))
        success = bool(result.get("success"))
        rate_limited = bool(result.get("rate_limited"))
        self.last_console_handoff_request_at = now
        self.last_console_handoff_reason = normalized_reason
        self._log_session_event(
            "console-handoff",
            (
                f"reason={normalized_reason} attempted={attempted} success={success} "
                f"rate_limited={rate_limited} target_session={payload.get('session_id', 'auto')} "
                f"failure_reason={result.get('failure_reason') or 'none'} "
                f"returncode={result.get('returncode') if result.get('returncode') is not None else 'none'}"
            ),
        )
        if not success:
            return False

        self._refresh_runtime_stack(refresh_service=True)
        await asyncio.sleep(1.0)
        await self._recreate_capturer(f"console_handoff:{normalized_reason}")
        return True

    async def _recreate_capturer(self, reason: str) -> bool:
        now = time.time()
        if now - self.last_capture_recreate_at < self.capture_recreate_cooldown:
            return False

        self.last_capture_recreate_at = now
        self._log_session_event("capture-recreate", reason)
        self.capture_empty_count = 0
        self.last_capture_empty_at = 0.0
        self.last_frame_signature = None
        self.last_frame_profile_key = None
        self.last_frame_sent_at = 0.0
        self.last_visual_change_at = now
        self.skipped_frame_count = 0
        self.capture_pressure = 0.0

        if self.service_client is not None:
            self._refresh_runtime_stack(refresh_service=True)
            helper_target_session_id = None
            preferred_session_id = self.service_runtime_profile.get("preferred_capture_host_session_id")
            if preferred_session_id not in (None, ""):
                helper_target_session_id = int(preferred_session_id)
            elif self.capture_host_session_id is not None:
                helper_target_session_id = int(self.capture_host_session_id)
            payload = {
                "wait_seconds": 3.0,
            }
            if helper_target_session_id is not None:
                payload["session_id"] = int(helper_target_session_id)
            try:
                response = await asyncio.to_thread(
                    self.service_client.invoke_admin_action,
                    "restart_capture_helper",
                    payload,
                )
                result = response.get("result") or {}
                helper_session = result.get("session") or {}
                resolved_session_id = helper_session.get("session_id", helper_target_session_id)
                self._set_active_capture_source(
                    "service_capture_pending",
                    backend="",
                    session_id=int(resolved_session_id) if resolved_session_id is not None else None,
                )
                self._refresh_runtime_stack(refresh_service=True)
                return True
            except Exception as exc:
                self._log_session_event("capture-recreate", f"service-restart-failed error={exc}")
                self._set_active_capture_source(
                    "service_capture_unavailable",
                    backend=self.capture_host_backend,
                    session_id=helper_target_session_id,
                )
                self._refresh_runtime_stack(refresh_service=True)
                return False

        self._log_session_event(
            "capture-recreate",
            "recreating local capture backend",
        )
        self.capturer = ScreenCapturer()
        self._set_active_capture_source("legacy_local_capture")
        self._refresh_runtime_stack(refresh_service=False)
        return True

    async def _handle_capture_empty(
        self,
        profile: dict,
        capture_source: str = "service_helper_empty",
        capture_context: dict | None = None,
    ):
        now = time.time()
        self.capture_empty_count += 1
        self.last_capture_empty_at = now
        diagnostics = self._get_desktop_diagnostics()
        capture_context = capture_context or {}
        helper_blocker = str(capture_context.get("helper_blocker") or "").strip()
        context_readiness = capture_context.get("remote_desktop_readiness")
        if isinstance(context_readiness, dict) and context_readiness:
            diagnostics["remote_desktop_readiness"] = context_readiness
            diagnostics["continuity_grade"] = str(
                context_readiness.get("continuity_grade")
                or diagnostics.get("continuity_grade")
                or "best_effort_rdp_only"
            )
            diagnostics["continuity_blockers"] = list(
                context_readiness.get("continuity_blockers")
                or diagnostics.get("continuity_blockers")
                or []
            )
            diagnostics["continuity_requirements"] = list(
                context_readiness.get("continuity_requirements")
                or diagnostics.get("continuity_requirements")
                or []
            )
            diagnostics["commercial_continuity_blocker"] = str(
                context_readiness.get("commercial_continuity_blocker")
                or diagnostics.get("commercial_continuity_blocker")
                or ""
            )

        if self.capture_empty_count in (1, 3, 10) or self.capture_empty_count % 30 == 0:
            self._log_session_event(
                "capture-empty",
                (
                    f"count={self.capture_empty_count} profile={self.capture_profile_name} "
                    f"fps={profile['fps']} session={diagnostics['current_session_id']} "
                    f"active_console={diagnostics['active_console_session']} "
                    f"input_desktop={'yes' if diagnostics['has_input_desktop'] else 'no'} "
                    f"backend={diagnostics['capture_backend'] or 'none'} "
                    f"source={capture_source} blocker={helper_blocker or diagnostics['commercial_continuity_blocker'] or 'none'} "
                    f"grade={diagnostics['continuity_grade']}"
                ),
            )

        if self.capture_empty_count < 3:
            return

        substrate_recovery = await self._ensure_persistent_display_substrate(
            "capture_empty",
            diagnostics,
        )
        if substrate_recovery.get("recovered") or substrate_recovery.get("changed"):
            return

        if diagnostics["likely_rdp_session"]:
            if await self._request_console_capture_handoff(
                "capture_empty_rdp_surface",
                diagnostics,
            ):
                return
            if not diagnostics["continuity_blocked_by_missing_substrate"] and await self._recreate_capturer(
                "capture_empty_rdp_surface_rebind",
            ):
                return

        if diagnostics["continuity_blocked_by_missing_substrate"]:
            await self._emit_session_warning(
                "missing_persistent_display_substrate",
                (
                    "检测到当前机器缺少持续显示基底，Windows 可能已经不再持续产出新的桌面像素。"
                    "这不是简单重试或切换 GDI/ImageGrab 能解决的问题；需要持久显示基底，例如物理显示器或受支持的虚拟显示/IDD。"
                ),
                capture_backend=diagnostics["capture_backend"],
                capture_runtime_mode=diagnostics["capture_runtime_mode"],
                windows_session_id=diagnostics["current_session_id"],
                active_console_session=diagnostics["active_console_session"],
                input_desktop=diagnostics["has_input_desktop"],
                capture_empty_count=self.capture_empty_count,
                capture_source=capture_source,
                capture_context=capture_context,
                continuity_grade=diagnostics["continuity_grade"],
                continuity_blockers=diagnostics["continuity_blockers"],
                continuity_requirements=diagnostics["continuity_requirements"],
                commercial_continuity_blocker=diagnostics["commercial_continuity_blocker"],
                display_substrate=diagnostics["display_substrate"],
                can_provision_virtual_display=diagnostics["can_provision_virtual_display"],
                virtual_display_provisioning_state=diagnostics["virtual_display_provisioning_state"],
                substrate_recovery=substrate_recovery,
            )
        elif diagnostics["likely_rdp_session"] or not diagnostics["has_input_desktop"]:
            await self._emit_session_warning(
                "rdp_capture_stalled",
                "检测到当前远控运行在非 console 交互桌面中，若 MSTSC 被最小化或断开，Windows 可能冻结画面采集并拒绝鼠标键盘注入。",
                capture_backend=diagnostics["capture_backend"],
                capture_runtime_mode=diagnostics["capture_runtime_mode"],
                windows_session_id=diagnostics["current_session_id"],
                active_console_session=diagnostics["active_console_session"],
                input_desktop=diagnostics["has_input_desktop"],
                capture_empty_count=self.capture_empty_count,
                capture_source=capture_source,
                capture_context=capture_context,
                continuity_grade=diagnostics["continuity_grade"],
                continuity_blockers=diagnostics["continuity_blockers"],
                continuity_requirements=diagnostics["continuity_requirements"],
                commercial_continuity_blocker=diagnostics["commercial_continuity_blocker"],
                display_substrate=diagnostics["display_substrate"],
                substrate_recovery=substrate_recovery,
            )
        else:
            await self._emit_session_warning(
                "capture_stalled",
                "远程桌面画面采集连续失败，服务侧会话/桌面拓扑需要进一步检查。",
                capture_backend=diagnostics["capture_backend"],
                capture_runtime_mode=diagnostics["capture_runtime_mode"],
                windows_session_id=diagnostics["current_session_id"],
                active_console_session=diagnostics["active_console_session"],
                input_desktop=diagnostics["has_input_desktop"],
                capture_empty_count=self.capture_empty_count,
                capture_source=capture_source,
                capture_context=capture_context,
                continuity_grade=diagnostics["continuity_grade"],
                continuity_blockers=diagnostics["continuity_blockers"],
                continuity_requirements=diagnostics["continuity_requirements"],
                commercial_continuity_blocker=diagnostics["commercial_continuity_blocker"],
                display_substrate=diagnostics["display_substrate"],
                substrate_recovery=substrate_recovery,
            )

    async def _handle_stale_frame(self, unchanged_for: float):
        diagnostics = self._get_desktop_diagnostics()
        recent_input_age = max(0.0, time.time() - self.last_input_at)
        substrate_recovery = await self._ensure_persistent_display_substrate(
            "stale_frame_after_input",
            diagnostics,
        )
        if substrate_recovery.get("recovered") or substrate_recovery.get("changed"):
            return

        if diagnostics["likely_rdp_session"] and not diagnostics["continuity_blocked_by_missing_substrate"]:
            if await self._request_console_capture_handoff(
                "stale_frame_after_input",
                diagnostics,
            ):
                return
            if await self._recreate_capturer("stale_frame_rdp_surface_rebind"):
                return
        if diagnostics["continuity_blocked_by_missing_substrate"]:
            message = (
                "检测到控制端已有输入，但系统当前缺少持续显示基底，桌面像素链路可能已经停止刷新。"
                "需要持久显示基底来保证在 RDP 最小化、断开、锁屏和 Session 切换后仍持续出图。"
            )
            code = "missing_persistent_display_substrate"
        else:
            message = (
                "检测到控制端已有输入，但远端画面在最近几秒内没有任何变化。"
                "如果当前会话宿主在 RDP 桌面中，最小化或断开 MSTSC 后，Windows 可能冻结截图链路。"
            )
            code = "rdp_capture_stalled" if diagnostics["likely_rdp_session"] else "capture_stalled"
        await self._emit_session_warning(
            code,
            message,
            capture_backend=diagnostics["capture_backend"],
            capture_runtime_mode=diagnostics["capture_runtime_mode"],
            windows_session_id=diagnostics["current_session_id"],
            active_console_session=diagnostics["active_console_session"],
            input_desktop=diagnostics["has_input_desktop"],
            skipped_frame_count=self.skipped_frame_count,
            unchanged_seconds=round(unchanged_for, 2),
            input_age_seconds=round(recent_input_age, 2),
            display_substrate=diagnostics["display_substrate"],
            substrate_recovery=substrate_recovery,
        )

    def handle_mouse_in_executor(self, message: dict):
        """把鼠标处理调度到输入专用线程，避免阻塞事件循环。"""
        loop = asyncio.get_running_loop()

        # 高频 move 合并（coalescing）：单线程输入队列若被每秒数十个 move 淹没，
        # 后续 click/drag 会延迟数十秒才执行（表现为"点不动/拖不动"）。
        # 这里始终只保留最新位置，队列中同时至多存在一个待处理 move。
        action_name = str(message.get("action") or "").strip().lower()
        if action_name in ("move", "mousemove"):
            with self._pending_move_lock:
                self._pending_move_message = message
                if self._move_task_scheduled:
                    return
                self._move_task_scheduled = True

            def _drain_moves():
                while True:
                    with self._pending_move_lock:
                        pending = self._pending_move_message
                        self._pending_move_message = None
                        if pending is None:
                            self._move_task_scheduled = False
                            return
                    try:
                        self.handle_mouse(pending)
                    except Exception as exc:
                        self._log_session_event(
                            "input_executor",
                            f"mouse failed type={type(exc).__name__} error={exc}",
                        )

            loop.run_in_executor(self._input_executor, _drain_moves)
            return

        def _safe_mouse():
            try:
                self.handle_mouse(message)
            except Exception as exc:
                self._log_session_event(
                    "input_executor",
                    f"mouse failed type={type(exc).__name__} error={exc}",
                )

        loop.run_in_executor(self._input_executor, _safe_mouse)

    def handle_keyboard_in_executor(self, message: dict):
        """把键盘处理调度到输入专用线程，避免阻塞事件循环。"""
        loop = asyncio.get_running_loop()

        def _safe_keyboard():
            try:
                self.handle_keyboard(message)
            except Exception as exc:
                self._log_session_event(
                    "input_executor",
                    f"keyboard failed type={type(exc).__name__} error={exc}",
                )

        loop.run_in_executor(self._input_executor, _safe_keyboard)

    async def message_loop(self):
        """消息处理循环"""
        try:
            while self.running:
                try:
                    message = await self.websocket.receive_text()
                except asyncio.CancelledError:
                    raise
                except Exception:
                    # 连接关闭或传输错误：正常结束会话
                    break
                try:
                    data = json.loads(message)
                except Exception as exc:
                    # 单条损坏消息只跳过，不允许终止整个会话
                    self._log_session_event(
                        "message_loop",
                        f"bad message skipped type={type(exc).__name__} error={exc}",
                    )
                    continue
                await self.handle_control(data)

        except Exception as e:
            print(f"[RemoteDesktop] Message loop error: {e}")
        finally:
            self.running = False

    async def handle_control(self, message: dict):
        """处理控制消息"""
        msg_type = message.get('type')

        try:
            if msg_type == 'mouse':
                self.last_input_at = time.time()
                log_remote_desktop_flow(
                    self.session_id,
                    "handle_control_mouse",
                    (
                        f"action={message.get('action')} button={message.get('button')} "
                        f"buttons={message.get('buttons')} "
                        f"normalized=({message.get('normalized_x')},{message.get('normalized_y')}) "
                        f"delta=({message.get('delta_x')},{message.get('delta_y')}) "
                        f"wheel={message.get('wheel_steps')}"
                    ),
                )
                self.handle_mouse_in_executor(message)
            elif msg_type == 'keyboard':
                self.last_input_at = time.time()
                self.handle_keyboard_in_executor(message)
            elif msg_type == 'settings':
                self.last_input_at = time.time()
                await self.handle_settings(message)
            elif msg_type == 'clipboard_get':
                self.last_input_at = time.time()
                await self.handle_clipboard_get()
            elif msg_type == 'clipboard_set':
                self.last_input_at = time.time()
                await self.handle_clipboard_set(message)
            elif msg_type == 'file_list_request':
                self.last_input_at = time.time()
                await self.handle_file_list_request(message)
            elif msg_type == 'file_upload_start':
                self.last_input_at = time.time()
                await self.handle_file_upload_start(message)
            elif msg_type == 'file_upload_chunk':
                self.last_input_at = time.time()
                await self.handle_file_upload_chunk(message)
            elif msg_type == 'file_upload_finish':
                self.last_input_at = time.time()
                await self.handle_file_upload_finish(message)
            elif msg_type == 'file_upload_cancel':
                self.last_input_at = time.time()
                await self.handle_file_upload_cancel(message)
            elif msg_type == 'file_download_request':
                self.last_input_at = time.time()
                await self.handle_file_download_request(message)
            elif msg_type == 'file_download_cancel':
                self.last_input_at = time.time()
                await self.handle_file_download_cancel(message)
            elif msg_type == 'ping':
                await self._send_json({
                    'type': 'pong',
                    'timestamp': message.get('timestamp')
                })
        except Exception as e:
            if msg_type == 'mouse':
                log_remote_desktop_flow(self.session_id, "handle_control_mouse_error", str(e))
            print(f"[RemoteDesktop] Control handling error: {e}")

    async def handle_settings(self, message: dict):
        """处理远程桌面会话设置。"""
        quality = self._clamp_int(message.get('quality'), self.quality, 35, 90)
        fps = self._clamp_int(message.get('fps'), self.fps, 4, 30)
        scale_percent = self._clamp_int(
            message.get('scale_percent'),
            int(round(self.scale * 100)),
            40,
            100,
        )
        adaptive = bool(message.get('adaptive', self.adaptive_streaming))
        wheel_speed = self._clamp_float(message.get('wheel_speed'), self.wheel_speed, 0.5, 3.0)
        mouse_sensitivity = self._clamp_float(
            message.get('mouse_sensitivity'),
            self.mouse_sensitivity,
            0.5,
            2.0,
        )
        preset = str(message.get('preset', self.color_preset) or self.color_preset).strip().lower()
        if preset not in {'smooth', 'balanced', 'high', 'custom'}:
            preset = 'custom'
        desktop_width = self._clamp_int(message.get('desktop_width'), 0, 0, 16384)
        desktop_height = self._clamp_int(message.get('desktop_height'), 0, 0, 16384)

        self.quality = quality
        self.fps = fps
        self.scale = scale_percent / 100.0
        self.adaptive_streaming = adaptive
        self.wheel_speed = wheel_speed
        self.mouse_sensitivity = mouse_sensitivity
        self.color_preset = preset
        self.capture_pressure = 0.0
        self.last_frame_profile_key = None
        self.last_frame_signature = None
        self.last_frame_sent_at = 0.0
        self.last_skip_log_at = 0.0

        resolution_result = None
        if desktop_width > 0 and desktop_height > 0:
            current_desktop = self._get_current_desktop_mode()
            if (
                current_desktop['width'] != desktop_width
                or current_desktop['height'] != desktop_height
            ):
                resolution_result = await asyncio.to_thread(
                    self.display_manager.apply_resolution,
                    desktop_width,
                    desktop_height,
                )
                if resolution_result[0]:
                    self._refresh_display_metrics()
                    await self._send_screen_info()
                else:
                    print(f"[RemoteDesktop] Desktop resolution apply failed: {resolution_result[1]}")

        print(
            f"[RemoteDesktop] Session settings updated: quality={self.quality} fps={self.fps} "
            f"scale={self.scale:.2f} adaptive={self.adaptive_streaming} "
            f"wheel={self.wheel_speed:.2f} sensitivity={self.mouse_sensitivity:.2f} preset={self.color_preset}"
        )

        await self._send_session_settings()
        if resolution_result is not None:
            current_desktop = resolution_result[2] or self._get_current_desktop_mode()
            await self._send_json({
                'type': 'settings_result',
                'category': 'desktop_resolution',
                'success': bool(resolution_result[0]),
                'message': str(resolution_result[1]),
                'desktop_width': int(current_desktop.get('width', 0) or 0),
                'desktop_height': int(current_desktop.get('height', 0) or 0),
            })

    async def handle_clipboard_get(self):
        success, text, message = await asyncio.to_thread(self.clipboard_manager.get_text)
        if success:
            await self._send_json({
                'type': 'clipboard_data',
                'success': True,
                'text': text,
                'message': message,
            })
            return

        await self._send_json({
            'type': 'clipboard_result',
            'success': False,
            'operation': 'get',
            'message': message,
        })

    async def handle_clipboard_set(self, message: dict):
        text = str(message.get('text') or '')
        success, result_message = await asyncio.to_thread(self.clipboard_manager.set_text, text)
        await self._send_json({
            'type': 'clipboard_result',
            'success': success,
            'operation': 'set',
            'message': result_message,
            'text_length': len(text),
        })

    async def handle_file_list_request(self, message: dict):
        limit = self._clamp_int(message.get('limit'), 50, 1, self.file_transfer_manager.MAX_LIST_ITEMS)
        files = await asyncio.to_thread(self.file_transfer_manager.list_files, limit)
        await self._send_json({
            'type': 'file_list',
            'files': files,
            'transfer_directory': self.file_transfer_manager.get_transfer_directory(),
        })

    async def handle_file_upload_start(self, message: dict):
        transfer_id = str(message.get('transfer_id') or '').strip()
        file_name = str(message.get('file_name') or '').strip()
        relative_path = str(message.get('relative_path') or '').strip()
        try:
            file_size = max(0, int(message.get('file_size') or 0))
        except (TypeError, ValueError):
            file_size = 0
        if self.file_transfer_manager.MAX_FILE_SIZE > 0:
            file_size = min(file_size, self.file_transfer_manager.MAX_FILE_SIZE)
        success, file_meta, result_message = await asyncio.to_thread(
            self.file_transfer_manager.start_upload,
            transfer_id,
            file_name,
            file_size,
            relative_path,
        )
        await self._send_file_transfer_status(
            direction='upload',
            transfer_id=transfer_id,
            status='started' if success else 'failed',
            message=result_message,
            file_meta=file_meta,
            progress=0,
            bytes_value=0,
            total_bytes=file_size,
        )

    async def handle_file_upload_chunk(self, message: dict):
        transfer_id = str(message.get('transfer_id') or '').strip()
        chunk_index = self._clamp_int(message.get('chunk_index'), 0, 0, 1_000_000)
        chunk_data = str(message.get('data') or '')
        success, payload, result_message = await asyncio.to_thread(
            self.file_transfer_manager.append_upload_chunk,
            transfer_id,
            chunk_index,
            chunk_data,
        )
        await self._send_file_transfer_status(
            direction='upload',
            transfer_id=transfer_id,
            status='progress' if success else 'failed',
            message=result_message,
            progress=payload.get('progress', 0),
            bytes_value=payload.get('bytes_received', 0),
            total_bytes=payload.get('total_bytes', 0),
            chunk_index=payload.get('chunk_index', chunk_index),
        )

    async def handle_file_upload_finish(self, message: dict):
        transfer_id = str(message.get('transfer_id') or '').strip()
        success, file_meta, result_message = await asyncio.to_thread(
            self.file_transfer_manager.finish_upload,
            transfer_id,
        )
        await self._send_file_transfer_status(
            direction='upload',
            transfer_id=transfer_id,
            status='completed' if success else 'failed',
            message=result_message,
            file_meta=file_meta,
            progress=100 if success else 0,
            bytes_value=file_meta.get('size', 0) if file_meta else 0,
            total_bytes=file_meta.get('size', 0) if file_meta else 0,
        )

        if success:
            await self.handle_file_list_request({'limit': 100})

    async def handle_file_upload_cancel(self, message: dict):
        transfer_id = str(message.get('transfer_id') or '').strip()
        await asyncio.to_thread(self.file_transfer_manager.cancel_upload, transfer_id)
        await self._send_file_transfer_status(
            direction='upload',
            transfer_id=transfer_id,
            status='canceled',
            message='上传已取消',
        )

    async def handle_file_download_request(self, message: dict):
        transfer_id = str(message.get('transfer_id') or f"download-{int(time.time() * 1000)}").strip()
        relative_path = str(message.get('relative_path') or '').strip()
        if not transfer_id:
            await self._send_file_transfer_status(
                direction='download',
                transfer_id='',
                status='failed',
                message='缺少下载传输标识',
            )
            return

        previous = self._download_tasks.get(transfer_id)
        if previous and not previous.done():
            previous.cancel()

        self._canceled_downloads.discard(transfer_id)
        task = asyncio.create_task(self._stream_file_download(transfer_id, relative_path))
        self._download_tasks[transfer_id] = task
        task.add_done_callback(lambda finished, current_id=transfer_id: self._finalize_download_task(current_id, finished))

    async def handle_file_download_cancel(self, message: dict):
        transfer_id = str(message.get('transfer_id') or '').strip()
        if not transfer_id:
            return

        self._canceled_downloads.add(transfer_id)
        task = self._download_tasks.get(transfer_id)
        if task and not task.done():
            task.cancel()

    async def _stream_file_download(self, transfer_id: str, relative_path: str):
        target_path, file_meta, result_message = await asyncio.to_thread(
            self.file_transfer_manager.resolve_download_file,
            relative_path,
        )
        if not target_path or not file_meta:
            await self._send_file_transfer_status(
                direction='download',
                transfer_id=transfer_id,
                status='failed',
                message=result_message,
            )
            return

        await self._send_file_transfer_status(
            direction='download',
            transfer_id=transfer_id,
            status='started',
            message='开始发送文件',
            file_meta=file_meta,
            progress=0,
            bytes_value=0,
            total_bytes=file_meta['size'],
        )

        bytes_sent = 0
        chunk_count = 0
        iterator = self.file_transfer_manager.iter_download_chunks(target_path)
        try:
            while True:
                if transfer_id in self._canceled_downloads:
                    await self._send_file_transfer_status(
                        direction='download',
                        transfer_id=transfer_id,
                        status='canceled',
                        message='下载已取消',
                        file_meta=file_meta,
                        progress=0 if file_meta['size'] <= 0 else round((bytes_sent / file_meta['size']) * 100.0, 2),
                        bytes_value=bytes_sent,
                        total_bytes=file_meta['size'],
                    )
                    return

                item = await asyncio.to_thread(lambda: next(iterator, None))
                if item is None:
                    break

                chunk_index, chunk_data, raw_size = item
                bytes_sent += raw_size
                chunk_count = chunk_index + 1
                progress = 100.0 if file_meta['size'] <= 0 else min(100.0, (bytes_sent / file_meta['size']) * 100.0)
                await self._send_json({
                    'type': 'file_download_chunk',
                    'transfer_id': transfer_id,
                    'file_name': file_meta['name'],
                    'relative_path': file_meta['relative_path'],
                    'chunk_index': chunk_index,
                    'data': chunk_data,
                    'bytes_sent': bytes_sent,
                    'total_bytes': file_meta['size'],
                    'progress': round(progress, 2),
                })
                await asyncio.sleep(0)

            await self._send_json({
                'type': 'file_download_complete',
                'transfer_id': transfer_id,
                'file_name': file_meta['name'],
                'relative_path': file_meta['relative_path'],
                'chunk_count': chunk_count,
                'total_bytes': file_meta['size'],
            })
            await self._send_file_transfer_status(
                direction='download',
                transfer_id=transfer_id,
                status='completed',
                message='文件下载完成',
                file_meta=file_meta,
                progress=100,
                bytes_value=file_meta['size'],
                total_bytes=file_meta['size'],
            )
        except asyncio.CancelledError:
            if transfer_id in self._canceled_downloads:
                await self._send_file_transfer_status(
                    direction='download',
                    transfer_id=transfer_id,
                    status='canceled',
                    message='下载已取消',
                    file_meta=file_meta,
                    progress=0 if file_meta['size'] <= 0 else round((bytes_sent / file_meta['size']) * 100.0, 2),
                    bytes_value=bytes_sent,
                    total_bytes=file_meta['size'],
                )
                return
            raise
        except Exception as exc:
            await self._send_file_transfer_status(
                direction='download',
                transfer_id=transfer_id,
                status='failed',
                message=f"文件下载失败: {exc}",
                file_meta=file_meta,
                progress=0,
                bytes_value=bytes_sent,
                total_bytes=file_meta['size'],
            )
        finally:
            with contextlib.suppress(Exception):
                iterator.close()

    def _finalize_download_task(self, transfer_id: str, task: asyncio.Task):
        self._download_tasks.pop(transfer_id, None)
        self._canceled_downloads.discard(transfer_id)
        try:
            task.result()
        except asyncio.CancelledError:
            pass
        except Exception as exc:
            print(f"[RemoteDesktop] Download task error: {exc}")

    async def _send_file_transfer_status(
        self,
        direction: str,
        transfer_id: str,
        status: str,
        message: str,
        file_meta: dict | None = None,
        progress: float | int = 0,
        bytes_value: int = 0,
        total_bytes: int = 0,
        chunk_index: int | None = None,
    ):
        payload = {
            'type': 'file_transfer_status',
            'direction': direction,
            'transfer_id': transfer_id,
            'status': status,
            'message': message,
            'progress': progress,
            'bytes': bytes_value,
            'total_bytes': total_bytes,
        }
        if file_meta:
            payload['file'] = file_meta
        if chunk_index is not None:
            payload['chunk_index'] = chunk_index
        await self._send_json(payload)

    def _clamp_int(self, value, default: int, minimum: int, maximum: int) -> int:
        try:
            parsed = int(value)
        except (TypeError, ValueError):
            parsed = default
        return max(minimum, min(maximum, parsed))

    def _clamp_float(self, value, default: float, minimum: float, maximum: float) -> float:
        try:
            parsed = float(value)
        except (TypeError, ValueError):
            parsed = default
        return max(minimum, min(maximum, parsed))

    def handle_mouse(self, message: dict):
        """处理鼠标事件，统一走协议解析和会话状态机。"""
        mouse_message = parse_mouse_message(message)
        if not mouse_message:
            log_remote_desktop_flow(
                self.session_id,
                "handle_mouse_parse",
                f"ignored invalid message={json.dumps(message, ensure_ascii=False, default=str)}",
            )
            return

        log_remote_desktop_flow(
            self.session_id,
            "handle_mouse_parse",
            (
                f"action={mouse_message.action.value} button={mouse_message.button.value} "
                f"normalized=({mouse_message.normalized_x:.4f},{mouse_message.normalized_y:.4f}) "
                f"buttons_mask={mouse_message.buttons_mask} "
                f"delta=({mouse_message.delta_x},{mouse_message.delta_y}) "
                f"wheel_steps={mouse_message.wheel_steps}"
            ),
        )

        self.coordinate_mapper.refresh_metrics()
        self.mouse_state.remember_position(
            mouse_message.normalized_x,
            mouse_message.normalized_y,
        )

        screen_x, screen_y = self.coordinate_mapper.denormalize_coordinate(
            mouse_message.normalized_x,
            mouse_message.normalized_y
        )
        log_remote_desktop_flow(
            self.session_id,
            "handle_mouse_denormalize",
            f"screen=({screen_x},{screen_y})",
        )

        events = self._build_mouse_events(mouse_message, screen_x, screen_y)
        for event in events:
            log_remote_desktop_flow(
                self.session_id,
                "handle_mouse_dispatch",
                (
                    f"type={event.type.value} button={event.button.value} "
                    f"target=({event.x},{event.y}) "
                    f"normalized=({event.normalized_x},{event.normalized_y}) "
                    f"delta={event.delta}"
                ),
            )
            self.input_injector.inject_mouse_event(event)

    def _build_mouse_events(self, mouse_message, screen_x: int, screen_y: int) -> list[MouseEvent]:
        move_event = self._create_mouse_event(
            MouseEventType.MOVE,
            screen_x,
            screen_y,
            mouse_message,
        )

        if mouse_message.action == RemoteMouseAction.MOVE:
            self.mouse_state.remember_screen_position(screen_x, screen_y)
            return [move_event]

        if mouse_message.action == RemoteMouseAction.DRAG_MOVE:
            drag_target_x, drag_target_y = self._resolve_drag_target(mouse_message, screen_x, screen_y)
            self.mouse_state.drag_in_progress = True
            self.mouse_state.remember_screen_position(drag_target_x, drag_target_y)
            return [MouseEvent(MouseEventType.MOVE, drag_target_x, drag_target_y)]

        if mouse_message.action == RemoteMouseAction.BUTTON_DOWN:
            self.mouse_state.drag_in_progress = False
            if self.mouse_state.press(mouse_message.button):
                self.mouse_state.remember_screen_position(screen_x, screen_y)
                print(f"[Input] Mouse down: x={screen_x} y={screen_y} button={mouse_message.button.value}")
                return [
                    self._create_mouse_event(
                        MouseEventType.DOWN,
                        screen_x,
                        screen_y,
                        mouse_message,
                        button=mouse_message.button,
                    )
                ]
            self.mouse_state.remember_screen_position(screen_x, screen_y)
            return [move_event]

        if mouse_message.action == RemoteMouseAction.BUTTON_UP:
            release_x, release_y = screen_x, screen_y
            was_dragging = self.mouse_state.drag_in_progress
            if was_dragging:
                release_x = self.mouse_state.current_screen_x
                release_y = self.mouse_state.current_screen_y

            if self.mouse_state.release(mouse_message.button):
                self.mouse_state.remember_screen_position(release_x, release_y)
                if not self.mouse_state.pressed_buttons:
                    self.mouse_state.drag_in_progress = False
                print(f"[Input] Mouse up: x={release_x} y={release_y} button={mouse_message.button.value}")
                return [
                    self._create_mouse_event(
                        MouseEventType.UP,
                        release_x,
                        release_y,
                        mouse_message,
                        button=mouse_message.button,
                        allow_normalized=not was_dragging,
                    )
                ]
            self.mouse_state.remember_screen_position(release_x, release_y)
            return [
                self._create_mouse_event(
                    MouseEventType.MOVE,
                    release_x,
                    release_y,
                    mouse_message,
                    allow_normalized=not was_dragging,
                )
            ]

        if mouse_message.action == RemoteMouseAction.WHEEL:
            if mouse_message.wheel_steps == 0:
                self.mouse_state.remember_screen_position(screen_x, screen_y)
                return [move_event]
            self.mouse_state.remember_screen_position(screen_x, screen_y)
            wheel_delta = self._scale_wheel_steps(mouse_message.wheel_steps)
            return [
                self._create_mouse_event(
                    MouseEventType.WHEEL,
                    screen_x,
                    screen_y,
                    mouse_message,
                    delta=wheel_delta,
                )
            ]

        return []

    def _create_mouse_event(
        self,
        event_type,
        screen_x: int,
        screen_y: int,
        mouse_message,
        button=MouseButton.LEFT,
        delta=0,
        allow_normalized: bool = True,
    ) -> MouseEvent:
        normalized_x = mouse_message.normalized_x if allow_normalized else None
        normalized_y = mouse_message.normalized_y if allow_normalized else None
        return MouseEvent(
            event_type,
            screen_x,
            screen_y,
            button=button,
            delta=delta,
            normalized_x=normalized_x,
            normalized_y=normalized_y,
        )

    def _resolve_drag_target(self, mouse_message, fallback_x: int, fallback_y: int) -> tuple[int, int]:
        if mouse_message.delta_x == 0 and mouse_message.delta_y == 0:
            return self.coordinate_mapper.clamp_screen_coordinate(
                self.mouse_state.current_screen_x,
                self.mouse_state.current_screen_y,
            )

        scaled_delta_x = self._scale_relative_delta(mouse_message.delta_x)
        scaled_delta_y = self._scale_relative_delta(mouse_message.delta_y)
        next_x = self.mouse_state.current_screen_x + scaled_delta_x
        next_y = self.mouse_state.current_screen_y + scaled_delta_y
        return self.coordinate_mapper.clamp_screen_coordinate(next_x, next_y)

    def _scale_relative_delta(self, delta: int) -> int:
        if delta == 0:
            return 0

        scaled = delta * self.mouse_sensitivity
        rounded = int(round(scaled))
        if rounded == 0:
            return 1 if scaled > 0 else -1
        return rounded

    def _scale_wheel_steps(self, wheel_steps: int) -> int:
        if wheel_steps == 0:
            return 0

        scaled = wheel_steps * self.wheel_speed
        rounded = int(round(scaled))
        if rounded == 0:
            rounded = 1 if scaled > 0 else -1
        return max(-24, min(24, rounded))

    def handle_keyboard(self, message: dict):
        """处理键盘事件"""
        action = message.get('action')
        key = str(message.get('key', '') or '').lower()
        text = str(message.get('text', '') or '')
        ctrl_key = bool(message.get('ctrlKey', False))
        shift_key = bool(message.get('shiftKey', False))
        alt_key = bool(message.get('altKey', False))

        if not action:
            return

        target_modifiers = {
            'ctrl': ctrl_key,
            'shift': shift_key,
            'alt': alt_key
        }

        print(f"[Input] Keyboard event: action={action} key={key}")

        if self.service_client is not None:
            try:
                self._invoke_service_input_action(
                    "inject_keyboard_event",
                    {
                        "action": action,
                        "key": key,
                        "text": text,
                        "ctrlKey": ctrl_key,
                        "shiftKey": shift_key,
                        "altKey": alt_key,
                    },
                )
                self._track_keyboard_state(action, key, target_modifiers)
                self._last_keyboard_delegate_error = None
                return
            except Exception as exc:
                error_text = str(exc)
                if error_text != self._last_keyboard_delegate_error:
                    self._log_session_event(
                        "keyboard_service_delegate",
                        f"fallback action={action} key={key or 'n/a'} error={error_text}",
                    )
                    self._last_keyboard_delegate_error = error_text

        if action == 'keydown':
            self._sync_modifier_states(target_modifiers)
            if key in self.MODIFIER_KEYS:
                if not self.modifier_states[key]:
                    pyautogui.keyDown(key)
                    self.modifier_states[key] = True
            elif key and key not in self.active_keys:
                pyautogui.keyDown(key)
                self.active_keys.add(key)
        elif action == 'keyup':
            if key in self.MODIFIER_KEYS:
                if self.modifier_states[key]:
                    pyautogui.keyUp(key)
                    self.modifier_states[key] = False
            elif key and key in self.active_keys:
                pyautogui.keyUp(key)
                self.active_keys.discard(key)

            self._sync_modifier_states(target_modifiers)
        elif action == 'press':
            modifiers = [name for name, enabled in target_modifiers.items() if enabled]
            if key and modifiers and key not in self.MODIFIER_KEYS:
                pyautogui.hotkey(*modifiers, key)
            elif key:
                pyautogui.press(key)
        elif action == 'type':
            if text:
                try:
                    from Input.input_controller import send_unicode_text

                    # 逐键模拟会被终端输入法拦截成拼音组合，必须走 UNICODE 注入
                    if not send_unicode_text(text):
                        pyautogui.typewrite(text, interval=0.01)
                except ImportError:
                    pyautogui.typewrite(text, interval=0.01)

    def _invoke_service_input_action(self, action: str, payload: dict | None = None) -> dict:
        if self.service_client is None:
            raise RuntimeError("service client unavailable")

        request_payload = dict(payload or {})
        if (
            not self.service_managed_session_routing
            and self.windows_session_id is not None
            and request_payload.get("session_id") in (None, "")
        ):
            request_payload["session_id"] = int(self.windows_session_id)
        return self.service_client.invoke_admin_action(action, request_payload)

    def _track_keyboard_state(self, action: str, key: str, target_modifiers: dict):
        if action == 'keydown':
            for modifier, enabled in target_modifiers.items():
                self.modifier_states[modifier] = enabled
            if key in self.MODIFIER_KEYS:
                self.modifier_states[key] = True
            elif key:
                self.active_keys.add(key)
            return

        if action == 'keyup':
            if key in self.MODIFIER_KEYS:
                self.modifier_states[key] = False
            elif key:
                self.active_keys.discard(key)
            for modifier, enabled in target_modifiers.items():
                self.modifier_states[modifier] = enabled
            return

        if action in {'press', 'type'}:
            for modifier, enabled in target_modifiers.items():
                self.modifier_states[modifier] = enabled

    def _sync_modifier_states(self, target_modifiers: dict):
        """根据前端状态同步修饰键，避免组合键卡死。"""
        for key, enabled in target_modifiers.items():
            current = self.modifier_states.get(key, False)
            if enabled and not current:
                pyautogui.keyDown(key)
                self.modifier_states[key] = True
            elif not enabled and current:
                pyautogui.keyUp(key)
                self.modifier_states[key] = False

    def _release_pressed_inputs(self):
        """会话结束时兜底释放已按下的按键。"""
        if self.service_client is not None:
            try:
                self._invoke_service_input_action("release_input_state")
            except Exception as exc:
                self._log_session_event("release_input_state", f"service_fallback error={exc}")

        pressed_buttons = self.mouse_state.release_all()
        if pressed_buttons:
            self.input_injector.release_mouse_buttons(pressed_buttons)

        for key in list(self.active_keys):
            try:
                pyautogui.keyUp(key)
            except Exception:
                pass
        self.active_keys.clear()

        for key, enabled in list(self.modifier_states.items()):
            if not enabled:
                continue
            try:
                pyautogui.keyUp(key)
            except Exception:
                pass
            self.modifier_states[key] = False

    def _enqueue_frame(self, frame_payload: dict) -> bool:
        """异步发送帧：fire-and-forget，不阻塞 capture_loop。
        二进制帧协议：[1B type][4B frameId][4B width][4B height][4B jpegLen][jpeg bytes]
        替代旧 base64-in-JSON，节省 ~33% 带宽 + 解码更快。
        """
        try:
            asyncio.create_task(self._send_binary_frame(frame_payload))
            return True
        except RuntimeError:
            return False

    async def _send_binary_frame(self, frame_payload: dict):
        """串行化 WebSocket 发送二进制帧。"""
        import struct
        async with self.send_lock:
            try:
                data_b64 = frame_payload.get("data", "")
                width = int(frame_payload.get("width") or 0)
                height = int(frame_payload.get("height") or 0)
                jpeg_bytes = base64.b64decode(data_b64)
                frame_id = self._binary_frame_seq = getattr(self, "_binary_frame_seq", 0) + 1
                header = struct.pack(">BIIII", 0x02, frame_id, width, height, len(jpeg_bytes))
                await self.websocket.send_bytes(header + jpeg_bytes)
            except Exception as exc:
                self._log_session_event("send_binary_frame", f"error: {exc}")

    async def _send_json(self, payload: dict):
        """串行化 WebSocket 发送控制消息（text JSON）。屏幕帧用 _send_binary_frame。"""
        async with self.send_lock:
            await self.websocket.send_json(payload)

    def _get_current_desktop_mode(self):
        desktop_mode = self.display_manager.get_current_mode()
        if desktop_mode:
            return desktop_mode
        return {
            'width': self.screen_info.get('primary_width', 0),
            'height': self.screen_info.get('primary_height', 0),
            'bits_per_pixel': 0,
            'display_frequency': 0,
        }

    def _refresh_display_metrics(self):
        self.coordinate_mapper.refresh_metrics()
        self.input_injector.refresh_virtual_desktop_metrics()
        try:
            self.screen_info = self.coordinate_mapper.get_screen_info()
        except Exception as exc:
            self._record_initialization_issue("refresh_display_metrics.screen_info", exc)
            self.screen_info = self._default_screen_info()
        try:
            mouse_x, mouse_y = self.coordinate_mapper.get_current_mouse_position()
            mouse_x, mouse_y = self.coordinate_mapper.clamp_screen_coordinate(mouse_x, mouse_y)
        except Exception as exc:
            self._record_initialization_issue("refresh_display_metrics.mouse_position", exc)
            mouse_x, mouse_y = 0, 0
        self.mouse_state.remember_screen_position(mouse_x, mouse_y)
        self._refresh_runtime_stack(refresh_service=False)
        self.last_frame_signature = None
        self.last_frame_profile_key = None
        self.last_frame_sent_at = 0.0
        self.last_skip_log_at = 0.0

    def _restore_original_resolution(self):
        if self._resolution_restored:
            return
        self._resolution_restored = True
        success, message, _ = self.display_manager.restore_original_mode()
        if success:
            print(f"[RemoteDesktop] {message}")
        else:
            print(f"[RemoteDesktop] Restore desktop resolution skipped: {message}")

    def stop(self):
        """停止会话"""
        self.running = False
        for transfer_id in list(self.file_transfer_manager._incoming_transfers.keys()):
            self.file_transfer_manager.cancel_upload(transfer_id)
        for transfer_id, task in list(self._download_tasks.items()):
            self._canceled_downloads.add(transfer_id)
            if not task.done():
                task.cancel()
        self._release_pressed_inputs()
        self._restore_original_resolution()
        self.input_injector.stop()
        try:
            self._input_executor.shutdown(wait=False, cancel_futures=True)
        except Exception:
            pass
        with contextlib.suppress(Exception):
            if self.capturer is not None:
                self.capturer.close()
        print(f"[RemoteDesktop] Session stopped: id={self.session_id}")


# 导出
__all__ = ['RemoteDesktopSession']
