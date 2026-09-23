# -*- coding: utf-8 -*-
"""后台线程健康注册表（P1-08）。

目标：线程异常退出/卡死不再"没人发现"。
用法：长循环线程每轮调用 mark_run(name, ok=True, error=None)；
启动时 register(name)；/api/health 暴露 snapshot() 给运维。
"""
from __future__ import annotations

import threading
import time
from typing import Any, Dict, Optional

from console_utils import safe_console_print

_LOCK = threading.Lock()
_WORKERS: Dict[str, Dict[str, Any]] = {}
_STARTED_AT = time.time()


def register(name: str, description: str = "") -> None:
    with _LOCK:
        _WORKERS.setdefault(
            name,
            {"description": description, "registered_at": time.time(),
             "last_run": None, "last_ok": None, "last_error": None,
             "runs": 0, "failures": 0},
        )


def mark_run(name: str, ok: bool = True, error: Optional[str] = None) -> None:
    with _LOCK:
        worker = _WORKERS.get(name)
        if not worker:
            register(name)
            worker = _WORKERS[name]
        worker["last_run"] = time.time()
        worker["last_ok"] = bool(ok)
        worker["runs"] = int(worker.get("runs", 0)) + 1
        if ok:
            worker["last_error"] = None
        else:
            worker["last_error"] = str(error or "unknown error")[:500]
            worker["failures"] = int(worker.get("failures", 0)) + 1
            safe_console_print(f"[WorkerHealth] {name} FAILED: {worker['last_error']}")


def snapshot() -> Dict[str, Any]:
    with _LOCK:
        workers = {
            name: {
                "description": w.get("description", ""),
                "last_run": w.get("last_run"),
                "last_ok": w.get("last_ok"),
                "last_error": w.get("last_error"),
                "runs": w.get("runs", 0),
                "failures": w.get("failures", 0),
                "seconds_since_last_run": (
                    round(time.time() - w["last_run"], 1) if w.get("last_run") else None
                ),
            }
            for name, w in _WORKERS.items()
        }
    unhealthy = [
        name for name, w in workers.items()
        if w["last_ok"] is False or (w["last_run"] is None)
    ]
    return {
        "status": "degraded" if unhealthy else "ok",
        "unhealthy_workers": unhealthy,
        "workers": workers,
        "uptime_seconds": round(time.time() - _STARTED_AT, 1),
    }
