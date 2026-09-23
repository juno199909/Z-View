# -*- coding: utf-8 -*-
"""Agent 策略路由（P1-01 从 assets_api 迁出）。"""
from __future__ import annotations

import json

from fastapi import APIRouter, HTTPException, Request

from auth_utils import get_request_username, require_agent_request
from console_utils import safe_console_print
from zvplatform.db import create_connection
from zvplatform.services.agent_policy_service import (
    ensure_agent_policies_table,
    load_agent_policies_with_meta,
    normalize_agent_policies,
)


router = APIRouter(tags=["agent-policies"])


@router.get("/api/v1/console/agent-policies")
def console_get_agent_policies(request: Request):
    return load_agent_policies_with_meta()


@router.put("/api/v1/console/agent-policies")
def console_update_agent_policies(data: dict, request: Request):
    normalized, errors = normalize_agent_policies(data)
    if errors:
        raise HTTPException(status_code=400, detail="; ".join(errors))

    conn = create_connection()
    if not conn:
        raise HTTPException(status_code=500, detail="Database connection failed")
    try:
        ensure_agent_policies_table(conn)
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO agent_policies (id, policies_json, updated_at)
            VALUES (1, %s, NOW())
            ON DUPLICATE KEY UPDATE policies_json = VALUES(policies_json), updated_at = NOW()
            """,
            (json.dumps(normalized, ensure_ascii=False),),
        )
        conn.commit()
    except HTTPException:
        raise
    except Exception as exc:
        try:
            conn.rollback()
        except Exception:
            pass
        raise HTTPException(status_code=500, detail=f"Failed to save policies: {exc}")
    finally:
        try:
            conn.close()
        except Exception:
            pass

    safe_console_print(
        f"[AgentPolicies] updated by {get_request_username(request)}: "
        f"{json.dumps(normalized, ensure_ascii=False)}"
    )
    return load_agent_policies_with_meta()


@router.get("/api/v1/agent/policies")
def agent_get_policies(request: Request):
    require_agent_request(request)
    return {"status": "success", **load_agent_policies_with_meta()}
