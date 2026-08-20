# -*- coding: utf-8 -*-
"""可观测性基础设施（P4-02/P4-03）。

- Request ID 中间件：每请求生成/透传 X-Request-ID，contextvars 全链路可用
- 统一日志格式：timestamp/level/service/request_id/message
"""
from __future__ import annotations

import contextvars
import json
import time
import uuid
from typing import Optional

# 请求级上下文（全链路可读：路由 handler / services / repositories）
request_id_var: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar("request_id", default=None)

SERVICE_NAME = "assets-api"


def new_request_id() -> str:
    return uuid.uuid4().hex[:16]


def get_request_id() -> Optional[str]:
    return request_id_var.get()


def format_log_line(level: str, message: str, service: str = SERVICE_NAME,
                    agent_id: Optional[int] = None, **extra) -> str:
    """统一日志行：timestamp | level | service | request_id | agent_id | message"""
    ts = time.strftime("%Y-%m-%d %H:%M:%S")
    rid = get_request_id() or "-"
    aid = str(agent_id) if agent_id is not None else "-"
    tail = ""
    if extra:
        try:
            tail = " " + json.dumps(extra, ensure_ascii=False, default=str)
        except Exception:
            pass
    return f"{ts} | {level:<5} | {service} | {rid} | {aid} | {message}{tail}"
