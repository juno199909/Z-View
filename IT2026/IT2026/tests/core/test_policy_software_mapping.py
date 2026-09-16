# -*- coding: utf-8 -*-
"""软件策略统一映射与排序测试（P1-05 Phase 2）。"""
import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from zvplatform.policy_engine import sort_candidates  # noqa: E402
from zvplatform.services.policy_registry import (  # noqa: E402
    map_software_policy_to_unified,
    software_policy_applies,
)


def _policy(pid, subtype, priority=0, target_type="all", target_ids=None):
    return {
        "id": pid, "policy_name": f"p{pid}", "policy_type": subtype,
        "enabled": True, "priority": priority,
        "target_type": target_type, "target_ids": target_ids,
    }


def test_map_target_type_to_scope():
    assert map_software_policy_to_unified(_policy(1, "blacklist"))["scope_type"] == "global"
    assert map_software_policy_to_unified(
        _policy(2, "whitelist", target_type="group", target_ids=[7])
    )["scope_type"] == "group"
    assert map_software_policy_to_unified(
        _policy(3, "force_install", target_type="asset", target_ids=[9])
    )["scope_type"] == "asset"


def test_applies_scope_matching():
    group_policy = map_software_policy_to_unified(_policy(2, "whitelist", target_type="group", target_ids=[7]))
    assert software_policy_applies({**group_policy, "target_id": 7}, group_id=7, asset_id=100)
    assert not software_policy_applies({**group_policy, "target_id": 7}, group_id=8, asset_id=100)
    asset_policy = map_software_policy_to_unified(_policy(3, "blacklist", target_type="asset", target_ids=[100]))
    assert software_policy_applies({**asset_policy, "target_id": 100}, group_id=7, asset_id=100)
    assert not software_policy_applies({**asset_policy, "target_id": 101}, group_id=7, asset_id=100)


def test_sort_scope_tiebreak_same_priority():
    """同优先级时终端定向 > 分组定向 > 全局（统一引擎 tiebreak 生效）。"""
    rows = [
        {**_policy(1, "blacklist", priority=10), "scope_type": "global"},
        {**_policy(2, "blacklist", priority=10), "scope_type": "group"},
        {**_policy(3, "blacklist", priority=10), "scope_type": "asset"},
    ]
    ordered = sort_candidates(rows)
    assert [r["id"] for r in ordered] == [3, 2, 1]
