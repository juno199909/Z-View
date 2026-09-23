# -*- coding: utf-8 -*-
"""Agent 控制通道客户端工具（P1-01 从 assets_api 迁出）。"""
from __future__ import annotations

from typing import Dict, Optional

from auth_utils import get_expected_agent_token

AGENT_CONTROL_PORT = int(__import__("os").environ.get("ZVIEW_AGENT_CONTROL_PORT", "9001") or "9001")


def build_agent_auth_headers(extra_headers: Optional[Dict[str, str]] = None) -> Dict[str, str]:
    headers: Dict[str, str] = {}
    token = str(get_expected_agent_token() or "").strip()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if extra_headers:
        headers.update(extra_headers)
    return headers
