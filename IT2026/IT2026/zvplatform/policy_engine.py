# -*- coding: utf-8 -*-
"""统一策略引擎（P1-05）。

解析规则（与历史行为完全一致，抽象为可测试纯函数）：
  1. 仅 enabled 策略与绑定参与（由调用方 SQL WHERE 保证）
  2. 排序：priority DESC → scope 权重 DESC（asset 3 > group 2 > global 1）→ id DESC
  3. 每个 policy_type 保留排序后的第一条作为生效策略
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

SCOPE_WEIGHTS = {"asset": 3, "group": 2, "global": 1}


def _sort_key(policy: Dict[str, Any]):
    return (
        int(policy.get("priority") or 0),
        SCOPE_WEIGHTS.get(str(policy.get("scope_type") or "global"), 0),
        int(policy.get("id") or 0),
    )


def sort_candidates(policies: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """按 priority DESC → scope 权重 DESC → id DESC 排序（纯函数，不改输入）。"""
    return sorted(policies, key=_sort_key, reverse=True)


def resolve_effective_policies(policies: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """排序候选策略并按 policy_type 去重，返回生效策略列表（保持首次出现顺序不保证，按排序优先级）。"""
    seen: Dict[str, Dict[str, Any]] = {}
    for policy in sort_candidates(policies):
        policy_type = policy.get("policy_type")
        if policy_type not in seen:
            seen[policy_type] = policy
    return list(seen.values())


def select_for_type(policies: List[Dict[str, Any]], policy_type: str) -> Optional[Dict[str, Any]]:
    """取指定 policy_type 的生效策略（无则 None）。"""
    for policy in resolve_effective_policies(policies):
        if policy.get("policy_type") == policy_type:
            return policy
    return None
