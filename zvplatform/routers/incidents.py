# -*- coding: utf-8 -*-
"""事件（Incident）控制台 API（P1 告警中心，V1.9.0）。"""
from __future__ import annotations

from typing import Optional

from auth_utils import get_request_username, require_request_permission
from fastapi import APIRouter, HTTPException, Request

from zvplatform.db import create_connection
from zvplatform.services.incident_service import (
    acknowledge_incident,
    close_incident,
    incident_stats,
    list_incidents,
)

router = APIRouter(tags=["incidents"])


def _conn():
    conn = create_connection()
    if not conn:
        raise HTTPException(status_code=500, detail="Database connection failed")
    return conn


@router.get("/api/v1/console/incidents")
def list_incidents_api(request: Request, status: Optional[str] = None, limit: int = 100):
    """事件列表（admin）。"""
    require_request_permission(getattr(request.state, "auth_user", None), request.url.path, request.method)
    conn = _conn()
    try:
        incidents = list_incidents(conn, status, limit)
        return {"incidents": incidents, "total": len(incidents)}
    finally:
        conn.close()


@router.get("/api/v1/console/incidents/stats")
def incident_stats_api(request: Request):
    """事件统计（admin）。"""
    require_request_permission(getattr(request.state, "auth_user", None), request.url.path, request.method)
    conn = _conn()
    try:
        return incident_stats(conn)
    finally:
        conn.close()


@router.post("/api/v1/console/incidents/{incident_id}/acknowledge")
def acknowledge_incident_api(request: Request, incident_id: str):
    """确认事件（admin）。"""
    require_request_permission(getattr(request.state, "auth_user", None), request.url.path, request.method)
    operator = get_request_username(request) or "admin"
    conn = _conn()
    try:
        if not acknowledge_incident(conn, incident_id, operator):
            raise HTTPException(status_code=404, detail="incident not found or already acknowledged")
        return {"incident_id": incident_id, "status": "acknowledged", "acknowledged_by": operator}
    finally:
        conn.close()


@router.post("/api/v1/console/incidents/{incident_id}/close")
def close_incident_api(request: Request, incident_id: str):
    """手动关闭事件（admin）。"""
    require_request_permission(getattr(request.state, "auth_user", None), request.url.path, request.method)
    operator = get_request_username(request) or "admin"
    conn = _conn()
    try:
        if not close_incident(conn, incident_id, operator):
            raise HTTPException(status_code=404, detail="incident not found or already resolved")
        return {"incident_id": incident_id, "status": "resolved", "resolved_by": operator}
    finally:
        conn.close()
