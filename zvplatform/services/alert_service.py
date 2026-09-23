# -*- coding: utf-8 -*-
"""告警评估服务（P1-01 从 assets_api 迁出）。"""
from __future__ import annotations

import json

from zvplatform.constants import (
    ALERT_OFFLINE_SECONDS,
    ALERT_THRESHOLDS,
    ALERT_TYPE_LABELS,
)
from zvplatform.common import compute_health_score, format_datetime, safe_float
from zvplatform.repositories.alert_repository import (
    ensure_alerts_table,
    fetch_asset_monitor_rows,
)


def build_current_alerts(rows: list[dict], thresholds: dict | None = None):
    """根据当前资产和心跳生成基础告警。

    thresholds: 生效阈值（V1.9.0 可配置化），None 时回落 constants 默认。
    """
    thresholds = thresholds or ALERT_THRESHOLDS
    offline_seconds = thresholds.get("offline_seconds", ALERT_OFFLINE_SECONDS)
    alerts = []

    for row in rows:
        asset_id = row["asset_id"]
        hostname = row.get("hostname") or f"资产 {asset_id}"
        ip_address = row.get("ip_address") or "-"
        real_status = row.get("real_status") or "offline"
        seconds_since_seen = row.get("seconds_since_seen")
        cpu_usage = safe_float(row.get("cpu_usage"))
        memory_usage = safe_float(row.get("memory_usage"))
        disk_usage = safe_float(row.get("disk_usage"))

        if seconds_since_seen is not None and seconds_since_seen > offline_seconds:
            offline_minutes = max(1, int(round(seconds_since_seen / 60)))
            severity = "critical" if seconds_since_seen >= max(600, offline_seconds * 2) else "warning"
            alerts.append({
                "asset_id": asset_id,
                "alert_type": "offline",
                "severity": severity,
                "message": f"{hostname} 已离线 {offline_minutes} 分钟",
                "current_value": safe_float(seconds_since_seen),
                "threshold_value": float(offline_seconds),
                "details_json": {
                    "hostname": hostname,
                    "ip_address": ip_address,
                    "last_seen": format_datetime(row.get("last_seen")),
                },
                "active_fingerprint": f"{asset_id}:offline",
            })

        if real_status != "online":
            continue

        for alert_type, metric_value in (
            ("cpu", cpu_usage),
            ("memory", memory_usage),
            ("disk", disk_usage),
        ):
            if metric_value is None:
                continue

            thresholds_for_type = thresholds[alert_type]
            severity = None
            threshold_value = None

            if metric_value >= thresholds_for_type["critical"]:
                severity = "critical"
                threshold_value = thresholds_for_type["critical"]
            elif metric_value >= thresholds_for_type["warning"]:
                severity = "warning"
                threshold_value = thresholds_for_type["warning"]

            if not severity:
                continue

            alerts.append({
                "asset_id": asset_id,
                "alert_type": alert_type,
                "severity": severity,
                "message": f"{hostname} {ALERT_TYPE_LABELS[alert_type]}达到 {metric_value:.1f}%",
                "current_value": metric_value,
                "threshold_value": float(threshold_value),
                "details_json": {
                    "hostname": hostname,
                    "ip_address": ip_address,
                    "heartbeat_time": format_datetime(row.get("heartbeat_time")),
                },
                "active_fingerprint": f"{asset_id}:{alert_type}",
            })

        health_score = compute_health_score(real_status, cpu_usage, memory_usage, disk_usage)
        if health_score is not None and health_score < thresholds["health"]["warning"]:
            severity = "critical" if health_score < thresholds["health"]["critical"] else "warning"
            threshold_value = (
                thresholds["health"]["critical"]
                if severity == "critical"
                else thresholds["health"]["warning"]
            )
            alerts.append({
                "asset_id": asset_id,
                "alert_type": "health",
                "severity": severity,
                "message": f"{hostname} 健康度降至 {health_score} 分",
                "current_value": safe_float(health_score),
                "threshold_value": float(threshold_value),
                "details_json": {
                    "hostname": hostname,
                    "ip_address": ip_address,
                    "cpu_usage": cpu_usage,
                    "memory_usage": memory_usage,
                    "disk_usage": disk_usage,
                },
                "active_fingerprint": f"{asset_id}:health",
            })

    return alerts


def sync_alerts(conn):
    """同步当前告警状态到数据库"""
    ensure_alerts_table(conn)

    # V1.9.0：读取生效阈值（DB 覆盖优先，NULL 回落默认）
    try:
        from zvplatform.services.alert_thresholds import get_effective_thresholds
        thresholds = get_effective_thresholds(conn)
    except Exception:
        thresholds = None
    current_alerts = build_current_alerts(fetch_asset_monitor_rows(conn), thresholds)

    # 网络监控告警（断网/丢包/高流量），恢复依赖 fingerprint 自动 resolve（Recovery Event）
    try:
        from zvplatform.services.network_service import build_network_alerts
        current_alerts.extend(build_network_alerts(conn))
    except Exception:
        pass

    cursor = conn.cursor(dictionary=True)

    try:
        cursor.execute("""
            SELECT id, active_fingerprint
            FROM alerts
            WHERE status = 'active' AND active_fingerprint IS NOT NULL
        """)
        existing_alerts = {
            row["active_fingerprint"]: row
            for row in cursor.fetchall()
            if row.get("active_fingerprint")
        }

        current_fingerprints = set()
        newly_triggered = []

        for alert in current_alerts:
            fingerprint = alert["active_fingerprint"]
            current_fingerprints.add(fingerprint)
            is_new = fingerprint not in existing_alerts
            details_json = json.dumps(alert["details_json"], ensure_ascii=False)
            cursor.execute("""
                INSERT INTO alerts (
                    asset_id, alert_type, severity, status, message,
                    current_value, threshold_value, details_json,
                    active_fingerprint, first_triggered_at, last_seen_at,
                    created_at, updated_at
                ) VALUES (
                    %s, %s, %s, 'active', %s,
                    %s, %s, %s,
                    %s, NOW(), NOW(),
                    NOW(), NOW()
                )
                ON DUPLICATE KEY UPDATE
                    asset_id = VALUES(asset_id),
                    alert_type = VALUES(alert_type),
                    severity = VALUES(severity),
                    status = 'active',
                    message = VALUES(message),
                    current_value = VALUES(current_value),
                    threshold_value = VALUES(threshold_value),
                    details_json = VALUES(details_json),
                    last_seen_at = NOW(),
                    resolved_at = NULL,
                    resolved_by = NULL,
                    updated_at = NOW()
            """, (
                alert["asset_id"],
                alert["alert_type"],
                alert["severity"],
                alert["message"],
                alert["current_value"],
                alert["threshold_value"],
                details_json,
                fingerprint,
            ))
            # V1.7.1 通知层：新触发（非恢复重现）的告警返回给通知分发
            if is_new:
                import time as _time

                newly_triggered.append({
                    **alert,
                    "id": cursor.lastrowid,
                    # 补齐 first_triggered_at（SQL 用 NOW()，通知 payload 此前为空串）
                    "first_triggered_at": _time.strftime("%Y-%m-%d %H:%M:%S"),
                })

        stale_fingerprints = set(existing_alerts.keys()) - current_fingerprints
        for fingerprint in stale_fingerprints:
            cursor.execute("""
                UPDATE alerts
                SET status = 'resolved',
                    active_fingerprint = NULL,
                    resolved_at = NOW(),
                    resolved_by = 'system',
                    updated_at = NOW()
                WHERE active_fingerprint = %s
            """, (fingerprint,))

        conn.commit()
        # V1.7.1 通知层：返回本轮新触发的告警（含 DB id，供通知去重打标）
        return newly_triggered
    finally:
        cursor.close()
