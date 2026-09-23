# -*- coding: utf-8 -*-
"""Agent 配置加载 BOM 兼容回归测试（1.9.55）。

背景：PowerShell 等工具重写 config.local.json 会引入 UTF-8 BOM，
旧加载器 encoding="utf-8" 遇 BOM 即异常（config.py 静默回退 {} →
agent 重启后丢 server_url/token 离线循环）。现统一 utf-8-sig。

运行: python tests/test_agent_config_bom.py
"""

import json
import sys
import tempfile
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import zvagent.config as agent_config  # noqa: E402

RESULTS = []


def record(name, ok, detail=""):
    RESULTS.append(bool(ok))
    print(f"[{'PASS' if ok else 'FAIL'}] {name} {detail}", flush=True)


def main():
    payload = {"server_url": "https://172.16.250.120:8443", "token": "abc"}

    with_bom = Path(tempfile.mkdtemp()) / "config.local.json"
    with_bom.write_bytes(b"\xef\xbb\xbf" + json.dumps(payload).encode("utf-8"))
    loaded = agent_config._load_config_from_file(with_bom)
    record("带 BOM 配置可加载", loaded.get("server_url") == payload["server_url"])

    without_bom = Path(tempfile.mkdtemp()) / "config.local.json"
    without_bom.write_text(json.dumps(payload), encoding="utf-8")
    loaded = agent_config._load_config_from_file(without_bom)
    record("无 BOM 配置可加载", loaded.get("server_url") == payload["server_url"])

    broken = Path(tempfile.mkdtemp()) / "config.local.json"
    broken.write_bytes(b"\xef\xbb\xbf{not json")
    loaded = agent_config._load_config_from_file(broken)
    record("坏 JSON 静默回退空配置", loaded == {})

    missing = Path(tempfile.mkdtemp()) / "missing.json"
    record("缺文件回退空配置", agent_config._load_config_from_file(missing) == {})

    passed, total = sum(RESULTS), len(RESULTS)
    print(f"\n{passed}/{total} passed", flush=True)
    return 0 if passed == total else 1


if __name__ == "__main__":
    raise SystemExit(main())
