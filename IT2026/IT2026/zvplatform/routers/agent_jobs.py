# -*- coding: utf-8 -*-
"""Agent 通用任务通道（V1.8.3）。

服务端下发（console 创建）→ 心跳响应携带 pending 任务 → Agent 执行 →
job_results 随心跳回传 → 事务历史落库。任务类型由 Agent 端 handler 注册表
决定（command/report/…），未知类型 Agent 显式报 failed。
"""
from __future__ import annotations

import json
import uuid
from typing import Optional

from fastapi import APIRouter, HTTPException, Request

from auth_utils import require_request_permission
from console_utils import safe_console_print
from zvplatform.db import create_connection

router = APIRouter(tags=["agent-jobs"])

_TABLE = "agent_jobs"
_VALID_STATES = ("pending", "dispatched", "succeeded", "failed")


def ensure_agent_jobs_table(conn) -> None:
    cursor = conn.cursor()
    try:
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS agent_jobs (
                id BIGINT PRIMARY KEY AUTO_INCREMENT,
                job_id VARCHAR(64) NOT NULL UNIQUE,
                asset_id INT NOT NULL,
                job_type VARCHAR(50) NOT NULL,
                payload JSON NULL,
                state VARCHAR(20) NOT NULL DEFAULT 'pending',
                result JSON NULL,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                INDEX idx_jobs_asset_state (asset_id, state)
            )
        """)
        conn.commit()
    finally:
        cursor.close()


def record_job_state(conn, job_id: str, state: str, result: Optional[dict] = None) -> None:
    """Agent 上报执行状态（幂等 upsert；终态不可被覆盖为中间态）。"""
    if state not in _VALID_STATES:
        return
    ensure_agent_jobs_table(conn)
    cursor = conn.cursor()
    try:
        cursor.execute(
            f"SELECT state FROM {_TABLE} WHERE job_id = %s",
            (job_id,),
        )
        row = cursor.fetchone()
        if row is None:
            return
        current_state = row[0] if not isinstance(row, dict) else row.get("state")
        if current_state in ("succeeded", "failed") and state in ("pending", "dispatched", "running"):
            return
        result_json = json.dumps(result, ensure_ascii=False) if result is not None else None
        cursor.execute(
            f"UPDATE {_TABLE} SET state = %s, result = %s WHERE job_id = %s",
            (state, result_json, job_id),
        )
        conn.commit()
    except Exception as exc:
        safe_console_print(f"[AgentJobs] record state failed: {exc}")
    finally:
        cursor.close()


def fetch_pending_jobs(conn, asset_id: int, limit: int = 5) -> list[dict]:
    """取该资产的 pending 任务并在下发时标记 dispatched。"""
    ensure_agent_jobs_table(conn)
    cursor = conn.cursor(dictionary=True)
    try:
        cursor.execute(
            f"SELECT job_id, job_type, payload FROM {_TABLE} "
            f"WHERE asset_id = %s AND state = 'pending' ORDER BY id LIMIT %s",
            (asset_id, limit),
        )
        rows = cursor.fetchall() or []
        jobs = []
        for row in rows:
            payload = row.get("payload")
            if isinstance(payload, str):
                try:
                    payload = json.loads(payload)
                except Exception:
                    payload = {}
            jobs.append({
                "job_id": row.get("job_id"),
                "job_type": row.get("job_type"),
                "payload": payload or {},
            })
        if jobs:
            cursor.execute(
                f"UPDATE {_TABLE} SET state = 'dispatched' "
                f"WHERE asset_id = %s AND state = 'pending' AND job_id IN ({','.join(['%s'] * len(jobs))})",
                tuple([asset_id] + [j["job_id"] for j in jobs]),
            )
            conn.commit()
        return jobs
    finally:
        cursor.close()


@router.get("/api/v1/console/agent-jobs")
def list_agent_jobs(request: Request, asset_id: Optional[int] = None, limit: int = 50):
    """任务列表（admin）。"""
    require_request_permission(getattr(request.state, "auth_user", None), request.url.path, request.method)
    conn = create_connection()
    if not conn:
        raise HTTPException(status_code=500, detail="Database connection failed")
    try:
        ensure_agent_jobs_table(conn)
        cursor = conn.cursor(dictionary=True)
        try:
            if asset_id:
                cursor.execute(
                    f"SELECT * FROM {_TABLE} WHERE asset_id = %s ORDER BY id DESC LIMIT %s",
                    (asset_id, min(limit, 200)),
                )
            else:
                cursor.execute(f"SELECT * FROM {_TABLE} ORDER BY id DESC LIMIT %s", (min(limit, 200),))
            rows = cursor.fetchall() or []
            return {"jobs": rows, "total": len(rows)}
        finally:
            cursor.close()
    finally:
        conn.close()


@router.post("/api/v1/console/agent-jobs")
def create_agent_job(request: Request, body: dict):
    """创建任务（admin）。body: {asset_id, job_type, payload}。"""
    require_request_permission(getattr(request.state, "auth_user", None), request.url.path, request.method)
    asset_id = body.get("asset_id")
    job_type = str(body.get("job_type") or "").strip()
    payload = body.get("payload")
    if not asset_id or not job_type:
        raise HTTPException(status_code=422, detail="asset_id and job_type are required")
    conn = create_connection()
    if not conn:
        raise HTTPException(status_code=500, detail="Database connection failed")
    try:
        ensure_agent_jobs_table(conn)
        job_id = f"JOB-{uuid.uuid4().hex[:12].upper()}"
        cursor = conn.cursor()
        try:
            cursor.execute(
                f"INSERT INTO {_TABLE} (job_id, asset_id, job_type, payload) VALUES (%s, %s, %s, %s)",
                (job_id, asset_id, job_type,
                 json.dumps(payload, ensure_ascii=False) if payload else None),
            )
            conn.commit()
        finally:
            cursor.close()
        return {"job_id": job_id, "asset_id": asset_id, "job_type": job_type, "state": "pending"}
    finally:
        conn.close()
