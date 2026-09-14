# -*- coding: utf-8 -*-
"""日志留存配置与清理（监控中心 · 日志配置，V1.9.23）。

留存天数持久化在 system_config（config_key = monitoring.log_retention_days，
json），未配置时使用内置默认值。支持按表立即清理。
"""
from __future__ import annotations

import json
from typing import Any, Dict, Optional

from fastapi import APIRouter, HTTPException, Request

from auth_utils import require_request_permission
from zvplatform.db import create_connection

router = APIRouter(tags=["log-retention"])

RETENTION_CONFIG_KEY = "monitoring.log_retention_days"

# 受管日志表注册表：label 为监控中心展示名，time_col 为留存判定时间列
RETENTION_TABLE_META: Dict[str, Dict[str, str]] = {
    "agent_heartbeat": {"label": "心跳明细", "time_col": "heartbeat_time"},
    "asset_changes": {"label": "资产变更历史", "time_col": "changed_at"},
    "system_activity_logs": {"label": "系统操作日志", "time_col": "created_at"},
    "security_policy_exec_results": {"label": "安全策略执行记录", "time_col": "executed_at"},
    "remote_sessions": {"label": "远控会话记录", "time_col": "disconnected_at"},
    "usb_events": {"label": "USB 事件", "time_col": "occurred_at"},
}

DEFAULT_RETENTION_DAYS: Dict[str, int] = {
    "agent_heartbeat": 15,
    "asset_changes": 180,
    "system_activity_logs": 180,
    "security_policy_exec_results": 180,
    "remote_sessions": 90,
    "usb_events": 180,
}

RETENTION_DELETE_BATCH = 20000


def _clamp_days(value: Any) -> Optional[int]:
    """留存天数合法性：1..3650 整数，非法返回 None。"""
    try:
        days = int(value)
    except (TypeError, ValueError):
        return None
    if days < 1 or days > 3650:
        return None
    return days


def load_retention_config(conn=None) -> Dict[str, int]:
    """读留存配置：system_config 覆盖内置默认（仅接受受管表，非法值回退默认）。"""
    own_conn = False
    if conn is None:
        conn = create_connection()
        own_conn = True
    config = dict(DEFAULT_RETENTION_DAYS)
    if conn is None:
        return config
    cursor = None
    try:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT config_value FROM system_config WHERE config_key = %s",
            (RETENTION_CONFIG_KEY,),
        )
        row = cursor.fetchone()
        if row:
            stored = row[0] if not isinstance(row, dict) else list(row.values())[0]
            try:
                saved = json.loads(stored)
            except (TypeError, ValueError):
                saved = {}
            if isinstance(saved, dict):
                for table, days in saved.items():
                    if table in RETENTION_TABLE_META and _clamp_days(days) is not None:
                        config[table] = _clamp_days(days)
    except Exception:
        pass
    finally:
        if cursor:
            cursor.close()
        if own_conn and conn:
            conn.close()
    return config


def save_retention_config(days_by_table: Dict[str, int], conn=None) -> Dict[str, int]:
    """写留存配置到 system_config（uk_config_key upsert），返回归一化后的配置。"""
    normalized: Dict[str, int] = {}
    for table, days in (days_by_table or {}).items():
        if table not in RETENTION_TABLE_META:
            continue
        clamped = _clamp_days(days)
        if clamped is None:
            raise HTTPException(status_code=400, detail=f"{table} 留存天数须为 1~3650 的整数")
        normalized[table] = clamped
    own_conn = False
    if conn is None:
        conn = create_connection()
        own_conn = True
    if conn is None:
        raise HTTPException(status_code=500, detail="Database connection failed")
    cursor = None
    try:
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO system_config (config_key, config_value, config_type, description, updated_at)
            VALUES (%s, %s, 'json', %s, NOW())
            ON DUPLICATE KEY UPDATE
                config_value = VALUES(config_value), updated_at = NOW()
            """,
            (
                RETENTION_CONFIG_KEY,
                json.dumps(normalized, ensure_ascii=False),
                "监控中心·日志留存配置（各日志表保留天数）",
            ),
        )
        conn.commit()
    finally:
        if cursor:
            cursor.close()
        if own_conn and conn:
            conn.close()
    # 与库内默认合并（未配置的表保持默认），保证返回完整配置
    merged = dict(DEFAULT_RETENTION_DAYS)
    merged.update(normalized)
    return merged


def run_retention_cleanup(conn=None) -> Dict[str, Any]:
    """按当前配置执行留存清理（分批删除），返回 {表: 删除行数}。"""
    config = load_retention_config(conn)
    own_conn = False
    if conn is None:
        conn = create_connection()
        own_conn = True
    if conn is None:
        raise HTTPException(status_code=500, detail="Database connection failed")
    deleted: Dict[str, Any] = {}
    cursor = None
    try:
        cursor = conn.cursor()
        for table, meta in RETENTION_TABLE_META.items():
            days = config.get(table)
            if not days:
                continue
            time_col = meta["time_col"]
            table_deleted = 0
            try:
                while True:
                    cursor.execute(
                        f"DELETE FROM {table} WHERE {time_col} < DATE_SUB(NOW(), INTERVAL %s DAY) "
                        f"LIMIT {RETENTION_DELETE_BATCH}",
                        (days,),
                    )
                    batch = cursor.rowcount
                    table_deleted += batch
                    if batch < RETENTION_DELETE_BATCH:
                        break
                conn.commit()
                deleted[table] = table_deleted
            except Exception as exc:
                conn.rollback()
                deleted[table] = f"error: {exc}"
    finally:
        if cursor:
            cursor.close()
        if own_conn and conn:
            conn.close()
    return deleted


def _table_stats(cursor, table: str, time_col: str) -> Dict[str, Any]:
    cursor.execute(f"SELECT COUNT(*) FROM `{table}`")
    rows = cursor.fetchone()[0]
    cursor.execute(f"SELECT MIN(`{time_col}`) FROM `{table}`")
    oldest = cursor.fetchone()[0]
    return {
        "table": table,
        "rows": rows,
        "oldest": oldest.strftime("%Y-%m-%d %H:%M:%S") if oldest else None,
    }


@router.get("/api/v1/monitoring/log-retention")
def get_log_retention(request: Request):
    """日志留存配置 + 各表现状（监控中心·日志配置页）。"""
    require_request_permission(getattr(request.state, "auth_user", None), request.url.path, request.method)
    from zvplatform.db import create_connection as _cc
    conn = _cc()
    if not conn:
        raise HTTPException(status_code=500, detail="Database connection failed")
    try:
        config = load_retention_config(conn)
        cursor = conn.cursor()
        tables = []
        for table, meta in RETENTION_TABLE_META.items():
            try:
                stats = _table_stats(cursor, table, meta["time_col"])
            except Exception:
                stats = {"table": table, "rows": None, "oldest": None}
            tables.append({
                **stats,
                "label": meta["label"],
                "time_col": meta["time_col"],
                "days": config.get(table),
            })
        cursor.close()
        return {"config": config, "tables": tables}
    finally:
        conn.close()


@router.put("/api/v1/monitoring/log-retention")
def update_log_retention(request: Request, body: dict):
    """更新日志留存配置（body: {days: {表: 天数}}）。"""
    require_request_permission(getattr(request.state, "auth_user", None), request.url.path, request.method)
    days_by_table = (body or {}).get("days")
    if not isinstance(days_by_table, dict) or not days_by_table:
        raise HTTPException(status_code=400, detail="days 不能为空")
    config = save_retention_config(days_by_table)
    return {"config": config, "message": "日志留存配置已保存，将于下一轮清理（最长 6 小时）生效，也可立即执行清理"}
    # 说明：save_retention_config 内部抛出的 HTTPException(400) 原样透出


@router.post("/api/v1/monitoring/log-retention/run")
def run_log_retention_now(request: Request):
    """按当前配置立即执行一轮留存清理。"""
    require_request_permission(getattr(request.state, "auth_user", None), request.url.path, request.method)
    deleted = run_retention_cleanup()
    return {"deleted": deleted, "message": "清理完成"}
