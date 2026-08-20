# -*- coding: utf-8 -*-
"""P5-03：统一 API Response 格式。

成功：{"success": true, "data": {...}, "error": null, "request_id": "..."}
失败：{"success": false, "data": null, "error": {"code": "...", "message": "..."}, "request_id": "..."}

渐进迁移：新端点直接用这些 helper；旧端点保持兼容，逐步替换。
"""
from __future__ import annotations

from typing import Any, Optional

from zvplatform.obs import get_request_id


def success_response(data: Any = None, message: str = "ok") -> dict:
    """统一成功响应。"""
    return {
        "success": True,
        "data": data,
        "error": None,
        "message": message,
        "request_id": get_request_id(),
    }


def error_response(code: str, message: str, status_code: int = 500,
                   data: Any = None) -> dict:
    """统一错误响应。"""
    return {
        "success": False,
        "data": data,
        "error": {"code": code, "message": message},
        "request_id": get_request_id(),
    }
