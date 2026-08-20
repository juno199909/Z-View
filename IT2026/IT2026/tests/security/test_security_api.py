import pytest
import requests

pytestmark = pytest.mark.security


class TestSecurityOverview:
    def test_overview(self, sec_url, auth_headers):
        r = requests.get(f"{sec_url}/overview", headers=auth_headers, timeout=15)
        assert r.status_code == 200
        body = r.json()
        assert "terminals" in body and "events" in body and "policies" in body

    def test_overview_no_auth(self, sec_url):
        r = requests.get(f"{sec_url}/overview", timeout=10)
        assert r.status_code == 401


class TestSecurityTerminals:
    def test_list(self, sec_url, auth_headers):
        r = requests.get(f"{sec_url}/terminals?page=1&page_size=5", headers=auth_headers, timeout=15)
        assert r.status_code == 200
        assert "data" in r.json() and "total" in r.json()

    def test_list_keyword(self, sec_url, auth_headers):
        r = requests.get(f"{sec_url}/terminals?page=1&page_size=5&keyword=XXH", headers=auth_headers, timeout=15)
        assert r.status_code == 200

    def test_detail(self, sec_url, auth_headers):
        r = requests.get(f"{sec_url}/terminals/28", headers=auth_headers, timeout=15)
        assert r.status_code == 200
        assert "asset" in r.json()

    def test_detail_not_found(self, sec_url, auth_headers):
        r = requests.get(f"{sec_url}/terminals/999999", headers=auth_headers, timeout=15)
        assert r.status_code == 404

    def test_list_invalid_page(self, sec_url, auth_headers):
        r = requests.get(f"{sec_url}/terminals?page=0", headers=auth_headers, timeout=15)
        assert r.status_code == 422


class TestSecurityEvents:
    def test_list(self, sec_url, auth_headers):
        r = requests.get(f"{sec_url}/events?page=1&page_size=5", headers=auth_headers, timeout=15)
        assert r.status_code == 200

    def test_stats(self, sec_url, auth_headers):
        r = requests.get(f"{sec_url}/events/stats", headers=auth_headers, timeout=15)
        assert r.status_code == 200
        assert "total" in r.json()

    def test_filter_type(self, sec_url, auth_headers):
        r = requests.get(f"{sec_url}/events?event_type=usb&page=1&page_size=5", headers=auth_headers, timeout=15)
        assert r.status_code == 200

    def test_handle_invalid_status(self, sec_url, auth_headers):
        r = requests.put(f"{sec_url}/events/1/handle", json={"status": "invalidstatus"}, headers=auth_headers, timeout=15)
        assert r.status_code == 422

    def test_handle_not_found(self, sec_url, auth_headers):
        r = requests.put(f"{sec_url}/events/999999/handle", json={"status": "resolved"}, headers=auth_headers, timeout=15)
        assert r.status_code == 404

    def test_batch_handle_empty(self, sec_url, auth_headers):
        r = requests.post(f"{sec_url}/events/batch-handle", json={"event_ids": [], "status": "resolved"}, headers=auth_headers, timeout=15)
        assert r.status_code == 400


class TestSecurityPolicies:
    def test_list(self, sec_url, auth_headers):
        r = requests.get(f"{sec_url}/policies?page=1&page_size=5", headers=auth_headers, timeout=15)
        assert r.status_code == 200

    def test_create_invalid_type(self, sec_url, auth_headers):
        r = requests.post(f"{sec_url}/policies", json={"policy_name": "x", "policy_type": "invalid", "config_json": "{}"}, headers=auth_headers, timeout=15)
        assert r.status_code == 422

    def test_create_invalid_json(self, sec_url, auth_headers):
        r = requests.post(f"{sec_url}/policies", json={"policy_name": "x", "policy_type": "usb", "config_json": "notjson"}, headers=auth_headers, timeout=15)
        assert r.status_code == 422

    def test_crud_lifecycle(self, sec_url, auth_headers):
        # create
        r = requests.post(f"{sec_url}/policies", json={"policy_name": "pytest-pol", "policy_type": "firewall", "priority": 1, "config_json": '{"rules":[]}'}, headers=auth_headers, timeout=15)
        assert r.status_code == 200
        pid = r.json()["id"]
        try:
            # detail
            assert requests.get(f"{sec_url}/policies/{pid}", headers=auth_headers, timeout=15).status_code == 200
            # disable
            assert requests.put(f"{sec_url}/policies/{pid}", json={"enabled": False}, headers=auth_headers, timeout=15).status_code == 200
            # update config -> v2
            r2 = requests.put(f"{sec_url}/policies/{pid}", json={"config_json": '{"rules":[{"name":"r1"}]}'}, headers=auth_headers, timeout=15)
            assert r2.status_code == 200
            # versions
            rv = requests.get(f"{sec_url}/policies/{pid}/versions", headers=auth_headers, timeout=15)
            assert rv.status_code == 200 and rv.json()["total"] >= 2
            # rollback
            rb = requests.post(f"{sec_url}/policies/{pid}/rollback", json={"version": 1}, headers=auth_headers, timeout=15)
            assert rb.status_code == 200
            # bind
            assert requests.post(f"{sec_url}/policies/{pid}/bind", json={"scope_type": "global"}, headers=auth_headers, timeout=15).status_code == 200
            # exec-results
            assert requests.get(f"{sec_url}/policies/{pid}/exec-results", headers=auth_headers, timeout=15).status_code == 200
        finally:
            requests.delete(f"{sec_url}/policies/{pid}", headers=auth_headers, timeout=10)

    def test_delete_not_found(self, sec_url, auth_headers):
        assert requests.delete(f"{sec_url}/policies/999999", headers=auth_headers, timeout=15).status_code == 404


class TestFirewall:
    def test_rules_list(self, sec_url, auth_headers):
        r = requests.get(f"{sec_url}/firewall/rules", headers=auth_headers, timeout=15)
        assert r.status_code == 200

    def test_apply_empty_rules(self, sec_url, auth_headers):
        r = requests.post(f"{sec_url}/firewall/apply", json={"scope_type": "asset", "asset_ids": [28], "rules": []}, headers=auth_headers, timeout=15)
        assert r.status_code == 422

    def test_apply_rule_no_name(self, sec_url, auth_headers):
        r = requests.post(f"{sec_url}/firewall/apply", json={"scope_type": "asset", "asset_ids": [28], "rules": [{"name": "", "direction": "in", "action": "block", "protocol": "TCP"}]}, headers=auth_headers, timeout=15)
        assert r.status_code == 422


class TestUsb:
    def test_devices(self, sec_url, auth_headers):
        assert requests.get(f"{sec_url}/usb/devices?page=1&page_size=5", headers=auth_headers, timeout=15).status_code == 200

    def test_events(self, sec_url, auth_headers):
        assert requests.get(f"{sec_url}/usb/events?page=1&page_size=5", headers=auth_headers, timeout=15).status_code == 200

    def test_invalid_action(self, sec_url, auth_headers):
        r = requests.post(f"{sec_url}/usb/policy", json={"scope_type": "asset", "asset_ids": [28], "action": "invalid"}, headers=auth_headers, timeout=15)
        assert r.status_code == 422


class TestAppControl:
    def test_logs(self, sec_url, auth_headers):
        assert requests.get(f"{sec_url}/app-control/logs?page=1&page_size=5", headers=auth_headers, timeout=15).status_code == 200

    def test_empty_lists(self, sec_url, auth_headers):
        r = requests.post(f"{sec_url}/app-control/policy", json={"scope_type": "asset", "asset_ids": [28], "blacklist": [], "whitelist": []}, headers=auth_headers, timeout=15)
        assert r.status_code == 422


class TestFileProtect:
    def test_baselines(self, sec_url, auth_headers):
        assert requests.get(f"{sec_url}/file-protect/baselines?page=1&page_size=5", headers=auth_headers, timeout=15).status_code == 200

    def test_empty_dirs(self, sec_url, auth_headers):
        r = requests.post(f"{sec_url}/file-protect/policy", json={"scope_type": "asset", "asset_ids": [28], "protected_dirs": []}, headers=auth_headers, timeout=15)
        assert r.status_code == 422


class TestBehavior:
    def test_events(self, sec_url, auth_headers):
        assert requests.get(f"{sec_url}/behavior/events?page=1&page_size=5", headers=auth_headers, timeout=15).status_code == 200


class TestRemoteOps:
    def test_scan_not_found(self, sec_url, auth_headers):
        assert requests.post(f"{sec_url}/remote/scan/999999", headers=auth_headers, timeout=15).status_code == 404

    def test_kill_no_target(self, sec_url, auth_headers):
        r = requests.post(f"{sec_url}/remote/kill-process/28", json={}, headers=auth_headers, timeout=15)
        assert r.status_code == 422


class TestAgentReport:
    def test_security_events(self, agent_url, agent_headers):
        r = requests.post(f"{agent_url}/security-events", json={"asset_id": 28, "events": []}, headers=agent_headers, timeout=15)
        assert r.status_code == 200

    def test_security_events_wrong_token(self, agent_url):
        r = requests.post(f"{agent_url}/security-events", json={"asset_id": 28, "events": []}, headers={"Authorization": "Bearer wrong"}, timeout=15)
        assert r.status_code == 401

    def test_security_policies(self, agent_url, agent_headers):
        r = requests.get(f"{agent_url}/security-policies?asset_id=28", headers=agent_headers, timeout=15)
        assert r.status_code == 200

    def test_security_policy_result_not_found(self, agent_url, agent_headers):
        r = requests.post(f"{agent_url}/security-policy-result", json={"policy_id": 999, "asset_id": 28, "status": "success"}, headers=agent_headers, timeout=15)
        assert r.status_code == 404