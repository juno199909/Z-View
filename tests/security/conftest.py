import os
import pytest
import requests

BASE_URL = os.environ.get("ZVIEW_TEST_BASE_URL", "http://127.0.0.1:8080")
# 凭据一律通过环境变量提供，禁止硬编码进仓库（凭据已入 Git 历史需轮换，见审计报告 P0-1）
ADMIN_USER = os.environ.get("ZVIEW_TEST_USER", "admin")
ADMIN_PASS = os.environ.get("ZVIEW_TEST_PASSWORD", "")
AGENT_TOKEN = os.environ.get("ZVIEW_AGENT_TOKEN", "")
TEST_ASSET_ID = int(os.environ.get("ZVIEW_TEST_ASSET_ID", "28"))


@pytest.fixture(scope="session")
def admin_token():
    if not ADMIN_PASS:
        pytest.skip("ZVIEW_TEST_PASSWORD 未设置，跳过需要认证的 API 测试")
    r = requests.post(
        f"{BASE_URL}/api/v1/auth/login",
        json={"username": ADMIN_USER, "password": ADMIN_PASS},
        timeout=15,
    )
    assert r.status_code == 200, f"login failed: {r.status_code} {r.text}"
    return r.json()["access_token"]


@pytest.fixture(scope="session")
def auth_headers(admin_token):
    return {"Authorization": f"Bearer {admin_token}"}


@pytest.fixture(scope="session")
def agent_headers():
    return {"Authorization": f"Bearer {AGENT_TOKEN}"}


@pytest.fixture
def sec_url():
    return f"{BASE_URL}/api/v1/security"


@pytest.fixture
def agent_url():
    return f"{BASE_URL}/api/v1/agent"