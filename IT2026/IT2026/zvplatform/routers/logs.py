# -*- coding: utf-8 -*-
"""统一日志路由（P1-01 从 assets_api 迁出）。"""
from __future__ import annotations

import csv
from datetime import datetime
import io
from typing import Optional

from fastapi import APIRouter, HTTPException, Query, Request, Response
from mysql.connector import Error

from auth_utils import (
    extract_bearer_token,
    normalize_actor_name,
    require_request_permission,
    verify_access_token,
    verify_agent_token,
)
from zvplatform.db import create_connection, format_datetime
from zvplatform.models import SystemActivityLogCreate
from zvplatform.repositories.log_repository import (
    ensure_system_activity_logs_table,
    insert_system_activity_log,
    normalize_log_row,
)
from zvplatform.services.log_service import (
    build_unified_logs_union,
    build_unified_logs_where,
)


router = APIRouter(prefix="/api/v1/logs", tags=["logs"])

def build_trusted_agent_operator_name(payload: SystemActivityLogCreate) -> str:
    if payload.asset_id:
        return normalize_actor_name(f"agent:{payload.asset_id}", fallback="agent")
    if payload.hostname:
        return normalize_actor_name(f"agent:{payload.hostname}", fallback="agent")
    if payload.ip_address:
        return normalize_actor_name(f"agent:{payload.ip_address}", fallback="agent")
    return "agent"


@router.post("")
def create_system_activity_log(payload: SystemActivityLogCreate, request: Request):
    """写入统一运行时日志。"""
    token = extract_bearer_token(request)
    auth_user = verify_access_token(token)
    agent_auth = None
    if auth_user:
        # 该接口对 Agent 上报做了中间件豁免，平台用户仍必须按 RBAC 校验写入权限。
        require_request_permission(auth_user, request.url.path, request.method)
        request.state.auth_user = auth_user
        payload.operator_name = normalize_actor_name(auth_user.get("username"), fallback="console")
    else:
        agent_auth = verify_agent_token(token)
        if agent_auth:
            request.state.agent_auth = agent_auth
            payload.operator_name = build_trusted_agent_operator_name(payload)

    if not auth_user and not agent_auth:
        raise HTTPException(status_code=401, detail="Unauthorized")

    conn = create_connection()
    if not conn:
        raise HTTPException(status_code=500, detail="Database connection failed")

    cursor = conn.cursor()
    try:
        ensure_system_activity_logs_table(conn)
        log_id, event_time = insert_system_activity_log(cursor, payload)
        conn.commit()

        return {
            "message": "Log created successfully",
            "id": log_id,
            "event_time": format_datetime(event_time),
        }
    except Error as e:
        conn.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        cursor.close()
        conn.close()


@router.get("")
def get_unified_logs(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=200),
    source_type: Optional[str] = Query(default=None),
    module: Optional[str] = Query(default=None),
    category: Optional[str] = Query(default=None),
    asset_id: Optional[int] = Query(default=None),
    level: Optional[str] = Query(default=None),
    result: Optional[str] = Query(default=None),
    operator: Optional[str] = Query(default=None),
    keyword: Optional[str] = Query(default=None),
    start_time: Optional[str] = Query(default=None),
    end_time: Optional[str] = Query(default=None),
):
    """聚合查询统一日志。"""
    conn = create_connection()
    if not conn:
        raise HTTPException(status_code=500, detail="Database connection failed")

    cursor = conn.cursor(dictionary=True)
    try:
        ensure_system_activity_logs_table(conn)
        union_sql = build_unified_logs_union(conn)
        where_sql, params = build_unified_logs_where(
            source_type=source_type,
            module=module,
            category=category,
            asset_id=asset_id,
            keyword=keyword,
            level=level,
            result=result,
            operator=operator,
            start_time=start_time,
            end_time=end_time,
        )

        cursor.execute(f"""
            SELECT COUNT(*) AS total
            FROM ({union_sql}) logs
            WHERE {where_sql}
        """, params)
        total = (cursor.fetchone() or {}).get("total", 0) or 0

        offset = (page - 1) * page_size
        cursor.execute(f"""
            SELECT *
            FROM ({union_sql}) logs
            WHERE {where_sql}
            ORDER BY logs.event_time DESC, logs.source_id DESC
            LIMIT %s OFFSET %s
        """, params + [page_size, offset])
        rows = cursor.fetchall()

        return {
            "data": [normalize_log_row(row) for row in rows],
            "total": total,
            "page": page,
            "page_size": page_size,
        }
    except Error as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        cursor.close()
        conn.close()


@router.get("/stats")
def get_unified_log_stats(
    start_time: Optional[str] = Query(default=None),
    end_time: Optional[str] = Query(default=None),
):
    """获取统一日志统计。"""
    conn = create_connection()
    if not conn:
        raise HTTPException(status_code=500, detail="Database connection failed")

    cursor = conn.cursor(dictionary=True)
    try:
        ensure_system_activity_logs_table(conn)
        union_sql = build_unified_logs_union(conn)
        where_sql, params = build_unified_logs_where(
            start_time=start_time,
            end_time=end_time,
        )

        cursor.execute(f"""
            SELECT
                COUNT(*) AS total,
                COUNT(CASE WHEN logs.event_time >= DATE_SUB(NOW(), INTERVAL 24 HOUR) THEN 1 END) AS total_24h,
                COUNT(CASE WHEN logs.event_time >= DATE_SUB(NOW(), INTERVAL 7 DAY) THEN 1 END) AS total_7days,
                COUNT(CASE WHEN logs.level = 'error' THEN 1 END) AS error_count,
                COUNT(CASE WHEN logs.level = 'warning' THEN 1 END) AS warning_count
            FROM ({union_sql}) logs
            WHERE {where_sql}
        """, params)
        summary = cursor.fetchone() or {}

        cursor.execute(f"""
            SELECT logs.level, COUNT(*) AS count
            FROM ({union_sql}) logs
            WHERE {where_sql}
            GROUP BY logs.level
            ORDER BY count DESC
        """, params)
        by_level = {row["level"] or "unknown": row["count"] for row in cursor.fetchall()}

        cursor.execute(f"""
            SELECT logs.module, COUNT(*) AS count
            FROM ({union_sql}) logs
            WHERE {where_sql}
            GROUP BY logs.module
            ORDER BY count DESC
        """, params)
        by_module = {row["module"] or "unknown": row["count"] for row in cursor.fetchall()}

        cursor.execute(f"""
            SELECT logs.source_type, COUNT(*) AS count
            FROM ({union_sql}) logs
            WHERE {where_sql}
            GROUP BY logs.source_type
            ORDER BY count DESC
        """, params)
        by_source_type = {row["source_type"] or "unknown": row["count"] for row in cursor.fetchall()}

        return {
            "total": summary.get("total", 0) or 0,
            "total_24h": summary.get("total_24h", 0) or 0,
            "total_7days": summary.get("total_7days", 0) or 0,
            "error_count": summary.get("error_count", 0) or 0,
            "warning_count": summary.get("warning_count", 0) or 0,
            "by_level": by_level,
            "by_module": by_module,
            "by_source_type": by_source_type,
        }
    except Error as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        cursor.close()
        conn.close()


@router.get("/export")
def export_unified_logs(
    source_type: Optional[str] = Query(default=None),
    module: Optional[str] = Query(default=None),
    category: Optional[str] = Query(default=None),
    asset_id: Optional[int] = Query(default=None),
    level: Optional[str] = Query(default=None),
    result: Optional[str] = Query(default=None),
    keyword: Optional[str] = Query(default=None),
    start_time: Optional[str] = Query(default=None),
    end_time: Optional[str] = Query(default=None),
):
    """导出统一日志 CSV"""
    conn = create_connection()
    if not conn:
        raise HTTPException(status_code=500, detail="Database connection failed")

    cursor = conn.cursor(dictionary=True)
    try:
        ensure_system_activity_logs_table(conn)
        union_sql = build_unified_logs_union(conn)
        where_sql, params = build_unified_logs_where(
            source_type=source_type,
            module=module,
            category=category,
            asset_id=asset_id,
            keyword=keyword,
            level=level,
            result=result,
            start_time=start_time,
            end_time=end_time,
        )

        cursor.execute(
            f"""
            SELECT *
            FROM ({union_sql}) logs
            WHERE {where_sql}
            ORDER BY logs.event_time DESC, logs.source_id DESC
            LIMIT 10000
            """,
            params,
        )
        rows = cursor.fetchall()

        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow([
            "日志ID", "来源", "模块", "类别", "动作", "级别", "结果",
            "资产ID", "主机名", "IP地址", "操作者", "标题", "消息", "时间",
        ])

        for row in rows:
            normalized = normalize_log_row(row)
            writer.writerow([
                normalized["id"],
                normalized["source_type"] or "",
                normalized["module"] or "",
                normalized["category"] or "",
                normalized["action"] or "",
                normalized["level"] or "",
                normalized["result"] or "",
                normalized["asset_id"] if normalized["asset_id"] is not None else "",
                normalized["hostname"] or "",
                normalized["ip_address"] or "",
                normalized["operator_name"] or "",
                normalized["title"] or "",
                normalized["message"] or "",
                normalized["event_time"] or "",
            ])

        csv_content = "\ufeff" + output.getvalue()
        return Response(
            content=csv_content,
            media_type="text/csv; charset=utf-8",
            headers={
                "Content-Disposition": f"attachment; filename=logs-{datetime.now().strftime('%Y%m%d-%H%M%S')}.csv"
            },
        )
    except Error as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        cursor.close()
        conn.close()


