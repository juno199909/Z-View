# -*- coding: utf-8 -*-
"""策略引擎单元测试（P1-05，无需运行中的服务器）"""
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from zvplatform.policy_engine import (  # noqa: E402
    resolve_effective_policies,
    select_for_type,
    sort_candidates,
)


def _p(pid, ptype, priority=0, scope="global"):
    return {"id": pid, "policy_type": ptype, "priority": priority, "scope_type": scope, "config": {}}


def test_highest_priority_wins():
    policies = [_p(1, "usb", 0), _p(2, "usb", 5)]
    assert select_for_type(policies, "usb")["id"] == 2


def test_scope_tiebreak_asset_over_group_over_global():
    policies = [_p(1, "usb", 5, "global"), _p(2, "usb", 5, "group"), _p(3, "usb", 5, "asset")]
    assert select_for_type(policies, "usb")["id"] == 3


def test_id_desc_final_tiebreak():
    policies = [_p(7, "usb", 5, "asset"), _p(9, "usb", 5, "asset")]
    assert select_for_type(policies, "usb")["id"] == 9


def test_deterministic_tie_breaker():
    # 同 priority/scope/id 时结果与输入顺序无关
    a = [_p(1, "usb", 5), _p(2, "usb", 5)]
    b = [_p(2, "usb", 5), _p(1, "usb", 5)]
    assert select_for_type(a, "usb")["id"] == select_for_type(b, "usb")["id"]


def test_types_resolved_independently():
    policies = [_p(1, "usb", 10), _p(2, "firewall", 1)]
    eff = resolve_effective_policies(policies)
    assert len(eff) == 2
    assert select_for_type(policies, "usb")["id"] == 1
    assert select_for_type(policies, "firewall")["id"] == 2


def test_empty():
    assert resolve_effective_policies([]) == []
    assert select_for_type([], "usb") is None


def test_sort_does_not_mutate_input():
    policies = [_p(1, "usb", 0), _p(2, "usb", 5)]
    snapshot = [dict(p) for p in policies]
    sort_candidates(policies)
    assert [p["id"] for p in policies] == [p["id"] for p in snapshot]
