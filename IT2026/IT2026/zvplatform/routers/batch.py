# -*- coding: utf-8 -*-
"""批量操作路由（P1-01 从 assets_api 迁出）。"""
from __future__ import annotations

import json
from typing import Optional

from fastapi import APIRouter, HTTPException, Query, Request
from mysql.connector import Error

from auth_utils import get_request_username
from zvplatform.db import create_connection, format_datetime
from zvplatform.models import BatchExecuteRequest
from zvplatform.services.batch_service import (
    build_batch_command,
    build_batch_parameters_text,
    build_batch_zview_cmd,
    execute_batch_command_on_agent,
    get_batch_operation_timeout,
    normalize_batch_result_row,
)


router = APIRouter(prefix="/api/v1/batch", tags=["batch"])


def ensure_batch_tables(conn):
    """确保批量操作主表和结果表存在。"""
    cursor = conn.cursor()
    try:
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS batch_operations (
                id BIGINT UNSIGNED PRIMARY KEY AUTO_INCREMENT,
                operation_type VARCHAR(50) NOT NULL,
                operator_name VARCHAR(100) NULL,
                parameters_json JSON NULL,
                parameters_text TEXT NULL,
                target_count INT NOT NULL,
                success_count INT NOT NULL DEFAULT 0,
                failed_count INT NOT NULL DEFAULT 0,
                status VARCHAR(20) DEFAULT 'pending',
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                completed_at DATETIME NULL,
                KEY idx_created_at (created_at)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS batch_operation_results (
                id BIGINT UNSIGNED PRIMARY KEY AUTO_INCREMENT,
                operation_id BIGINT UNSIGNED NOT NULL,
                asset_id BIGINT UNSIGNED NULL,
                hostname VARCHAR(255) NULL,
                ip_address VARCHAR(64) NULL,
                status VARCHAR(20) NOT NULL,
                command_text TEXT NULL,
                stdout_log MEDIUMTEXT NULL,
                stderr_log MEDIUMTEXT NULL,
                output_text MEDIUMTEXT NULL,
                returncode INT NULL,
                error_message TEXT NULL,
                executed_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                KEY idx_operation_id (operation_id),
                KEY idx_asset_id (asset_id),
                CONSTRAINT fk_batch_results_operation FOREIGN KEY (operation_id)
                    REFERENCES batch_operations (id) ON DELETE CASCADE,
                CONSTRAINT fk_batch_results_asset FOREIGN KEY (asset_id)
                    REFERENCES assets (id) ON DELETE CASCADE
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """)
        conn.commit()
    finally:
        cursor.close()


@router.post("/execute")
def execute_batch(payload: BatchExecuteRequest, request: Request):
    """执行批量操作（重启/关机/脚本/软件/自由命令）"""
    if not payload.terminal_ids:
        raise HTTPException(status_code=400, detail="terminal_ids is required")

    operation_type = payload.operation_type
    parameters = payload.parameters
    operator = payload.operator_name or get_request_username(request, fallback="console")

    conn = create_connection()
    if not conn:
        raise HTTPException(status_code=500, detail="Database connection failed")

    cursor = None
    try:
        from zvplatform.services.batch_service import ensure_batch_tables as _ebt
        _ebt(conn)

        command_text = build_batch_command(operation_type, parameters)
        parameters_text = build_batch_parameters_text(operation_type, parameters)
        timeout_sec = get_batch_operation_timeout(operation_type)

        cursor = conn.cursor(dictionary=True)
        # 校验终端存在
        placeholders = ", ".join(["%s"] * len(payload.terminal_ids))
        cursor.execute(
            f"SELECT id, hostname, ip_address, status FROM assets "
            f"WHERE id IN ({placeholders}) AND deleted_at IS NULL",
            payload.terminal_ids,
        )
        assets = cursor.fetchall()
        if not assets:
            raise HTTPException(status_code=404, detail="No valid assets found")

        # 创建操作记录
        cursor.execute(
            "INSERT INTO batch_operations (operation_type, operator_name, parameters_json, "
            "parameters_text, target_count, status) VALUES (%s, %s, %s, %s, %s, 'running')",
            (operation_type, operator, json.dumps(parameters, ensure_ascii=False),
             parameters_text, len(assets)),
        )
        operation_id = cursor.lastrowid
        conn.commit()
    except HTTPException:
        raise
    except Error as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        if cursor:
            cursor.close()
        conn.close()

    # 执行
    results = []
    success_count = 0
    failed_count = 0
    for asset in assets:
        result = execute_batch_command_on_agent(
            asset, operation_type, parameters, operator
        )
        if result.get("status") == "success":
            success_count += 1
        else:
            failed_count += 1
        results.append(result)

    # 更新操作状态
    conn = create_connection()
    if conn:
        try:
            cursor = conn.cursor()
            cursor.execute(
                "UPDATE batch_operations SET success_count=%s, failed_count=%s, "
                "status='completed', completed_at=NOW() WHERE id=%s",
                (success_count, failed_count, operation_id),
            )
            # 写结果
            for r in results:
                cursor.execute(
                    "INSERT INTO batch_operation_results (operation_id, asset_id, hostname, "
                    "ip_address, status, command_text, stdout_log, stderr_log, output_text, "
                    "returncode, error_message) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                    (operation_id, r.get("asset_id"), r.get("hostname"), r.get("ip_address"),
                     r.get("status"), r.get("command_text"), r.get("stdout_log"),
                     r.get("stderr_log"), r.get("output_text"), r.get("returncode"),
                     r.get("error_message")),
                )
            conn.commit()
        except Error:
            conn.rollback()
        finally:
            cursor.close()
            conn.close()

    return {
        "message": "Batch operation completed",
        "operation_id": operation_id,
        "operation_type": operation_type,
        "total": len(assets),
        "success": success_count,
        "failed": failed_count,
        "results": results,
    }


@router.get("/history")
def get_batch_history(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=100),
    operation_type: Optional[str] = Query(default=None),
):
    """获取批量操作历史"""
    conn = create_connection()
    if not conn:
        raise HTTPException(status_code=500, detail="Database connection failed")

    cursor = None
    try:
        from zvplatform.services.batch_service import ensure_batch_tables as _ebt
        _ebt(conn)
        cursor = conn.cursor(dictionary=True)

        where = []
        params = []
        if operation_type:
            where.append("operation_type = %s")
            params.append(operation_type)
        where_sql = (" AND " + " AND ".join(where)) if where else ""

        cursor.execute(f"SELECT COUNT(*) AS total FROM batch_operations WHERE 1=1{where_sql}", params)
        total = cursor.fetchone()["total"]

        offset = (page - 1) * page_size
        cursor.execute(
            f"SELECT * FROM batch_operations WHERE 1=1{where_sql} "
            f"ORDER BY created_at DESC LIMIT %s OFFSET %s",
            params + [page_size, offset],
        )
        rows = cursor.fetchall()
        for row in rows:
            if row.get("created_at"):
                row["created_at"] = format_datetime(row["created_at"])
            if row.get("completed_at"):
                row["completed_at"] = format_datetime(row["completed_at"])
        return {"data": rows, "total": total, "page": page, "page_size": page_size}
    except Error as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        if cursor:
            cursor.close()
        conn.close()


@router.get("/{operation_id}/results")
def get_batch_results(operation_id: int):
    """获取批量操作执行结果"""
    conn = create_connection()
    if not conn:
        raise HTTPException(status_code=500, detail="Database connection failed")

    cursor = None
    try:
        cursor = conn.cursor(dictionary=True)
        cursor.execute("SELECT * FROM batch_operations WHERE id=%s", (operation_id,))
        operation = cursor.fetchone()
        if not operation:
            raise HTTPException(status_code=404, detail="Operation not found")

        cursor.execute(
            "SELECT * FROM batch_operation_results WHERE operation_id=%s "
            "ORDER BY executed_at DESC",
            (operation_id,),
        )
        results = cursor.fetchall()
        for r in results:
            for dt_field in ("executed_at", "created_at"):
                if r.get(dt_field):
                    r[dt_field] = format_datetime(r[dt_field])
        return {"operation": operation, "results": results}
    except HTTPException:
        raise
    except Error as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        if cursor:
            cursor.close()
        conn.close()
