from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Callable

from .runtime_paths import get_runtime_log_file


def append_log_line(component: str, message: str, log_path: Path | None = None) -> None:
    target = log_path or get_runtime_log_file()
    line = f"[{datetime.now():%Y-%m-%d %H:%M:%S}] [{component}] {message}"
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        with open(target, "a", encoding="utf-8") as file:
            file.write(line + "\n")
    except Exception:
        return


def make_component_logger(component: str, log_path: Path | None = None) -> Callable[[str], None]:
    def _log(message: str) -> None:
        append_log_line(component, message, log_path=log_path)

    return _log
