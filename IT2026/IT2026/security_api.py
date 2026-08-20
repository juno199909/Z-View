"""
Z-View 终端安全管理 API
挂载到主后端 assets_api (8080)，复用认证/DB/资产体系。
功能: 安全总览/终端安全/安全事件中心/防火墙/USB/程序管控/文件保护/行为监控/策略中心/远程运维/Agent上报
"""

import json
import datetime
from datetime import datetime as dt, timedelta
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, FastAPI, HTTPException, Query, Request
from pydantic import BaseModel, Field
import mysql.connector
from mysql.connector import Error

from auth_utils import get_request_username
from console_utils import safe_console_print
from config_utils import get_db_config


router = APIRouter(prefix="/api/v1/security", tags=["security"])


def get_db():
    try:
        conn = mysql.connector.connect(**get_db_config())
        cur = conn.cursor()
        cur.execute("SET time_zone = '+8:00'")
        cur.close()
        return conn
    except Error as e:
        safe_console_print(f"[SecurityAPI] DB connect failed: {e}")
        return None


def fmt_dt(value):
    if not value:
        return None
    if isinstance(value, dt):
        return value.strftime('%Y-%m-%d %H:%M:%S')
    return str(value)


# ============================================================
# ensure tables (idempotent, called at startup)
# ============================================================

def ensure_security_tables(conn):
    cur = conn.cursor()
    sql = """
    CREATE TABLE IF NOT EXISTS security_policies (
        id BIGINT PRIMARY KEY AUTO_INCREMENT,
        policy_name VARCHAR(255) NOT NULL,
        policy_type ENUM('firewall','usb','app_control','file_protect','behavior') NOT NULL,
        description TEXT,
        enabled BOOLEAN DEFAULT TRUE,
        priority INT DEFAULT 0,
        version INT DEFAULT 1,
        config_json LONGTEXT NOT NULL,
        created_by VARCHAR(100),
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
        updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
        INDEX idx_type_enabled (policy_type, enabled),
        INDEX idx_priority (priority),
        INDEX idx_name (policy_name)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
    CREATE TABLE IF NOT EXISTS security_policy_versions (
        id BIGINT PRIMARY KEY AUTO_INCREMENT,
        policy_id BIGINT NOT NULL,
        version INT NOT NULL,
        config_json LONGTEXT NOT NULL,
        changed_by VARCHAR(100),
        change_note VARCHAR(500),
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
        INDEX idx_policy (policy_id),
        UNIQUE KEY uk_policy_version (policy_id, version),
        FOREIGN KEY (policy_id) REFERENCES security_policies(id) ON DELETE CASCADE
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
    CREATE TABLE IF NOT EXISTS security_policy_bindings (
        id BIGINT PRIMARY KEY AUTO_INCREMENT,
        policy_id BIGINT NOT NULL,
        scope_type ENUM('global','group','asset') NOT NULL,
        scope_id BIGINT NULL,
        enabled BOOLEAN DEFAULT TRUE,
        created_by VARCHAR(100),
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
        INDEX idx_policy (policy_id),
        INDEX idx_scope (scope_type, scope_id),
        FOREIGN KEY (policy_id) REFERENCES security_policies(id) ON DELETE CASCADE
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
    CREATE TABLE IF NOT EXISTS security_policy_exec_results (
        id BIGINT PRIMARY KEY AUTO_INCREMENT,
        policy_id BIGINT NOT NULL,
        asset_id BIGINT UNSIGNED NOT NULL,
        scope_type VARCHAR(20),
        status ENUM('pending','success','failed','partial') NOT NULL DEFAULT 'pending',
        applied_rules INT DEFAULT 0,
        failed_rules INT DEFAULT 0,
        error_detail TEXT,
        executed_at DATETIME,
        reported_at DATETIME,
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
        INDEX idx_policy_asset (policy_id, asset_id),
        INDEX idx_status (status),
        INDEX idx_asset (asset_id),
        FOREIGN KEY (policy_id) REFERENCES security_policies(id) ON DELETE CASCADE
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
    CREATE TABLE IF NOT EXISTS security_events (
        id BIGINT PRIMARY KEY AUTO_INCREMENT,
        asset_id BIGINT UNSIGNED NOT NULL,
        event_type ENUM('virus','suspicious_process','usb','firewall','app_control',
                        'file_anomaly','registry_anomaly','network_anomaly','policy_exec') NOT NULL,
        severity ENUM('info','low','medium','high','critical') NOT NULL,
        title VARCHAR(255),
        description TEXT,
        process_name VARCHAR(255),
        process_pid INT,
        file_path VARCHAR(500),
        remote_ip VARCHAR(45),
        remote_port INT,
        details_json LONGTEXT,
        status ENUM('open','processing','resolved','ignored') DEFAULT 'open',
        handler VARCHAR(100),
        handle_note TEXT,
        handled_at DATETIME,
        occurred_at DATETIME NOT NULL,
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
        INDEX idx_asset_time (asset_id, occurred_at),
        INDEX idx_type_severity (event_type, severity),
        INDEX idx_status (status),
        INDEX idx_occurred (occurred_at),
        FOREIGN KEY (asset_id) REFERENCES assets(id) ON DELETE CASCADE
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
    CREATE TABLE IF NOT EXISTS usb_devices (
        id BIGINT PRIMARY KEY AUTO_INCREMENT,
        asset_id BIGINT UNSIGNED NOT NULL,
        device_id VARCHAR(255),
        vid_pid VARCHAR(20),
        serial_number VARCHAR(255),
        device_class VARCHAR(50),
        friendly_name VARCHAR(255),
        manufacturer VARCHAR(255),
        first_seen DATETIME,
        last_seen DATETIME,
        status ENUM('allowed','blocked','unknown') DEFAULT 'unknown',
        INDEX idx_asset (asset_id),
        INDEX idx_vidpid (vid_pid),
        INDEX idx_device_id (device_id)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
    CREATE TABLE IF NOT EXISTS usb_events (
        id BIGINT PRIMARY KEY AUTO_INCREMENT,
        asset_id BIGINT UNSIGNED NOT NULL,
        device_id VARCHAR(255),
        vid_pid VARCHAR(20),
        event_type ENUM('insert','remove','blocked','allowed') NOT NULL,
        device_class VARCHAR(50),
        friendly_name VARCHAR(255),
        details_json TEXT,
        occurred_at DATETIME NOT NULL,
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
        INDEX idx_asset_time (asset_id, occurred_at),
        INDEX idx_event_type (event_type),
        INDEX idx_device_id (device_id)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
    CREATE TABLE IF NOT EXISTS process_launch_logs (
        id BIGINT PRIMARY KEY AUTO_INCREMENT,
        asset_id BIGINT UNSIGNED NOT NULL,
        process_name VARCHAR(255),
        pid INT,
        ppid INT,
        path VARCHAR(500),
        cmd_line TEXT,
        user VARCHAR(100),
        md5 VARCHAR(32),
        matched_policy VARCHAR(255),
        action ENUM('allowed','blocked','alerted') DEFAULT 'allowed',
        launched_at DATETIME NOT NULL,
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
        INDEX idx_asset_time (asset_id, launched_at),
        INDEX idx_process_name (process_name),
        INDEX idx_action (action)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
    CREATE TABLE IF NOT EXISTS file_protect_baselines (
        id BIGINT PRIMARY KEY AUTO_INCREMENT,
        asset_id BIGINT UNSIGNED NOT NULL,
        dir_path VARCHAR(500) NOT NULL,
        file_path VARCHAR(500) NOT NULL,
        file_size BIGINT,
        md5 VARCHAR(32),
        baseline_at DATETIME,
        INDEX idx_asset_dir (asset_id, dir_path),
        INDEX idx_file_path (file_path)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
    CREATE TABLE IF NOT EXISTS file_anomaly_events (
        id BIGINT PRIMARY KEY AUTO_INCREMENT,
        asset_id BIGINT UNSIGNED NOT NULL,
        dir_path VARCHAR(500),
        file_path VARCHAR(500) NOT NULL,
        anomaly_type ENUM('modified','created','deleted','renamed','mass_change') NOT NULL,
        old_md5 VARCHAR(32),
        new_md5 VARCHAR(32),
        process_name VARCHAR(255),
        details_json TEXT,
        occurred_at DATETIME NOT NULL,
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
        INDEX idx_asset_time (asset_id, occurred_at),
        INDEX idx_dir (dir_path),
        INDEX idx_anomaly_type (anomaly_type)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
    """
    for stmt in [s.strip() for s in sql.split(';') if s.strip()]:
        try:
            cur.execute(stmt)
        except Error as e:
            safe_console_print(f"[SecurityAPI] ensure table warn: {e}")
    cur.close()


# ============================================================
# 安全总览
# ============================================================

@router.get("/overview")
def security_overview():
    """安全态势总览"""
    conn = get_db()
    if not conn:
        raise HTTPException(status_code=500, detail="Database connection failed")
    cur = conn.cursor(dictionary=True)
    try:
        ensure_security_tables(conn)
        # 终端总数与在线数
        cur.execute("""
            SELECT
                COUNT(*) AS total,
                SUM(CASE WHEN last_seen IS NOT NULL AND TIMESTAMPDIFF(SECOND,last_seen,NOW())<=90 THEN 1 ELSE 0 END) AS online
            FROM assets WHERE deleted_at IS NULL
        """)
        asset_row = cur.fetchone() or {}
        total = int(asset_row.get("total") or 0)
        online = int(asset_row.get("online") or 0)

        # 安全事件统计
        cur.execute("""
            SELECT
                COUNT(*) AS total_events,
                SUM(CASE WHEN status='open' THEN 1 ELSE 0 END) AS open_events,
                SUM(CASE WHEN severity='critical' THEN 1 ELSE 0 END) AS critical,
                SUM(CASE WHEN severity='high' THEN 1 ELSE 0 END) AS high,
                SUM(CASE WHEN occurred_at >= DATE_SUB(NOW(),INTERVAL 24 HOUR) THEN 1 ELSE 0 END) AS events_24h
            FROM security_events
        """)
        ev_row = cur.fetchone() or {}

        # 事件按类型
        cur.execute("""
            SELECT event_type, COUNT(*) AS count
            FROM security_events
            WHERE occurred_at >= DATE_SUB(NOW(), INTERVAL 7 DAY)
            GROUP BY event_type ORDER BY count DESC
        """)
        by_type = {r["event_type"]: int(r["count"]) for r in cur.fetchall()}

        # 事件按级别
        cur.execute("""
            SELECT severity, COUNT(*) AS count
            FROM security_events
            WHERE status='open'
            GROUP BY severity
        """)
        by_severity = {r["severity"]: int(r["count"]) for r in cur.fetchall()}

        # 策略覆盖
        cur.execute("SELECT COUNT(*) AS c FROM security_policies WHERE enabled=TRUE")
        active_policies = int((cur.fetchone() or {}).get("c") or 0)
        cur.execute("SELECT COUNT(*) AS c FROM security_policy_bindings WHERE enabled=TRUE")
        active_bindings = int((cur.fetchone() or {}).get("c") or 0)

        # 风险终端（有 open critical/high 事件的终端）
        cur.execute("""
            SELECT a.id, a.hostname, a.ip_address, COUNT(*) AS event_count
            FROM security_events se
            JOIN assets a ON a.id = se.asset_id
            WHERE se.status='open' AND se.severity IN ('critical','high')
            GROUP BY a.id, a.hostname, a.ip_address
            ORDER BY event_count DESC LIMIT 10
        """)
        risk_terminals = []
        for r in cur.fetchall():
            risk_terminals.append({
                "id": r["id"], "hostname": r["hostname"],
                "ip_address": r["ip_address"], "event_count": int(r["event_count"]),
            })

        return {
            "terminals": {"total": total, "online": online, "offline": total - online},
            "events": {
                "total": int(ev_row.get("total_events") or 0),
                "open": int(ev_row.get("open_events") or 0),
                "critical": int(ev_row.get("critical") or 0),
                "high": int(ev_row.get("high") or 0),
                "events_24h": int(ev_row.get("events_24h") or 0),
                "by_type": by_type,
                "by_severity": by_severity,
            },
            "policies": {"active": active_policies, "bindings": active_bindings},
            "risk_terminals": risk_terminals,
        }
    except Error as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        cur.close()
        conn.close()


# ============================================================
# 终端安全状态
# ============================================================

@router.get("/terminals")
def security_terminals(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=200),
    keyword: Optional[str] = Query(None),
    status: Optional[str] = Query(None),
):
    """终端安全状态列表（关联资产 + 安全事件数）"""
    conn = get_db()
    if not conn:
        raise HTTPException(status_code=500, detail="Database connection failed")
    cur = conn.cursor(dictionary=True)
    try:
        ensure_security_tables(conn)
        where = ["a.deleted_at IS NULL"]
        params = []
        if keyword:
            where.append("(a.hostname LIKE %s OR a.ip_address LIKE %s)")
            params += [f"%{keyword}%", f"%{keyword}%"]
        where_sql = " AND ".join(where)

        cur.execute(f"SELECT COUNT(*) AS total FROM assets a WHERE {where_sql}", params)
        total = (cur.fetchone() or {}).get("total", 0)

        offset = (page - 1) * page_size
        cur.execute(f"""
            SELECT a.id, a.hostname, a.ip_address, a.mac_address, a.os_type, a.os_version,
                   a.status, a.agent_install_status, a.agent_version, a.last_seen,
                   CASE WHEN a.last_seen IS NOT NULL AND TIMESTAMPDIFF(SECOND,a.last_seen,NOW())<=90
                        THEN 'online' ELSE 'offline' END AS real_status,
                   (SELECT COUNT(*) FROM security_events se WHERE se.asset_id=a.id AND se.status='open') AS open_events,
                   (SELECT MAX(se.occurred_at) FROM security_events se WHERE se.asset_id=a.id) AS last_event_time
            FROM assets a
            WHERE {where_sql}
            ORDER BY open_events DESC, a.id ASC
            LIMIT %s OFFSET %s
        """, params + [page_size, offset])
        rows = cur.fetchall()
        for r in rows:
            r["last_seen"] = fmt_dt(r.get("last_seen"))
            r["last_event_time"] = fmt_dt(r.get("last_event_time"))
        return {"data": rows, "total": total, "page": page, "page_size": page_size}
    except Error as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        cur.close()
        conn.close()


@router.get("/terminals/{asset_id}")
def security_terminal_detail(asset_id: int):
    """单终端安全详情"""
    conn = get_db()
    if not conn:
        raise HTTPException(status_code=500, detail="Database connection failed")
    cur = conn.cursor(dictionary=True)
    try:
        ensure_security_tables(conn)
        cur.execute("""
            SELECT id, hostname, ip_address, mac_address, os_type, os_version,
                   status, agent_install_status, agent_version, last_seen
            FROM assets WHERE id=%s AND deleted_at IS NULL
        """, (asset_id,))
        asset = cur.fetchone()
        if not asset:
            raise HTTPException(status_code=404, detail="Asset not found")
        asset["last_seen"] = fmt_dt(asset.get("last_seen"))

        # 事件统计
        cur.execute("""
            SELECT severity, COUNT(*) AS count FROM security_events
            WHERE asset_id=%s GROUP BY severity
        """, (asset_id,))
        events_by_severity = {r["severity"]: int(r["count"]) for r in cur.fetchall()}

        cur.execute("""
            SELECT event_type, COUNT(*) AS count FROM security_events
            WHERE asset_id=%s GROUP BY event_type
        """, (asset_id,))
        events_by_type = {r["event_type"]: int(r["count"]) for r in cur.fetchall()}

        # 最近事件
        cur.execute("""
            SELECT id, event_type, severity, title, status, occurred_at
            FROM security_events WHERE asset_id=%s
            ORDER BY occurred_at DESC LIMIT 10
        """, (asset_id,))
        recent_events = []
        for r in cur.fetchall():
            r["occurred_at"] = fmt_dt(r.get("occurred_at"))
            recent_events.append(r)

        # USB 设备数
        cur.execute("SELECT COUNT(*) AS c FROM usb_devices WHERE asset_id=%s", (asset_id,))
        usb_count = int((cur.fetchone() or {}).get("c") or 0)

        # 绑定的策略
        cur.execute("""
            SELECT sp.id, sp.policy_name, sp.policy_type, spb.scope_type
            FROM security_policy_bindings spb
            JOIN security_policies sp ON sp.id=spb.policy_id
            WHERE spb.enabled=TRUE AND (
                (spb.scope_type='global') OR
                (spb.scope_type='asset' AND spb.scope_id=%s) OR
                (spb.scope_type='group' AND spb.scope_id=(SELECT group_id FROM assets WHERE id=%s))
            )
        """, (asset_id, asset_id))
        policies = cur.fetchall()

        return {
            "asset": asset,
            "events": {
                "by_severity": events_by_severity,
                "by_type": events_by_type,
                "recent": recent_events,
            },
            "usb_devices": usb_count,
            "policies": policies,
        }
    except HTTPException:
        raise
    except Error as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        cur.close()
        conn.close()


# ============================================================
# 安全事件中心
# ============================================================

class SecurityEventHandle(BaseModel):
    status: str = Field(..., description="open/processing/resolved/ignored")
    handle_note: Optional[str] = None


class SecurityEventBatchHandle(BaseModel):
    event_ids: List[int]
    status: str
    handle_note: Optional[str] = None


@router.get("/events")
def list_security_events(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=200),
    event_type: Optional[str] = Query(None),
    severity: Optional[str] = Query(None),
    status: Optional[str] = Query(None),
    asset_id: Optional[int] = Query(None),
    keyword: Optional[str] = Query(None),
    start_time: Optional[str] = Query(None),
    end_time: Optional[str] = Query(None),
):
    """安全事件列表"""
    conn = get_db()
    if not conn:
        raise HTTPException(status_code=500, detail="Database connection failed")
    cur = conn.cursor(dictionary=True)
    try:
        ensure_security_tables(conn)
        where = []
        params = []
        if event_type:
            where.append("se.event_type=%s"); params.append(event_type)
        if severity:
            where.append("se.severity=%s"); params.append(severity)
        if status:
            where.append("se.status=%s"); params.append(status)
        if asset_id:
            where.append("se.asset_id=%s"); params.append(asset_id)
        if keyword:
            where.append("(se.title LIKE %s OR se.description LIKE %s OR se.process_name LIKE %s)")
            params += [f"%{keyword}%"] * 3
        if start_time:
            where.append("se.occurred_at>=%s"); params.append(start_time)
        if end_time:
            where.append("se.occurred_at<=%s"); params.append(end_time)
        where_sql = (" WHERE " + " AND ".join(where)) if where else ""

        cur.execute(f"""
            SELECT COUNT(*) AS total FROM security_events se {where_sql}
        """, params)
        total = (cur.fetchone() or {}).get("total", 0)

        offset = (page - 1) * page_size
        cur.execute(f"""
            SELECT se.*, a.hostname, a.ip_address
            FROM security_events se
            LEFT JOIN assets a ON a.id=se.asset_id
            {where_sql}
            ORDER BY se.occurred_at DESC
            LIMIT %s OFFSET %s
        """, params + [page_size, offset])
        rows = cur.fetchall()
        for r in rows:
            r["occurred_at"] = fmt_dt(r.get("occurred_at"))
            r["handled_at"] = fmt_dt(r.get("handled_at"))
            r["created_at"] = fmt_dt(r.get("created_at"))
            if r.get("details_json"):
                try:
                    r["details"] = json.loads(r["details_json"])
                except Exception:
                    r["details"] = None
                r.pop("details_json")
        return {"data": rows, "total": total, "page": page, "page_size": page_size}
    except Error as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        cur.close()
        conn.close()


@router.get("/events/stats")
def security_event_stats(
    start_time: Optional[str] = Query(None),
    end_time: Optional[str] = Query(None),
):
    """安全事件统计"""
    conn = get_db()
    if not conn:
        raise HTTPException(status_code=500, detail="Database connection failed")
    cur = conn.cursor(dictionary=True)
    try:
        ensure_security_tables(conn)
        where = []
        params = []
        if start_time:
            where.append("occurred_at>=%s"); params.append(start_time)
        if end_time:
            where.append("occurred_at<=%s"); params.append(end_time)
        where_sql = (" WHERE " + " AND ".join(where)) if where else ""

        cur.execute(f"""
            SELECT
                COUNT(*) AS total,
                SUM(CASE WHEN status='open' THEN 1 ELSE 0 END) AS open_count,
                SUM(CASE WHEN status='resolved' THEN 1 ELSE 0 END) AS resolved_count,
                SUM(CASE WHEN severity='critical' THEN 1 ELSE 0 END) AS critical,
                SUM(CASE WHEN severity='high' THEN 1 ELSE 0 END) AS high,
                SUM(CASE WHEN severity='medium' THEN 1 ELSE 0 END) AS medium,
                SUM(CASE WHEN severity='low' THEN 1 ELSE 0 END) AS low,
                SUM(CASE WHEN occurred_at>=DATE_SUB(NOW(),INTERVAL 24 HOUR) THEN 1 ELSE 0 END) AS events_24h,
                SUM(CASE WHEN occurred_at>=DATE_SUB(NOW(),INTERVAL 7 DAY) THEN 1 ELSE 0 END) AS events_7d
            FROM security_events {where_sql}
        """, params)
        summary = cur.fetchone() or {}

        cur.execute(f"""
            SELECT event_type, COUNT(*) AS count
            FROM security_events {where_sql}
            GROUP BY event_type ORDER BY count DESC
        """, params)
        by_type = {r["event_type"]: int(r["count"]) for r in cur.fetchall()}

        cur.execute(f"""
            SELECT severity, COUNT(*) AS count
            FROM security_events {where_sql}
            GROUP BY severity
        """, params)
        by_severity = {r["severity"]: int(r["count"]) for r in cur.fetchall()}

        return {
            "total": int(summary.get("total") or 0),
            "open": int(summary.get("open_count") or 0),
            "resolved": int(summary.get("resolved_count") or 0),
            "critical": int(summary.get("critical") or 0),
            "high": int(summary.get("high") or 0),
            "medium": int(summary.get("medium") or 0),
            "low": int(summary.get("low") or 0),
            "events_24h": int(summary.get("events_24h") or 0),
            "events_7d": int(summary.get("events_7d") or 0),
            "by_type": by_type,
            "by_severity": by_severity,
        }
    except Error as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        cur.close()
        conn.close()


@router.get("/events/{event_id}")
def security_event_detail(event_id: int):
    """安全事件详情"""
    conn = get_db()
    if not conn:
        raise HTTPException(status_code=500, detail="Database connection failed")
    cur = conn.cursor(dictionary=True)
    try:
        ensure_security_tables(conn)
        cur.execute("""
            SELECT se.*, a.hostname, a.ip_address
            FROM security_events se
            LEFT JOIN assets a ON a.id=se.asset_id
            WHERE se.id=%s
        """, (event_id,))
        row = cur.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Event not found")
        for k in ("occurred_at", "handled_at", "created_at"):
            row[k] = fmt_dt(row.get(k))
        if row.get("details_json"):
            try:
                row["details"] = json.loads(row["details_json"])
            except Exception:
                row["details"] = None
        return row
    except HTTPException:
        raise
    except Error as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        cur.close()
        conn.close()


@router.put("/events/{event_id}/handle")
def handle_security_event(event_id: int, payload: SecurityEventHandle, request: Request):
    """处置安全事件"""
    if payload.status not in ("open", "processing", "resolved", "ignored"):
        raise HTTPException(status_code=422, detail="Invalid status")
    conn = get_db()
    if not conn:
        raise HTTPException(status_code=500, detail="Database connection failed")
    cur = conn.cursor()
    try:
        ensure_security_tables(conn)
        cur.execute("SELECT id FROM security_events WHERE id=%s", (event_id,))
        if not cur.fetchone():
            raise HTTPException(status_code=404, detail="Event not found")
        handler = get_request_username(request, fallback="console")
        cur.execute("""
            UPDATE security_events SET status=%s, handler=%s, handle_note=%s, handled_at=NOW()
            WHERE id=%s
        """, (payload.status, handler, payload.handle_note, event_id))
        conn.commit()
        return {"message": "Event handled", "id": event_id, "status": payload.status}
    except HTTPException:
        raise
    except Error as e:
        conn.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        cur.close()
        conn.close()


@router.post("/events/batch-handle")
def batch_handle_security_events(payload: SecurityEventBatchHandle, request: Request):
    """批量处置安全事件"""
    if not payload.event_ids:
        raise HTTPException(status_code=400, detail="event_ids is required")
    if payload.status not in ("open", "processing", "resolved", "ignored"):
        raise HTTPException(status_code=422, detail="Invalid status")
    conn = get_db()
    if not conn:
        raise HTTPException(status_code=500, detail="Database connection failed")
    cur = conn.cursor()
    try:
        ensure_security_tables(conn)
        handler = get_request_username(request, fallback="console")
        placeholders = ",".join(["%s"] * len(payload.event_ids))
        cur.execute(f"""
            UPDATE security_events SET status=%s, handler=%s, handle_note=%s, handled_at=NOW()
            WHERE id IN ({placeholders})
        """, [payload.status, handler, payload.handle_note] + payload.event_ids)
        updated = cur.rowcount
        conn.commit()
        return {"message": "Batch handled", "updated": updated, "status": payload.status}
    except Error as e:
        conn.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        cur.close()
        conn.close()


# ============================================================
# USB 管控
# ============================================================

@router.get("/usb/devices")
def list_usb_devices(
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=500),
    asset_id: Optional[int] = Query(None),
    keyword: Optional[str] = Query(None),
):
    conn = get_db()
    if not conn:
        raise HTTPException(status_code=500, detail="Database connection failed")
    cur = conn.cursor(dictionary=True)
    try:
        ensure_security_tables(conn)
        where = []
        params = []
        if asset_id:
            where.append("ud.asset_id=%s"); params.append(asset_id)
        if keyword:
            where.append("(ud.friendly_name LIKE %s OR ud.vid_pid LIKE %s OR ud.device_id LIKE %s)")
            params += [f"%{keyword}%"] * 3
        where_sql = (" WHERE " + " AND ".join(where)) if where else ""
        cur.execute(f"""
            SELECT COUNT(*) AS total FROM usb_devices ud {where_sql}
        """, params)
        total = (cur.fetchone() or {}).get("total", 0)
        offset = (page - 1) * page_size
        cur.execute(f"""
            SELECT ud.*, a.hostname, a.ip_address
            FROM usb_devices ud LEFT JOIN assets a ON a.id=ud.asset_id
            {where_sql} ORDER BY ud.last_seen DESC LIMIT %s OFFSET %s
        """, params + [page_size, offset])
        rows = cur.fetchall()
        for r in rows:
            r["first_seen"] = fmt_dt(r.get("first_seen"))
            r["last_seen"] = fmt_dt(r.get("last_seen"))
        return {"data": rows, "total": total, "page": page, "page_size": page_size}
    except Error as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        cur.close()
        conn.close()


@router.get("/usb/events")
def list_usb_events(
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=500),
    asset_id: Optional[int] = Query(None),
    event_type: Optional[str] = Query(None),
):
    conn = get_db()
    if not conn:
        raise HTTPException(status_code=500, detail="Database connection failed")
    cur = conn.cursor(dictionary=True)
    try:
        ensure_security_tables(conn)
        where = []
        params = []
        if asset_id:
            where.append("ue.asset_id=%s"); params.append(asset_id)
        if event_type:
            where.append("ue.event_type=%s"); params.append(event_type)
        where_sql = (" WHERE " + " AND ".join(where)) if where else ""
        cur.execute(f"SELECT COUNT(*) AS total FROM usb_events ue {where_sql}", params)
        total = (cur.fetchone() or {}).get("total", 0)
        offset = (page - 1) * page_size
        cur.execute(f"""
            SELECT ue.*, a.hostname, a.ip_address
            FROM usb_events ue LEFT JOIN assets a ON a.id=ue.asset_id
            {where_sql} ORDER BY ue.occurred_at DESC LIMIT %s OFFSET %s
        """, params + [page_size, offset])
        rows = cur.fetchall()
        for r in rows:
            r["occurred_at"] = fmt_dt(r.get("occurred_at"))
        return {"data": rows, "total": total, "page": page, "page_size": page_size}
    except Error as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        cur.close()
        conn.close()


class UsbPolicyApply(BaseModel):
    scope_type: str = "asset"
    asset_ids: List[int] = Field(default_factory=list)
    group_id: Optional[int] = None
    action: str = "block"
    device_whitelist: List[str] = Field(default_factory=list)


@router.post("/usb/policy")
def apply_usb_policy(payload: UsbPolicyApply, request: Request):
    """下发USB策略（创建策略+绑定+同步下发到 Agent 执行+记录结果）"""
    if payload.action not in ("block", "allow"):
        raise HTTPException(status_code=422, detail="action 必须是 block 或 allow")
    conn = get_db()
    if not conn:
        raise HTTPException(status_code=500, detail="Database connection failed")
    cur = conn.cursor(dictionary=True)
    try:
        ensure_security_tables(conn)
        config = {"action": payload.action, "device_whitelist": payload.device_whitelist}
        operator = get_request_username(request, fallback="console")
        cur.execute("""
            INSERT INTO security_policies (policy_name, policy_type, description, enabled, priority, version, config_json, created_by)
            VALUES (%s,'usb',%s,TRUE,0,1,%s,%s)
        """, (f"USB-{payload.action}-{datetime.datetime.now().strftime('%m%d%H%M')}",
              f"USB policy {payload.action}", json.dumps(config, ensure_ascii=False), operator))
        policy_id = cur.lastrowid
        # 绑定
        if payload.scope_type == "global":
            cur.execute("INSERT INTO security_policy_bindings (policy_id, scope_type, scope_id, created_by) VALUES (%s,'global',NULL,%s)", (policy_id, operator))
        elif payload.scope_type == "group" and payload.group_id:
            cur.execute("INSERT INTO security_policy_bindings (policy_id, scope_type, scope_id, created_by) VALUES (%s,'group',%s,%s)", (policy_id, payload.group_id, operator))
        else:
            for aid in payload.asset_ids:
                cur.execute("INSERT INTO security_policy_bindings (policy_id, scope_type, scope_id, created_by) VALUES (%s,'asset',%s,%s)", (policy_id, aid, operator))
        conn.commit()
        # 同步下发：block -> usb_block, allow -> usb_allow
        cmd_type = "usb_block" if payload.action == "block" else "usb_allow"
        target_ids = _resolve_binding_asset_ids(cur, payload.scope_type, payload.group_id, payload.asset_ids)
        dispatch_results = _dispatch_policy_to_assets(
            cur, conn, policy_id, payload.scope_type, target_ids, cmd_type, {},
        )
        return {
            "message": "USB policy created and dispatched", "policy_id": policy_id,
            "targets": len(target_ids), "dispatch_results": dispatch_results,
        }
    except Error as e:
        conn.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        cur.close()
        conn.close()


# ============================================================
# 防火墙
# ============================================================

class FirewallRuleSpec(BaseModel):
    name: str
    direction: str = "in"
    action: str = "allow"
    protocol: str = "TCP"
    local_port: Optional[str] = None
    remote_ip: Optional[str] = None


class FirewallPolicyApply(BaseModel):
    scope_type: str = "global"
    asset_ids: List[int] = Field(default_factory=list)
    group_id: Optional[int] = None
    rules: List[FirewallRuleSpec] = Field(default_factory=list)


@router.post("/firewall/apply")
def apply_firewall_policy(payload: FirewallPolicyApply, request: Request):
    """下发防火墙策略（创建策略+绑定+同步下发到 Agent 执行+记录结果）"""
    if not payload.rules:
        raise HTTPException(status_code=422, detail="至少需要一条防火墙规则")
    for r in payload.rules:
        if not r.name or not r.name.strip():
            raise HTTPException(status_code=422, detail="每条规则必须有名称")
    conn = get_db()
    if not conn:
        raise HTTPException(status_code=500, detail="Database connection failed")
    cur = conn.cursor(dictionary=True)
    try:
        ensure_security_tables(conn)
        config = {"rules": [r.dict() for r in payload.rules]}
        operator = get_request_username(request, fallback="console")
        cur.execute("""
            INSERT INTO security_policies (policy_name, policy_type, description, enabled, priority, version, config_json, created_by)
            VALUES (%s,'firewall',%s,TRUE,0,1,%s,%s)
        """, (f"FW-{datetime.datetime.now().strftime('%m%d%H%M')}", "Firewall policy",
              json.dumps(config, ensure_ascii=False), operator))
        policy_id = cur.lastrowid
        if payload.scope_type == "global":
            cur.execute("INSERT INTO security_policy_bindings (policy_id, scope_type, scope_id, created_by) VALUES (%s,'global',NULL,%s)", (policy_id, operator))
        elif payload.scope_type == "group" and payload.group_id:
            cur.execute("INSERT INTO security_policy_bindings (policy_id, scope_type, scope_id, created_by) VALUES (%s,'group',%s,%s)", (policy_id, payload.group_id, operator))
        else:
            for aid in payload.asset_ids:
                cur.execute("INSERT INTO security_policy_bindings (policy_id, scope_type, scope_id, created_by) VALUES (%s,'asset',%s,%s)", (policy_id, aid, operator))
        conn.commit()
        # 同步下发到绑定范围内的在线终端
        target_ids = _resolve_binding_asset_ids(cur, payload.scope_type, payload.group_id, payload.asset_ids)
        dispatch_results = _dispatch_policy_to_assets(
            cur, conn, policy_id, payload.scope_type, target_ids,
            "firewall_apply", {"rules": config["rules"]}, count_field="applied",
        )
        return {
            "message": "Firewall policy created and dispatched",
            "policy_id": policy_id, "rule_count": len(payload.rules),
            "targets": len(target_ids), "dispatch_results": dispatch_results,
        }
    except Error as e:
        conn.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        cur.close()
        conn.close()


@router.get("/firewall/status/{asset_id}")
def firewall_status(asset_id: int):
    """查询终端防火墙状态（需 Agent 上报，此处从最近策略执行结果推断）"""
    conn = get_db()
    if not conn:
        raise HTTPException(status_code=500, detail="Database connection failed")
    cur = conn.cursor(dictionary=True)
    try:
        ensure_security_tables(conn)
        cur.execute("""
            SELECT sp.policy_name, sper.status, sper.applied_rules, sper.failed_rules, sper.executed_at
            FROM security_policy_exec_results sper
            JOIN security_policies sp ON sp.id=sper.policy_id
            WHERE sper.asset_id=%s AND sp.policy_type='firewall'
            ORDER BY sper.executed_at DESC LIMIT 5
        """, (asset_id,))
        return {"asset_id": asset_id, "results": [dict(r) for r in cur.fetchall()]}
    except Error as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        cur.close()
        conn.close()


@router.get("/firewall/rules")
def firewall_rules(asset_id: Optional[int] = Query(None)):
    """防火墙规则列表（占位：实际规则在 Agent 端，通过策略下发管理）"""
    conn = get_db()
    if not conn:
        raise HTTPException(status_code=500, detail="Database connection failed")
    cur = conn.cursor(dictionary=True)
    try:
        ensure_security_tables(conn)
        where = ["sp.policy_type='firewall'"]
        params = []
        if asset_id:
            where.append("spb.scope_type='asset' AND spb.scope_id=%s"); params.append(asset_id)
        where_sql = " WHERE " + " AND ".join(where)
        cur.execute(f"""
            SELECT sp.id, sp.policy_name, sp.config_json, sp.enabled, sp.created_at
            FROM security_policies sp
            LEFT JOIN security_policy_bindings spb ON spb.policy_id=sp.id
            {where_sql} GROUP BY sp.id ORDER BY sp.created_at DESC
        """, params)
        rows = cur.fetchall()
        for r in rows:
            try:
                r["config"] = json.loads(r.pop("config_json") or "{}")
            except Exception:
                r["config"] = {}
            r["created_at"] = fmt_dt(r.get("created_at"))
        return {"data": rows, "total": len(rows)}
    except Error as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        cur.close()
        conn.close()


# ============================================================
# 程序管控
# ============================================================

@router.get("/app-control/logs")
def app_control_logs(
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=500),
    asset_id: Optional[int] = Query(None),
    process_name: Optional[str] = Query(None),
    action: Optional[str] = Query(None),
):
    conn = get_db()
    if not conn:
        raise HTTPException(status_code=500, detail="Database connection failed")
    cur = conn.cursor(dictionary=True)
    try:
        ensure_security_tables(conn)
        where = []
        params = []
        if asset_id:
            where.append("pl.asset_id=%s"); params.append(asset_id)
        if process_name:
            where.append("pl.process_name LIKE %s"); params.append(f"%{process_name}%")
        if action:
            where.append("pl.action=%s"); params.append(action)
        where_sql = (" WHERE " + " AND ".join(where)) if where else ""
        cur.execute(f"SELECT COUNT(*) AS total FROM process_launch_logs pl {where_sql}", params)
        total = (cur.fetchone() or {}).get("total", 0)
        offset = (page - 1) * page_size
        cur.execute(f"""
            SELECT pl.*, a.hostname, a.ip_address
            FROM process_launch_logs pl LEFT JOIN assets a ON a.id=pl.asset_id
            {where_sql} ORDER BY pl.launched_at DESC LIMIT %s OFFSET %s
        """, params + [page_size, offset])
        rows = cur.fetchall()
        for r in rows:
            r["launched_at"] = fmt_dt(r.get("launched_at"))
        return {"data": rows, "total": total, "page": page, "page_size": page_size}
    except Error as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        cur.close()
        conn.close()


class AppControlPolicyApply(BaseModel):
    scope_type: str = "global"
    asset_ids: List[int] = Field(default_factory=list)
    group_id: Optional[int] = None
    blacklist: List[str] = Field(default_factory=list)
    whitelist: List[str] = Field(default_factory=list)
    alert_unknown: bool = True


@router.post("/app-control/policy")
def apply_app_control_policy(payload: AppControlPolicyApply, request: Request):
    """下发程序管控策略（创建策略+绑定+同步下发扫描+记录结果）"""
    if not payload.blacklist and not payload.whitelist:
        raise HTTPException(status_code=422, detail="黑名单或白名单至少需要一个非空")
    conn = get_db()
    if not conn:
        raise HTTPException(status_code=500, detail="Database connection failed")
    cur = conn.cursor(dictionary=True)
    try:
        ensure_security_tables(conn)
        config = {"blacklist": payload.blacklist, "whitelist": payload.whitelist, "alert_unknown": payload.alert_unknown}
        operator = get_request_username(request, fallback="console")
        cur.execute("""
            INSERT INTO security_policies (policy_name, policy_type, description, enabled, priority, version, config_json, created_by)
            VALUES (%s,'app_control',%s,TRUE,0,1,%s,%s)
        """, (f"AppCtrl-{datetime.datetime.now().strftime('%m%d%H%M')}", "App control policy",
              json.dumps(config, ensure_ascii=False), operator))
        policy_id = cur.lastrowid
        if payload.scope_type == "global":
            cur.execute("INSERT INTO security_policy_bindings (policy_id, scope_type, scope_id, created_by) VALUES (%s,'global',NULL,%s)", (policy_id, operator))
        elif payload.scope_type == "group" and payload.group_id:
            cur.execute("INSERT INTO security_policy_bindings (policy_id, scope_type, scope_id, created_by) VALUES (%s,'group',%s,%s)", (policy_id, payload.group_id, operator))
        else:
            for aid in payload.asset_ids:
                cur.execute("INSERT INTO security_policy_bindings (policy_id, scope_type, scope_id, created_by) VALUES (%s,'asset',%s,%s)", (policy_id, aid, operator))
        conn.commit()
        # 同步下发：扫描命中黑名单的进程（仅扫描告警，不自动杀进程，由管理员远程处置）
        target_ids = _resolve_binding_asset_ids(cur, payload.scope_type, payload.group_id, payload.asset_ids)
        dispatch_results = _dispatch_policy_to_assets(
            cur, conn, policy_id, payload.scope_type, target_ids,
            "process_scan_blacklist", {"blacklist": payload.blacklist},
        )
        return {
            "message": "App control policy created and dispatched", "policy_id": policy_id,
            "targets": len(target_ids), "dispatch_results": dispatch_results,
        }
    except Error as e:
        conn.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        cur.close()
        conn.close()


# ============================================================
# 文件保护
# ============================================================

@router.get("/file-protect/baselines")
def file_protect_baselines(
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=500),
    asset_id: Optional[int] = Query(None),
):
    conn = get_db()
    if not conn:
        raise HTTPException(status_code=500, detail="Database connection failed")
    cur = conn.cursor(dictionary=True)
    try:
        ensure_security_tables(conn)
        where = []
        params = []
        if asset_id:
            where.append("fb.asset_id=%s"); params.append(asset_id)
        where_sql = (" WHERE " + " AND ".join(where)) if where else ""
        cur.execute(f"SELECT COUNT(*) AS total FROM file_protect_baselines fb {where_sql}", params)
        total = (cur.fetchone() or {}).get("total", 0)
        offset = (page - 1) * page_size
        cur.execute(f"""
            SELECT fb.*, a.hostname, a.ip_address
            FROM file_protect_baselines fb LEFT JOIN assets a ON a.id=fb.asset_id
            {where_sql} ORDER BY fb.baseline_at DESC LIMIT %s OFFSET %s
        """, params + [page_size, offset])
        rows = cur.fetchall()
        for r in rows:
            r["baseline_at"] = fmt_dt(r.get("baseline_at"))
        return {"data": rows, "total": total, "page": page, "page_size": page_size}
    except Error as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        cur.close()
        conn.close()


@router.get("/file-protect/anomalies")
def file_protect_anomalies(
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=500),
    asset_id: Optional[int] = Query(None),
):
    conn = get_db()
    if not conn:
        raise HTTPException(status_code=500, detail="Database connection failed")
    cur = conn.cursor(dictionary=True)
    try:
        ensure_security_tables(conn)
        where = []
        params = []
        if asset_id:
            where.append("fa.asset_id=%s"); params.append(asset_id)
        where_sql = (" WHERE " + " AND ".join(where)) if where else ""
        cur.execute(f"SELECT COUNT(*) AS total FROM file_anomaly_events fa {where_sql}", params)
        total = (cur.fetchone() or {}).get("total", 0)
        offset = (page - 1) * page_size
        cur.execute(f"""
            SELECT fa.*, a.hostname, a.ip_address
            FROM file_anomaly_events fa LEFT JOIN assets a ON a.id=fa.asset_id
            {where_sql} ORDER BY fa.occurred_at DESC LIMIT %s OFFSET %s
        """, params + [page_size, offset])
        rows = cur.fetchall()
        for r in rows:
            r["occurred_at"] = fmt_dt(r.get("occurred_at"))
        return {"data": rows, "total": total, "page": page, "page_size": page_size}
    except Error as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        cur.close()
        conn.close()


class FileProtectPolicyApply(BaseModel):
    scope_type: str = "global"
    asset_ids: List[int] = Field(default_factory=list)
    group_id: Optional[int] = None
    protected_dirs: List[str] = Field(default_factory=list)
    mass_change_threshold: int = 50


@router.post("/file-protect/policy")
def apply_file_protect_policy(payload: FileProtectPolicyApply, request: Request):
    """下发文件保护策略（创建策略+绑定+同步下发建立基线+记录结果）"""
    if not payload.protected_dirs:
        raise HTTPException(status_code=422, detail="至少需要一个保护目录")
    conn = get_db()
    if not conn:
        raise HTTPException(status_code=500, detail="Database connection failed")
    cur = conn.cursor(dictionary=True)
    try:
        ensure_security_tables(conn)
        config = {"protected_dirs": payload.protected_dirs, "mass_change_threshold": payload.mass_change_threshold}
        operator = get_request_username(request, fallback="console")
        cur.execute("""
            INSERT INTO security_policies (policy_name, policy_type, description, enabled, priority, version, config_json, created_by)
            VALUES (%s,'file_protect',%s,TRUE,0,1,%s,%s)
        """, (f"FileProt-{datetime.datetime.now().strftime('%m%d%H%M')}", "File protect policy",
              json.dumps(config, ensure_ascii=False), operator))
        policy_id = cur.lastrowid
        if payload.scope_type == "global":
            cur.execute("INSERT INTO security_policy_bindings (policy_id, scope_type, scope_id, created_by) VALUES (%s,'global',NULL,%s)", (policy_id, operator))
        elif payload.scope_type == "group" and payload.group_id:
            cur.execute("INSERT INTO security_policy_bindings (policy_id, scope_type, scope_id, created_by) VALUES (%s,'group',%s,%s)", (policy_id, payload.group_id, operator))
        else:
            for aid in payload.asset_ids:
                cur.execute("INSERT INTO security_policy_bindings (policy_id, scope_type, scope_id, created_by) VALUES (%s,'asset',%s,%s)", (policy_id, aid, operator))
        conn.commit()
        # 同步下发：对每个保护目录建立基线
        target_ids = _resolve_binding_asset_ids(cur, payload.scope_type, payload.group_id, payload.asset_ids)
        all_results = []
        for aid in target_ids:
            dir_results = []
            for d in payload.protected_dirs:
                res = _dispatch_to_agent(aid, "file_baseline", {"dir_path": d})
                ok = res.get("success", False)
                _record_exec_result(cur, conn, policy_id, aid, payload.scope_type, res, applied_rules=1 if ok else 0, failed_rules=0 if ok else 1)
                dir_results.append({"dir": d, "success": ok, "count": res.get("count", 0)})
            all_results.append({"asset_id": aid, "dirs": dir_results})
        return {
            "message": "File protect policy created and dispatched", "policy_id": policy_id,
            "targets": len(target_ids), "dispatch_results": all_results,
        }
    except Error as e:
        conn.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        cur.close()
        conn.close()


# ============================================================
# 行为监控
# ============================================================

@router.get("/behavior/events")
def behavior_events(
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=500),
    asset_id: Optional[int] = Query(None),
    event_type: Optional[str] = Query(None),
    severity: Optional[str] = Query(None),
):
    """行为事件流（复用 security_events 表，过滤行为相关类型）"""
    conn = get_db()
    if not conn:
        raise HTTPException(status_code=500, detail="Database connection failed")
    cur = conn.cursor(dictionary=True)
    try:
        ensure_security_tables(conn)
        behavior_types = ('suspicious_process', 'registry_anomaly', 'network_anomaly', 'file_anomaly', 'virus')
        where = [f"se.event_type IN {behavior_types}"]
        params = []
        if asset_id:
            where.append("se.asset_id=%s"); params.append(asset_id)
        if event_type:
            where.append("se.event_type=%s"); params.append(event_type)
        if severity:
            where.append("se.severity=%s"); params.append(severity)
        where_sql = " WHERE " + " AND ".join(where)
        cur.execute(f"SELECT COUNT(*) AS total FROM security_events se {where_sql}", params)
        total = (cur.fetchone() or {}).get("total", 0)
        offset = (page - 1) * page_size
        cur.execute(f"""
            SELECT se.*, a.hostname, a.ip_address
            FROM security_events se LEFT JOIN assets a ON a.id=se.asset_id
            {where_sql} ORDER BY se.occurred_at DESC LIMIT %s OFFSET %s
        """, params + [page_size, offset])
        rows = cur.fetchall()
        for r in rows:
            r["occurred_at"] = fmt_dt(r.get("occurred_at"))
        return {"data": rows, "total": total, "page": page, "page_size": page_size}
    except Error as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        cur.close()
        conn.close()


# ============================================================
# 策略中心
# ============================================================

class SecurityPolicyCreate(BaseModel):
    policy_name: str
    policy_type: str
    description: Optional[str] = None
    priority: int = 0
    config_json: str = "{}"


class SecurityPolicyUpdate(BaseModel):
    policy_name: Optional[str] = None
    description: Optional[str] = None
    enabled: Optional[bool] = None
    priority: Optional[int] = None
    config_json: Optional[str] = None


class SecurityPolicyBind(BaseModel):
    scope_type: str
    scope_id: Optional[int] = None


class SecurityPolicyRollback(BaseModel):
    version: int


@router.get("/policies")
def list_security_policies(
    page: int = Query(1, ge=1),
    page_size: int = Query(100, ge=1, le=500),
    policy_type: Optional[str] = Query(None),
    enabled: Optional[bool] = Query(None),
):
    conn = get_db()
    if not conn:
        raise HTTPException(status_code=500, detail="Database connection failed")
    cur = conn.cursor(dictionary=True)
    try:
        ensure_security_tables(conn)
        where = []
        params = []
        if policy_type:
            where.append("sp.policy_type=%s"); params.append(policy_type)
        if enabled is not None:
            where.append("sp.enabled=%s"); params.append(enabled)
        where_sql = (" WHERE " + " AND ".join(where)) if where else ""
        cur.execute(f"SELECT COUNT(*) AS total FROM security_policies sp {where_sql}", params)
        total = (cur.fetchone() or {}).get("total", 0)
        offset = (page - 1) * page_size
        cur.execute(f"""
            SELECT sp.*,
                   (SELECT COUNT(*) FROM security_policy_bindings spb WHERE spb.policy_id=sp.id AND spb.enabled=TRUE) AS binding_count
            FROM security_policies sp {where_sql}
            ORDER BY sp.priority DESC, sp.created_at DESC LIMIT %s OFFSET %s
        """, params + [page_size, offset])
        rows = cur.fetchall()
        for r in rows:
            r["created_at"] = fmt_dt(r.get("created_at"))
            r["updated_at"] = fmt_dt(r.get("updated_at"))
        return {"data": rows, "total": total, "page": page, "page_size": page_size}
    except Error as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        cur.close()
        conn.close()


@router.post("/policies")
def create_security_policy(payload: SecurityPolicyCreate, request: Request):
    if payload.policy_type not in ("firewall", "usb", "app_control", "file_protect", "behavior"):
        raise HTTPException(status_code=422, detail="Invalid policy_type")
    try:
        json.loads(payload.config_json)
    except Exception:
        raise HTTPException(status_code=422, detail="Invalid config_json")
    conn = get_db()
    if not conn:
        raise HTTPException(status_code=500, detail="Database connection failed")
    cur = conn.cursor()
    try:
        ensure_security_tables(conn)
        operator = get_request_username(request, fallback="console")
        cur.execute("""
            INSERT INTO security_policies (policy_name, policy_type, description, enabled, priority, version, config_json, created_by)
            VALUES (%s,%s,%s,TRUE,%s,1,%s,%s)
        """, (payload.policy_name, payload.policy_type, payload.description,
              payload.priority, payload.config_json, operator))
        policy_id = cur.lastrowid
        # 存初始版本
        cur.execute("""
            INSERT INTO security_policy_versions (policy_id, version, config_json, changed_by, change_note)
            VALUES (%s,1,%s,%s,'initial')
        """, (policy_id, payload.config_json, operator))
        conn.commit()
        return {"message": "Policy created", "id": policy_id, "version": 1}
    except Error as e:
        conn.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        cur.close()
        conn.close()


@router.get("/policies/{policy_id}")
def security_policy_detail(policy_id: int):
    conn = get_db()
    if not conn:
        raise HTTPException(status_code=500, detail="Database connection failed")
    cur = conn.cursor(dictionary=True)
    try:
        ensure_security_tables(conn)
        cur.execute("SELECT * FROM security_policies WHERE id=%s", (policy_id,))
        policy = cur.fetchone()
        if not policy:
            raise HTTPException(status_code=404, detail="Policy not found")
        for k in ("created_at", "updated_at"):
            policy[k] = fmt_dt(policy.get(k))
        try:
            policy["config"] = json.loads(policy.pop("config_json") or "{}")
        except Exception:
            policy["config"] = {}
        cur.execute("SELECT * FROM security_policy_bindings WHERE policy_id=%s ORDER BY scope_type", (policy_id,))
        bindings = cur.fetchall()
        for b in bindings:
            b["created_at"] = fmt_dt(b.get("created_at"))
        policy["bindings"] = bindings
        return policy
    except HTTPException:
        raise
    except Error as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        cur.close()
        conn.close()


@router.put("/policies/{policy_id}")
def update_security_policy(policy_id: int, payload: SecurityPolicyUpdate, request: Request):
    conn = get_db()
    if not conn:
        raise HTTPException(status_code=500, detail="Database connection failed")
    cur = conn.cursor()
    try:
        ensure_security_tables(conn)
        cur.execute("SELECT id, version, config_json FROM security_policies WHERE id=%s", (policy_id,))
        row = cur.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Policy not found")
        old_version = row[1]
        old_config = row[2]
        updates = []
        values = []
        if payload.policy_name is not None:
            updates.append("policy_name=%s"); values.append(payload.policy_name)
        if payload.description is not None:
            updates.append("description=%s"); values.append(payload.description)
        if payload.enabled is not None:
            updates.append("enabled=%s"); values.append(payload.enabled)
        if payload.priority is not None:
            updates.append("priority=%s"); values.append(payload.priority)
        new_config = old_config
        if payload.config_json is not None:
            try:
                json.loads(payload.config_json)
            except Exception:
                raise HTTPException(status_code=422, detail="Invalid config_json")
            updates.append("config_json=%s"); values.append(payload.config_json)
            new_config = payload.config_json
            # config 变更则版本+1
            new_version = old_version + 1
            updates.append("version=%s"); values.append(new_version)
            operator = get_request_username(request, fallback="console")
            cur.execute("""
                INSERT INTO security_policy_versions (policy_id, version, config_json, changed_by, change_note)
                VALUES (%s,%s,%s,%s,'update')
            """, (policy_id, new_version, new_config, operator))
        if not updates:
            raise HTTPException(status_code=400, detail="No fields to update")
        updates.append("updated_at=NOW()")
        values.append(policy_id)
        cur.execute(f"UPDATE security_policies SET {','.join(updates)} WHERE id=%s", values)
        conn.commit()
        return {"message": "Policy updated", "id": policy_id}
    except HTTPException:
        raise
    except Error as e:
        conn.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        cur.close()
        conn.close()


@router.delete("/policies/{policy_id}")
def delete_security_policy(policy_id: int):
    conn = get_db()
    if not conn:
        raise HTTPException(status_code=500, detail="Database connection failed")
    cur = conn.cursor()
    try:
        ensure_security_tables(conn)
        cur.execute("SELECT id FROM security_policies WHERE id=%s", (policy_id,))
        if not cur.fetchone():
            raise HTTPException(status_code=404, detail="Policy not found")
        cur.execute("DELETE FROM security_policies WHERE id=%s", (policy_id,))
        conn.commit()
        return {"message": "Policy deleted", "id": policy_id}
    except HTTPException:
        raise
    except Error as e:
        conn.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        cur.close()
        conn.close()


@router.post("/policies/{policy_id}/bind")
def bind_security_policy(policy_id: int, payload: SecurityPolicyBind, request: Request):
    if payload.scope_type not in ("global", "group", "asset"):
        raise HTTPException(status_code=422, detail="Invalid scope_type")
    if payload.scope_type in ("group", "asset") and not payload.scope_id:
        raise HTTPException(status_code=422, detail="scope_id required for group/asset")
    conn = get_db()
    if not conn:
        raise HTTPException(status_code=500, detail="Database connection failed")
    cur = conn.cursor()
    try:
        ensure_security_tables(conn)
        cur.execute("SELECT id FROM security_policies WHERE id=%s", (policy_id,))
        if not cur.fetchone():
            raise HTTPException(status_code=404, detail="Policy not found")
        operator = get_request_username(request, fallback="console")
        scope_id = None if payload.scope_type == "global" else payload.scope_id
        cur.execute("""
            INSERT INTO security_policy_bindings (policy_id, scope_type, scope_id, created_by)
            VALUES (%s,%s,%s,%s)
        """, (policy_id, payload.scope_type, scope_id, operator))
        binding_id = cur.lastrowid
        conn.commit()
        return {"message": "Policy bound", "binding_id": binding_id}
    except HTTPException:
        raise
    except Error as e:
        conn.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        cur.close()
        conn.close()


@router.delete("/policies/{policy_id}/bind/{binding_id}")
def unbind_security_policy(policy_id: int, binding_id: int):
    conn = get_db()
    if not conn:
        raise HTTPException(status_code=500, detail="Database connection failed")
    cur = conn.cursor()
    try:
        ensure_security_tables(conn)
        cur.execute("DELETE FROM security_policy_bindings WHERE id=%s AND policy_id=%s", (binding_id, policy_id))
        if cur.rowcount == 0:
            raise HTTPException(status_code=404, detail="Binding not found")
        conn.commit()
        return {"message": "Binding removed", "binding_id": binding_id}
    except HTTPException:
        raise
    except Error as e:
        conn.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        cur.close()
        conn.close()


@router.get("/policies/{policy_id}/versions")
def security_policy_versions(policy_id: int):
    conn = get_db()
    if not conn:
        raise HTTPException(status_code=500, detail="Database connection failed")
    cur = conn.cursor(dictionary=True)
    try:
        ensure_security_tables(conn)
        cur.execute("""
            SELECT id, version, changed_by, change_note, created_at
            FROM security_policy_versions WHERE policy_id=%s ORDER BY version DESC
        """, (policy_id,))
        rows = cur.fetchall()
        for r in rows:
            r["created_at"] = fmt_dt(r.get("created_at"))
        return {"data": rows, "total": len(rows)}
    except Error as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        cur.close()
        conn.close()


@router.post("/policies/{policy_id}/rollback")
def rollback_security_policy(policy_id: int, payload: SecurityPolicyRollback, request: Request):
    conn = get_db()
    if not conn:
        raise HTTPException(status_code=500, detail="Database connection failed")
    cur = conn.cursor()
    try:
        ensure_security_tables(conn)
        cur.execute("SELECT config_json FROM security_policy_versions WHERE policy_id=%s AND version=%s", (policy_id, payload.version))
        row = cur.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Version not found")
        old_config = row[0]
        cur.execute("SELECT version FROM security_policies WHERE id=%s", (policy_id,))
        prow = cur.fetchone()
        if not prow:
            raise HTTPException(status_code=404, detail="Policy not found")
        new_version = prow[0] + 1
        operator = get_request_username(request, fallback="console")
        cur.execute("UPDATE security_policies SET config_json=%s, version=%s, updated_at=NOW() WHERE id=%s", (old_config, new_version, policy_id))
        cur.execute("""
            INSERT INTO security_policy_versions (policy_id, version, config_json, changed_by, change_note)
            VALUES (%s,%s,%s,%s,%s)
        """, (policy_id, new_version, old_config, operator, f"rollback to v{payload.version}"))
        conn.commit()
        return {"message": "Policy rolled back", "id": policy_id, "new_version": new_version, "rolled_to": payload.version}
    except HTTPException:
        raise
    except Error as e:
        conn.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        cur.close()
        conn.close()


@router.get("/policies/{policy_id}/exec-results")
def security_policy_exec_results(
    policy_id: int,
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
):
    conn = get_db()
    if not conn:
        raise HTTPException(status_code=500, detail="Database connection failed")
    cur = conn.cursor(dictionary=True)
    try:
        ensure_security_tables(conn)
        cur.execute("""
            SELECT sper.*, a.hostname, a.ip_address
            FROM security_policy_exec_results sper
            LEFT JOIN assets a ON a.id=sper.asset_id
            WHERE sper.policy_id=%s ORDER BY sper.executed_at DESC
            LIMIT %s OFFSET %s
        """, (policy_id, page_size, (page - 1) * page_size))
        rows = cur.fetchall()
        for r in rows:
            r["executed_at"] = fmt_dt(r.get("executed_at"))
            r["reported_at"] = fmt_dt(r.get("reported_at"))
        return {"data": rows, "page": page, "page_size": page_size}
    except Error as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        cur.close()
        conn.close()


# ============================================================
# 远程安全运维
# ============================================================

@router.post("/remote/scan/{asset_id}")
def remote_scan(asset_id: int, request: Request):
    """远程下发安全扫描命令到 Agent"""
    return _dispatch_agent_security_command(asset_id, "security_scan", {}, request)


@router.post("/remote/kill-process/{asset_id}")
def remote_kill_process(asset_id: int, data: dict, request: Request):
    """远程结束进程"""
    pid = data.get("pid")
    name = data.get("name")
    if not pid and not name:
        raise HTTPException(status_code=422, detail="pid or name required")
    return _dispatch_agent_security_command(asset_id, "kill_process", {"pid": pid, "name": name}, request)


@router.post("/remote/isolate/{asset_id}")
def remote_isolate(asset_id: int, request: Request):
    """隔离终端（下发防火墙阻断规则，保留控制端口9001）"""
    return _dispatch_agent_security_command(asset_id, "isolate_host", {}, request)


@router.post("/remote/unisolate/{asset_id}")
def remote_unisolate(asset_id: int, request: Request):
    """解除隔离（删除隔离防火墙规则）"""
    return _dispatch_agent_security_command(asset_id, "unisolate_host", {}, request)


def _resolve_binding_asset_ids(cur, scope_type: str, group_id: Optional[int], asset_ids: List[int]) -> List[int]:
    """解析策略绑定范围内的在线终端 asset_id 列表（校验资产存在且已安装Agent）"""
    if scope_type == "asset":
        ids = [int(a) for a in (asset_ids or []) if a]
        if not ids:
            return []
        placeholders = ",".join(["%s"] * len(ids))
        cur.execute(f"""
            SELECT id FROM assets WHERE id IN ({placeholders}) AND deleted_at IS NULL
              AND agent_install_status='installed'
        """, ids)
        return [int(r["id"]) for r in cur.fetchall()] if cur.description else [int(r[0]) for r in cur.fetchall()]
    if scope_type == "group" and group_id:
        cur.execute("""
            SELECT id FROM assets WHERE group_id=%s AND deleted_at IS NULL
              AND agent_install_status='installed'
        """, (group_id,))
        return [int(r["id"]) for r in cur.fetchall()] if cur.description else [int(r[0]) for r in cur.fetchall()]
    # global: 所有在线已安装 Agent 的终端
    cur.execute("""
        SELECT id FROM assets WHERE deleted_at IS NULL AND agent_install_status='installed'
    """)
    return [int(r["id"]) for r in cur.fetchall()] if cur.description else [int(r[0]) for r in cur.fetchall()]


def _dispatch_to_agent(asset_id: int, command_type: str, params: dict) -> Dict[str, Any]:
    """直接调用 Agent /api/v1/security-command（不含 DB 审计，供 apply 端点复用）"""
    import requests as req
    from config_utils import get_env
    agent_token = get_env("ZVIEW_AGENT_TOKEN", "")
    control_port = int(get_env("ZVIEW_AGENT_CONTROL_PORT", "9001") or "9001")
    try:
        resp = req.post(
            f"http://127.0.0.1:{control_port}/api/v1/security-command",
            json={"command_type": command_type, "params": params},
            headers={"Authorization": f"Bearer {agent_token}"},
            timeout=70,
        )
        if resp.status_code == 200:
            return resp.json()
        return {"success": False, "error": f"Agent returned {resp.status_code}: {resp.text[:200]}"}
    except Exception as exc:
        return {"success": False, "error": str(exc)}


def _record_exec_result(cur, conn, policy_id: int, asset_id: int, scope_type: str,
                        result: Dict[str, Any], applied_rules: int = 0, failed_rules: int = 0):
    """记录策略执行结果到 security_policy_exec_results"""
    status = "success" if result.get("success") else "failed"
    err = result.get("error") or (result.get("stderr") if not result.get("success") else None)
    cur.execute("""
        INSERT INTO security_policy_exec_results
            (policy_id, asset_id, scope_type, status, applied_rules, failed_rules, error_detail, executed_at, reported_at)
        VALUES (%s,%s,%s,%s,%s,%s,%s,NOW(),NOW())
    """, (policy_id, asset_id, scope_type, status, applied_rules, failed_rules, err))
    conn.commit()


def _dispatch_policy_to_assets(cur, conn, policy_id: int, scope_type: str,
                               asset_ids: List[int], command_type: str, params: dict,
                               count_field: str = "applied") -> List[Dict[str, Any]]:
    """对绑定范围内的每个终端下发安全命令并记录结果"""
    results = []
    for aid in asset_ids:
        res = _dispatch_to_agent(aid, command_type, params)
        applied = 0
        failed = 0
        if count_field == "applied" and isinstance(res.get("applied"), int):
            applied = res["applied"]
            failed = res.get("failed", 0)
        _record_exec_result(cur, conn, policy_id, aid, scope_type, res, applied, failed)
        results.append({"asset_id": aid, "success": res.get("success", False),
                        "applied": applied, "failed": failed,
                        "error": res.get("error") if not res.get("success") else None})
    return results


def _dispatch_agent_security_command(asset_id: int, command_type: str, params: dict, request: Request):
    """通过 Agent 控制通道下发安全命令（直接调 Agent /api/v1/security-command）"""
    import requests as req
    from config_utils import get_env
    conn = get_db()
    if not conn:
        raise HTTPException(status_code=500, detail="Database connection failed")
    cur = conn.cursor(dictionary=True)
    try:
        cur.execute("SELECT id, hostname, ip_address FROM assets WHERE id=%s AND deleted_at IS NULL", (asset_id,))
        asset = cur.fetchone()
        if not asset:
            raise HTTPException(status_code=404, detail="Asset not found")
        operator = get_request_username(request, fallback="console")
        # 记录审计
        cur.execute("""
            INSERT INTO system_activity_logs (source_type, module, action, level, result, asset_id, message, event_time, created_at)
            VALUES ('platform','security',%s,'info','success',%s,%s,NOW(),NOW())
        """, (command_type, asset_id, f"Security command {command_type} dispatched to {asset['hostname']} by {operator}"))
        conn.commit()
        # 直接调用 Agent /api/v1/security-command 端点（按资产 IP 下发，控制通道 9001 对 0.0.0.0 监听且白名单放行平台 IP）
        agent_token = get_env("ZVIEW_AGENT_TOKEN", "")
        control_port = int(get_env("ZVIEW_AGENT_CONTROL_PORT", "9001") or "9001")
        try:
            resp = req.post(
                f"http://{asset['ip_address']}:{control_port}/api/v1/security-command",
                json={"command_type": command_type, "params": params},
                headers={"Authorization": f"Bearer {agent_token}"},
                timeout=70,
            )
            if resp.status_code == 200:
                result = resp.json()
                return {"asset_id": asset_id, "command_type": command_type, "result": result, "dispatched": True}
            return {"asset_id": asset_id, "command_type": command_type, "error": f"Agent returned {resp.status_code}: {resp.text[:200]}", "dispatched": False}
        except Exception as exc:
            return {"asset_id": asset_id, "command_type": command_type, "error": str(exc), "dispatched": False}
    except HTTPException:
        raise
    except Error as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        cur.close()
        conn.close()


# ============================================================
# Agent 上报接口（agent_token 认证，在 assets_api 中注册为 exempt）
# ============================================================

class SecurityEventReport(BaseModel):
    asset_id: int
    events: List[Dict[str, Any]] = Field(default_factory=list)


class SecurityStatusReport(BaseModel):
    asset_id: int
    firewall_enabled: Optional[bool] = None
    firewall_profile: Optional[str] = None
    firewall_rule_count: Optional[int] = None
    usb_policy: Optional[str] = None
    usb_storage_blocked: Optional[bool] = None
    open_events: Optional[int] = None
    details: Optional[Dict[str, Any]] = None


def _insert_security_event(cur, asset_id: int, ev: dict):
    cur.execute("""
        INSERT INTO security_events (
            asset_id, event_type, severity, title, description,
            process_name, process_pid, file_path, remote_ip, remote_port,
            details_json, status, occurred_at
        ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'open',%s)
    """, (
        asset_id,
        ev.get("event_type", "suspicious_process"),
        ev.get("severity", "medium"),
        (ev.get("title") or "")[:255],
        ev.get("description"),
        (ev.get("process_name") or "")[:255],
        ev.get("process_pid"),
        (ev.get("file_path") or "")[:500],
        (ev.get("remote_ip") or "")[:45],
        ev.get("remote_port"),
        json.dumps(ev.get("details"), ensure_ascii=False) if ev.get("details") else None,
        ev.get("occurred_at") or datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    ))


# 这些端点在 assets_api.py 中挂载（agent_token 认证），此处仅提供函数
def agent_report_security_events(payload: SecurityEventReport):
    conn = get_db()
    if not conn:
        raise HTTPException(status_code=500, detail="Database connection failed")
    cur = conn.cursor()
    try:
        ensure_security_tables(conn)
        inserted = 0
        for ev in payload.events:
            try:
                _insert_security_event(cur, payload.asset_id, ev)
                inserted += 1
            except Exception:
                conn.rollback()
        conn.commit()
        return {"message": "Events received", "inserted": inserted}
    except Error as e:
        conn.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        cur.close()
        conn.close()


def mount_security_api(app: FastAPI):
    """将 security router 挂载到主 FastAPI app"""
    app.include_router(router)