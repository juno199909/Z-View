# -*- coding: utf-8 -*-
"""管理端访问令牌签发/校验与 RBAC 映射回归测试。

验证点：
  1. issue_access_token → verify_access_token 往返一致（username/role）
  2. 篡改令牌 / 空令牌 / 垃圾令牌 → 校验返回 None
  3. resolve_required_permission 关键路径映射（auth:manage / remote_desktop:control 等）
  4. 角色权限模板：viewer 只读，operator 含 policies:write

运行: python tests/test_auth_tokens.py
"""

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import auth_utils  # noqa: E402

RESULTS = []


def record(name, ok, detail=""):
    RESULTS.append(bool(ok))
    print(f"[{'PASS' if ok else 'FAIL'}] {name} {detail}", flush=True)


def main():
    # ---- 1. 签发/校验往返 ----
    issued = auth_utils.issue_access_token("admin", expires_in_seconds=600)
    token = issued.get("access_token") or issued.get("token")
    record("签发返回令牌", bool(token))
    verified = auth_utils.verify_access_token(token)
    record("往返校验 username=admin",
           bool(verified) and verified.get("username") == "admin")
    record("往返校验 role=admin", bool(verified) and verified.get("role") == "admin")

    # ---- 2. 异常令牌拒绝 ----
    tampered = token[:-4] + ("aaaa" if not token.endswith("aaaa") else "bbbb")
    record("篡改令牌拒绝", auth_utils.verify_access_token(tampered) is None)
    record("空令牌拒绝", auth_utils.verify_access_token("") is None)
    record("垃圾令牌拒绝", auth_utils.verify_access_token("garbage.token.value") is None)
    record("None 令牌拒绝", auth_utils.verify_access_token(None) is None)

    # ---- 3. 关键路径权限映射 ----
    cases = [
        ("/api/v1/auth/users", "GET", "auth:manage"),
        ("/api/v1/console/agent-exit-policy", "GET", "policies:read"),
        ("/api/v1/console/agent-exit-policy", "PUT", "policies:write"),
        ("/api/v1/console/agent-credentials", "POST", "policies:write"),
        ("/api/v1/assets/1/remote-control", "POST", "remote_desktop:control"),
        ("/api/v1/assets/1/command", "POST", "automation:execute"),
    ]
    for path, method, expected in cases:
        got = auth_utils.resolve_required_permission(path, method)
        record(f"映射 {method} {path} → {expected}", got == expected)

    # ---- 4. 角色权限模板 ----
    viewer = auth_utils.get_role_permissions("viewer")
    operator = auth_utils.get_role_permissions("operator")
    record("viewer 无 policies:write", "policies:write" not in viewer)
    record("viewer 有 assets:read", "assets:read" in viewer)
    record("operator 有 policies:write", "policies:write" in operator)
    record("operator 无 auth:manage", "auth:manage" not in operator)
    record("未知角色回退 viewer",
          auth_utils.get_role_permissions("hacker") == auth_utils.get_role_permissions("viewer"))

    passed, total = sum(RESULTS), len(RESULTS)
    print(f"\n{passed}/{total} passed", flush=True)
    return 0 if passed == total else 1


if __name__ == "__main__":
    raise SystemExit(main())
