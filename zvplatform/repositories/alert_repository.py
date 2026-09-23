# -*- coding: utf-8 -*-
"""告警数据访问层（P1-06）。"""
from __future__ import annotations

import json
from typing import Any, Dict, List, Optional, Tuple

from zvplatform.constants import ALERT_ONLINE_SECONDS
from zvplatform.common import safe_float
from zvplatform.db import format_datetime


def ensure_alerts_table(conn):
    """确保告警表存在"""
    cursor = conn.cursor()
    try:
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS alerts (
                id BIGINT PRIMARY KEY AUTO_INCREMENT,
                asset_id BIGINT NOT NULL,
                alert_type VARCHAR(50) NOT NULL,
                severity VARCHAR(20) NOT NULL DEFAULT 'warning',
                status VARCHAR(20) NOT NULL DEFAULT 'active',
                message VARCHAR(500) NOT NULL,
                current_value DECIMAL(10,2) NULL,
                threshold_value DECIMAL(10,2) NULL,
                details_json JSON NULL,
                active_fingerprint VARCHAR(255) NULL,
                first_triggered_at DATETIME NOT NULL,
                last_seen_at DATETIME NOT NULL,
                resolved_at DATETIME NULL,
                resolved_by VARCHAR(100) NULL,
                notified_at DATETIME NULL,
                created_at DATETIME NOT NULL,
                updated_at DATETIME NOT NULL,
                INDEX idx_alert_status (status),
                INDEX idx_alert_asset (asset_id),
                INDEX idx_alert_type (alert_type),
                INDEX idx_alert_last_seen (last_seen_at),
                UNIQUE KEY uk_alert_active_fingerprint (active_fingerprint)
            )
        """)
        # V1.7.1 告警通知：notified_at 增量迁移（存量表无该列；MySQL 无 ADD COLUMN IF NOT EXISTS）
        try:
            cursor.execute("ALTER TABLE alerts ADD COLUMN notified_at DATETIME NULL")
        except Exception:
            conn.rollback()  # 列已存在
        conn.commit()
    finally:
        cursor.close()


def mark_alerts_notified(conn, alert_ids: list):
    """通知分发后打标（去重：每条告警只通知一次，恢复后再次触发视为新告警）"""
    if not alert_ids:
        return
    cursor = conn.cursor()
    try:
        placeholders = ",".join(["%s"] * len(alert_ids))
        cursor.execute(
            f"UPDATE alerts SET notified_at = NOW() WHERE id IN ({placeholders})",
            tuple(alert_ids),
        )
        conn.commit()
    finally:
        cursor.close()


def fetch_asset_monitor_rows(conn, alert_online_seconds: int = ALERT_ONLINE_SECONDS):
    """获取资产与最新监控数据"""
    cursor = conn.cursor(dictionary=True)
    try:
        cursor.execute("""
            SELECT
                a.id AS asset_id,
                a.hostname,
                a.ip_address,
                a.last_seen,
                CASE
                    WHEN a.last_seen IS NULL THEN NULL
                    ELSE TIMESTAMPDIFF(SECOND, a.last_seen, NOW())
                END AS seconds_since_seen,
                CASE
                    WHEN a.last_seen IS NOT NULL
                         AND TIMESTAMPDIFF(SECOND, a.last_seen, NOW()) <= %s
                    THEN 'online'
                    ELSE 'offline'
                END AS real_status,
                h.cpu_usage,
                h.memory_usage,
                h.disk_usage,
                h.heartbeat_time
            FROM assets a
            LEFT JOIN agent_heartbeat h ON h.id = (
                SELECT h2.id
                FROM agent_heartbeat h2
                WHERE h2.asset_id = a.id
                ORDER BY h2.heartbeat_time DESC,
                         CASE
                             WHEN COALESCE(h2.disk_info, '') <> ''
                               OR COALESCE(h2.logged_users, '') <> ''
                               OR COALESCE(h2.process_count, 0) > 0
                               OR COALESCE(h2.cpu_usage, 0) <> 0
                               OR COALESCE(h2.memory_usage, 0) <> 0
                               OR COALESCE(h2.disk_usage, 0) <> 0
                             THEN 0 ELSE 1
                         END,
                         h2.id DESC
                LIMIT 1
            )
            WHERE a.deleted_at IS NULL
        """, (alert_online_seconds,))
        return cursor.fetchall()
    finally:
        cursor.close()


def normalize_alert_row(row: Dict[str, Any]):
    """统一前端告警输出结构"""
    details = row.get("details_json")
    if isinstance(details, str):
        try:
            details = json.loads(details)
        except json.JSONDecodeError:
            details = None

    return {
        "id": row["id"],
        "asset_id": row["asset_id"],
        "hostname": row.get("hostname") or (details or {}).get("hostname") or f"资产 {row['asset_id']}",
        "ip_address": row.get("ip_address") or (details or {}).get("ip_address") or "-",
        "alert_type": row["alert_type"],
        "severity": row["severity"],
        "status": row["status"],
        "message": row["message"],
        "current_value": safe_float(row.get("current_value")),
        "threshold_value": safe_float(row.get("threshold_value")),
        "created_at": format_datetime(row.get("first_triggered_at")),
        "last_seen_at": format_datetime(row.get("last_seen_at")),
        "resolved_at": format_datetime(row.get("resolved_at")),
        "resolved_by": row.get("resolved_by"),
        "details": details,
    }


def build_alert_filters(
    status: Optional[str] = None,
    severity: Optional[str] = None,
    alert_type: Optional[str] = None,
    keyword: Optional[str] = None,
    hostname: Optional[str] = None,
    start_time: Optional[str] = None,
    end_time: Optional[str] = None,
) -> Tuple[str, List[Any]]:
    # 告警中心只展示终端相关告警（排除无 asset_id 的平台级记录）
    where_clauses = ["al.asset_id IS NOT NULL"]
    params: List[Any] = []

    if status:
        where_clauses.append("al.status = %s")
        params.append(status)

    if severity:
        where_clauses.append("al.severity = %s")
        params.append(severity)

    if alert_type:
        where_clauses.append("al.alert_type = %s")
        params.append(alert_type)

    if hostname:
        hostname_like = f"%{hostname}%"
        where_clauses.append("(a.hostname LIKE %s OR a.ip_address LIKE %s)")
        params.extend([hostname_like, hostname_like])

    if keyword:
        keyword_like = f"%{keyword}%"
        where_clauses.append("""
            (
                al.message LIKE %s
                OR a.hostname LIKE %s
                OR a.ip_address LIKE %s
            )
        """)
        params.extend([keyword_like, keyword_like, keyword_like])

    if start_time:
        where_clauses.append("COALESCE(al.first_triggered_at, al.created_at) >= %s")
        params.append(start_time)

    if end_time:
        where_clauses.append("COALESCE(al.first_triggered_at, al.created_at) <= %s")
        params.append(end_time)

    return " AND ".join(where_clauses), params
