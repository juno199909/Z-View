# -*- coding: utf-8 -*-
"""Agent 退出密码策略与权限映射回归测试（1.9.55）。

验证点：
  1. /console/agent-exit-policy 的 RBAC 映射（GET→policies:read，写→policies:write）
  2. 管理端启用/停用：密码长度校验、sha256 落库、GET 状态回读
  3. Agent 校验端点：未设密码放行、错密码拒绝、对密码放行、
     空密码拒绝、DB 不可用 fail-closed
  4. 全部使用 stub 连接，不触碰真实 system_config

运行: python tests/test_agent_exit_policy.py
"""

import sys
import hashlib
from pathlib import Path
from types import SimpleNamespace

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from fastapi import HTTPException  # noqa: E402

import zvplatform.routers.agent_exit_policy as ep  # noqa: E402
from auth_utils import resolve_required_permission  # noqa: E402

RESULTS = []


def record(name, ok, detail=""):
    RESULTS.append(bool(ok))
    print(f"[{'PASS' if ok else 'FAIL'}] {name} {detail}", flush=True)


class FakeCursor:
    def __init__(self, rows=None):
        self.rows = list(rows or [])
        self.executed = []

    def execute(self, sql, params=None):
        self.executed.append((sql, params))

    def fetchone(self):
        return self.rows[0] if self.rows else None

    def close(self):
        pass


class FakeConn:
    def __init__(self, rows=None):
        self._cursor = FakeCursor(rows)
        self.committed = False
        self.closed = False

    def cursor(self):
        return self._cursor

    def commit(self):
        self.committed = True

    def close(self):
        self.closed = True


def make_request(method, role="admin", permissions=("*",)):
    return SimpleNamespace(
        state=SimpleNamespace(auth_user={"role": role, "permissions": list(permissions)}),
        url=SimpleNamespace(path="/api/v1/console/agent-exit-policy"),
        method=method,
    )


def with_db(rows=None, conn=None):
    fake = conn or FakeConn(rows)
    ep.create_connection = lambda: fake
    ep.require_agent_request = lambda request: {"auth_type": "agent"}
    ep.require_request_permission = lambda user, path, method: None
    return fake


def sha(value):
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def main():
    # ---- 1. RBAC 映射 ----
    record("rbac GET 映射 policies:read",
           resolve_required_permission("/api/v1/console/agent-exit-policy", "GET") == "policies:read")
    record("rbac PUT 映射 policies:write",
           resolve_required_permission("/api/v1/console/agent-exit-policy", "PUT") == "policies:write")
    viewer_ok = None
    try:
        ep.get_agent_exit_policy(make_request("GET", role="viewer", permissions=("policies:read",)))
        viewer_ok = True
    except HTTPException as exc:
        viewer_ok = exc.status_code != 403
    record("rbac viewer 可读", viewer_ok)
    viewer_write_denied = False
    try:
        ep.update_agent_exit_policy(make_request("PUT", role="viewer", permissions=("policies:read",)),
                                    {"enabled": True, "password": "abcd1234"})
    except HTTPException as exc:
        viewer_write_denied = exc.status_code == 403
    record("rbac viewer 写入被拒(403)", viewer_write_denied)

    # ---- 2. 管理端启用/停用 ----
    fake = with_db()
    short_rejected = False
    try:
        ep.update_agent_exit_policy(make_request("PUT"), {"enabled": True, "password": "abc"})
    except HTTPException as exc:
        short_rejected = exc.status_code == 400
    record("启用时长<4 拒绝(400)", short_rejected)
    record("启用时长<4 未写库", len(fake._cursor.executed) == 0)

    fake = with_db()
    result = ep.update_agent_exit_policy(make_request("PUT"), {"enabled": True, "password": "test1234"})
    record("启用成功返回 enabled=True", result.get("enabled") is True)
    record("启用已 commit", fake.committed)
    insert_params = fake._cursor.executed[0][1] if fake._cursor.executed else None
    record("落库为 sha256 哈希", bool(insert_params) and insert_params[1] == sha("test1234"))

    fake = with_db()
    result = ep.update_agent_exit_policy(make_request("PUT"), {"enabled": False})
    record("停用成功返回 enabled=False", result.get("enabled") is False)
    delete_sql = fake._cursor.executed[0][0] if fake._cursor.executed else ""
    record("停用执行 DELETE", "DELETE" in delete_sql.upper())

    # ---- 3. Agent 校验端点 ----
    ep.require_agent_request = lambda request: {"auth_type": "agent"}
    ep.require_request_permission = lambda user, path, method: None

    fake = with_db(rows=[])  # 未设密码
    stub_agent = SimpleNamespace(state=SimpleNamespace())
    result = ep.verify_agent_exit_password(stub_agent, {"password": ""})
    record("未设密码 required=False 放行", result == {"required": False, "valid": True})

    fake = with_db(rows=[(sha("realpass"),)])  # 真实 mysql cursor 返回 tuple 行
    result = ep.verify_agent_exit_password(stub_agent, {"password": "wrong"})
    record("错密码 required+拒绝", result == {"required": True, "valid": False})
    result = ep.verify_agent_exit_password(stub_agent, {"password": ""})
    record("空密码 required+拒绝", result == {"required": True, "valid": False})
    result = ep.verify_agent_exit_password(stub_agent, {"password": "realpass"})
    record("对密码 required+放行", result == {"required": True, "valid": True})

    ep.create_connection = lambda: None  # DB 不可用
    result = ep.verify_agent_exit_password(stub_agent, {"password": "whatever"})
    record("DB 不可用 fail-closed", result.get("required") is True and result.get("valid") is False
           and result.get("server_error") is True)

    # ---- 4. 管理端 GET 状态 ----
    fake = with_db(rows=[(sha("x"),)])
    record("GET 已启用回读", ep.get_agent_exit_policy(make_request("GET")).get("enabled") is True)
    record("GET 不回传哈希", "config_value" not in str(ep.get_agent_exit_policy(make_request("GET"))))

    passed, total = sum(RESULTS), len(RESULTS)
    print(f"\n{passed}/{total} passed", flush=True)
    return 0 if passed == total else 1


if __name__ == "__main__":
    raise SystemExit(main())
