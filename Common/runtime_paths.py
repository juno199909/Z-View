from __future__ import annotations

import os
import sys
from pathlib import Path


def get_app_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[1]


def get_program_data_dir() -> Path:
    if os.name == "nt":
        return Path(os.environ.get("ProgramData", r"C:\ProgramData"))
    return get_app_dir()


def get_runtime_dir() -> Path:
    if os.name == "nt":
        return get_program_data_dir() / "CMDB-Agent" / "runtime"
    return get_app_dir() / "runtime-data"


def get_runtime_log_file() -> Path:
    if os.name == "nt":
        return get_program_data_dir() / "CMDB-Agent" / "logs" / "agent-runtime.log"
    return get_app_dir() / "logs" / "agent-runtime.log"


def get_default_service_pipe_name() -> str:
    return "CMDB-Agent-Privileged"


def get_high_integrity_helper_pipe_name(session_id: int) -> str:
    return f"CMDB-Agent-Privileged-Session-{int(session_id)}"
