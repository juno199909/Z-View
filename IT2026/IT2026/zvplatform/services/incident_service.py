# -*- coding: utf-8 -*-
"""事件（Incident）聚合服务（P1 告警中心，V1.9.0）。

模型：告警（alert）是单点信号，事件（incident）是聚合的故障单元。
- 一终端一开放事件：同一资产的活跃告警归并进一个 open incident（风暴不刷屏）；
- 新告警挂靠（alert_count++、severity 取最高、last_alert_at 更新）；
- 资产活跃告警全部恢复 → 事件自动 resolved（resolved_by=system）；
- 管理员可 acknowledge（确认）/ close（手动关闭）。

由告警同步线程（60s）在 sync_alerts 后调用 sync_incidents。
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime

from console_utils import safe_console_print

_SEVERITY_ORDER = {"info": 0, "warning": 1, "critical": 2}
_OPEN_STATES = ("open", "acknowledged")

_INCIDENTS_TABLE = "incidents"


def ensure_incidents_table(conn) -> None:
    cursor = conn.cursor()
    try:
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS incidents (
                id BIGINT PRIMARY KEY AUTO_INCREMENT,
                incident_id VARCHAR(64) NOT NULL UNIQUE,
                asset_id INT NOT NULL,
                hostname VARCHAR(100) NULL,
                title VARCHAR(200) NOT NULL,
                severity VARCHAR(20) NOT NULL DEFAULT 'warning',
                status VARCHAR(20) NOT NULL DEFAULT 'open',
                alert_count INT NOT NULL DEFAULT 0,
                alert_types JSON NULL,
                summary TEXT NULL,
                opened_at DATETIME NOT NULL,
                last_alert_at DATETIME NOT NULL,
                acknowledged_at DATETIME NULL,
                acknowledged_by VARCHAR(100) NULL,
                resolved_at DATETIME NULL,
                resolved_by VARCHAR(100) NULL,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                INDEX idx_inc_asset (asset_id),
                INDEX idx_inc_status (status)
            )
        """)
        try:
            cursor.execute("ALTER TABLE alerts ADD COLUMN incident_id BIGINT NULL")
            cursor.execute("CREATE INDEX idx_alert_incident ON alerts (incident_id)")
        except Exception:
            conn.rollback()  # 列/索引已存在
        conn.commit()
    finally:
        cursor.close()


def _max_severity(severities: list) -> str:
    """取最高严重度；info < warning < critical。"""
    if not severities:
        return "warning"
    top, top_rank = None, -1
    for s in severities:
        rank = _SEVERITY_ORDER.get(str(s or "warning"), 1)
        if rank > top_rank:
            top, top_rank = str(s or "warning"), rank
    return top or "warning"


def sync_incidents(conn) -> dict:
    """按当前活跃告警聚合事件（每资产一个开放事件）。返回统计摘要。"""
    ensure_incidents_table(conn)
    cursor = conn.cursor(dictionary=True)
    result = {"opened": 0, "updated": 0, "resolved": 0, "open_total": 0}
    try:
        # 1. 当前活跃告警按资产分组
        cursor.execute("""
            SELECT id, asset_id, alert_type, severity, message, first_triggered_at
            FROM alerts
            WHERE status = 'active' AND asset_id IS NOT NULL
            ORDER BY asset_id, id
        """)
        active_rows = cursor.fetchall() or []
        active_by_asset: dict = {}
        for row in active_rows:
            active_by_asset.setdefault(int(row["asset_id"]), []).append(row)

        # 2. 现有开放事件（含 hostname/title 供恢复通知使用）
        cursor.execute(
            f"SELECT id, incident_id, asset_id, hostname, title, severity, alert_count "
            f"FROM {_INCIDENTS_TABLE} WHERE status IN ('open', 'acknowledged')"
        )
        open_by_asset = {int(r["asset_id"]): r for r in (cursor.fetchall() or [])}
        resolved_incidents: list[dict] = []

        # 3. 主机名补全
        hostnames = {}
        asset_ids = list(active_by_asset.keys())
        if asset_ids:
            placeholders = ",".join(["%s"] * len(asset_ids))
            cursor.execute(
                f"SELECT id, hostname FROM assets WHERE id IN ({placeholders})",
                tuple(asset_ids),
            )
            hostnames = {int(r["id"]): r.get("hostname") for r in (cursor.fetchall() or [])}

        now = datetime.now()

        # 4. 有活跃告警的资产：开新 / 挂靠更新
        for asset_id, alerts in active_by_asset.items():
            severities = [a.get("severity") or "warning" for a in alerts]
            top_severity = _max_severity(severities)
            types = sorted({str(a.get("alert_type")) for a in alerts})
            hostname = hostnames.get(asset_id) or str(asset_id)
            title = f"{hostname}：{len(alerts)} 条活跃告警（{', '.join(types)}）"
            existing = open_by_asset.get(asset_id)
            if existing:
                cursor.execute(
                    f"UPDATE {_INCIDENTS_TABLE} SET alert_count = %s, severity = %s, "
                    f"alert_types = %s, last_alert_at = %s WHERE id = %s",
                    (len(alerts), top_severity, json.dumps(types, ensure_ascii=False),
                     now, existing["id"]),
                )
                result["updated"] += 1
            else:
                # 随机后缀防同秒碰撞（沙箱/重启场景下同一秒可能多次开关）
                incident_id = f"INC-{asset_id}-{now.strftime('%Y%m%d%H%M%S')}-{uuid.uuid4().hex[:6].upper()}"
                cursor.execute(
                    f"INSERT INTO {_INCIDENTS_TABLE} "
                    f"(incident_id, asset_id, hostname, title, severity, status, alert_count, "
                    f"alert_types, summary, opened_at, last_alert_at) "
                    f"VALUES (%s, %s, %s, %s, %s, 'open', %s, %s, %s, %s, %s)",
                    (incident_id, asset_id, hostname, title, top_severity,
                     len(alerts), json.dumps(types, ensure_ascii=False),
                     alerts[0].get("message"), now, now),
                )
                result["opened"] += 1

        # 5. 开放事件但其资产已无活跃告警 → 自动恢复（收集详情供恢复通知）
        for asset_id, existing in open_by_asset.items():
            if asset_id in active_by_asset:
                continue
            cursor.execute(
                f"UPDATE {_INCIDENTS_TABLE} SET status = 'resolved', resolved_at = %s, "
                f"resolved_by = 'system' WHERE id = %s AND status IN ('open', 'acknowledged')",
                (now, existing["id"]),
            )
            result["resolved"] += 1
            resolved_incidents.append({
                "incident_id": existing.get("incident_id"),
                "asset_id": asset_id,
                "hostname": existing.get("hostname"),
                "title": existing.get("title"),
                "alert_count": existing.get("alert_count"),
                "resolved_at": now.strftime("%Y-%m-%d %H:%M:%S"),
            })
        result["resolved_incidents"] = resolved_incidents

        # 6. 活跃告警挂靠 incident_id
        cursor.execute(
            f"SELECT id, asset_id FROM {_INCIDENTS_TABLE} WHERE status IN ('open', 'acknowledged')"
        )
        inc_ids_by_asset = {int(r["asset_id"]): r["id"] for r in (cursor.fetchall() or [])}
        for asset_id, alerts in active_by_asset.items():
            incident_pk = inc_ids_by_asset.get(asset_id)
            if not incident_pk:
                continue
            alert_ids = [a["id"] for a in alerts]
            placeholders = ",".join(["%s"] * len(alert_ids))
            cursor.execute(
                f"UPDATE alerts SET incident_id = %s WHERE id IN ({placeholders})",
                tuple([incident_pk] + alert_ids),
            )

        conn.commit()

        cursor.execute(
            f"SELECT COUNT(*) c FROM {_INCIDENTS_TABLE} WHERE status IN ('open', 'acknowledged')"
        )
        result["open_total"] = (cursor.fetchone() or {}).get("c", 0)
        return result
    except Exception as exc:
        safe_console_print(f"[Incidents] sync failed: {exc}")
        try:
            conn.rollback()
        except Exception:
            pass
        return result
    finally:
        cursor.close()


def acknowledge_incident(conn, incident_id: str, operator: str) -> bool:
    """管理员确认事件（open → acknowledged）。"""
    ensure_incidents_table(conn)
    cursor = conn.cursor()
    try:
        cursor.execute(
            f"UPDATE {_INCIDENTS_TABLE} SET status = 'acknowledged', acknowledged_at = %s, "
            f"acknowledged_by = %s WHERE incident_id = %s AND status = 'open'",
            (datetime.now(), operator, incident_id),
        )
        conn.commit()
        return cursor.rowcount > 0
    finally:
        cursor.close()


def close_incident(conn, incident_id: str, operator: str) -> bool:
    """管理员手动关闭事件（开放状态 → resolved）。"""
    ensure_incidents_table(conn)
    cursor = conn.cursor()
    try:
        cursor.execute(
            f"UPDATE {_INCIDENTS_TABLE} SET status = 'resolved', resolved_at = %s, "
            f"resolved_by = %s WHERE incident_id = %s AND status IN ('open', 'acknowledged')",
            (datetime.now(), operator, incident_id),
        )
        conn.commit()
        return cursor.rowcount > 0
    finally:
        cursor.close()


def list_incidents(conn, status: str | None = None, limit: int = 100) -> list[dict]:
    ensure_incidents_table(conn)
    cursor = conn.cursor(dictionary=True)
    try:
        if status:
            cursor.execute(
                f"SELECT * FROM {_INCIDENTS_TABLE} WHERE status = %s ORDER BY last_alert_at DESC LIMIT %s",
                (status, min(limit, 500)),
            )
        else:
            cursor.execute(
                f"SELECT * FROM {_INCIDENTS_TABLE} ORDER BY last_alert_at DESC LIMIT %s",
                (min(limit, 500),),
            )
        return cursor.fetchall() or []
    finally:
        cursor.close()


def incident_stats(conn) -> dict:
    ensure_incidents_table(conn)
    cursor = conn.cursor(dictionary=True)
    try:
        cursor.execute(f"SELECT status, COUNT(*) c FROM {_INCIDENTS_TABLE} GROUP BY status")
        by_status = {r["status"]: r["c"] for r in (cursor.fetchall() or [])}
        cursor.execute(
            f"SELECT COUNT(*) c FROM {_INCIDENTS_TABLE} WHERE severity = 'critical' "
            f"AND status IN ('open', 'acknowledged')"
        )
        critical_open = (cursor.fetchone() or {}).get("c", 0)
        return {
            "open": by_status.get("open", 0),
            "acknowledged": by_status.get("acknowledged", 0),
            "resolved": by_status.get("resolved", 0),
            "critical_open": critical_open,
        }
    finally:
        cursor.close()
