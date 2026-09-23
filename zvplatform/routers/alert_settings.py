# -*- coding: utf-8 -*-
"""告警通知与阈值配置路由（P1 告警中心；1.9.55 自 assets_api 迁入 #16 模块化）。

- GET/PUT /api/v1/console/alert-notify-config（通知渠道配置，smtp_password 脱敏）
- POST /api/v1/console/alert-notify-test（逐通道试发）
- GET/PUT /api/v1/console/alert-thresholds（NULL=回落默认，校验范围与 warning<critical）
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

from auth_utils import require_request_permission
from zvplatform.db import create_connection


router = APIRouter(tags=["alert-settings"])


@router.get("/api/v1/console/alert-notify-config")
def get_alert_notify_config_api(request: Request):
    """读取告警通知配置（密码脱敏）。"""
    require_request_permission(getattr(request.state, "auth_user", None), request.url.path, request.method)
    from zvplatform.services.alert_notify import get_notify_config
    conn = create_connection()
    if not conn:
        raise HTTPException(status_code=500, detail="Database connection failed")
    try:
        cfg = get_notify_config(conn)
        cfg["has_smtp_password"] = bool(cfg.get("smtp_password"))
        cfg.pop("smtp_password", None)
        return cfg
    finally:
        conn.close()


@router.put("/api/v1/console/alert-notify-config")
def update_alert_notify_config_api(request: Request, patch: dict):
    """更新告警通知配置（合并式；smtp_password 传空串表示保持不变）。"""
    require_request_permission(getattr(request.state, "auth_user", None), request.url.path, request.method)
    if not isinstance(patch, dict):
        raise HTTPException(status_code=422, detail="Body must be a JSON object")
    from zvplatform.services.alert_notify import update_notify_config
    conn = create_connection()
    if not conn:
        raise HTTPException(status_code=500, detail="Database connection failed")
    try:
        cfg = update_notify_config(conn, patch)
        cfg["has_smtp_password"] = bool(cfg.get("smtp_password"))
        cfg.pop("smtp_password", None)
        return cfg
    finally:
        conn.close()


@router.post("/api/v1/console/alert-notify-test")
def test_alert_notify_config(request: Request):
    """发送测试通知（admin）：按已保存配置逐通道试发，返回每通道结果。"""
    require_request_permission(getattr(request.state, "auth_user", None), request.url.path, request.method)
    from zvplatform.services.alert_notify import send_test_notification
    conn = create_connection()
    if not conn:
        raise HTTPException(status_code=500, detail="Database connection failed")
    try:
        return send_test_notification(conn)
    finally:
        conn.close()


@router.get("/api/v1/console/alert-thresholds")
def get_alert_thresholds_api(request: Request):
    """读取告警阈值配置（NULL = 使用默认）。"""
    require_request_permission(getattr(request.state, "auth_user", None), request.url.path, request.method)
    from zvplatform.services.alert_thresholds import get_threshold_config, get_effective_thresholds
    conn = create_connection()
    if not conn:
        raise HTTPException(status_code=500, detail="Database connection failed")
    try:
        return {
            "config": get_threshold_config(conn),
            "effective": get_effective_thresholds(conn),
        }
    finally:
        conn.close()


@router.put("/api/v1/console/alert-thresholds")
def update_alert_thresholds_api(request: Request, patch: dict):
    """更新告警阈值（合并式；NULL/空串 = 回落默认；校验范围与 warning<critical）。"""
    require_request_permission(getattr(request.state, "auth_user", None), request.url.path, request.method)
    if not isinstance(patch, dict):
        raise HTTPException(status_code=422, detail="Body must be a JSON object")
    from zvplatform.services.alert_thresholds import get_threshold_config, get_effective_thresholds, update_threshold_config
    conn = create_connection()
    if not conn:
        raise HTTPException(status_code=500, detail="Database connection failed")
    try:
        try:
            update_threshold_config(conn, patch)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc))
        return {
            "config": get_threshold_config(conn),
            "effective": get_effective_thresholds(conn),
        }
    finally:
        conn.close()
