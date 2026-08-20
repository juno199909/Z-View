"""
Z-View Agent - 正式启动入口
将历史 `.backup` 文件包装为稳定的可执行入口，并暴露统一的 `app`。
"""

from __future__ import annotations

import argparse
import contextlib
import ctypes
import csv
import importlib
from ctypes import wintypes
from datetime import datetime
from importlib.machinery import SourceFileLoader
from importlib.util import module_from_spec, spec_from_loader
from functools import lru_cache
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import threading
import time

from agent_consent_ipc import (
    build_high_integrity_helper_launch_command,
    build_ui_launch_command,
    get_current_process_session_id,
)
from console_utils import enable_utf8_stdio, safe_console_print
from RemoteAgent.high_integrity_helper import HighIntegritySessionHelperRuntime
from RemoteAgent.privileged_client import PrivilegedServiceClient
from RemoteAgent.runtime import RemoteDesktopUserAgentRuntime
from RemoteService.service_runtime import ServiceRuntime
from RemoteService.session_manager import LegacySessionBridge, SessionManager


_CORE_MODULE_CANDIDATES = (
    Path(__file__).with_name("cmdb_agent_core.py"),
    Path(__file__).with_suffix(".py.backup"),
)

if getattr(sys, "frozen", False):
    _APP_DIR = Path(sys.executable).resolve().parent
else:
    _APP_DIR = Path(__file__).resolve().parent

if os.name == "nt":
    _PROGRAM_DATA_DIR = Path(os.environ.get("ProgramData", r"C:\ProgramData"))
    _RUNTIME_LOG_FILE = _PROGRAM_DATA_DIR / "CMDB-Agent" / "logs" / "agent-runtime.log"
    _ROLE_RUNTIME_DIR = _PROGRAM_DATA_DIR / "CMDB-Agent" / "runtime"
else:
    _RUNTIME_LOG_FILE = _APP_DIR / "logs" / "agent-runtime.log"
    _ROLE_RUNTIME_DIR = _APP_DIR / "runtime-data"

os.chdir(_APP_DIR)
enable_utf8_stdio()
print = safe_console_print
SERVICE_NAME = "CMDB-Agent"
SERVICE_DISPLAY_NAME = "Z-View Agent"
SERVICE_DESCRIPTION = "Z-View unified endpoint agent service"
SERVICE_CONTROL_COMMANDS = {
    "install",
    "update",
    "remove",
    "start",
    "stop",
    "restart",
    "debug",
}
_INSTANCE_MUTEX_HANDLES: dict[str, object] = {}
_ROLE_MUTEX_ACCESS = 0x00100000
_PROCESS_QUERY_INFORMATION = 0x0400
_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
_SYNCHRONIZE_ACCESS = 0x00100000
_WAIT_OBJECT_0 = 0x00000000
_WAIT_TIMEOUT = 0x00000102
_USER_SESSION_SUPERVISOR_RETRY_SECONDS = 15
_USER_SESSION_SUPERVISOR_LAUNCH_COOLDOWN_SECONDS = 20
_ROLE_HEARTBEAT_INTERVAL_SECONDS = 5
_ROLE_HEARTBEAT_STALE_SECONDS = 45
_SERVICE_RUNTIME: ServiceRuntime | None = None


def _configure_kernel32_process_apis():
    if os.name != "nt":
        return

    try:
        kernel32 = ctypes.windll.kernel32
        kernel32.CreateMutexW.argtypes = [wintypes.LPVOID, wintypes.BOOL, wintypes.LPCWSTR]
        kernel32.CreateMutexW.restype = wintypes.HANDLE
        kernel32.OpenMutexW.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.LPCWSTR]
        kernel32.OpenMutexW.restype = wintypes.HANDLE
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel32.CloseHandle.restype = wintypes.BOOL
        kernel32.ProcessIdToSessionId.argtypes = [wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)]
        kernel32.ProcessIdToSessionId.restype = wintypes.BOOL
        kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel32.OpenProcess.restype = wintypes.HANDLE
        kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        kernel32.WaitForSingleObject.restype = wintypes.DWORD
    except Exception:
        return


_configure_kernel32_process_apis()


class _WTSSessionInfo(ctypes.Structure):
    _fields_ = [
        ("SessionId", wintypes.DWORD),
        ("pWinStationName", wintypes.LPWSTR),
        ("State", wintypes.DWORD),
    ]


WTS_CURRENT_SERVER_HANDLE = wintypes.HANDLE(0)
WTS_ACTIVE = 0
WTS_CONNECTED = 1
INVALID_SESSION_ID = 0xFFFFFFFF
WTS_USERNAME = 5
WTS_DOMAIN_NAME = 7


@lru_cache(maxsize=1)
def load_core_module():
    bundled_import_error = None
    try:
        bundled_module = importlib.import_module("cmdb_agent_core")
        if hasattr(bundled_module, "CONFIG") and hasattr(bundled_module, "SOFTWARE_CONFIG"):
            return bundled_module
        bundled_import_error = ImportError("Bundled cmdb_agent_core is missing required configuration attributes")
    except Exception as exc:
        bundled_import_error = exc

    if getattr(sys, "frozen", False):
        raise ImportError("Unable to load bundled cmdb_agent_core") from bundled_import_error

    core_module_path = next((candidate for candidate in _CORE_MODULE_CANDIDATES if candidate.exists()), None)
    if core_module_path is None:
        candidate_text = ", ".join(str(candidate) for candidate in _CORE_MODULE_CANDIDATES)
        raise ImportError(f"无法找到 Agent 核心模块，候选路径: {candidate_text}")

    loader = SourceFileLoader("cmdb_agent_core", str(core_module_path))
    spec = spec_from_loader(loader.name, loader)

    if spec is None:
        raise ImportError(f"无法为 {core_module_path} 创建模块加载规范")

    module = module_from_spec(spec)
    loader.exec_module(module)
    return module


def __getattr__(name: str):
    return getattr(load_core_module(), name)


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="Z-View",
        description="Z-View unified executable entrypoint",
    )
    parser.add_argument(
        "--run-agent",
        action="store_true",
        help="run the backend agent worker directly",
    )
    parser.add_argument(
        "--consent-ui",
        action="store_true",
        help="run the user-session tray and remote-control consent UI",
    )
    parser.add_argument(
        "--user-session-agent",
        action="store_true",
        help="run the user-session remote desktop agent with tray UI",
    )
    parser.add_argument(
        "--no-remote-desktop",
        action="store_true",
        help="disable the remote desktop server for this worker role",
    )
    parser.add_argument(
        "--disable-session-supervisor",
        action="store_true",
        help=argparse.SUPPRESS,
    )
    parser.add_argument(
        "--service-host",
        action="store_true",
        help=argparse.SUPPRESS,
    )
    parser.add_argument(
        "--restart-user-session-agent",
        action="store_true",
        help=argparse.SUPPRESS,
    )
    parser.add_argument(
        "--high-integrity-helper",
        action="store_true",
        help=argparse.SUPPRESS,
    )
    parser.add_argument(
        "--target-session-id",
        type=int,
        help=argparse.SUPPRESS,
    )
    parser.add_argument(
        "--restart-wait-seconds",
        type=int,
        default=3,
        help=argparse.SUPPRESS,
    )
    return parser


def keep_worker_alive():
    """Keep non-web workers alive after background modules are started."""
    print("[Agent] Background modules active; entering keepalive loop")
    try:
        while True:
            time.sleep(30)
    except KeyboardInterrupt:
        print("[Agent] Keepalive loop interrupted, shutting down")


def build_role_mutex_name(
    role_name: str,
    session_bound: bool = False,
    session_id: int | None = None,
) -> str:
    session_suffix = ""
    if session_bound:
        resolved_session_id = session_id
        if resolved_session_id is None:
            resolved_session_id = get_current_process_session_id()
        session_suffix = f"-session-{resolved_session_id if resolved_session_id is not None else 'unknown'}"

    return f"Global\\CMDB-Agent-{role_name}{session_suffix}"


def append_runtime_log(component: str, message: str):
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{timestamp}] [{component}] {message}"

    try:
        _RUNTIME_LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(_RUNTIME_LOG_FILE, "a", encoding="utf-8") as file:
            file.write(line + "\n")
    except Exception:
        pass


def log_runtime_event(component: str, message: str):
    line = f"[{component}] {message}"
    print(line)
    append_runtime_log(component, message)


def resolve_role_runtime_path(
    role_name: str,
    session_bound: bool = False,
    session_id: int | None = None,
) -> Path:
    resolved_session_id = session_id
    if session_bound and resolved_session_id is None:
        resolved_session_id = get_current_process_session_id()

    suffix = ""
    if session_bound:
        suffix = f"-session-{resolved_session_id if resolved_session_id is not None else 'unknown'}"

    safe_role_name = role_name.replace("\\", "-").replace("/", "-").replace(":", "-")
    return _ROLE_RUNTIME_DIR / f"{safe_role_name}{suffix}.json"


def get_process_session_id(process_id: int) -> int | None:
    if os.name != "nt":
        return None

    try:
        session_id = wintypes.DWORD()
        if ctypes.windll.kernel32.ProcessIdToSessionId(int(process_id), ctypes.byref(session_id)):
            return int(session_id.value)
    except Exception:
        return None
    return None


def is_process_running(process_id: int) -> bool:
    if process_id <= 0:
        return False

    if os.name != "nt":
        return True

    kernel32 = ctypes.windll.kernel32
    desired_access = _SYNCHRONIZE_ACCESS | _PROCESS_QUERY_LIMITED_INFORMATION
    handle = kernel32.OpenProcess(desired_access, False, int(process_id))
    if not handle:
        handle = kernel32.OpenProcess(
            _SYNCHRONIZE_ACCESS | _PROCESS_QUERY_INFORMATION,
            False,
            int(process_id),
        )
    if not handle:
        return False

    try:
        wait_result = kernel32.WaitForSingleObject(handle, 0)
        return wait_result == _WAIT_TIMEOUT
    finally:
        kernel32.CloseHandle(handle)


def write_role_runtime_state(
    role_name: str,
    session_bound: bool = False,
    session_id: int | None = None,
    process_id: int | None = None,
):
    state_path = resolve_role_runtime_path(role_name, session_bound=session_bound, session_id=session_id)
    state_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "role": role_name,
        "session_bound": bool(session_bound),
        "session_id": session_id,
        "pid": int(process_id or os.getpid()),
        "updated_at": time.time(),
        "updated_at_iso": datetime.now().isoformat(timespec="seconds"),
    }
    temp_path = state_path.with_suffix(".tmp")
    with open(temp_path, "w", encoding="utf-8") as file:
        json.dump(payload, file, ensure_ascii=False, indent=2)
    os.replace(temp_path, state_path)


def read_role_runtime_state(
    role_name: str,
    session_bound: bool = False,
    session_id: int | None = None,
) -> dict[str, object] | None:
    state_path = resolve_role_runtime_path(role_name, session_bound=session_bound, session_id=session_id)
    if not state_path.exists():
        return None

    try:
        with open(state_path, "r", encoding="utf-8") as file:
            payload = json.load(file)
    except Exception:
        return None

    if not isinstance(payload, dict):
        return None
    return payload


def delete_role_runtime_state(
    role_name: str,
    session_bound: bool = False,
    session_id: int | None = None,
):
    state_path = resolve_role_runtime_path(role_name, session_bound=session_bound, session_id=session_id)
    with contextlib.suppress(FileNotFoundError):
        state_path.unlink()


def has_recent_role_runtime_state(
    role_name: str,
    session_bound: bool = False,
    session_id: int | None = None,
    exclude_pid: int | None = None,
) -> bool:
    payload = read_role_runtime_state(role_name, session_bound=session_bound, session_id=session_id)
    if not payload:
        return False

    pid = int(payload.get("pid") or 0)
    if pid <= 0 or (exclude_pid is not None and pid == exclude_pid):
        return False

    updated_at = float(payload.get("updated_at") or 0.0)
    if updated_at <= 0 or (time.time() - updated_at) > _ROLE_HEARTBEAT_STALE_SECONDS:
        return False

    if not is_process_running(pid):
        return False

    if session_bound:
        expected_session_id = session_id
        if expected_session_id is None:
            expected_session_id = get_current_process_session_id()
        actual_session_id = get_process_session_id(pid)
        if (
            expected_session_id is not None
            and actual_session_id is not None
            and int(actual_session_id) != int(expected_session_id)
        ):
            return False

    return True


def list_frozen_executable_processes() -> list[dict[str, int | str]]:
    if os.name != "nt" or not getattr(sys, "frozen", False):
        return []

    image_name = Path(sys.executable).name
    creation_flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        completed = subprocess.run(
            ["tasklist", "/fo", "csv", "/nh", "/fi", f"IMAGENAME eq {image_name}"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="ignore",
            creationflags=creation_flags,
            timeout=8,
            check=False,
        )
    except Exception:
        return []

    if completed.returncode not in (0, 1):
        return []

    processes: list[dict[str, int | str]] = []
    for row in csv.reader(completed.stdout.splitlines()):
        if len(row) < 4:
            continue
        try:
            pid = int(str(row[1]).replace(",", "").strip())
            session_id = int(str(row[3]).replace(",", "").strip())
        except Exception:
            continue

        processes.append(
            {
                "image_name": str(row[0]).strip(),
                "pid": pid,
                "session_id": session_id,
                "session_name": str(row[2]).strip(),
            }
        )

    return processes


_ZVIEW_CMDLINE_CACHE: dict[str, Any] = {"at": 0.0, "data": {}}


def _get_zview_process_command_lines() -> dict[int, tuple[int, str]]:
    """返回 {pid: (session_id, command_line)}，带短 TTL 缓存。"""
    now = time.time()
    if now - float(_ZVIEW_CMDLINE_CACHE.get("at") or 0.0) < 5.0:
        cached = _ZVIEW_CMDLINE_CACHE.get("data") or {}
        return dict(cached)

    data: dict[int, tuple[int, str]] = {}
    try:
        completed = subprocess.run(
            [
                "powershell",
                "-NoProfile",
                "-Command",
                "Get-CimInstance Win32_Process -Filter \"Name='Z-View.exe'\" | "
                "ForEach-Object { Write-Output ('{0}|{1}|{2}' -f $_.ProcessId, $_.SessionId, $_.CommandLine) }",
            ],
            capture_output=True,
            text=True,
            timeout=25,
            check=False,
        )
        for line in (completed.stdout or "").splitlines():
            parts = line.split("|", 2)
            if len(parts) < 3:
                continue
            try:
                pid = int(parts[0].strip())
                process_session_id = int(parts[1].strip())
            except ValueError:
                continue
            data[pid] = (process_session_id, parts[2])
    except Exception:
        pass

    _ZVIEW_CMDLINE_CACHE["at"] = now
    _ZVIEW_CMDLINE_CACHE["data"] = dict(data)
    return dict(data)


def list_user_session_agent_pids(session_id: int | None, exclude_pid: int | None = None) -> list[int]:
    """按 --user-session-agent 命令行参数精确识别用户态代理进程。

    不能按镜像名+会话枚举：helper/consent-ui 是同 exe 同会话的不同角色，
    心跳文件又存在启动初期的空窗，误判会让 supervisor 认为代理已在运行
    而永远不拉起（重启后 user-session-agent 缺失的直接原因）。
    """
    if session_id is None:
        return []

    pids: set[int] = set()
    for pid, (process_session_id, command_line) in _get_zview_process_command_lines().items():
        if process_session_id != int(session_id):
            continue
        if "--user-session-agent" in (command_line or ""):
            pids.add(pid)

    if exclude_pid is not None:
        pids.discard(int(exclude_pid))

    return sorted(pids)


def list_role_runtime_pids(
    role_name: str,
    session_id: int | None,
    exclude_pid: int | None = None,
) -> list[int]:
    if session_id is None:
        return []

    if not has_recent_role_runtime_state(
        role_name,
        session_bound=True,
        session_id=session_id,
        exclude_pid=exclude_pid,
    ):
        return []

    payload = read_role_runtime_state(
        role_name,
        session_bound=True,
        session_id=session_id,
    ) or {}
    process_id = int(payload.get("pid") or 0)
    if process_id <= 0:
        return []
    return [process_id]


def terminate_process(process_id: int) -> bool:
    if process_id <= 0 or not is_process_running(process_id):
        return True

    creation_flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        completed = subprocess.run(
            ["taskkill", "/PID", str(int(process_id)), "/F"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="ignore",
            creationflags=creation_flags,
            timeout=10,
            check=False,
        )
    except Exception:
        return False

    return completed.returncode == 0 or not is_process_running(process_id)


def cleanup_duplicate_user_session_agent_processes(
    session_id: int | None,
    preferred_pid: int | None = None,
) -> list[int]:
    if session_id is None:
        return []

    session_pids = list_user_session_agent_pids(session_id)
    if len(session_pids) <= 1:
        return session_pids

    keep_pid = preferred_pid if preferred_pid in session_pids else max(session_pids)
    for pid in session_pids:
        if pid == keep_pid:
            continue
        if terminate_process(pid):
            log_runtime_event(
                "UserSessionSupervisor",
                f"terminated duplicate user-session agent: session={session_id} pid={pid} keep_pid={keep_pid}",
            )
        else:
            log_runtime_event(
                "UserSessionSupervisor",
                f"failed to terminate duplicate user-session agent: session={session_id} pid={pid} keep_pid={keep_pid}",
            )

    return list_user_session_agent_pids(session_id)


def start_role_runtime_heartbeat(
    role_name: str,
    session_bound: bool = False,
    session_id: int | None = None,
) -> threading.Thread | None:
    if os.name != "nt":
        return None

    resolved_session_id = session_id
    if session_bound and resolved_session_id is None:
        resolved_session_id = get_current_process_session_id()

    state_path = resolve_role_runtime_path(
        role_name,
        session_bound=session_bound,
        session_id=resolved_session_id,
    )
    heartbeat_context = (
        f"role={role_name} session_bound={bool(session_bound)} "
        f"session={resolved_session_id if resolved_session_id is not None else 'unknown'} "
        f"pid={os.getpid()} path={state_path}"
    )
    first_success_logged = False
    last_failure_signature = ""
    last_failure_logged_at = 0.0

    def log_heartbeat_success(source: str):
        nonlocal first_success_logged
        if first_success_logged:
            return
        first_success_logged = True
        log_runtime_event("RoleHeartbeat", f"heartbeat state write ok ({source}): {heartbeat_context}")

    def log_heartbeat_failure(source: str, exc: Exception):
        nonlocal last_failure_signature, last_failure_logged_at
        failure_signature = f"{type(exc).__name__}: {exc}"
        now = time.time()
        should_log = (
            source == "initial"
            or failure_signature != last_failure_signature
            or (now - last_failure_logged_at) >= 30
        )
        if not should_log:
            return
        last_failure_signature = failure_signature
        last_failure_logged_at = now
        log_runtime_event(
            "RoleHeartbeat",
            f"heartbeat state write failed ({source}): {heartbeat_context} error={failure_signature}",
        )

    def heartbeat_loop():
        while True:
            try:
                write_role_runtime_state(
                    role_name,
                    session_bound=session_bound,
                    session_id=resolved_session_id,
                )
                log_heartbeat_success("loop")
            except Exception as exc:
                log_heartbeat_failure("loop", exc)
            time.sleep(_ROLE_HEARTBEAT_INTERVAL_SECONDS)

    try:
        write_role_runtime_state(
            role_name,
            session_bound=session_bound,
            session_id=resolved_session_id,
        )
        log_heartbeat_success("initial")
    except Exception as exc:
        log_heartbeat_failure("initial", exc)

    thread = threading.Thread(
        target=heartbeat_loop,
        name=f"{role_name}-heartbeat",
        daemon=True,
    )
    thread.start()
    return thread


def is_role_mutex_active(
    role_name: str,
    session_bound: bool = False,
    session_id: int | None = None,
) -> bool:
    if os.name != "nt":
        return False

    mutex_name = build_role_mutex_name(
        role_name,
        session_bound=session_bound,
        session_id=session_id,
    )

    try:
        handle = ctypes.windll.kernel32.OpenMutexW(_ROLE_MUTEX_ACCESS, False, mutex_name)
        if not handle:
            return False

        ctypes.windll.kernel32.CloseHandle(handle)
        return True
    except Exception as exc:
        print(f"[Agent] Mutex probe unavailable for {mutex_name}: {exc}")
        return False


def build_user_session_agent_command() -> list[str]:
    if getattr(sys, "frozen", False):
        return [str(Path(sys.executable).resolve()), "--user-session-agent"]

    return [str(Path(sys.executable).resolve()), str(Path(__file__).resolve()), "--user-session-agent"]


def build_high_integrity_helper_command(target_session_id: int | None = None) -> list[str]:
    return build_high_integrity_helper_launch_command(target_session_id)


def run_user_session_agent_restart_helper(
    target_session_id: int | None = None,
    wait_seconds: int = 3,
) -> int:
    session_id = target_session_id
    if session_id is None:
        session_id = get_current_process_session_id()

    wait_seconds = max(1, int(wait_seconds or 1))
    log_runtime_event(
        "UserSessionAgentRepair",
        f"restart helper begin: target_session={session_id if session_id is not None else 'unknown'} wait={wait_seconds}s",
    )
    time.sleep(wait_seconds)

    deadline = time.time() + 45
    while time.time() < deadline:
        if not is_role_mutex_active(
            "user-session-agent",
            session_bound=True,
            session_id=session_id,
        ):
            command = build_user_session_agent_command()
            if not command:
                log_runtime_event("UserSessionAgentRepair", "user-session agent command unavailable")
                return 1

            creation_flags = (
                getattr(subprocess, "DETACHED_PROCESS", 0)
                | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
                | getattr(subprocess, "CREATE_NO_WINDOW", 0)
            )
            subprocess.Popen(
                command,
                cwd=str(_APP_DIR),
                creationflags=creation_flags,
            )
            log_runtime_event(
                "UserSessionAgentRepair",
                f"restart helper relaunched session agent: {format_command_for_log(command)}",
            )
            return 0

        time.sleep(1)

    log_runtime_event(
        "UserSessionAgentRepair",
        f"restart helper timeout waiting for session mutex release: target_session={session_id if session_id is not None else 'unknown'}",
    )
    return 1


def format_command_for_log(command: list[str]) -> str:
    try:
        return subprocess.list2cmdline(command)
    except Exception:
        return " ".join(command)


def get_user_session_debug_label(session_id: int) -> str:
    parts = [f"session={session_id}"]
    if os.name != "nt":
        return " ".join(parts)

    try:
        import win32ts

        def _read_text(info_class) -> str:
            value = win32ts.WTSQuerySessionInformation(
                win32ts.WTS_CURRENT_SERVER_HANDLE,
                session_id,
                info_class,
            )
            if isinstance(value, bytes):
                value = value.decode("utf-16-le", errors="ignore")
            return str(value or "").replace("\x00", "").strip()

        username = _read_text(win32ts.WTSUserName)
        domain = _read_text(win32ts.WTSDomainName)
        station = _read_text(win32ts.WTSWinStationName)
        if username and domain:
            parts.append(f"user={domain}\\{username}")
        elif username:
            parts.append(f"user={username}")
        if station:
            parts.append(f"station={station}")
    except Exception:
        pass

    return " ".join(parts)


def _setup_wts_enumeration():
    if os.name != "nt":
        return None

    kernel32 = getattr(ctypes.windll, "kernel32", None)
    wtsapi32 = getattr(ctypes.windll, "wtsapi32", None)
    if kernel32 is None or wtsapi32 is None:
        return None

    kernel32.WTSGetActiveConsoleSessionId.restype = wintypes.DWORD
    wtsapi32.WTSEnumerateSessionsW.argtypes = [
        wintypes.HANDLE,
        wintypes.DWORD,
        wintypes.DWORD,
        ctypes.POINTER(ctypes.POINTER(_WTSSessionInfo)),
        ctypes.POINTER(wintypes.DWORD),
    ]
    wtsapi32.WTSEnumerateSessionsW.restype = wintypes.BOOL
    wtsapi32.WTSFreeMemory.argtypes = [wintypes.LPVOID]
    wtsapi32.WTSFreeMemory.restype = None
    return kernel32, wtsapi32


def get_interactive_session_ids() -> list[int]:
    apis = _setup_wts_enumeration()
    if apis is None:
        return []

    kernel32, wtsapi32 = apis
    session_pointer = ctypes.POINTER(_WTSSessionInfo)()
    session_count = wintypes.DWORD(0)
    active_console_session = kernel32.WTSGetActiveConsoleSessionId()
    preferred_console = (
        int(active_console_session)
        if active_console_session != INVALID_SESSION_ID
        else None
    )
    active_sessions_with_user: list[int] = []
    active_sessions_without_user: list[int] = []
    connected_sessions_with_user: list[int] = []

    try:
        success = wtsapi32.WTSEnumerateSessionsW(
            WTS_CURRENT_SERVER_HANDLE,
            0,
            1,
            ctypes.byref(session_pointer),
            ctypes.byref(session_count),
        )
        if not success:
            return []

        for index in range(session_count.value):
            session_info = session_pointer[index]
            station_name = (session_info.pWinStationName or "").lower()
            if station_name == "services":
                continue

            session_id = int(session_info.SessionId)
            identity = _query_session_identity(session_id)
            if not identity:
                # No user identity — skip (Services / headless console etc.)
                continue
            if session_info.State == WTS_ACTIVE:
                active_sessions_with_user.append(session_id)
            elif session_info.State == WTS_CONNECTED:
                connected_sessions_with_user.append(session_id)
            else:
                # Disconnected / Idle / etc. — keep the session as long as it
                # has a user identity, so RDP-target consoles still have a
                # place to host the user-session agent.
                connected_sessions_with_user.append(session_id)

        ordered_sessions: list[int] = []
        # On Windows server / headless / VMware console, the console session
        # often has no logged-on user identity (e.g. RDP-target console).
        # Always try the active console first regardless of identity, so the
        # supervisor can still launch the user-session agent there. Fall
        # back to sessions with a real interactive user afterwards.
        if preferred_console is not None:
            ordered_sessions.append(preferred_console)

        for session_id in active_sessions_with_user + connected_sessions_with_user:
            if session_id not in ordered_sessions:
                ordered_sessions.append(session_id)

        if ordered_sessions:
            return ordered_sessions

        return []
    except Exception as exc:
        log_runtime_event("UserSessionSupervisor", f"failed to enumerate interactive sessions: {exc}")
        return []
    finally:
        if session_pointer:
            with contextlib.suppress(Exception):
                wtsapi32.WTSFreeMemory(session_pointer)


def _query_session_identity(session_id: int) -> str:
    username = _query_session_text(session_id, WTS_USERNAME)
    if not username:
        return ""

    domain = _query_session_text(session_id, WTS_DOMAIN_NAME)
    if domain:
        return f"{domain}\\{username}"
    return username


def _query_session_text(session_id: int, info_class: int) -> str:
    apis = _setup_wts_enumeration()
    if apis is None:
        return ""

    _, wtsapi32 = apis
    try:
        wtsapi32.WTSQuerySessionInformationW.argtypes = [
            wintypes.HANDLE,
            wintypes.DWORD,
            wintypes.DWORD,
            ctypes.POINTER(wintypes.LPWSTR),
            ctypes.POINTER(wintypes.DWORD),
        ]
        wtsapi32.WTSQuerySessionInformationW.restype = wintypes.BOOL
    except Exception:
        return ""

    buffer = wintypes.LPWSTR()
    bytes_returned = wintypes.DWORD(0)
    try:
        success = wtsapi32.WTSQuerySessionInformationW(
            WTS_CURRENT_SERVER_HANDLE,
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
                wtsapi32.WTSFreeMemory(buffer)


def launch_user_session_agent_for_session(session_id: int) -> bool:
    command = build_user_session_agent_command()
    if not command:
        log_runtime_event("UserSessionSupervisor", f"user-session agent command unavailable for session {session_id}")
        return False

    session_label = get_user_session_debug_label(session_id)
    win32api = None
    win32profile = None
    user_token = None
    primary_token = None
    process_handle = None
    thread_handle = None
    environment = None
    launch_step = "import_pywin32"
    try:
        import win32api
        import win32con
        import win32process
        import win32profile
        import win32security
        import win32ts

        log_runtime_event(
            "UserSessionSupervisor",
            f"launch preparation begin: {session_label} command={format_command_for_log(command)} cwd={_APP_DIR}",
        )
        launch_step = "WTSQueryUserToken"
        user_token = win32ts.WTSQueryUserToken(session_id)
        log_runtime_event("UserSessionSupervisor", f"WTSQueryUserToken succeeded: {session_label}")
        primary_token = user_token
        log_runtime_event("UserSessionSupervisor", f"primary token ready: {session_label}")
        launch_step = "CreateEnvironmentBlock"
        environment = win32profile.CreateEnvironmentBlock(primary_token, False)
        log_runtime_event("UserSessionSupervisor", f"CreateEnvironmentBlock succeeded: {session_label}")
        startup = win32process.STARTUPINFO()
        startup.lpDesktop = "winsta0\\default"
        creation_flags = (
            win32con.CREATE_NEW_PROCESS_GROUP
            | win32con.CREATE_UNICODE_ENVIRONMENT
        )
        command_line = subprocess.list2cmdline(command)
        launch_step = "CreateProcessAsUser"
        process_handle, thread_handle, process_id, _ = win32process.CreateProcessAsUser(
            primary_token,
            None,
            command_line,
            None,
            None,
            False,
            creation_flags,
            environment,
            str(_APP_DIR),
            startup,
        )
        log_runtime_event(
            "UserSessionSupervisor",
            f"user-session remote desktop role launched: {session_label} pid={process_id}",
        )
        return True
    except Exception as exc:
        log_runtime_event(
            "UserSessionSupervisor",
            f"launch failed at step={launch_step}: {session_label} error={exc}",
        )
        return False
    finally:
        with contextlib.suppress(Exception):
            if environment is not None and win32profile is not None:
                win32profile.DestroyEnvironmentBlock(environment)
        with contextlib.suppress(Exception):
            if thread_handle is not None:
                win32api.CloseHandle(thread_handle)
        with contextlib.suppress(Exception):
            if process_handle is not None:
                win32api.CloseHandle(process_handle)
        with contextlib.suppress(Exception):
            if primary_token is not None and primary_token is not user_token:
                win32api.CloseHandle(primary_token)
        with contextlib.suppress(Exception):
            if user_token is not None:
                win32api.CloseHandle(user_token)


def launch_high_integrity_helper_for_session(session_id: int) -> dict[str, object]:
    command = build_high_integrity_helper_command(session_id)
    if not command:
        log_runtime_event("HighIntegrityHelper", f"helper command unavailable for session {session_id}")
        return {
            "started": False,
            "launch_mode": "unavailable",
            "session_id": int(session_id),
            "error": "helper command unavailable",
        }

    session_label = get_user_session_debug_label(session_id)
    try:
        launch_result = _launch_session_helper_with_service_token(session_id, command)
        log_runtime_event(
            "HighIntegrityHelper",
            f"helper launched via service token: {session_label} pid={launch_result.get('pid')}",
        )
        return {
            "started": True,
            "launch_mode": "service_token",
            "session_id": int(session_id),
            **launch_result,
        }
    except Exception as exc:
        log_runtime_event(
            "HighIntegrityHelper",
            f"service token launch failed: {session_label} error={exc}; falling back to user token",
        )

    try:
        launch_result = _launch_session_helper_with_user_token(session_id, command)
        log_runtime_event(
            "HighIntegrityHelper",
            f"helper launched via user token fallback: {session_label} pid={launch_result.get('pid')}",
        )
        return {
            "started": True,
            "launch_mode": "user_token_fallback",
            "session_id": int(session_id),
            **launch_result,
        }
    except Exception as exc:
        log_runtime_event(
            "HighIntegrityHelper",
            f"helper launch failed: {session_label} error={exc}",
        )
        return {
            "started": False,
            "launch_mode": "failed",
            "session_id": int(session_id),
            "error": str(exc),
        }


def _launch_session_helper_with_service_token(session_id: int, command: list[str]) -> dict[str, object]:
    import win32api
    import win32con
    import win32process
    import win32profile
    import win32security

    process_handle = None
    thread_handle = None
    environment = None
    process_token = None
    primary_token = None
    launch_step = "OpenProcessToken"
    try:
        process_token = win32security.OpenProcessToken(
            win32api.GetCurrentProcess(),
            win32con.TOKEN_DUPLICATE
            | win32con.TOKEN_ASSIGN_PRIMARY
            | win32con.TOKEN_QUERY
            | win32con.TOKEN_ADJUST_DEFAULT
            | win32con.TOKEN_ADJUST_SESSIONID,
        )
        launch_step = "DuplicateTokenEx"
        primary_token = win32security.DuplicateTokenEx(
            process_token,
            win32security.SecurityImpersonation,
            win32con.MAXIMUM_ALLOWED,
            win32security.TokenPrimary,
        )
        launch_step = "SetTokenInformation(TokenSessionId)"
        win32security.SetTokenInformation(
            primary_token,
            win32security.TokenSessionId,
            int(session_id),
        )
        launch_step = "CreateEnvironmentBlock"
        environment = win32profile.CreateEnvironmentBlock(primary_token, False)
        startup = win32process.STARTUPINFO()
        startup.lpDesktop = "winsta0\\default"
        creation_flags = (
            win32con.CREATE_NEW_PROCESS_GROUP
            | win32con.CREATE_UNICODE_ENVIRONMENT
            | getattr(win32con, "CREATE_NO_WINDOW", 0)
        )
        command_line = subprocess.list2cmdline(command)
        launch_step = "CreateProcessAsUser"
        process_handle, thread_handle, process_id, _ = win32process.CreateProcessAsUser(
            primary_token,
            None,
            command_line,
            None,
            None,
            False,
            creation_flags,
            environment,
            str(_APP_DIR),
            startup,
        )
        return {
            "pid": int(process_id),
            "command": format_command_for_log(command),
        }
    except Exception as exc:
        raise RuntimeError(f"{launch_step}: {exc}") from exc
    finally:
        with contextlib.suppress(Exception):
            if environment is not None:
                win32profile.DestroyEnvironmentBlock(environment)
        with contextlib.suppress(Exception):
            if thread_handle is not None:
                win32api.CloseHandle(thread_handle)
        with contextlib.suppress(Exception):
            if process_handle is not None:
                win32api.CloseHandle(process_handle)
        with contextlib.suppress(Exception):
            if primary_token is not None:
                win32api.CloseHandle(primary_token)
        with contextlib.suppress(Exception):
            if process_token is not None:
                win32api.CloseHandle(process_token)


def _launch_session_helper_with_user_token(session_id: int, command: list[str]) -> dict[str, object]:
    import win32api
    import win32con
    import win32process
    import win32profile
    import win32security
    import win32ts

    user_token = None
    primary_token = None
    process_handle = None
    thread_handle = None
    environment = None
    launch_step = "WTSQueryUserToken"
    try:
        user_token = win32ts.WTSQueryUserToken(session_id)
        launch_step = "DuplicateTokenEx"
        primary_token = win32security.DuplicateTokenEx(
            user_token,
            win32security.SecurityImpersonation,
            win32con.MAXIMUM_ALLOWED,
            win32security.TokenPrimary,
        )
        launch_step = "CreateEnvironmentBlock"
        environment = win32profile.CreateEnvironmentBlock(primary_token, False)
        startup = win32process.STARTUPINFO()
        startup.lpDesktop = "winsta0\\default"
        creation_flags = (
            win32con.CREATE_NEW_PROCESS_GROUP
            | win32con.CREATE_UNICODE_ENVIRONMENT
        )
        command_line = subprocess.list2cmdline(command)
        launch_step = "CreateProcessAsUser"
        process_handle, thread_handle, process_id, _ = win32process.CreateProcessAsUser(
            primary_token,
            None,
            command_line,
            None,
            None,
            False,
            creation_flags,
            environment,
            str(_APP_DIR),
            startup,
        )
        return {
            "pid": int(process_id),
            "command": format_command_for_log(command),
        }
    except Exception as exc:
        raise RuntimeError(f"{launch_step}: {exc}") from exc
    finally:
        with contextlib.suppress(Exception):
            if environment is not None:
                win32profile.DestroyEnvironmentBlock(environment)
        with contextlib.suppress(Exception):
            if thread_handle is not None:
                win32api.CloseHandle(thread_handle)
        with contextlib.suppress(Exception):
            if process_handle is not None:
                win32api.CloseHandle(process_handle)
        with contextlib.suppress(Exception):
            if primary_token is not None:
                win32api.CloseHandle(primary_token)
        with contextlib.suppress(Exception):
            if user_token is not None:
                win32api.CloseHandle(user_token)


def start_user_session_supervisor():
    if os.name != "nt":
        return

    launch_cooldowns: dict[int, float] = {}
    last_health_snapshots: dict[int, tuple[bool, bool, tuple[int, ...], int]] = {}

    def supervisor_loop():
        log_runtime_event("UserSessionSupervisor", "started")
        last_logged_sessions: tuple[int, ...] | None = None
        last_primary_session_id: int | None | object = object()
        while True:
            try:
                session_ids = get_interactive_session_ids()
                session_snapshot = tuple(session_ids)
                primary_session_id = session_ids[0] if session_ids else None
                if session_snapshot != last_logged_sessions:
                    session_text = ", ".join(
                        get_user_session_debug_label(session_id)
                        for session_id in session_ids
                    ) if session_ids else "none"
                    log_runtime_event("UserSessionSupervisor", f"interactive sessions: {session_text}")
                    last_logged_sessions = session_snapshot
                if primary_session_id != last_primary_session_id:
                    if primary_session_id is None:
                        log_runtime_event("UserSessionSupervisor", "primary remote host session: none")
                    else:
                        log_runtime_event(
                            "UserSessionSupervisor",
                            f"primary remote host session: {get_user_session_debug_label(primary_session_id)}",
                        )
                    last_primary_session_id = primary_session_id
                if session_ids:
                    for session_id in session_ids:
                        heartbeat_payload = read_role_runtime_state(
                            "user-session-agent",
                            session_bound=True,
                            session_id=session_id,
                        ) or {}
                        heartbeat_pid = int(heartbeat_payload.get("pid") or 0)
                        mutex_active = is_role_mutex_active(
                            "user-session-agent",
                            session_bound=True,
                            session_id=session_id,
                        )
                        heartbeat_active = has_recent_role_runtime_state(
                            "user-session-agent",
                            session_bound=True,
                            session_id=session_id,
                        )
                        session_pids = list_user_session_agent_pids(session_id)
                        if len(session_pids) > 1:
                            session_pids = cleanup_duplicate_user_session_agent_processes(
                                session_id,
                                preferred_pid=heartbeat_pid or None,
                            )

                        snapshot = (
                            bool(mutex_active),
                            bool(heartbeat_active),
                            tuple(session_pids),
                            heartbeat_pid,
                        )
                        if last_health_snapshots.get(session_id) != snapshot:
                            log_runtime_event(
                                "UserSessionSupervisor",
                                "session health: "
                                f"{get_user_session_debug_label(session_id)} "
                                f"mutex_active={mutex_active} "
                                f"heartbeat_active={heartbeat_active} "
                                f"heartbeat_pid={heartbeat_pid if heartbeat_pid > 0 else 'none'} "
                                f"session_pids={session_pids if session_pids else 'none'}",
                            )
                            last_health_snapshots[session_id] = snapshot

                        if primary_session_id is None:
                            continue

                        # Only treat a session as "stray" if the primary has a
                        # confirmed, recently-beating user-session agent.
                        # Otherwise the primary is probably a headless / no-user
                        # console (VMware, RDP-target console) and the RDP
                        # session with a real interactive user is the
                        # authoritative host. Letting it run avoids killing
                        # the only working session.
                        primary_alive = False
                        primary_hb = read_role_runtime_state(
                            "user-session-agent",
                            session_bound=True,
                            session_id=primary_session_id,
                        ) or {}
                        primary_pids = list_user_session_agent_pids(primary_session_id)
                        primary_heartbeat_recent = has_recent_role_runtime_state(
                            "user-session-agent",
                            session_bound=True,
                            session_id=primary_session_id,
                        )
                        primary_mutex = is_role_mutex_active(
                            "user-session-agent",
                            session_bound=True,
                            session_id=primary_session_id,
                        )
                        primary_alive = bool(primary_heartbeat_recent and (primary_mutex or primary_pids))

                        if session_id != primary_session_id and primary_alive:
                            stray_pids: list[int] = []
                            candidate_pids = {
                                int(pid)
                                for pid in session_pids + ([heartbeat_pid] if heartbeat_pid > 0 else [])
                                if int(pid) > 0
                            }
                            for pid in sorted(candidate_pids):
                                if terminate_process(pid):
                                    stray_pids.append(pid)
                            if stray_pids:
                                log_runtime_event(
                                    "UserSessionSupervisor",
                                    "terminated non-primary user-session agent(s): "
                                    f"session={session_id} primary_session={primary_session_id} pids={stray_pids}",
                                )
                            elif mutex_active or heartbeat_active or session_pids:
                                log_runtime_event(
                                    "UserSessionSupervisor",
                                    "non-primary user-session agent state detected but no terminable pid found: "
                                    f"session={session_id} primary_session={primary_session_id} "
                                    f"mutex_active={mutex_active} heartbeat_active={heartbeat_active}",
                                )
                            continue

                        if mutex_active or heartbeat_active or session_pids:
                            continue

                        now = time.time()
                        last_attempt_at = launch_cooldowns.get(session_id, 0.0)
                        if now - last_attempt_at < _USER_SESSION_SUPERVISOR_LAUNCH_COOLDOWN_SECONDS:
                            continue

                        launch_cooldowns[session_id] = now
                        log_runtime_event(
                            "UserSessionSupervisor",
                            f"launch attempt for {get_user_session_debug_label(session_id)}",
                        )
                        launch_user_session_agent_for_session(session_id)

                    active_session_set = set(session_ids)
                    for session_id in list(launch_cooldowns.keys()):
                        if session_id not in active_session_set:
                            launch_cooldowns.pop(session_id, None)
                            last_health_snapshots.pop(session_id, None)
            except Exception as exc:
                log_runtime_event("UserSessionSupervisor", f"supervisor loop error: {exc}")

            time.sleep(_USER_SESSION_SUPERVISOR_RETRY_SECONDS)

    thread = threading.Thread(
        target=supervisor_loop,
        name="cmdb-user-session-supervisor",
        daemon=True,
    )
    thread.start()


def run_agent_service(
    enable_remote_desktop: bool = True,
    disable_session_supervisor: bool = False,
    start_consent_ui: bool = False,
):
    module = load_core_module()
    config = module.CONFIG
    software_config = module.SOFTWARE_CONFIG

    print("[Agent] Startup begin")
    print(f"[Agent] Started at: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"[Agent] Hostname: {socket.gethostname()}")
    print(f"[Agent] Z-View server: {config['server_url']}")
    print(f"[Agent] Software server: {software_config['server_url']}")
    print("[Agent] Modules: cmdb_reporter, remote_desktop, software_management")

    module.start_cmdb_reporter()
    module.start_agent_control_server()

    print("[Software] Startup sequence begin")
    try:
        print("[Software] Step 1: Getting asset_id from server...")
        asset_id = module.get_asset_id_from_server()
        print(f"[Software] Step 2: Asset ID = {asset_id}")

        if asset_id:
            print(f"[Software] Step 3: Starting software management with asset_id={asset_id}")
            module.start_software_management(asset_id)
            module.start_security_policy_sync(asset_id)
            print("[Software] Step 4: Software management + security policy sync started")
        else:
            print("[Software] ERROR: Failed to get asset_id, software management disabled")
    except Exception as exc:
        print(f"[Software] ERROR: Startup failed with exception: {exc}")
        import traceback
        traceback.print_exc()

    print("[Agent] Startup handoff to remote desktop server")
    if enable_remote_desktop:
        module.start_remote_desktop_server()
        if start_consent_ui:
            if launch_consent_ui_background():
                log_runtime_event("ConsentUI", "background tray helper launched for direct agent startup")
            else:
                log_runtime_event("ConsentUI", "background tray helper failed to launch for direct agent startup")
    else:
        print("[Agent] Remote desktop server disabled for this role")

    if disable_session_supervisor:
        print("[Agent] Session supervisor delegated to service runtime")
    else:
        start_user_session_supervisor()
    keep_worker_alive()


def run_consent_ui():
    session_id = get_current_process_session_id()
    if not acquire_role_mutex("consent-ui", session_bound=True):
        log_runtime_event(
            "ConsentUI",
            f"duplicate startup skipped: session={session_id if session_id is not None else 'unknown'} pid={os.getpid()}",
        )
        return True

    start_role_runtime_heartbeat("consent-ui", session_bound=True, session_id=session_id)
    log_runtime_event(
        "ConsentUI",
        f"startup handoff begin: session={session_id if session_id is not None else 'unknown'} pid={os.getpid()}",
    )

    try:
        from cmdb_agent_consent_ui import main as consent_ui_main
        log_runtime_event("ConsentUI", "module import succeeded")
    except BaseException as exc:
        log_runtime_event(
            "ConsentUI",
            f"module import failed: type={type(exc).__name__} error={exc}",
        )
        if not isinstance(exc, SystemExit):
            import traceback
            traceback.print_exc()
        return False

    try:
        consent_ui_main()
        log_runtime_event("ConsentUI", "main returned normally")
        return True
    except BaseException as exc:
        log_runtime_event(
            "ConsentUI",
            f"main exited unexpectedly: type={type(exc).__name__} error={exc}",
        )
        if not isinstance(exc, SystemExit):
            import traceback
            traceback.print_exc()
        return False


def launch_consent_ui_background() -> bool:
    command = build_ui_launch_command()
    if not command:
        log_runtime_event("ConsentUI", "background launch skipped: command unavailable")
        return False

    try:
        creation_flags = 0
        if os.name == "nt":
            creation_flags = (
                getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
                | getattr(subprocess, "DETACHED_PROCESS", 0)
                | getattr(subprocess, "CREATE_NO_WINDOW", 0)
            )

        process = subprocess.Popen(
            command,
            cwd=str(_APP_DIR),
            creationflags=creation_flags,
        )
        log_runtime_event(
            "ConsentUI",
            f"background helper launched: pid={process.pid} command={subprocess.list2cmdline(command)}",
        )
        return True
    except Exception as exc:
        log_runtime_event("ConsentUI", f"background launch failed: {exc}")
        return False


def acquire_role_mutex(role_name: str, session_bound: bool = False) -> bool:
    """Prevent duplicate long-running roles inside the same machine/session."""
    if os.name != "nt":
        return True

    mutex_name = build_role_mutex_name(role_name, session_bound=session_bound)

    try:
        handle = ctypes.windll.kernel32.CreateMutexW(None, False, mutex_name)
        if not handle:
            print(f"[Agent] Failed to create mutex: {mutex_name}")
            return True

        _INSTANCE_MUTEX_HANDLES[mutex_name] = handle
        already_exists = ctypes.windll.kernel32.GetLastError() == 183
        if already_exists:
            print(f"[Agent] Role already active, skip duplicate startup: {mutex_name}")
            return False
    except Exception as exc:
        print(f"[Agent] Mutex guard unavailable for {mutex_name}: {exc}")
        return True

    return True


def run_user_session_agent():
    """Run the interactive-session remote desktop agent with tray UI."""
    session_id = get_current_process_session_id()
    if has_recent_role_runtime_state(
        "user-session-agent",
        session_bound=True,
        session_id=session_id,
        exclude_pid=os.getpid(),
    ):
        log_runtime_event(
            "UserSessionAgent",
            f"existing session agent heartbeat detected, skip duplicate startup: session={session_id if session_id is not None else 'unknown'} pid={os.getpid()}",
        )
        return

    if not acquire_role_mutex("user-session-agent", session_bound=True):
        return

    module = load_core_module()
    log_runtime_event("UserSessionAgent", "core module loaded")
    start_role_runtime_heartbeat("user-session-agent", session_bound=True, session_id=session_id)
    log_runtime_event(
        "UserSessionAgent",
        f"startup begin: session={session_id if session_id is not None else 'unknown'} pid={os.getpid()}",
    )
    sibling_session_pids = cleanup_duplicate_user_session_agent_processes(
        session_id,
        preferred_pid=os.getpid(),
    )
    if sibling_session_pids:
        log_runtime_event(
            "UserSessionAgent",
            f"session agent process set stabilized: session={session_id if session_id is not None else 'unknown'} pids={sibling_session_pids}",
        )

    service_client = None
    try:
        service_client = PrivilegedServiceClient()
    except Exception as exc:
        log_runtime_event("UserSessionAgent", f"service IPC client init failed, continue degraded: {exc}")

    runtime = RemoteDesktopUserAgentRuntime(
        session_id=session_id,
        start_remote_desktop_server=lambda: module.start_remote_desktop_server(wait=True),
        launch_consent_ui_background=launch_consent_ui_background,
        keepalive=keep_worker_alive,
        log_runtime_event=log_runtime_event,
        service_client=service_client,
        cleanup_runtime_state=lambda: delete_role_runtime_state(
            "user-session-agent",
            session_bound=True,
            session_id=session_id,
        ),
    )
    runtime.run()


def run_high_integrity_helper(target_session_id: int | None = None):
    session_id = target_session_id if target_session_id is not None else get_current_process_session_id()
    if session_id is None:
        raise RuntimeError("high-integrity helper requires a session id")

    if has_recent_role_runtime_state(
        "high-integrity-helper",
        session_bound=True,
        session_id=session_id,
        exclude_pid=os.getpid(),
    ):
        log_runtime_event(
            "HighIntegrityHelper",
            f"existing helper heartbeat detected, skip duplicate startup: session={session_id} pid={os.getpid()}",
        )
        return

    if not acquire_role_mutex("high-integrity-helper", session_bound=True):
        return

    start_role_runtime_heartbeat("high-integrity-helper", session_bound=True, session_id=session_id)
    log_runtime_event(
        "HighIntegrityHelper",
        f"startup begin: session={session_id} pid={os.getpid()}",
    )
    runtime = HighIntegritySessionHelperRuntime(session_id=session_id)
    runtime.run_forever()


def build_agent_worker_command() -> list[str]:
    if getattr(sys, "frozen", False):
        return [
            str(Path(sys.executable).resolve()),
            "--run-agent",
            "--no-remote-desktop",
            "--disable-session-supervisor",
        ]

    return [
        str(Path(sys.executable).resolve()),
        str(Path(__file__).resolve()),
        "--run-agent",
        "--no-remote-desktop",
        "--disable-session-supervisor",
    ]


def should_handle_service_command(raw_args: list[str]) -> bool:
    return any(arg.lower() in SERVICE_CONTROL_COMMANDS for arg in raw_args)


def should_run_service_host(args: argparse.Namespace, raw_args: list[str]) -> bool:
    if args.service_host:
        return True

    if raw_args:
        return False

    session_id = get_current_process_session_id()
    return session_id == 0


def run_service_worker_loop(stop_event_handle, win32event):
    worker_process: subprocess.Popen | None = None

    try:
        while True:
            if worker_process is None or worker_process.poll() is not None:
                command = build_agent_worker_command()
                creation_flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
                print(f"[Service] Starting worker: {' '.join(command)}")
                worker_process = subprocess.Popen(
                    command,
                    cwd=str(_APP_DIR),
                    creationflags=creation_flags,
                )

            wait_result = win32event.WaitForSingleObject(stop_event_handle, 5000)
            if wait_result == win32event.WAIT_OBJECT_0:
                break

            if worker_process is not None and worker_process.poll() is not None:
                print(f"[Service] Worker exited with code {worker_process.returncode}; restarting")
                time.sleep(2)
                worker_process = None
    finally:
        stop_worker_process(worker_process)


def stop_worker_process(worker_process: subprocess.Popen | None):
    if worker_process is None or worker_process.poll() is not None:
        return

    print(f"[Service] Stopping worker process pid={worker_process.pid}")
    with contextlib.suppress(Exception):
        worker_process.terminate()

    try:
        worker_process.wait(timeout=10)
        return
    except Exception:
        pass

    with contextlib.suppress(Exception):
        worker_process.kill()


def get_service_class():
    import win32event
    import win32service
    import win32serviceutil

    class CMDBAgentWindowsService(win32serviceutil.ServiceFramework):
        _svc_name_ = SERVICE_NAME
        _svc_display_name_ = SERVICE_DISPLAY_NAME
        _svc_description_ = SERVICE_DESCRIPTION
        _exe_args_ = "--service-host"

        def __init__(self, args):
            super().__init__(args)
            self.hWaitStop = win32event.CreateEvent(None, 0, 0, None)

        def SvcStop(self):
            self.ReportServiceStatus(win32service.SERVICE_STOP_PENDING)
            win32event.SetEvent(self.hWaitStop)

        def SvcDoRun(self):
            print("[Service] Host startup begin")
            global _SERVICE_RUNTIME
            _SERVICE_RUNTIME = build_service_runtime()
            _SERVICE_RUNTIME.start()
            try:
                run_service_worker_loop(self.hWaitStop, win32event)
            finally:
                if _SERVICE_RUNTIME is not None:
                    _SERVICE_RUNTIME.stop()
                    _SERVICE_RUNTIME = None
            print("[Service] Host stopped")

    return CMDBAgentWindowsService


def handle_service_command_line(raw_args: list[str]):
    import win32serviceutil

    service_class = get_service_class()
    original_argv = sys.argv[:]
    try:
        sys.argv = [sys.argv[0], *raw_args]
        win32serviceutil.HandleCommandLine(service_class)
    finally:
        sys.argv = original_argv


def build_service_runtime() -> ServiceRuntime:
    bridge = LegacySessionBridge(
        get_interactive_session_ids=get_interactive_session_ids,
        get_user_session_debug_label=get_user_session_debug_label,
        launch_user_session_agent_for_session=launch_user_session_agent_for_session,
        launch_high_integrity_helper_for_session=launch_high_integrity_helper_for_session,
        read_role_runtime_state=read_role_runtime_state,
        has_recent_role_runtime_state=has_recent_role_runtime_state,
        is_role_mutex_active=is_role_mutex_active,
        list_user_session_agent_pids=list_user_session_agent_pids,
        list_high_integrity_helper_pids=lambda session_id: list_role_runtime_pids("high-integrity-helper", session_id),
        cleanup_duplicate_user_session_agent_processes=cleanup_duplicate_user_session_agent_processes,
        terminate_process=terminate_process,
        log_runtime_event=log_runtime_event,
    )
    return ServiceRuntime(
        session_manager=SessionManager(
            bridge=bridge,
            retry_seconds=_USER_SESSION_SUPERVISOR_RETRY_SECONDS,
            launch_cooldown_seconds=_USER_SESSION_SUPERVISOR_LAUNCH_COOLDOWN_SECONDS,
        ),
        logger=lambda message: log_runtime_event("ServiceRuntime", message),
    )


def run_service_host():
    import servicemanager

    service_class = get_service_class()
    servicemanager.Initialize()
    servicemanager.PrepareToHostSingle(service_class)
    servicemanager.StartServiceCtrlDispatcher()


def ensure_windows_dpi_awareness() -> bool:
    """尽早将进程标记为 Per-Monitor DPI 感知。

    远控的 screen_info 上报、坐标换算与抓屏区域全部依赖屏幕度量；进程若处于
    DPI 不感知状态，在 125%/150% 缩放的会话里会读到 1536x864 一类虚拟化值，
    造成远控画面被裁剪、画面内容与点击映射错位。必须在任何窗口或抓屏库
    （tkinter、dxcam、pyautogui 等）初始化之前调用；若感知已被其他组件抢先
    设置，本函数会静默失败并保持现有状态。
    """
    if os.name != "nt":
        return False
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)  # PROCESS_PER_MONITOR_DPI_AWARE
        return True
    except Exception:
        pass
    try:
        user32 = getattr(ctypes.windll, "user32", None)
        if user32 is not None and hasattr(user32, "SetProcessDPIAware"):
            return bool(user32.SetProcessDPIAware())
    except Exception:
        pass
    return False


def main(argv: list[str] | None = None):
    ensure_windows_dpi_awareness()
    raw_args = list(sys.argv[1:] if argv is None else argv)

    if should_handle_service_command(raw_args):
        handle_service_command_line(raw_args)
        return

    args = build_arg_parser().parse_args(raw_args)

    if args.restart_user_session_agent:
        raise SystemExit(
            run_user_session_agent_restart_helper(
                target_session_id=args.target_session_id,
                wait_seconds=args.restart_wait_seconds,
            )
        )

    if args.consent_ui:
        run_consent_ui()
        return

    if args.user_session_agent:
        run_user_session_agent()
        return

    if args.high_integrity_helper:
        run_high_integrity_helper(args.target_session_id)
        return

    if args.run_agent:
        run_agent_service(
            enable_remote_desktop=not args.no_remote_desktop,
            disable_session_supervisor=args.disable_session_supervisor,
        )
        return

    if should_run_service_host(args, raw_args):
        run_service_host()
        return

    run_agent_service(
        enable_remote_desktop=not args.no_remote_desktop,
        disable_session_supervisor=True,
        start_consent_ui=not args.no_remote_desktop,
    )


if __name__ == "__main__":
    main()
