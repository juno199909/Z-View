# -*- coding: utf-8 -*-
"""Agent 运行时共享状态（V1.8.2 迁入自 cmdb_agent_core.py）。

_AGENT_STATE 由心跳/注册/远控等多线程读写（hostname/asset_id/running 等），
_LOCK 为其配套互斥锁。对象由 core 与 zvagent.heartbeat 共享（同一 dict 引用）。
"""
from __future__ import annotations

import socket
import threading
from typing import Any

_AGENT_STATE: dict[str, Any] = {
    "asset_id": None,
    "hostname": socket.gethostname(),
    "main_ip": None,
    "main_mac": None,
    "running": False,
    "threads": {},
}

_LOCK = threading.Lock()
