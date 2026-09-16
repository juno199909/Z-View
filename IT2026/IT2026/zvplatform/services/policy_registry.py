# -*- coding: utf-8 -*-
"""统一策略引擎——类型注册表与按资产解析（P1-05 Phase 1）。

模型：security_policies（策略）+ security_policy_bindings（global/group/asset 绑定）
+ security_policy_versions（版本/回滚），解析规则收敛在 zvplatform/policy_engine 纯函数。

Phase 1 类型：
- firewall / usb：既有安全策略（security_api 下发链路不变）
- agent：终端 Agent 策略（intervals + remote_desktop），心跳下发时与 agent_policies
  全局兜底合并——asset > group > global 绑定的生效配置覆盖同名分节字段
Phase 2（未开放写入）：software（软件管控规则模型并入）
"""
from __future__ import annotations

import json
from typing import Any, Dict, List, Optional, Tuple

from console_utils import safe_console_print

from zvplatform.policy_engine import resolve_effective_policies, select_for_type

WRITABLE_POLICY_TYPES = ("firewall", "usb", "agent")
POLICY_TYPE_LABELS = {
    "firewall": "防火墙",
    "usb": "USB 管控",
    "agent": "终端 Agent",
    "software": "软件管控（Phase 2）",
}


def ensure_policy_types(conn) -> None:
    """幂等扩展 security_policies.policy_type ENUM（追加 'agent'/'software'）。

    MySQL ENUM 追加值是安全的在线 DDL；仅在缺失时执行，避免每次请求 ALTER。
    """
    cursor = conn.cursor()
    try:
        cursor.execute(
            "SELECT COLUMN_TYPE FROM INFORMATION_SCHEMA.COLUMNS "
            "WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='security_policies' "
            "AND COLUMN_NAME='policy_type'"
        )
        row = cursor.fetchone()
        column_type = str(row[0] if row else "")
        missing = [t for t in ("agent", "software") if f"'{t}'" not in column_type]
        if not missing:
            return
        wanted = ["firewall", "usb", "agent", "software"]
        joined = ",".join(f"'{t}'" for t in wanted)
        cursor.execute(
            f"ALTER TABLE security_policies MODIFY COLUMN policy_type ENUM({joined}) NOT NULL"
        )
        conn.commit()
        safe_console_print(f"[PolicyRegistry] policy_type ENUM extended: +{missing}")
    finally:
        cursor.close()


def validate_policy_config(policy_type: str, config: dict) -> Tuple[dict, list]:
    """按类型校验 config_json，返回 (clean, errors)。未知类型透传（保持 firewall/usb 现状）。"""
    if policy_type == "agent":
        from zvplatform.services.agent_policy_service import normalize_agent_policy_config

        return normalize_agent_policy_config(config)
    return config, []


def _fetch_asset_scope(conn, asset_id: int) -> Tuple[Optional[int], None]:
    cursor = conn.cursor(dictionary=True)
    try:
        cursor.execute("SELECT group_id FROM assets WHERE id=%s", (asset_id,))
        row = cursor.fetchone()
        return (row or {}).get("group_id"), None
    finally:
        cursor.close()


def resolve_unified_policies(conn, asset_id: int) -> List[Dict[str, Any]]:
    """按统一引擎解析某终端的全部生效策略（asset > group > global，priority 参与）。"""
    from security_api import ensure_security_tables

    ensure_security_tables(conn)
    ensure_policy_types(conn)
    group_id, _ = _fetch_asset_scope(conn, asset_id)

    cursor = conn.cursor(dictionary=True)
    try:
        cursor.execute(
            """
            SELECT sp.id, sp.policy_name, sp.policy_type, sp.priority, sp.version,
                   sp.config_json, spb.scope_type, spb.scope_id
            FROM security_policy_bindings spb
            JOIN security_policies sp ON sp.id=spb.policy_id
            WHERE spb.enabled=TRUE AND sp.enabled=TRUE AND (
                spb.scope_type='global'
                OR (spb.scope_type='asset' AND spb.scope_id=%s)
                OR (spb.scope_type='group' AND spb.scope_id=%s)
            )
            """,
            (asset_id, group_id),
        )
        rows = cursor.fetchall()
    finally:
        cursor.close()

    candidates = []
    for r in rows:
        try:
            config = json.loads(r.get("config_json") or "{}")
        except (TypeError, ValueError):
            config = {}
        candidates.append({**r, "config": config})
    return resolve_effective_policies(candidates)


def resolve_agent_override(conn, asset_id: int) -> Optional[dict]:
    """取该终端生效的 agent 类型统一策略 config（无则 None）。"""
    try:
        effective = resolve_unified_policies(conn, asset_id)
    except Exception as exc:
        safe_console_print(f"[PolicyRegistry] resolve agent override failed: {exc}")
        return None
    policy = select_for_type(effective, "agent")
    if not policy:
        return None
    clean, errors = validate_policy_config("agent", policy.get("config") or {})
    if errors:
        safe_console_print(
            f"[PolicyRegistry] agent policy {policy.get('id')} config invalid: {errors}"
        )
        return None
    return clean or None


def merge_agent_policies(base: dict, override: Optional[dict]) -> dict:
    """分节浅合并：override 字段覆盖 base 同名字段（纯函数）。"""
    if not override:
        return base
    merged = json.loads(json.dumps(base))
    for section, values in override.items():
        if isinstance(values, dict) and isinstance(merged.get(section), dict):
            merged[section] = {**merged[section], **values}
        else:
            merged[section] = values
    return merged


def build_effective_agent_policies(conn, asset_id: int) -> dict:
    """心跳下发的 agent 策略：agent_policies 全局兜底 + 统一引擎 asset 级覆盖合并。

    合并语义：分节（intervals/remote_desktop）浅合并，覆盖字段优先；
    任一环节失败都回退纯全局兜底，绝不阻断心跳。
    """
    from zvplatform.services.agent_policy_service import load_agent_policies

    base = load_agent_policies()
    override = resolve_agent_override(conn, asset_id)
    return merge_agent_policies(base, override)
