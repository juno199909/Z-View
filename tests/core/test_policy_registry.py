# -*- coding: utf-8 -*-
"""统一策略引擎注册表测试（P1-05 Phase 1）：类型校验 + 心跳合并语义。"""
import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from zvplatform.services.policy_registry import (  # noqa: E402
    merge_agent_policies,
    validate_policy_config,
)
from zvplatform.services.agent_policy_service import (  # noqa: E402
    normalize_agent_policy_config,
)


def test_validate_agent_config_clean():
    clean, errors = validate_policy_config("agent", {
        "intervals": {"heartbeat": 15},
        "remote_desktop": {"allow_shell": True, "shell_timeout_seconds": 120},
    })
    assert errors == []
    assert clean == {
        "intervals": {"heartbeat": 15},
        "remote_desktop": {"allow_shell": True, "shell_timeout_seconds": 120},
    }


def test_validate_agent_config_rejects_bad_values():
    clean, errors = validate_policy_config("agent", {
        "intervals": {"heartbeat": 1},
        "remote_desktop": {"allow_shell": "maybe", "shell_timeout_seconds": 9999},
        "unknown_section": {},
    })
    assert clean == {}
    assert any("intervals.heartbeat" in e for e in errors)
    assert any("allow_shell" in e for e in errors)
    assert any("shell_timeout_seconds" in e for e in errors)
    assert any("unknown policy sections" in e for e in errors)


def test_validate_passthrough_for_legacy_types():
    config = {"rules": [{"match": "exact"}]}
    clean, errors = validate_policy_config("firewall", config)
    assert errors == []
    assert clean == config


def test_merge_agent_policies_override_wins():
    base = {
        "intervals": {"heartbeat": 30, "software": 120, "hardware": 86400},
        "remote_desktop": {
            "require_consent": True, "consent_timeout_seconds": 90,
            "allow_if_no_user": False, "disable_uac_secure_desktop": True,
            "allow_shell": False, "shell_timeout_seconds": 60,
        },
    }
    merged = merge_agent_policies(base, {
        "intervals": {"heartbeat": 15},
        "remote_desktop": {"allow_shell": True},
    })
    assert merged["intervals"]["heartbeat"] == 15
    assert merged["intervals"]["software"] == 120  # 未覆盖字段保留兜底
    assert merged["remote_desktop"]["allow_shell"] is True
    assert merged["remote_desktop"]["require_consent"] is True
    # 纯函数：不污染 base
    assert base["intervals"]["heartbeat"] == 30


def test_merge_agent_policies_no_override_returns_base():
    base = {"intervals": {"heartbeat": 30}}
    assert merge_agent_policies(base, None) is base
    assert merge_agent_policies(base, {}) is base


def test_agent_config_shared_by_console_and_registry():
    """控制台更新与统一策略注册表必须产出同一校验结果（防两套口径漂移）。"""
    payload = {"remote_desktop": {"require_consent": "yes", "allow_shell": False}}
    a = normalize_agent_policy_config(payload)
    b = validate_policy_config("agent", payload)
    assert a == b
