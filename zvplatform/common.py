# -*- coding: utf-8 -*-
"""纯函数工具集（P1-01 从 assets_api 迁出，原处 import 回来保持兼容）。"""
from __future__ import annotations

import json

from typing import Optional

# 兼容再导出：多个迁移模块从 common 引用 format_datetime（实现位于 zvplatform.db）
from zvplatform.db import format_datetime  # noqa: F401



def safe_float(value):
    """安全转换浮点数"""
    if value is None:
        return None
    try:
        return round(float(value), 2)
    except (TypeError, ValueError):
        return None


def compute_health_score(status: Optional[str], cpu_usage, memory_usage, disk_usage):
    """按终端概览一致的规则计算健康度"""
    if status != "online":
        return 0

    metrics = [cpu_usage, memory_usage, disk_usage]
    if all(metric is None for metric in metrics):
        return None

    score = 40

    if cpu_usage is not None:
        if cpu_usage < 70:
            score += 20
        elif cpu_usage < 80:
            score += 10
        elif cpu_usage < 90:
            score += 5
    else:
        score += 20

    if memory_usage is not None:
        if memory_usage < 80:
            score += 20
        elif memory_usage < 90:
            score += 10
        elif memory_usage < 95:
            score += 5
    else:
        score += 20

    if disk_usage is not None:
        if disk_usage < 85:
            score += 20
        elif disk_usage < 90:
            score += 10
        elif disk_usage < 95:
            score += 5
    else:
        score += 20

    return score


def parse_json_field(value):
    """尽量将 JSON 字段恢复为结构化对象，失败时保留原值。"""
    if value in (None, "", b""):
        return None
    if isinstance(value, (dict, list)):
        return value
    if isinstance(value, (bytes, bytearray)):
        value = value.decode("utf-8", errors="ignore")
    if isinstance(value, str):
        try:
            return json.loads(value)
        except (TypeError, ValueError, json.JSONDecodeError):
            return value
    return value


def truncate_text(value, limit: int = 160) -> str:
    text = (value or "").strip()
    if not text:
        return ""
    if len(text) <= limit:
        return text
    return f"{text[:limit - 3]}..."


def get_request_client_ip(request):
    """提取请求来源 IP，优先取反向代理头（P1-01 从 assets_api 迁出）。"""
    if not request:
        return None

    forwarded_for = (request.headers.get("x-forwarded-for") or "").strip()
    if forwarded_for:
        return forwarded_for.split(",")[0].strip() or None

    real_ip = (request.headers.get("x-real-ip") or "").strip()
    if real_ip:
        return real_ip or None

    client = getattr(request, "client", None)
    return getattr(client, "host", None)
