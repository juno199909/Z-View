# -*- coding: utf-8 -*-
"""Scoped RBAC 纯函数测试（P1：分组范围解析与规范化）。"""
import os
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from auth_utils import (  # noqa: E402
    get_user_scoped_group_ids,
    _normalize_scoped_group_ids,
    verify_access_token,
    issue_access_token,
)


def test_admin_always_unrestricted():
    assert get_user_scoped_group_ids({"role": "admin", "scoped_group_ids": [7, 8]}) is None


def test_empty_scope_means_unrestricted():
    """存量账号兼容：未配置范围 = 不限制。"""
    assert get_user_scoped_group_ids({"role": "operator"}) is None
    assert get_user_scoped_group_ids({"role": "operator", "scoped_group_ids": []}) is None
    assert get_user_scoped_group_ids({"role": "viewer", "scoped_group_ids": None}) is None


def test_scoped_user_returns_group_list():
    user = {"role": "operator", "scoped_group_ids": [7, "8", 9]}
    assert get_user_scoped_group_ids(user) == [7, 8, 9]
    assert get_user_scoped_group_ids({"role": "viewer", "scoped_group_ids": [3]}) == [3]


def test_invalid_scope_entries_filtered():
    assert _normalize_scoped_group_ids("operator", [1, "x", -2, 0, 3, 3]) == [1, 3]
    assert _normalize_scoped_group_ids("admin", [1, 2]) == []


def test_token_roundtrip_carries_scope():
    """令牌签发/校验往返：受限用户令牌携带范围，admin 范围为空。"""
    profile = issue_access_token("admin", expires_in_seconds=120)
    user = verify_access_token(profile["access_token"])
    assert user is not None
    assert user["scoped_group_ids"] is None  # admin 不限制
