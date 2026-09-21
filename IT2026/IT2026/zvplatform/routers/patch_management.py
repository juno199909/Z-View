# -*- coding: utf-8 -*-
"""补丁管理路由（Patch Management Phase 1；1.9.55 自 assets_api 迁入 #16 模块化）。

- GET /api/v1/console/patch-status?asset_id=（各终端 WU 待安装补丁汇总，可按终端过滤）
数据由 Agent 心跳上报写入 agent_patches 表（agent_patches.py 上报链路）。
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, HTTPException, Request

from auth_utils import require_request_permission
from zvplatform.db import create_connection


router = APIRouter(tags=["patch-management"])


@router.get("/api/v1/console/patch-status")
def list_patch_status_api(request: Request, asset_id: Optional[int] = None):
    """补丁状态列表（admin）：各终端 WU 待安装补丁（Patch Management Phase 1）。"""
    require_request_permission(getattr(request.state, "auth_user", None), request.url.path, request.method)
    conn = create_connection()
    if not conn:
        raise HTTPException(status_code=500, detail="Database connection failed")
    cursor = conn.cursor(dictionary=True)
    try:
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS agent_patches (
                asset_id INT PRIMARY KEY,
                pending_count INT NOT NULL DEFAULT 0,
                reboot_required TINYINT(1) NOT NULL DEFAULT 0,
                last_scan DATETIME NULL,
                patches JSON NULL,
                error VARCHAR(500) NULL,
                updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
            )
        """)
        conn.commit()
        if asset_id:
            cursor.execute(
                "SELECT p.*, a.hostname, a.ip_address FROM agent_patches p "
                "JOIN assets a ON a.id = p.asset_id WHERE p.asset_id = %s",
                (asset_id,),
            )
        else:
            cursor.execute(
                "SELECT p.*, a.hostname, a.ip_address FROM agent_patches p "
                "JOIN assets a ON a.id = p.asset_id ORDER BY p.pending_count DESC"
            )
        rows = cursor.fetchall() or []
        total_pending = sum(int(r.get("pending_count") or 0) for r in rows)
        reboot_count = sum(1 for r in rows if r.get("reboot_required"))
        return {"terminals": rows, "total": len(rows),
                "total_pending": total_pending, "reboot_required_count": reboot_count}
    finally:
        cursor.close()
        conn.close()
