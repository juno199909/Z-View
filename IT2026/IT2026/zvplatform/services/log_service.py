# -*- coding: utf-8 -*-
"""统一日志聚合服务（P1-01 从 assets_api 迁出）。"""
from __future__ import annotations

from typing import Any, List, Optional, Tuple

from zvplatform.db import table_exists

UNIFIED_LOG_TEXT_COLLATION = "utf8mb4_unicode_ci"


def unified_log_text_sql(expr: str, alias: str) -> str:
    """统一聚合日志文本字段的字符集与排序规则，避免 UNION 时出现 collation 冲突。"""
    return (
        f"CAST({expr} AS CHAR CHARACTER SET utf8mb4) "
        f"COLLATE {UNIFIED_LOG_TEXT_COLLATION} AS {alias}"
    )




def build_empty_unified_logs_select() -> str:
    return f"""
        SELECT
            NULL AS source_id,
            {unified_log_text_sql("NULL", "source_type")},
            {unified_log_text_sql("NULL", "module")},
            {unified_log_text_sql("NULL", "category")},
            {unified_log_text_sql("NULL", "action")},
            {unified_log_text_sql("NULL", "level")},
            {unified_log_text_sql("NULL", "result")},
            NULL AS asset_id,
            {unified_log_text_sql("NULL", "hostname")},
            {unified_log_text_sql("NULL", "ip_address")},
            {unified_log_text_sql("NULL", "operator_name")},
            {unified_log_text_sql("NULL", "session_id")},
            {unified_log_text_sql("NULL", "title")},
            {unified_log_text_sql("NULL", "message")},
            NULL AS event_time,
            {unified_log_text_sql("NULL", "details_json")},
            {unified_log_text_sql("NULL", "stdout_log")},
            {unified_log_text_sql("NULL", "stderr_log")}
        WHERE 1 = 0
    """




def build_unified_logs_union(conn) -> str:
    """拼装统一日志查询，按当前可用表动态聚合。"""
    selects = []

    if table_exists(conn, "system_activity_logs"):
        selects.append(f"""
            SELECT
                l.id AS source_id,
                {unified_log_text_sql("COALESCE(l.source_type, 'agent')", "source_type")},
                {unified_log_text_sql("l.module", "module")},
                {unified_log_text_sql("l.category", "category")},
                {unified_log_text_sql("l.action", "action")},
                {unified_log_text_sql("l.level", "level")},
                {unified_log_text_sql("l.result", "result")},
                l.asset_id AS asset_id,
                {unified_log_text_sql("l.hostname", "hostname")},
                {unified_log_text_sql("l.ip_address", "ip_address")},
                {unified_log_text_sql("l.operator_name", "operator_name")},
                {unified_log_text_sql("l.session_id", "session_id")},
                {unified_log_text_sql("l.title", "title")},
                {unified_log_text_sql("l.message", "message")},
                l.event_time AS event_time,
                {unified_log_text_sql("l.details_json", "details_json")},
                {unified_log_text_sql("l.stdout_log", "stdout_log")},
                {unified_log_text_sql("l.stderr_log", "stderr_log")}
            FROM system_activity_logs l
        """)

    if table_exists(conn, "alerts"):
        selects.append(f"""
            SELECT
                al.id AS source_id,
                {unified_log_text_sql("'alert'", "source_type")},
                {unified_log_text_sql("'alert_center'", "module")},
                {unified_log_text_sql("al.alert_type", "category")},
                {unified_log_text_sql("CASE WHEN al.status = 'resolved' THEN 'resolve' ELSE 'trigger' END", "action")},
                {unified_log_text_sql("al.severity", "level")},
                {unified_log_text_sql("al.status", "result")},
                al.asset_id AS asset_id,
                {unified_log_text_sql("a.hostname", "hostname")},
                {unified_log_text_sql("a.ip_address", "ip_address")},
                {unified_log_text_sql("al.resolved_by", "operator_name")},
                {unified_log_text_sql("NULL", "session_id")},
                {unified_log_text_sql("CONCAT(COALESCE(a.hostname, CONCAT('资产 ', al.asset_id)), ' 告警')", "title")},
                {unified_log_text_sql("al.message", "message")},
                COALESCE(al.last_seen_at, al.first_triggered_at, al.created_at) AS event_time,
                {unified_log_text_sql("al.details_json", "details_json")},
                {unified_log_text_sql("NULL", "stdout_log")},
                {unified_log_text_sql("NULL", "stderr_log")}
            FROM alerts al
            LEFT JOIN assets a ON a.id = al.asset_id
        """)

    if table_exists(conn, "software_task_results") and table_exists(conn, "software_tasks"):
        selects.append(f"""
            SELECT
                r.id AS source_id,
                {unified_log_text_sql("'software_task'", "source_type")},
                {unified_log_text_sql("'software_management'", "module")},
                {unified_log_text_sql("'task_result'", "category")},
                {unified_log_text_sql("COALESCE(t.task_type, 'task')", "action")},
                {unified_log_text_sql('''CASE
                    WHEN r.status = 'failed' THEN 'error'
                    WHEN r.status IN ('timeout', 'cancelled') THEN 'warning'
                    ELSE 'info'
                END''', "level")},
                {unified_log_text_sql("r.status", "result")},
                r.asset_id AS asset_id,
                {unified_log_text_sql("a.hostname", "hostname")},
                {unified_log_text_sql("a.ip_address", "ip_address")},
                {unified_log_text_sql("t.created_by", "operator_name")},
                {unified_log_text_sql("NULL", "session_id")},
                {unified_log_text_sql("COALESCE(t.task_name, p.display_name, t.software_name, CONCAT('任务 ', r.task_id))", "title")},
                {unified_log_text_sql('''COALESCE(
                    r.error_message,
                    CONCAT(
                        COALESCE(a.hostname, CONCAT('资产 ', r.asset_id)),
                        ' ',
                        COALESCE(t.task_type, 'task'),
                        ' ',
                        COALESCE(t.software_name, p.display_name, '软件包'),
                        ' 状态: ',
                        COALESCE(r.status, 'unknown')
                    )
                )''', "message")},
                COALESCE(r.updated_at, r.end_time, r.start_time, r.created_at) AS event_time,
                {unified_log_text_sql('''JSON_OBJECT(
                    'task_id', r.task_id,
                    'task_name', t.task_name,
                    'task_type', t.task_type,
                    'package_id', t.package_id,
                    'package_name', p.display_name,
                    'software_name', t.software_name,
                    'progress', r.progress,
                    'download_progress', r.download_progress,
                    'install_progress', r.install_progress,
                    'duration', r.duration,
                    'error_code', r.error_code
                )''', "details_json")},
                {unified_log_text_sql("r.stdout_log", "stdout_log")},
                {unified_log_text_sql("r.stderr_log", "stderr_log")}
            FROM software_task_results r
            LEFT JOIN software_tasks t ON t.id = r.task_id
            LEFT JOIN software_packages p ON p.id = t.package_id
            LEFT JOIN assets a ON a.id = r.asset_id
        """)

    if table_exists(conn, "software_policy_logs"):
        selects.append(f"""
            SELECT
                pl.id AS source_id,
                {unified_log_text_sql("'policy_log'", "source_type")},
                {unified_log_text_sql("'software_policy'", "module")},
                {unified_log_text_sql("'policy_execution'", "category")},
                {unified_log_text_sql("COALESCE(pl.action, 'policy_check')", "action")},
                {unified_log_text_sql('''CASE
                    WHEN pl.result = 'failed' THEN 'error'
                    WHEN pl.result = 'blocked' THEN 'warning'
                    ELSE 'info'
                END''', "level")},
                {unified_log_text_sql("pl.result", "result")},
                pl.asset_id AS asset_id,
                {unified_log_text_sql("a.hostname", "hostname")},
                {unified_log_text_sql("a.ip_address", "ip_address")},
                {unified_log_text_sql("NULL", "operator_name")},
                {unified_log_text_sql("NULL", "session_id")},
                {unified_log_text_sql("COALESCE(pl.software_name, CONCAT('策略 ', pl.policy_id))", "title")},
                {unified_log_text_sql("COALESCE(pl.message, CONCAT('策略执行: ', COALESCE(pl.action, 'policy_check')))", "message")},
                pl.created_at AS event_time,
                {unified_log_text_sql('''JSON_OBJECT(
                    'policy_id', pl.policy_id,
                    'software_name', pl.software_name
                )''', "details_json")},
                {unified_log_text_sql("NULL", "stdout_log")},
                {unified_log_text_sql("NULL", "stderr_log")}
            FROM software_policy_logs pl
            LEFT JOIN assets a ON a.id = pl.asset_id
        """)

    if table_exists(conn, "software_audit_logs"):
        selects.append(f"""
            SELECT
                sal.id AS source_id,
                {unified_log_text_sql("'software_audit'", "source_type")},
                {unified_log_text_sql("'software_management'", "module")},
                {unified_log_text_sql("COALESCE(sal.target_type, 'audit')", "category")},
                {unified_log_text_sql("sal.operation_type", "action")},
                {unified_log_text_sql('''CASE
                    WHEN sal.result = 'failed' THEN 'error'
                    ELSE 'info'
                END''', "level")},
                {unified_log_text_sql("sal.result", "result")},
                NULL AS asset_id,
                {unified_log_text_sql("NULL", "hostname")},
                {unified_log_text_sql("sal.operator_ip", "ip_address")},
                {unified_log_text_sql("sal.operator", "operator_name")},
                {unified_log_text_sql("NULL", "session_id")},
                {unified_log_text_sql("COALESCE(sal.target_name, sal.operation_type)", "title")},
                {unified_log_text_sql("COALESCE(sal.error_message, sal.operation_type)", "message")},
                sal.created_at AS event_time,
                {unified_log_text_sql("sal.operation_details", "details_json")},
                {unified_log_text_sql("NULL", "stdout_log")},
                {unified_log_text_sql("NULL", "stderr_log")}
            FROM software_audit_logs sal
        """)

    if not selects:
        return build_empty_unified_logs_select()

    return "\nUNION ALL\n".join(selects)



def build_unified_logs_where(
    source_type: Optional[str] = None,
    module: Optional[str] = None,
    category: Optional[str] = None,
    asset_id: Optional[int] = None,
    keyword: Optional[str] = None,
    level: Optional[str] = None,
    result: Optional[str] = None,
    operator: Optional[str] = None,
    start_time: Optional[str] = None,
    end_time: Optional[str] = None
) -> Tuple[str, List[Any]]:
    clauses = ["1 = 1"]
    params: List[Any] = []

    if source_type:
        clauses.append("logs.source_type = %s")
        params.append(source_type)

    if module:
        clauses.append("logs.module = %s")
        params.append(module)

    if operator:
        clauses.append("logs.operator_name LIKE %s")
        params.append(f"%{operator}%")

    if category:
        clauses.append("logs.category = %s")
        params.append(category)

    if asset_id is not None:
        clauses.append("logs.asset_id = %s")
        params.append(asset_id)

    if level:
        clauses.append("logs.level = %s")
        params.append(level)

    if result:
        clauses.append("logs.result = %s")
        params.append(result)

    if keyword:
        keyword_like = f"%{keyword}%"
        clauses.append("""
            (
                logs.message LIKE %s
                OR logs.title LIKE %s
                OR logs.hostname LIKE %s
                OR logs.ip_address LIKE %s
                OR logs.operator_name LIKE %s
            )
        """)
        params.extend([keyword_like, keyword_like, keyword_like, keyword_like, keyword_like])

    if start_time:
        clauses.append("logs.event_time >= %s")
        params.append(start_time)

    if end_time:
        clauses.append("logs.event_time <= %s")
        params.append(end_time)

    return " AND ".join(clauses), params
