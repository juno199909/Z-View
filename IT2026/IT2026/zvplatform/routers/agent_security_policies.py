# -*- coding: utf-8 -*-
"""终端安全策略 Agent 上报端点（P1-05；1.9.55 自 assets_api 迁入 #16 模块化）。

- GET /api/v1/agent/security-policies?asset_id=（按 global > group > asset 三级优先级解析生效策略）
- POST /api/v1/agent/security-policy-result（回传策略执行结果，写 security_policy_exec_results）

均为 agent_token 认证（AUTH_EXEMPTIONS 豁免 + require_agent_request）；
设备凭据（zv1 一机一密）只能访问自身资产的数据。
"""
from __future__ import annotations

import json

from fastapi import APIRouter, HTTPException, Query, Request
from mysql.connector import Error

from auth_utils import require_agent_request
from zvplatform.db import create_connection


router = APIRouter(tags=["agent-security-policies"])


def _enforce_device_asset_binding(request: Request, asset_id: int) -> None:
    """设备凭据只能访问自身资产的数据（全局 token 在迁移窗口内不受限）"""
    from auth_utils import get_request_agent_auth
    auth_info = get_request_agent_auth(request)
    if auth_info and auth_info.get("agent_auth_type") == "device":
        if auth_info.get("agent_id") != asset_id:
            raise HTTPException(status_code=403, detail="Agent credential does not match this asset")


@router.get("/api/v1/agent/security-policies")
def agent_get_security_policies(
    request: Request,
    asset_id: int = Query(...),
):
    """Agent 拉取安全策略（按三级优先级解析：global > group > asset）"""
    require_agent_request(request)
    _enforce_device_asset_binding(request, asset_id)
    conn = create_connection()
    if not conn:
        raise HTTPException(status_code=500, detail="Database connection failed")
    cursor = conn.cursor(dictionary=True)
    try:
        from zvplatform.routers.security import ensure_security_tables
        ensure_security_tables(conn)
        # 查询 asset 的 group_id
        cursor.execute("SELECT group_id FROM assets WHERE id=%s", (asset_id,))
        row = cursor.fetchone()
        group_id = row.get("group_id") if row else None

        # 优先级：asset > group > global（数值越大优先级越高，取最高优先级的生效配置合并）
        cursor.execute("""
            SELECT sp.id, sp.policy_name, sp.policy_type, sp.priority, sp.version,
                   sp.config_json, spb.scope_type, spb.scope_id
            FROM security_policy_bindings spb
            JOIN security_policies sp ON sp.id=spb.policy_id
            WHERE spb.enabled=TRUE AND sp.enabled=TRUE AND (
                spb.scope_type='global'
                OR (spb.scope_type='asset' AND spb.scope_id=%s)
                OR (spb.scope_type='group' AND spb.scope_id=%s)
            )
        """, (asset_id, group_id))  # P1-05：排序与去重收敛到 policy_engine
        policies = []
        for r in cursor.fetchall():
            try:
                config = json.loads(r.get("config_json") or "{}")
            except Exception:
                config = {}
            policies.append({
                "id": r["id"],
                "policy_name": r["policy_name"],
                "policy_type": r["policy_type"],
                "priority": int(r["priority"] or 0),
                "version": int(r["version"] or 1),
                "scope_type": r["scope_type"],
                "config": config,
            })
        # P1-05：统一策略引擎解析生效策略
        from zvplatform.policy_engine import resolve_effective_policies
        return {"status": "success", "asset_id": asset_id,
                "policies": resolve_effective_policies(policies)}
    except Error as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        cursor.close()
        conn.close()


@router.post("/api/v1/agent/security-policy-result")
def agent_security_policy_result(data: dict, request: Request):
    """Agent 回传策略执行结果"""
    require_agent_request(request)
    _enforce_device_asset_binding(request, int(data.get("asset_id") or 0))
    conn = create_connection()
    if not conn:
        raise HTTPException(status_code=500, detail="Database connection failed")
    cursor = conn.cursor()
    try:
        from zvplatform.routers.security import ensure_security_tables
        ensure_security_tables(conn)
        policy_id = data.get("policy_id")
        asset_id = data.get("asset_id")
        status = data.get("status", "success")
        if not policy_id or not asset_id:
            raise HTTPException(status_code=422, detail="policy_id and asset_id required")
        cursor.execute("SELECT id FROM security_policies WHERE id=%s", (policy_id,))
        if not cursor.fetchone():
            raise HTTPException(status_code=404, detail="Policy not found")
        cursor.execute("""
            INSERT INTO security_policy_exec_results
                (policy_id, asset_id, scope_type, status, applied_rules, failed_rules, error_detail, executed_at, reported_at)
            VALUES (%s,%s,%s,%s,%s,%s,%s,NOW(),NOW())
        """, (
            policy_id, asset_id, data.get("scope_type", "asset"),
            status, int(data.get("applied_rules") or 0), int(data.get("failed_rules") or 0),
            data.get("error_detail"),
        ))
        conn.commit()
        return {"status": "success"}
    except HTTPException:
        raise
    except Error as e:
        conn.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        cursor.close()
        conn.close()
