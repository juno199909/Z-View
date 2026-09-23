# -*- coding: utf-8 -*-
"""Agent 退出策略路由（1.9.55）。

托盘「退出代理」密码验证：
- 管理端：GET/PUT /api/v1/console/agent-exit-policy（policies:read / policies:write）
  密码仅存 sha256 哈希（system_config，config_key=agent.exit_password_hash），明文不落库。
- Agent 端：POST /api/v1/agent/exit/verify（agent token 鉴权）
  未设密码返回 required=false（保持旧行为直接放行）；数据库不可用按 fail-closed 拒绝退出。
"""
from __future__ import annotations

import hashlib
import hmac
from typing import Optional

from fastapi import APIRouter, HTTPException, Request

from auth_utils import require_agent_request, require_request_permission
from zvplatform.db import create_connection


router = APIRouter(tags=["agent-exit-policy"])

EXIT_PASSWORD_HASH_KEY = "agent.exit_password_hash"


def _hash_password(password: str) -> str:
    return hashlib.sha256(password.encode("utf-8")).hexdigest()


def _load_exit_password_hash(conn) -> Optional[str]:
    cursor = conn.cursor()
    try:
        cursor.execute(
            "SELECT config_value FROM system_config WHERE config_key = %s",
            (EXIT_PASSWORD_HASH_KEY,),
        )
        row = cursor.fetchone()
        if not row:
            return None
        value = str(row[0] or "").strip()
        return value or None
    finally:
        cursor.close()


@router.get("/api/v1/console/agent-exit-policy")
def get_agent_exit_policy(request: Request):
    """查询退出密码验证开关（不回传哈希）。"""
    require_request_permission(getattr(request.state, "auth_user", None), request.url.path, request.method)
    conn = create_connection()
    if not conn:
        raise HTTPException(status_code=500, detail="Database connection failed")
    try:
        return {"enabled": _load_exit_password_hash(conn) is not None}
    finally:
        conn.close()


@router.put("/api/v1/console/agent-exit-policy")
def update_agent_exit_policy(request: Request, body: dict):
    """启用/停用退出密码验证（body: {enabled: bool, password?: str}）。"""
    require_request_permission(getattr(request.state, "auth_user", None), request.url.path, request.method)
    payload = body or {}
    enabled = bool(payload.get("enabled"))
    password = str(payload.get("password") or "").strip()
    if enabled and len(password) < 4:
        raise HTTPException(status_code=400, detail="退出密码至少 4 位")
    conn = create_connection()
    if not conn:
        raise HTTPException(status_code=500, detail="Database connection failed")
    cursor = None
    try:
        cursor = conn.cursor()
        if enabled:
            cursor.execute(
                """
                INSERT INTO system_config (config_key, config_value, config_type, description, updated_at)
                VALUES (%s, %s, 'string', %s, NOW())
                ON DUPLICATE KEY UPDATE
                    config_value = VALUES(config_value), updated_at = NOW()
                """,
                (
                    EXIT_PASSWORD_HASH_KEY,
                    _hash_password(password),
                    "Agent 退出代理密码哈希（sha256，明文不落库）",
                ),
            )
            message = "已启用退出密码验证，终端托盘退出需输入该密码"
        else:
            cursor.execute(
                "DELETE FROM system_config WHERE config_key = %s",
                (EXIT_PASSWORD_HASH_KEY,),
            )
            message = "已关闭退出密码验证，终端托盘退出无需密码"
        conn.commit()
        return {"enabled": enabled, "message": message}
    finally:
        if cursor:
            cursor.close()
        conn.close()


@router.post("/api/v1/agent/exit/verify")
def verify_agent_exit_password(request: Request, body: dict):
    """Agent 托盘退出前校验密码（agent token 鉴权；fail-closed）。"""
    require_agent_request(request)
    password = str((body or {}).get("password") or "")
    conn = create_connection()
    if not conn:
        return {"required": True, "valid": False, "server_error": True}
    try:
        expected = _load_exit_password_hash(conn)
        if expected is None:
            return {"required": False, "valid": True}
        valid = bool(password) and hmac.compare_digest(_hash_password(password), expected)
        return {"required": True, "valid": valid}
    finally:
        conn.close()
