# -*- coding: utf-8 -*-
"""资产 CRUD 端点回归测试（全离线桩，不触真实 DB）。

覆盖：列表结构/日期格式化、单资产 custom_fields 解析、创建（INSERT 参数含
custom_fields + 变更历史记录）、更新（custom_fields 校验与写入、变更 diff）、
非法入参拒绝。

运行: python tests/test_assets_crud.py
"""

import datetime
import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import zvplatform.routers.assets as am  # noqa: E402

RESULTS = []


def record(name, ok, detail=""):
    RESULTS.append(bool(ok))
    print(f"[{'PASS' if ok else 'FAIL'}] {name} {detail}", flush=True)


class StubCursor:
    """脚本化游标：按 execute 顺序消费结果脚本。"""

    def __init__(self, script):
        self.script = list(script)
        self.executed = []
        self.lastrowid = 4321
        self.debug_tag = ""

    def execute(self, sql, params=None):
        self.executed.append((" ".join(sql.split())[:90], params))
        item = self.script.pop(0) if self.script else {"rows": []}
        self._current = item
        if os.environ.get("ZV_TEST_DEBUG"):
            print(f"[cursor {self.debug_tag}] sql={self.executed[-1][0][:50]!r} "
                  f"→ {'row' if 'row' in item else ('rows×' + str(len(item.get('rows', []))))} 剩余脚本={len(self.script)}",
                  flush=True)

    def fetchone(self):
        if "row" in self._current:
            return self._current["row"]
        if "rows" in self._current:
            return self._current["rows"][0] if self._current["rows"] else None
        return None

    def fetchall(self):
        return self._current.get("rows", []) or []

    def close(self):
        pass


class StubConn:
    def __init__(self, script):
        self._cursor = StubCursor(script)
        self.committed = False
        self.closed = False

    def cursor(self, dictionary=False, **kwargs):
        return self._cursor

    def commit(self):
        self.committed = True

    def close(self):
        self.closed = True


def make_request(method="GET"):
    return SimpleNamespace(
        state=SimpleNamespace(auth_user={"username": "tester", "role": "admin"}),
        url=SimpleNamespace(path="/api/v1/assets"),
        method=method,
        headers={},
    )


def install_stubs(cursor_script, fetch_rows=None, changes=None):
    conn = StubConn(cursor_script)
    am.get_db_connection = lambda: conn
    am.get_request_username = lambda request, fallback="console": "tester"
    am.get_user_scoped_group_ids = lambda user: None
    fetch_calls = []
    fetch_queue = list(fetch_rows or [])
    am.fetch_asset_row = lambda cursor, asset_id, **kw: (
        fetch_calls.append(asset_id), fetch_queue.pop(0) if fetch_queue else {})[1]
    recorded = []
    am.record_asset_changes = lambda cursor, asset_id, before, after, **kw: (
        recorded.append({"asset_id": asset_id, "before": before, "after": after, "kw": kw}))
    am.bind_helpers(
        get_asset_agent_target=lambda cursor, asset_id: {"id": asset_id, "hostname": "h",
                                                          "ip_address": "1.2.3.4", "status": "online",
                                                          "agent_install_status": "installed"},
        proxy_agent_json_request=lambda *a, **k: {"success": True},
        record_system_activity_log=lambda payload: 1,
        ensure_asset_changes_table=lambda conn: None,
        AGENT_CONTROL_PORT=9001,
    )
    return conn, fetch_calls, recorded


NOW = datetime.datetime.now()


def main():
    # ---- 1. 列表：结构/分页/日期格式化/实时状态 ----
    rows = [{
        "id": 1, "hostname": "host-a", "ip_address": "10.0.0.1", "mac_address": None,
        "asset_type": "pc", "status": "unknown", "agent_install_status": "installed",
        "agent_version": "1.9.55", "manufacturer": None, "model": None,
        "os_type": "windows", "os_version": "11", "cpu_cores": 8, "memory_mb": 16384,
        "disk_gb": 512, "location": None, "owner": None, "group_id": None,
        "last_seen": NOW, "created_at": NOW, "updated_at": NOW,
        "heartbeat_time": NOW, "real_status": "online", "group_name": None,
        "cpu_usage": 12.5, "memory_usage": 40.0, "disk_usage": 55.0, "logged_users": 1,
    }]
    conn, _, _ = install_stubs([{"row": {"total": 1}}, {"rows": rows}])
    result = am.get_assets(make_request(), page=1, page_size=20)
    record("列表返回 total/data/page", result["total"] == 1 and len(result["data"]) == 1
           and result["page"] == 1 and result["page_size"] == 20)
    row = result["data"][0]
    record("列表 status 采用实时计算", row["status"] == "online")
    record("列表日期格式化为字符串", isinstance(row["last_seen"], str) and "-" in row["last_seen"])

    # ---- 2. 单资产：custom_fields JSON 字符串解析为对象 ----
    conn, _, _ = install_stubs([{"row": {
        "id": 1, "hostname": "host-a", "status": "online", "custom_fields": '{"机柜": "B12"}',
        "last_seen": NOW, "created_at": NOW, "updated_at": NOW, "real_status": "online",
    }}])
    asset = am.get_asset(1)
    record("单资产 custom_fields 解析为对象", asset["custom_fields"] == {"机柜": "B12"})

    # ---- 3. 创建：INSERT 参数含 custom_fields + 变更历史记录 ----
    conn, _, recorded = install_stubs([{"row": None}])  # IP 唯一性检查
    payload = am.Asset(hostname="new-host", ip_address="10.0.0.9",
                       custom_fields={"机柜": "A01", "维保": "张工"})
    result = am.create_asset(payload, make_request("POST"))
    record("创建返回 id", result.get("id") == 4321)
    insert_params = conn._cursor.executed[1][1] if len(conn._cursor.executed) > 1 else None
    record("INSERT 参数含 custom_fields JSON", bool(insert_params)
           and json.loads(insert_params[-1]) == {"机柜": "A01", "维保": "张工"})
    record("创建写变更历史(change_type=create)", recorded and recorded[0]["kw"].get("change_type") == "create")

    # ---- 4. 创建：非法 asset_type 拒绝 ----
    import fastapi
    rejected = False
    try:
        am.create_asset(am.Asset(hostname="x", asset_type="alien"), make_request("POST"))
    except fastapi.HTTPException as exc:
        rejected = exc.status_code == 422
    record("非法 asset_type 422", rejected)

    # ---- 5. 更新：custom_fields dict 写入 + diff 记录 ----
    before = {"id": 1, "hostname": "host-a", "notes": None, "custom_fields": None}
    after = {**before, "custom_fields": {"位置": "机柜A"}}
    conn, fetch_calls, recorded = install_stubs([{"row": {"id": 1}}],
                                                fetch_rows=[before, after])
    result = am.update_asset(1, {"custom_fields": {"位置": "机柜A"}}, make_request("PUT"))
    record("更新返回成功消息", result.get("message") == "Asset updated successfully")
    update_sql, update_params = conn._cursor.executed[1]
    record("UPDATE 语句含 custom_fields", "custom_fields = %s" in update_sql)
    record("UPDATE 参数为 JSON 对象", json.loads(update_params[0]) == {"位置": "机柜A"})
    record("更新写变更 diff(before/after)", recorded and recorded[0]["before"] == before
           and recorded[0]["after"]["custom_fields"] == {"位置": "机柜A"})

    # ---- 6. 更新：custom_fields 非法值拒绝 ----
    conn, _, _ = install_stubs([{"row": {"id": 1}}], fetch_rows=[{}, {}])
    bad_json = False
    try:
        am.update_asset(1, {"custom_fields": "{not-json"}, make_request("PUT"))
    except fastapi.HTTPException as exc:
        bad_json = exc.status_code == 422
    record("custom_fields 非法 JSON 字符串 422", bad_json)
    conn, _, _ = install_stubs([{"row": {"id": 1}}], fetch_rows=[{}, {}])
    not_dict = False
    diag = ""
    try:
        r = am.update_asset(1, {"custom_fields": 42}, make_request("PUT"))
        diag = f"returned {r}"
    except fastapi.HTTPException as exc:
        not_dict = exc.status_code == 422
        import traceback as _tb
        diag = f"HTTPException {exc.status_code} {exc.detail}\n" + _tb.format_exc()
    except Exception as exc:
        import traceback as _tb
        diag = f"{type(exc).__name__}: {exc}\n" + _tb.format_exc()
    record("custom_fields 非 dict 422", not_dict, diag)

    # ---- 7. 更新：空 body 400 ----
    conn, _, _ = install_stubs([{"row": {"id": 1}}], fetch_rows=[{}, {}])
    empty_400 = False
    try:
        am.update_asset(1, {}, make_request("PUT"))
    except fastapi.HTTPException as exc:
        empty_400 = exc.status_code == 400
    record("空更新体 400", empty_400)

    passed, total = sum(RESULTS), len(RESULTS)
    print(f"\n{passed}/{total} passed", flush=True)
    return 0 if passed == total else 1


if __name__ == "__main__":
    raise SystemExit(main())
