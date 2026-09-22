# -*- coding: utf-8 -*-
"""1.9.56 专项 2：自 cmdb_agent_core.py 逐字迁入（#16/#10 模块化，逻辑未改）。"""
import hashlib
import json
import time
from urllib.parse import urljoin

import requests

from zvagent import __version__ as AGENT_VERSION
from zvagent.auth import _agent_headers, _agent_requests_verify, _platform_base
from zvagent.collectors.patches import collect_windows_update_status
from zvagent.collectors.system import get_primary_network_info
from zvagent.state import _AGENT_STATE, _LOCK

# 补丁上报状态与全量同步间隔（原 core 模块级常量随迁）
_PATCH_FULL_SYNC_INTERVAL = 6
_PATCH_REPORT_STATE = {}


def _patch_payload_hash(patch_status: dict) -> str:
    """对补丁列表做顺序无关哈希（不变则跳过上报）。"""
    normalized = sorted(
        (
            str(p.get("kb") or ""),
            str(p.get("title") or ""),
            str(p.get("severity") or ""),
        )
        for p in patch_status.get("pending") or []
    )
    normalized.append(str(patch_status.get("pending_count") or 0))
    normalized.append(str(patch_status.get("reboot_required") or False))
    return hashlib.sha256(json.dumps(normalized, ensure_ascii=False).encode("utf-8")).hexdigest()


def _patch_report_loop():
    """补丁状态上报（独立低频线程，6h 全量 + 变更即报）。

    WU 在线搜索代价高（10~60s），采集侧带 1h TTL 缓存；
    循环节奏：采集 → 上报 → 休眠 300s。
    """
    while _AGENT_STATE["running"]:
        try:
            asset_id = _AGENT_STATE.get("asset_id")
            if not asset_id:
                time.sleep(120)
                continue

            patch_status = collect_windows_update_status()
            payload_hash = _patch_payload_hash(patch_status)
            now = time.time()
            unchanged = payload_hash == _PATCH_REPORT_STATE["hash"]
            full_sync_due = now - _PATCH_REPORT_STATE["last_full_sync"] >= _PATCH_FULL_SYNC_INTERVAL
            if unchanged and not full_sync_due and not patch_status.get("error"):
                time.sleep(300)
                continue

            ip, mac = get_primary_network_info()
            payload = {
                "asset_id": asset_id,
                "hostname": _AGENT_STATE["hostname"],
                "ip_address": ip,
                "mac_address": mac,
                "status": "online",
                "report_type": "patches",
                "agent_version": AGENT_VERSION,
                "patch_status": patch_status,
            }
            url = urljoin(_platform_base(), "/api/v1/agent/heartbeat")
            resp = requests.post(url, json=payload, headers=_agent_headers(), timeout=60, verify=_agent_requests_verify())
            if resp.status_code in (200, 201):
                _PATCH_REPORT_STATE["hash"] = payload_hash
                _PATCH_REPORT_STATE["last_full_sync"] = now
                print(f"[Patches] 上报完成 (pending={patch_status.get('pending_count')})")
            else:
                print(f"[Patches] HTTP {resp.status_code}")
        except Exception as exc:
            print(f"[Patches] 错误: {exc}")

        time.sleep(300)


# =============================================================================
# 软件管理 - 策略同步 + 任务轮询

