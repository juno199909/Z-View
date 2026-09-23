# -*- coding: utf-8 -*-
"""统一日志数据访问层（P1-01 从 assets_api 迁出）。"""
from __future__ import annotations

import datetime
import json
from typing import Any, Dict, Optional, Tuple

from zvplatform.common import parse_json_field
from zvplatform.db import format_datetime
from zvplatform.models import SystemActivityLogCreate

def ensure_system_activity_logs_table(conn):
    """确保统一运行时日志表存在。"""
    cursor = conn.cursor()
    try:
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS system_activity_logs (
                id BIGINT PRIMARY KEY AUTO_INCREMENT,
                source_type VARCHAR(50) NOT NULL DEFAULT 'agent',
                module VARCHAR(100) NOT NULL,
                category VARCHAR(100) NULL,
                action VARCHAR(100) NOT NULL,
                level VARCHAR(20) NOT NULL DEFAULT 'info',
                result VARCHAR(50) NULL,
                asset_id BIGINT NULL,
                hostname VARCHAR(255) NULL,
                ip_address VARCHAR(64) NULL,
                operator_name VARCHAR(100) NULL,
                session_id VARCHAR(100) NULL,
                title VARCHAR(255) NULL,
                message TEXT NOT NULL,
                details_json JSON NULL,
                stdout_log MEDIUMTEXT NULL,
                stderr_log MEDIUMTEXT NULL,
                event_time DATETIME NOT NULL,
                created_at DATETIME NOT NULL,
                INDEX idx_system_activity_event_time (event_time),
                INDEX idx_system_activity_module (module),
                INDEX idx_system_activity_asset (asset_id),
                INDEX idx_system_activity_source_type (source_type)
            )
        """)
        conn.commit()
    finally:
        cursor.close()

def serialize_log_details(details: Any):
    """统一序列化日志详情，便于写入 JSON 列。"""
    if details in (None, "", b""):
        return None
    if isinstance(details, bytes):
        details = details.decode("utf-8", errors="ignore")
    if isinstance(details, str):
        details = details.strip()
        if not details:
            return None
        try:
            json.loads(details)
            return details
        except (TypeError, ValueError, json.JSONDecodeError):
            return json.dumps({"raw": details}, ensure_ascii=False)
    try:
        return json.dumps(details, ensure_ascii=False)
    except (TypeError, ValueError):
        return json.dumps({"raw": str(details)}, ensure_ascii=False)

def insert_system_activity_log(cursor, payload: SystemActivityLogCreate) -> Tuple[Optional[int], datetime.datetime]:
    """复用统一日志写入逻辑，避免各模块重复拼 SQL。"""
    event_time = payload.event_time or datetime.datetime.now()
    details_json = serialize_log_details(payload.details)
    cursor.execute("""
        INSERT INTO system_activity_logs (
            source_type, module, category, action, level, result,
            asset_id, hostname, ip_address, operator_name, session_id,
            title, message, details_json, stdout_log, stderr_log,
            event_time, created_at
        ) VALUES (
            %s, %s, %s, %s, %s, %s,
            %s, %s, %s, %s, %s,
            %s, %s, %s, %s, %s,
            %s, NOW()
        )
    """, (
        payload.source_type,
        payload.module,
        payload.category,
        payload.action,
        payload.level,
        payload.result,
        payload.asset_id,
        payload.hostname,
        payload.ip_address,
        payload.operator_name,
        payload.session_id,
        payload.title,
        payload.message,
        details_json,
        payload.stdout_log,
        payload.stderr_log,
        event_time,
    ))
    return cursor.lastrowid, event_time

def normalize_log_row(row: Dict[str, Any]):
    details = parse_json_field(row.get("details_json"))
    return {
        "id": f"{row.get('source_type')}-{row.get('source_id')}",
        "source_id": row.get("source_id"),
        "source_type": row.get("source_type"),
        "module": row.get("module"),
        "category": row.get("category"),
        "action": row.get("action"),
        "level": row.get("level"),
        "result": row.get("result"),
        "asset_id": row.get("asset_id"),
        "hostname": row.get("hostname"),
        "ip_address": row.get("ip_address"),
        "operator_name": row.get("operator_name"),
        "session_id": row.get("session_id"),
        "title": row.get("title"),
        "message": row.get("message"),
        "event_time": format_datetime(row.get("event_time")),
        "details": details,
        "stdout_log": row.get("stdout_log"),
        "stderr_log": row.get("stderr_log")
    }
