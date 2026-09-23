# -*- coding: utf-8 -*-
"""告警中心路由（P1-01 从 assets_api 迁出）。

延迟导入 sync_alerts：告警同步服务已迁至 platform.services.alert_service，
此处直接静态导入（platform 包不依赖 assets_api，无循环）。
"""
from __future__ import annotations

import csv
import datetime
import io
from typing import Optional

from fastapi import APIRouter, HTTPException, Query, Request, Response
from mysql.connector import Error
from pydantic import BaseModel

from auth_utils import get_request_username
from zvplatform.db import create_connection
from zvplatform.repositories.alert_repository import (
    build_alert_filters,
    ensure_alerts_table,
    normalize_alert_row,
)
from zvplatform.services.alert_service import sync_alerts


router = APIRouter(prefix="/api/v1/alerts", tags=["alerts"])


class AlertBatchResolveRequest(BaseModel):
    ids: list[int]


@router.get("/stats")
def get_alert_stats():
    """获取告警统计"""
    conn = create_connection()
    if not conn:
        raise HTTPException(status_code=500, detail="Database connection failed")

    try:
        ensure_alerts_table(conn)
        try:
            sync_alerts(conn)
        except Exception as sync_error:
            conn.rollback()
            print(f"[Alerts] Sync skipped during stats request: {sync_error}")

        cursor = conn.cursor(dictionary=True)

        cursor.execute("""
            SELECT
                COUNT(CASE WHEN first_triggered_at >= DATE_SUB(NOW(), INTERVAL 7 DAY) THEN 1 END) AS total_7days,
                COUNT(CASE WHEN status = 'active' THEN 1 END) AS active,
                COUNT(CASE WHEN status = 'resolved' THEN 1 END) AS resolved
            FROM alerts
        """)
        summary = cursor.fetchone() or {}

        cursor.execute("""
            SELECT severity, COUNT(*) AS count
            FROM alerts
            WHERE status = 'active'
            GROUP BY severity
        """)
        by_severity = {row["severity"]: row["count"] for row in cursor.fetchall()}

        cursor.execute("""
            SELECT alert_type, COUNT(*) AS count
            FROM alerts
            WHERE status = 'active'
            GROUP BY alert_type
        """)
        by_type = {row["alert_type"]: row["count"] for row in cursor.fetchall()}

        return {
            "total_7days": summary.get("total_7days", 0) or 0,
            "active": summary.get("active", 0) or 0,
            "resolved": summary.get("resolved", 0) or 0,
            "unresolved": summary.get("active", 0) or 0,
            "by_severity": by_severity,
            "by_type": by_type,
        }
    except Error as e:
        raise HTTPException(status_code=500, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        if "cursor" in locals():
            cursor.close()
        conn.close()


@router.get("")
def get_alerts(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=100),
    status: Optional[str] = Query(default=None),
    severity: Optional[str] = Query(default=None),
    alert_type: Optional[str] = Query(default=None),
    keyword: Optional[str] = Query(default=None),
    hostname: Optional[str] = Query(default=None),
    start_time: Optional[str] = Query(default=None),
    end_time: Optional[str] = Query(default=None),
):
    """获取告警列表"""
    conn = create_connection()
    if not conn:
        raise HTTPException(status_code=500, detail="Database connection failed")

    cursor = conn.cursor(dictionary=True)
    try:
        sync_alerts(conn)
        where_sql, params = build_alert_filters(
            status=status,
            severity=severity,
            alert_type=alert_type,
            keyword=keyword,
            hostname=hostname,
            start_time=start_time,
            end_time=end_time,
        )

        cursor.execute(f"""
            SELECT COUNT(*) AS total
            FROM alerts al
            LEFT JOIN assets a ON a.id = al.asset_id
            WHERE {where_sql}
        """, params)
        total = cursor.fetchone()["total"]

        offset = (page - 1) * page_size
        cursor.execute(f"""
            SELECT
                al.*,
                a.hostname,
                a.ip_address
            FROM alerts al
            LEFT JOIN assets a ON a.id = al.asset_id
            WHERE {where_sql}
            ORDER BY
                CASE al.status WHEN 'active' THEN 0 ELSE 1 END,
                CASE al.severity WHEN 'critical' THEN 0 WHEN 'warning' THEN 1 ELSE 2 END,
                al.last_seen_at DESC,
                al.id DESC
            LIMIT %s OFFSET %s
        """, params + [page_size, offset])
        rows = cursor.fetchall()

        return {
            "data": [normalize_alert_row(row) for row in rows],
            "total": total,
            "page": page,
            "page_size": page_size,
        }
    except Error as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        cursor.close()
        conn.close()


@router.get("/export")
def export_alerts(
    status: Optional[str] = Query(default=None),
    severity: Optional[str] = Query(default=None),
    alert_type: Optional[str] = Query(default=None),
    keyword: Optional[str] = Query(default=None),
    start_time: Optional[str] = Query(default=None),
    end_time: Optional[str] = Query(default=None),
):
    """导出告警列表 CSV"""
    conn = create_connection()
    if not conn:
        raise HTTPException(status_code=500, detail="Database connection failed")

    severity_labels = {
        "critical": "严重",
        "error": "错误",
        "warning": "警告",
        "info": "信息",
    }
    type_labels = {
        "cpu": "CPU",
        "memory": "内存",
        "disk": "磁盘",
        "offline": "离线",
        "health": "健康度",
        "warranty": "保修",
    }
    status_labels = {
        "active": "活跃",
        "resolved": "已解决",
    }

    cursor = conn.cursor(dictionary=True)
    try:
        sync_alerts(conn)
        where_sql, params = build_alert_filters(
            status=status,
            severity=severity,
            alert_type=alert_type,
            keyword=keyword,
            start_time=start_time,
            end_time=end_time,
        )
        cursor.execute(
            f"""
            SELECT
                al.*,
                a.hostname,
                a.ip_address
            FROM alerts al
            LEFT JOIN assets a ON a.id = al.asset_id
            WHERE {where_sql}
            ORDER BY
                CASE al.status WHEN 'active' THEN 0 ELSE 1 END,
                CASE al.severity WHEN 'critical' THEN 0 WHEN 'warning' THEN 1 ELSE 2 END,
                al.last_seen_at DESC,
                al.id DESC
            """,
            params,
        )
        rows = cursor.fetchall()

        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow([
            "告警ID",
            "资产ID",
            "主机名",
            "IP地址",
            "告警类型",
            "严重程度",
            "状态",
            "告警信息",
            "当前值",
            "阈值",
            "首次触发时间",
            "最近出现时间",
            "解决时间",
            "解决人",
        ])

        for row in rows:
            normalized = normalize_alert_row(row)
            writer.writerow([
                normalized["id"],
                normalized["asset_id"],
                normalized["hostname"],
                normalized["ip_address"],
                type_labels.get(normalized["alert_type"], normalized["alert_type"]),
                severity_labels.get(normalized["severity"], normalized["severity"]),
                status_labels.get(normalized["status"], normalized["status"]),
                normalized["message"],
                normalized["current_value"] if normalized["current_value"] is not None else "",
                normalized["threshold_value"] if normalized["threshold_value"] is not None else "",
                normalized["created_at"] or "",
                normalized["last_seen_at"] or "",
                normalized["resolved_at"] or "",
                normalized["resolved_by"] or "",
            ])

        csv_content = "\ufeff" + output.getvalue()
        return Response(
            content=csv_content,
            media_type="text/csv; charset=utf-8",
            headers={
                "Content-Disposition": f"attachment; filename=alerts-{datetime.datetime.now().strftime('%Y%m%d-%H%M%S')}.csv"
            },
        )
    except Error as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        cursor.close()
        conn.close()


@router.get("/{alert_id}/detail")
def get_alert_detail(alert_id: int):
    """获取告警详情"""
    conn = create_connection()
    if not conn:
        raise HTTPException(status_code=500, detail="Database connection failed")

    cursor = conn.cursor(dictionary=True)
    try:
        sync_alerts(conn)
        cursor.execute("""
            SELECT
                al.*,
                a.hostname,
                a.ip_address
            FROM alerts al
            LEFT JOIN assets a ON a.id = al.asset_id
            WHERE al.id = %s
            LIMIT 1
        """, (alert_id,))
        row = cursor.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Alert not found")
        return normalize_alert_row(row)
    except Error as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        cursor.close()
        conn.close()


@router.post("/resolve-batch")
def resolve_alerts_batch(payload: AlertBatchResolveRequest, request: Request):
    """批量标记告警为已解决"""
    if not payload.ids:
        raise HTTPException(status_code=400, detail="Alert ids are required")

    conn = create_connection()
    if not conn:
        raise HTTPException(status_code=500, detail="Database connection failed")

    cursor = conn.cursor(dictionary=True)
    try:
        ensure_alerts_table(conn)
        placeholders = ", ".join(["%s"] * len(payload.ids))

        cursor.execute(
            f"""
            SELECT id, status
            FROM alerts
            WHERE id IN ({placeholders})
            """,
            payload.ids,
        )
        rows = cursor.fetchall()
        if not rows:
            raise HTTPException(status_code=404, detail="No alerts found")

        existing_ids = {row["id"] for row in rows}
        missing_ids = [alert_id for alert_id in payload.ids if alert_id not in existing_ids]

        active_ids = [row["id"] for row in rows if row["status"] == "active"]
        resolved_by = get_request_username(request, fallback="console")

        resolved_count = 0
        if active_ids:
            active_placeholders = ", ".join(["%s"] * len(active_ids))
            cursor.execute(
                f"""
                UPDATE alerts
                SET status = 'resolved',
                    active_fingerprint = NULL,
                    resolved_at = NOW(),
                    resolved_by = %s,
                    updated_at = NOW()
                WHERE id IN ({active_placeholders})
                """,
                [resolved_by] + active_ids,
            )
            resolved_count = cursor.rowcount or 0
            conn.commit()

        return {
            "message": "Batch resolve completed",
            "requested": len(payload.ids),
            "resolved": resolved_count,
            "resolved_count": resolved_count,
            "already_resolved": len(rows) - len(active_ids),
            "missing_ids": missing_ids,
        }
    except HTTPException:
        raise
    except Error as e:
        conn.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        cursor.close()
        conn.close()


@router.put("/{alert_id}/resolve")
def resolve_alert(alert_id: int, request: Request):
    """标记告警为已解决"""
    conn = create_connection()
    if not conn:
        raise HTTPException(status_code=500, detail="Database connection failed")

    cursor = conn.cursor(dictionary=True)
    try:
        ensure_alerts_table(conn)

        cursor.execute("""
            SELECT id, status
            FROM alerts
            WHERE id = %s
        """, (alert_id,))
        row = cursor.fetchone()

        if not row:
            raise HTTPException(status_code=404, detail="Alert not found")

        if row["status"] != "active":
            return {"message": "Alert already resolved"}

        resolved_by = get_request_username(request, fallback="console")
        cursor.execute("""
            UPDATE alerts
            SET status = 'resolved',
                active_fingerprint = NULL,
                resolved_at = NOW(),
                resolved_by = %s,
                updated_at = NOW()
            WHERE id = %s
        """, (resolved_by, alert_id))
        conn.commit()

        return {"message": "Alert resolved successfully"}
    except Error as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        cursor.close()
        conn.close()
