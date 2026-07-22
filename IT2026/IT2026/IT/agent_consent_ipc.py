"""
Shared helpers for local remote-control consent IPC.
"""

from __future__ import annotations

import hashlib
import json
import os
import socket
import sys
from pathlib import Path
from typing import Any


CONSENT_PIPE_PREFIX = r"\\.\pipe\CMDB-Agent-Consent"
CONSENT_AUTH_SALT = "cmdb-agent-consent-v1"
TRAY_SETTINGS_DEFAULTS: dict[str, Any] = {
    "allow_remote_requests": True,
    "skip_consent_for_session": False,
    "show_balloon_notifications": True,
}


def get_app_base_dir() -> Path:
    """Return the deployed application directory for source or frozen runs."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


APP_BASE_DIR = get_app_base_dir()


def get_runtime_data_dir() -> Path:
    if os.name == "nt":
        program_data = Path(os.environ.get("ProgramData", r"C:\ProgramData"))
        return program_data / "CMDB-Agent"
    return APP_BASE_DIR / "runtime-data"


def ensure_directory(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    return path


def get_log_directory() -> Path:
    return ensure_directory(get_runtime_data_dir() / "logs")


def resolve_runtime_log_path() -> Path:
    return get_log_directory() / "agent-runtime.log"


def resolve_tray_settings_path() -> Path:
    return ensure_directory(get_runtime_data_dir()) / "user_session_settings.json"


def load_tray_settings() -> dict[str, Any]:
    settings = dict(TRAY_SETTINGS_DEFAULTS)
    settings_path = resolve_tray_settings_path()

    try:
        with open(settings_path, "r", encoding="utf-8") as file:
            saved_settings = json.load(file)
            if isinstance(saved_settings, dict):
                settings.update(saved_settings)
    except Exception:
        pass

    settings["allow_remote_requests"] = bool(settings.get("allow_remote_requests", True))
    settings["skip_consent_for_session"] = bool(settings.get("skip_consent_for_session", False))
    settings["show_balloon_notifications"] = bool(settings.get("show_balloon_notifications", True))
    return settings


def save_tray_settings(settings: dict[str, Any]) -> Path:
    merged = dict(TRAY_SETTINGS_DEFAULTS)
    merged.update(settings or {})
    settings_path = resolve_tray_settings_path()
    ensure_directory(settings_path.parent)

    with open(settings_path, "w", encoding="utf-8") as file:
        json.dump(merged, file, ensure_ascii=False, indent=2)

    return settings_path


def resolve_tray_icon_path() -> Path | None:
    candidates: list[Path] = [
        APP_BASE_DIR / "favicon.ico",
        Path.cwd() / "favicon.ico",
        APP_BASE_DIR / "frontend" / "public" / "favicon.ico",
        APP_BASE_DIR / "frontend" / "dist" / "favicon.ico",
    ]

    bundled_root = getattr(sys, "_MEIPASS", None)
    if bundled_root:
        candidates.extend(
            [
                Path(bundled_root) / "favicon.ico",
                Path(bundled_root) / "frontend" / "public" / "favicon.ico",
            ]
        )

    seen: set[str] = set()
    for candidate in candidates:
        normalized = str(candidate.resolve()) if candidate.exists() else str(candidate)
        if normalized in seen:
            continue
        seen.add(normalized)
        if candidate.exists():
            return candidate

    return None


def resolve_config_path() -> Path:
    candidates = [APP_BASE_DIR / "config.json", Path.cwd() / "config.json"]

    bundled_root = getattr(sys, "_MEIPASS", None)
    if bundled_root:
        candidates.append(Path(bundled_root) / "config.json")

    for candidate in candidates:
        if candidate.exists():
            return candidate

    return candidates[0]


def load_agent_config() -> dict[str, Any]:
    config_path = resolve_config_path()
    try:
        with open(config_path, "r", encoding="utf-8") as file:
            return json.load(file)
    except Exception:
        return {}


def build_consent_authkey(config: dict[str, Any] | None = None) -> bytes:
    source = config or load_agent_config()
    token = str(source.get("token") or "")
    hostname = socket.gethostname()
    seed = f"{token}|{hostname}|{CONSENT_AUTH_SALT}"
    return hashlib.sha256(seed.encode("utf-8")).digest()


def build_consent_pipe_name(session_id: int) -> str:
    return f"{CONSENT_PIPE_PREFIX}-{int(session_id)}"


def get_agent_entry_candidates() -> list[Path]:
    candidates: list[Path] = []

    if getattr(sys, "frozen", False):
        candidates.append(Path(sys.executable).resolve())

    candidates.extend(
        [
            APP_BASE_DIR / "Z-View.exe",
            APP_BASE_DIR / "CMDB-Agent.exe",
            APP_BASE_DIR / "cmdb_agent_unified_v2.py",
            APP_BASE_DIR / "cmdb_agent_consent_ui.py",
        ]
    )
    return [candidate for candidate in candidates if candidate.exists()]


def _build_agent_command(args: list[str]) -> list[str]:
    for candidate in get_agent_entry_candidates():
        if candidate.suffix.lower() == ".exe":
            return [str(candidate), *args]

        if candidate.name == "cmdb_agent_unified_v2.py":
            pythonw = Path(sys.executable).with_name("pythonw.exe")
            launcher = pythonw if pythonw.exists() else Path(sys.executable)
            return [str(launcher), str(candidate), *args]

        if candidate.suffix.lower() == ".py":
            pythonw = Path(sys.executable).with_name("pythonw.exe")
            launcher = pythonw if pythonw.exists() else Path(sys.executable)
            return [str(launcher), str(candidate), *args]

    return []


def build_ui_launch_command() -> list[str]:
    return _build_agent_command(["--consent-ui"])


def build_user_session_agent_launch_command() -> list[str]:
    return _build_agent_command(["--user-session-agent"])


def build_user_session_agent_restart_command(
    session_id: int | None = None,
    wait_seconds: int = 3,
) -> list[str]:
    args = [
        "--restart-user-session-agent",
        "--restart-wait-seconds",
        str(max(1, int(wait_seconds))),
    ]
    if session_id is not None:
        args.extend(["--target-session-id", str(int(session_id))])
    return _build_agent_command(args)


def get_support_bundle_sources() -> list[Path]:
    candidates = [
        resolve_config_path(),
        resolve_tray_settings_path(),
        resolve_runtime_log_path(),
        get_log_directory(),
        APP_BASE_DIR / "logs",
        APP_BASE_DIR / "runtime_logs",
        APP_BASE_DIR / "runtime-logs",
    ]

    sources: list[Path] = []
    seen: set[str] = set()
    for candidate in candidates:
        try:
            resolved = str(candidate.resolve())
        except Exception:
            resolved = str(candidate)
        if resolved in seen or not candidate.exists():
            continue
        seen.add(resolved)
        sources.append(candidate)
    return sources


def get_current_process_session_id() -> int | None:
    try:
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.windll.kernel32
        session_id = wintypes.DWORD()
        if kernel32.ProcessIdToSessionId(kernel32.GetCurrentProcessId(), ctypes.byref(session_id)):
            return int(session_id.value)
    except Exception:
        return None
    return None


def get_current_username() -> str:
    return (
        os.environ.get("USERNAME")
        or os.environ.get("USER")
        or "unknown-user"
    )
