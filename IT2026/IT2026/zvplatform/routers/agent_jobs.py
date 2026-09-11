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
from zvplatform.models import SystemActivityLogCreate
from zvplatform.repositories.log_repository import insert_system_activity_log

router = APIRouter(tags=["agent-jobs"])

_TABLE = "agent_jobs"
_VALID_STATES = ("pending", "dispatched", "succeeded", "failed")
# V1.9.x 安全边界：服务端任务类型白名单（与 Agent 端 handler 注册表对应）
_KNOWN_JOB_TYPES = ("command", "report")
_MAX_PENDING_PER_ASSET = 10
_JOB_EXPIRY_HOURS = 24  # pending 超时未下发即过期（陈旧任务不应执行）


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
    """Agent 上报执行状态（幂等 upsert；终态不可被覆盖为中间态）。V1.9.x：终态审计。"""
    if state not in _VALID_STATES:
        return
    ensure_agent_jobs_table(conn)
    cursor = conn.cursor(dictionary=True)
    job_row = None
    try:
        cursor.execute(
            f"SELECT job_id, asset_id, job_type, state FROM {_TABLE} WHERE job_id = %s",
            (job_id,),
        )
        job_row = cursor.fetchone()
    except Exception:
        job_row = None
    finally:
        cursor.close()
    if not job_row:
        return
    current_state = job_row.get("state")
    if current_state in ("succeeded", "failed") and state in ("pending", "dispatched", "running"):
        return
    result_json = json.dumps(result, ensure_ascii=False) if result is not None else None
    cursor = conn.cursor()
    try:
        cursor.execute(
            f"UPDATE {_TABLE} SET state = %s, result = %s WHERE job_id = %s",
            (state, result_json, job_id),
        )
        conn.commit()
    except Exception as exc:
        safe_console_print(f"[AgentJobs] record state failed: {exc}")
    finally:
        cursor.close()
    # V1.9.x：终态审计
    if state in ("succeeded", "failed"):
        try:
            audit_cursor = conn.cursor()
            try:
                insert_system_activity_log(audit_cursor, SystemActivityLogCreate(
                    source_type="agent",
                    module="agent_jobs",
                    category="operation",
                    action=f"agent_job_{state}",
                    level="info" if state == "succeeded" else "warning",
                    result="success" if state == "succeeded" else "failed",
                    asset_id=job_row.get("asset_id"),
                    hostname=None,
                    operator_name="agent-job-executor",
                    title=f"任务执行{ '成功' if state == 'succeeded' else '失败' }: {job_row.get('job_type')}",
                    message=f"job_id={job_id} state={state}",
                    details={"job_id": job_id, "job_type": job_row.get("job_type"), "result": result},
                ))
                conn.commit()
            finally:
                audit_cursor.close()
        except Exception:
            pass


def fetch_pending_jobs(conn, asset_id: int, limit: int = 5) -> list[dict]:
    """取该资产的 pending 任务并在下发时标记 dispatched。

    V1.9.x 安全边界：先过期陈旧 pending（超 _JOB_EXPIRY_HOURS 未下发即
    expired，陈旧任务不应在终端执行）。
    """
    ensure_agent_jobs_table(conn)
    cursor = conn.cursor(dictionary=True)
    try:
        cursor.execute(
            f"UPDATE {_TABLE} SET state = 'expired' "
            f"WHERE asset_id = %s AND state = 'pending' "
            f"AND created_at < NOW() - INTERVAL {_JOB_EXPIRY_HOURS} HOUR",
            (asset_id,),
        )
        conn.commit()
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
    if job_type not in _KNOWN_JOB_TYPES:
        raise HTTPException(status_code=422, detail=f"unknown job_type: {job_type} (allowed: {', '.join(_KNOWN_JOB_TYPES)})")
    conn = create_connection()
    if not conn:
        raise HTTPException(status_code=500, detail="Database connection failed")
    try:
        ensure_agent_jobs_table(conn)
        # V1.9.x 安全边界：资产校验（任务只能下发给存在的未删除资产）
        cursor = conn.cursor()
        try:
            cursor.execute(
                "SELECT hostname FROM assets WHERE id = %s AND deleted_at IS NULL",
                (asset_id,),
            )
            asset_row = cursor.fetchone()
        finally:
            cursor.close()
        if not asset_row:
            raise HTTPException(status_code=404, detail=f"asset {asset_id} not found")
        # 每资产 pending/dispatched 上限（防任务堆积）
        cursor = conn.cursor()
        try:
            cursor.execute(
                f"SELECT COUNT(*) FROM {_TABLE} WHERE asset_id = %s AND state IN ('pending', 'dispatched')",
                (asset_id,),
            )
            if (cursor.fetchone() or [0])[0] >= _MAX_PENDING_PER_ASSET:
                raise HTTPException(status_code=429, detail="too many pending jobs for this asset")
        finally:
            cursor.close()
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
        # V1.9.x：任务创建审计
        try:
            audit_cursor = conn.cursor()
            try:
                insert_system_activity_log(audit_cursor, SystemActivityLogCreate(
                    source_type="platform",
                    module="agent_jobs",
                    category="operation",
                    action="agent_job_created",
                    level="info",
                    result="success",
                    asset_id=asset_id,
                    hostname=asset_row[0] if not isinstance(asset_row, dict) else asset_row.get("hostname"),
                    operator_name=getattr(request.state, "auth_user", None) and
                                  getattr(request.state.auth_user, "username", "admin") or "admin",
                    title=f"下发任务 {job_type}",
                    message=f"job_id={job_id} type={job_type}",
                    details={"job_id": job_id, "job_type": job_type, "payload": payload},
                ))
                conn.commit()
            finally:
                audit_cursor.close()
        except Exception:
            pass
        return {"job_id": job_id, "asset_id": asset_id, "job_type": job_type, "state": "pending"}
    finally:
        conn.close()
