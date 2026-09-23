# -*- coding: utf-8 -*-
"""设备凭据管理路由（P0-06；1.9.55 自 assets_api 迁入 #16 模块化）。

- GET /api/v1/console/agent-credentials（注册状态，含未注册资产）
- DELETE /api/v1/console/agent-credentials/{asset_id}（吊销，下次全局 token 心跳自动重签）
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from mysql.connector import Error

from auth_utils import get_request_username, require_request_permission
from zvplatform.db import create_connection, format_datetime
from zvplatform.models import SystemActivityLogCreate
from zvplatform.repositories.agent_credential_repository import ensure_agent_credentials_table
from zvplatform.repositories.log_repository import insert_system_activity_log


router = APIRouter(tags=["agent-credentials"])


def _fmt_dt(value):
    return format_datetime(value)


@router.get("/api/v1/console/agent-credentials")
def list_agent_credentials(request: Request):
    """设备凭据注册状态（含未注册资产）"""
    require_request_permission(getattr(request.state, "auth_user", None), request.url.path, request.method)
    conn = create_connection()
    if not conn:
        raise HTTPException(status_code=500, detail="Database connection failed")
    cursor = conn.cursor(dictionary=True)
    try:
        ensure_agent_credentials_table(conn)
        cursor.execute("""
            SELECT a.id AS asset_id, a.hostname, a.ip_address, a.status, a.last_seen,
                   ac.status AS credential_status, ac.created_at AS enrolled_at, ac.last_used_at
            FROM assets a
            LEFT JOIN agent_credentials ac ON ac.asset_id = a.id
            WHERE a.deleted_at IS NULL
            ORDER BY (ac.asset_id IS NULL) ASC, a.id ASC
        """)
        rows = cursor.fetchall()
        for r in rows:
            r["last_seen"] = _fmt_dt(r.get("last_seen"))
            r["enrolled_at"] = _fmt_dt(r.get("enrolled_at"))
            r["last_used_at"] = _fmt_dt(r.get("last_used_at"))
            r["enrolled"] = r.get("credential_status") == "active"
        enrolled = sum(1 for r in rows if r["enrolled"])
        return {"data": rows, "total": len(rows), "enrolled": enrolled, "pending": len(rows) - enrolled}
    except Error as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        cursor.close()
        conn.close()


@router.delete("/api/v1/console/agent-credentials/{asset_id}")
def revoke_agent_credential(asset_id: int, request: Request):
    """吊销设备凭据：Agent 下次全局 token 心跳时自动重新签发"""
    require_request_permission(getattr(request.state, "auth_user", None), request.url.path, request.method)
    conn = create_connection()
    if not conn:
        raise HTTPException(status_code=500, detail="Database connection failed")
    cursor = conn.cursor()
    try:
        ensure_agent_credentials_table(conn)
        cursor.execute(
            "UPDATE agent_credentials SET status='revoked' WHERE asset_id=%s AND status='active'",
            (asset_id,),
        )
        revoked = cursor.rowcount
        conn.commit()
        if not revoked:
            raise HTTPException(status_code=404, detail="No active credential for this asset")
        conn.commit()
        # 写审计
        try:
            insert_system_activity_log(cursor, SystemActivityLogCreate(
                source_type="platform",
                module="agent_credentials",
                category="security",
                action="agent_credential_revoke",
                level="warning",
                result="success",
                asset_id=asset_id,
                operator_name=get_request_username(request, fallback="console"),
                title="吊销 Agent 设备凭据",
                message=f"Agent 设备凭据已吊销，等待全局 token 心跳重新签发 (asset_id={asset_id})",
                details={"asset_id": asset_id},
            ))
            conn.commit()
        except Error:
            conn.rollback()
        return {"status": "success", "asset_id": asset_id, "revoked": True}
    except HTTPException:
        raise
    except Error as e:
        conn.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        cursor.close()
        conn.close()
