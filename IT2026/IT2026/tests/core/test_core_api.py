import pytest
import requests

pytestmark = pytest.mark.core


class TestAuth:
    def test_login_wrong_password(self, base_url):
        r = requests.post(f"{base_url}/api/v1/auth/login",
                          json={"username": "admin", "password": "wrong"}, timeout=10)
        assert r.status_code == 401

    def test_login_missing_fields(self, base_url):
        r = requests.post(f"{base_url}/api/v1/auth/login", json={}, timeout=10)
        assert r.status_code == 422

    def test_me_no_token(self, base_url):
        assert requests.get(f"{base_url}/api/v1/auth/me", timeout=10).status_code == 401

    def test_me_bad_token(self, base_url):
        r = requests.get(f"{base_url}/api/v1/auth/me",
                         headers={"Authorization": "Bearer fake.token"}, timeout=10)
        assert r.status_code == 401

    def test_me_ok(self, base_url, auth_headers):
        r = requests.get(f"{base_url}/api/v1/auth/me", headers=auth_headers, timeout=10)
        assert r.status_code == 200


class TestAssets:
    def test_stats(self, base_url, auth_headers):
        assert requests.get(f"{base_url}/api/v1/assets/stats", headers=auth_headers, timeout=15).status_code == 200

    def test_list_pagination(self, base_url, auth_headers):
        r = requests.get(f"{base_url}/api/v1/assets?page=1&page_size=3", headers=auth_headers, timeout=15)
        assert r.status_code == 200 and "total" in r.json()

    def test_list_invalid_page(self, base_url, auth_headers):
        assert requests.get(f"{base_url}/api/v1/assets?page=0", headers=auth_headers, timeout=15).status_code == 422

    def test_detail_not_found(self, base_url, auth_headers):
        assert requests.get(f"{base_url}/api/v1/assets/999999", headers=auth_headers, timeout=15).status_code == 404

    def test_create_invalid_enum(self, base_url, auth_headers):
        r = requests.post(f"{base_url}/api/v1/assets",
                          json={"asset_type": "desktop", "hostname": "t", "ip_address": "192.0.2.1"},
                          headers=auth_headers, timeout=15)
        assert r.status_code == 422

    def test_create_invalid_ip(self, base_url, auth_headers):
        r = requests.post(f"{base_url}/api/v1/assets",
                          json={"asset_type": "pc", "hostname": "t", "ip_address": "999.1.1.1"},
                          headers=auth_headers, timeout=15)
        assert r.status_code == 422

    def test_crud_lifecycle(self, base_url, auth_headers):
        r = requests.post(f"{base_url}/api/v1/assets",
                          json={"asset_type": "pc", "hostname": "pytest-core", "ip_address": "192.0.2.198"},
                          headers=auth_headers, timeout=15)
        assert r.status_code == 200
        aid = r.json()["id"]
        try:
            assert requests.put(f"{base_url}/api/v1/assets/{aid}", json={"location": "T"},
                                headers=auth_headers, timeout=15).status_code == 200
            r2 = requests.post(f"{base_url}/api/v1/assets/batch-delete", json={"ids": [aid]},
                               headers=auth_headers, timeout=15)
            assert r2.status_code == 200
        finally:
            requests.post(f"{base_url}/api/v1/assets/batch-delete", json={"ids": [aid]},
                          headers=auth_headers, timeout=10)

    def test_duplicate_ip_conflict(self, base_url, auth_headers):
        r1 = requests.post(f"{base_url}/api/v1/assets",
                           json={"asset_type": "pc", "hostname": "dup-a", "ip_address": "192.0.2.197"},
                           headers=auth_headers, timeout=15)
        assert r1.status_code == 200
        aid = r1.json()["id"]
        try:
            r2 = requests.post(f"{base_url}/api/v1/assets",
                               json={"asset_type": "pc", "hostname": "dup-b", "ip_address": "192.0.2.197"},
                               headers=auth_headers, timeout=15)
            assert r2.status_code == 409
        finally:
            requests.post(f"{base_url}/api/v1/assets/batch-delete", json={"ids": [aid]},
                          headers=auth_headers, timeout=10)


class TestGroups:
    def test_list(self, base_url, auth_headers):
        assert requests.get(f"{base_url}/api/v1/groups", headers=auth_headers, timeout=15).status_code == 200

    def test_partial_update_not_found(self, base_url, auth_headers):
        r = requests.put(f"{base_url}/api/v1/groups/999999", json={"description": "x"},
                         headers=auth_headers, timeout=15)
        assert r.status_code == 404


class TestAlerts:
    def test_stats(self, base_url, auth_headers):
        assert requests.get(f"{base_url}/api/v1/alerts/stats", headers=auth_headers, timeout=15).status_code == 200

    def test_list_severity_filter(self, base_url, auth_headers):
        r = requests.get(f"{base_url}/api/v1/alerts?severity=critical&page=1&page_size=2",
                         headers=auth_headers, timeout=15)
        assert r.status_code == 200

    def test_handle_invalid_status(self, base_url, auth_headers):
        r = requests.put(f"{base_url}/api/v1/alerts/1/resolve", json={"resolution_note": "x"},
                         headers=auth_headers, timeout=15)
        assert r.status_code in (200, 404, 422)


class TestPolicyTieBreaker:
    """R 整改回归：策略解析同优先级确定性（asset>group>global → id DESC）"""

    def test_agent_policies_deterministic(self, base_url, agent_headers):
        r = requests.get(f"{base_url}/api/v1/agent/security-policies?asset_id=28",
                         headers=agent_headers, timeout=15)
        assert r.status_code == 200
        policies = r.json().get("policies") or []
        seen_types = set()
        for p in policies:
            assert p["policy_type"] not in seen_types  # 每 type 仅一条
            seen_types.add(p["policy_type"])