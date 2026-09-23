# -*- coding: utf-8 -*-
"""软件安装记录查询端点回归测试（全离线桩，不触真实 DB）。

覆盖：/software/all 全量与按终端过滤、/software/stats Top N 结构。

运行: python tests/test_software_records.py
"""

import datetime
import sys
from pathlib import Path
from types import SimpleNamespace

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import zvplatform.routers.software as sw  # noqa: E402

RESULTS = []


def record(name, ok, detail=""):
    RESULTS.append(bool(ok))
    print(f"[{'PASS' if ok else 'FAIL'}] {name} {detail}", flush=True)


class StubCursor:
    def __init__(self, script):
        self.script = list(script)
        self.executed = []

    def execute(self, sql, params=None):
        self.executed.append((" ".join(sql.split()), params))
        self._current = self.script.pop(0) if self.script else {"rows": []}

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

    def cursor(self, dictionary=False, **kwargs):
        return self._cursor

    def commit(self):
        self.committed = True

    def close(self):
        pass


def make_request():
    return SimpleNamespace(state=SimpleNamespace(auth_user={"username": "tester", "role": "admin"}),
                           url=SimpleNamespace(path="/api/v1/software"), method="GET")


def install_stubs(script):
    conn = StubConn(script)
    sw.get_db_connection = lambda: conn
    return conn


NOW = datetime.datetime.now()


def main():
    # ---- 1. /software/all 全量 ----
    rows = [{"id": 1, "software_name": "Edge", "version": "120", "vendor": "Microsoft",
             "install_date": "2026-01-01", "size": None, "size_mb": 300.5,
             "asset_id": 1, "hostname": "host-a", "ip_address": "10.0.0.1"}]
    conn = install_stubs([{"rows": rows}])
    result = sw.get_all_software(asset_id=None)
    record("全量查询返回 data", result.get("data") == rows)
    record("全量查询 SQL 不含 asset_id 过滤",
           "asset_id = %s" not in conn._cursor.executed[0][0])

    # ---- 2. /software/all 按终端过滤 ----
    conn = install_stubs([{"rows": rows}])
    result = sw.get_all_software(asset_id=2241)
    sql, params = conn._cursor.executed[0]
    record("按终端过滤 SQL 含条件", "asset_id = %s" in sql and params == (2241,))
    record("按终端过滤返回 data", result.get("data") == rows)

    # ---- 3. /software/stats Top N ----
    stat_rows = [{"software_name": "Edge", "version": "120", "vendor": "Microsoft",
                  "install_count": 2, "hostnames": "host-a, host-b", "installed_assets": "host-a, host-b"}]
    conn = install_stubs([{"rows": stat_rows}])
    result = sw.get_software_stats(limit=10)
    record("stats 返回 data/total", result.get("data") == stat_rows and result.get("total") == 1)
    sql, params = conn._cursor.executed[0]
    record("stats SQL 含 LIMIT 与 GROUP BY",
           "LIMIT %s" in sql and "GROUP BY" in sql and params == (10,))

    passed, total = sum(RESULTS), len(RESULTS)
    print(f"\n{passed}/{total} passed", flush=True)
    return 0 if passed == total else 1


if __name__ == "__main__":
    raise SystemExit(main())
