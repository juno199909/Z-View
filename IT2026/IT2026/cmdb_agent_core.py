"""
Z-View Agent Core Module - 备份恢复版
包含：资产采集、心跳上报、软件管理、远程桌面服务端
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import os
import platform
import socket
import subprocess
import sys
import threading
import time
import traceback
import uuid
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Optional, Dict
from urllib.parse import urljoin, urlparse

import psutil
import requests

from config_utils import ensure_env_loaded, get_env
from console_utils import enable_utf8_stdio, safe_console_print

enable_utf8_stdio()
print = safe_console_print
ensure_env_loaded()

# =============================================================================
# V1.8.1：config/auth/upgrade 迁入 zvagent 包（本模块保留薄委托 re-export）
# =============================================================================

from zvagent.config import (  # noqa: F401
    CONFIG,
    SOFTWARE_CONFIG,
    _DEFAULT_CONFIG,
    _DEFAULT_SOFTWARE_CONFIG,
    _apply_env_overrides,
    _load_user_config,
    _merge_config,
)
from zvagent import __version__ as AGENT_VERSION  # noqa: F401
from zvagent.auth import (  # noqa: F401
    _AGENT_ERROR_LOG_PATH,
    _AGENT_TLS_CACHE_PATH,
    _agent_headers,
    _agent_requests_verify,
    _ca_bundle_path,
    _device_credentials,
    _load_device_credentials,
    _log_agent_error,
    _platform_base,
    _save_device_credentials,
    _software_headers,
    clear_device_credentials,
)
from zvagent.upgrade import (  # noqa: F401
    _UPGRADE_STATE,
    _UPGRADE_STATE_PATH,
    _acquire_upgrade_lock,
    _consume_upgrade_result_after_start,
    _get_last_upgrade_state,
    _migrate_to_onedir,
    _record_upgrade_failure,
    _release_upgrade_lock,
    _upgrade_backoff_remaining,
    _upgrade_state_write,
    _verify_authenticode_signature,
    perform_self_upgrade,
)

# =============================================================================
# V1.8.2：state/collectors/policy/heartbeat 迁入 zvagent 包（薄委托 re-export）
# =============================================================================

from zvagent.state import _AGENT_STATE, _LOCK  # noqa: F401
from zvagent.collectors.system import (  # noqa: F401
    collect_hardware_info,
    collect_system_status,
    get_primary_network_info,
)
from zvagent.collectors.software import collect_software_list  # noqa: F401
from zvagent.collectors.patches import collect_windows_update_status  # noqa: F401
from zvagent.policy import (  # noqa: F401
    _AGENT_POLICIES_CACHE_PATH,
    _apply_agent_policies,
    _current_interval,
    _load_cached_agent_policies,
    _set_prompt_on_secure_desktop,
)
from zvagent.heartbeat import (  # noqa: F401
    _heartbeat_loop,
    _check_heartbeat_self_heal,
    _network_report_loop,
    _software_payload_hash,
    _software_report_loop,
    _hardware_report_loop,
    _system_status_loop,
    get_asset_id_from_server,
    trigger_immediate_report,
)

# 1.9.56 专项 2：自有逻辑迁入 zvagent 子模块，此处 re-export 兼容存量导入（#16/#10）
from zvagent.patches_report import (  # noqa: F401
    _patch_payload_hash,
    _patch_report_loop,
)
from zvagent.software import (  # noqa: F401
    SoftwareManager,
    start_software_management,
)
from zvagent.control import (  # noqa: F401
    _truncate_control_output,
    _raw_command_allowed,
    _clamp_timeout,
    _dispatch_zview_cmd,
    handle_control_command_payload,
    execute_control_command,
    _job_command_handler,
    AgentControlRequestHandler,
    AgentControlServer,
    StarletteWebSocketAdapter,
)
from zvagent.remote import (  # noqa: F401
    RemoteDesktopServer,
    start_remote_desktop_server,
)
from zvagent.policy import (  # noqa: F401
    SecurityPolicySync,
    apply_agent_firewall_whitelist,
    start_security_policy_sync,
)

# =============================================================================
# 心跳上报线程
# =============================================================================



# ============================================================
# Agent 自动升级（R13）+ 升级状态机（P0-06）
# 状态：CHECK → DOWNLOAD → VERIFY_HASH → VERIFY_SIGNATURE → BACKUP
#       → INSTALL(bat) → START → HEALTH_CHECK → COMMIT
# =============================================================================
# V1.9.11 修复：删除本地重复的 _heartbeat_loop 定义（shadowing 事故）
#
# 历史：V1.8.2 将 heartbeat 正本迁入 zvagent.heartbeat 并在此 re-export（第 100 行），
# 但本地遗留了一份旧副本 `def _heartbeat_loop`，把导入的同名函数遮蔽了——运行时
# 线程（下方 threads 列表）永远执行无 jobs 执行块的旧副本，服务端心跳响应里的
# `jobs` 键被静默忽略，且 job_results 从不随心跳上报。这就是"任务 dispatched 后
# Agent 端不执行、不回传"的根因（1.9.5/1.9.7/1.9.9 三连复现、PYZ 验证通过却
# 零 [V1910DBG] 痕迹的矛盾点）。
#
# 现在 _heartbeat_loop / _check_heartbeat_self_heal 等均由第 100 行的
# from zvagent.heartbeat import 提供，正本含通用任务通道完整逻辑：
#   响应 body["jobs"] → zvagent.jobs.execute_pending_jobs → 结果随下次心跳上报。
#
# 2026-09-12 全量清理：本地重复定义共 8 处（_heartbeat_loop、
# get_asset_id_from_server、_check_heartbeat_self_heal、_system_status_loop、
# _hardware_report_loop、_software_payload_hash、_software_report_loop、
# _network_report_loop）已全部删除，逐个与 zvagent 正本 diff 确认一致后移除。
# 规则：本文件对 zvagent 只做 re-export（`# noqa: F401` 导入 + 兼容引用），
# 禁止再定义同名函数——那是任务通道 Agent 端不执行事故的根因。
# =============================================================================


_SOFTWARE_FULL_SYNC_INTERVAL = 6 * 3600  # 无变化时也定期全量上报，兜底数据一致性
_SOFTWARE_REPORT_STATE = {"hash": None, "last_full_sync": 0.0}


# =============================================================================
# Patch Management Phase 1 - WU 补丁状态采集上报（基于 Windows Update 状态）
# =============================================================================

_PATCH_FULL_SYNC_INTERVAL = 6 * 3600  # 无变化时也定期全量上报，兜底数据一致性
_PATCH_REPORT_STATE = {"hash": None, "last_full_sync": 0.0}




def start_cmdb_reporter():
    """启动资产采集和心跳上报线程。"""
    _AGENT_STATE["running"] = True
    # P0-06：升级后新进程启动时消费 bat 终态标记（COMMIT/ROLLBACK）
    _consume_upgrade_result_after_start()

    threads = [
        ("heartbeat", _heartbeat_loop),
        ("system_status", _system_status_loop),
        ("hardware_report", _hardware_report_loop),
        ("software_report", _software_report_loop),
        ("network_report", _network_report_loop),
        ("patch_report", _patch_report_loop),
    ]

    for name, target in threads:
        t = threading.Thread(target=target, name=f"agent-{name}", daemon=True)
        t.start()
        _AGENT_STATE["threads"][name] = t
        print(f"[Agent] 线程启动: {name}")


_control_server_instance: AgentControlServer | None = None

def start_agent_control_server():
    """Start the authenticated HTTP control plane for commands and reports."""
    global _control_server_instance
    if _control_server_instance is not None:
        return
    _control_server_instance = AgentControlServer(
        host="0.0.0.0",
        port=int(CONFIG.get("control_port") or 9001),
    )
    _control_server_instance.start()


# 模块加载时恢复最近一次平台下发的策略（重启后立即生效，无需等待下一轮心跳）
_load_cached_agent_policies()
