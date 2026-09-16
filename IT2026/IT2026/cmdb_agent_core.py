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
# =============================================================================

class SoftwareManager:
    def __init__(self, asset_id: int):
        self.asset_id = asset_id
        self.running = False
        self.thread: threading.Thread | None = None
        self.download_dir = Path(SOFTWARE_CONFIG.get("download_dir", r"C:\CMDB-Agent\Downloads"))
        self.download_dir.mkdir(parents=True, exist_ok=True)

    def start(self):
        if self.running:
            return
        self.running = True
        self.thread = threading.Thread(target=self._loop, name="software-manager", daemon=True)
        self.thread.start()
        print(f"[SoftwareManager] 启动 (asset_id={self.asset_id})")

    def stop(self):
        self.running = False

    def _loop(self):
        policy_interval = SOFTWARE_CONFIG.get("intervals", {}).get("policy_sync", 300)
        task_interval = SOFTWARE_CONFIG.get("intervals", {}).get("task_poll", 30)
        last_policy_sync = 0.0

        while self.running:
            now = time.time()

            # 策略同步
            if now - last_policy_sync >= policy_interval:
                try:
                    self._sync_policies()
                    last_policy_sync = now
                except Exception as exc:
                    print(f"[SoftwareManager] 策略同步失败: {exc}")

            # 任务轮询
            try:
                self._poll_and_execute_tasks()
            except Exception as exc:
                print(f"[SoftwareManager] 任务轮询失败: {exc}")

            time.sleep(task_interval)

    def _sync_policies(self):
        url = urljoin(SOFTWARE_CONFIG["server_url"], "/api/v1/software/agent/policies")
        payload = {"asset_id": self.asset_id}
        resp = requests.post(url, json=payload, headers=_software_headers(), timeout=30, verify=_agent_requests_verify())
        if resp.status_code == 200:
            print("[SoftwareManager] 策略同步成功")
        else:
            print(f"[SoftwareManager] 策略同步 HTTP {resp.status_code}")

    def _poll_and_execute_tasks(self):
        url = urljoin(SOFTWARE_CONFIG["server_url"], "/api/v1/software/agent/tasks/poll")
        payload = {"asset_id": self.asset_id}
        resp = requests.post(url, json=payload, headers=_software_headers(), timeout=30, verify=_agent_requests_verify())
        if resp.status_code != 200:
            return

        data = resp.json()
        tasks = data.get("tasks", []) if isinstance(data, dict) else []
        if not tasks:
            return

        for task in tasks:
            try:
                self._execute_task(task)
            except Exception as exc:
                print(f"[SoftwareManager] 任务执行失败: {exc}")
                self._report_task_result(task.get("result_id") or task.get("id"), "failed", str(exc))

    def _execute_task(self, task: dict):
        # 后端当前下发 task_id/result_id/package_info；保留旧字段兼容历史接口。
        result_id = task.get("result_id") or task.get("id")
        task_id = task.get("task_id") or task.get("id")
        task_type = task.get("task_type") or task.get("type") or "install"
        package_info = task.get("package_info") or {}
        package_id = package_info.get("id") or task.get("package_id")

        print(
            f"[SoftwareManager] 执行任务: task_id={task_id} result_id={result_id} "
            f"type={task_type} package={package_id}"
        )

        if not result_id:
            print("[SoftwareManager] 任务缺少 result_id，无法回传执行结果")
            return

        if task_type in ("install", "upgrade") and package_id:
            self._do_install(result_id, task_id, int(package_id), task, package_info)
        elif task_type == "uninstall" and package_id:
            self._do_uninstall(result_id, task_id, int(package_id), task, package_info)
        else:
            self._report_task_result(result_id, "failed", f"unsupported_task_type={task_type}")

    def _do_install(self, result_id, task_id, package_id: int, task: dict, package_info: dict):
        # 下载软件包
        download_url = urljoin(
            SOFTWARE_CONFIG["server_url"],
            f"/api/v1/software/agent/packages/{package_id}/download?asset_id={self.asset_id}",
        )
        file_name = self._safe_file_name(package_info.get("file_name") or f"package_{package_id}.bin")
        file_path = self.download_dir / file_name
        expected_hash = str(package_info.get("file_hash") or "").strip()

        max_retries = SOFTWARE_CONFIG.get("max_retries", 3)
        for attempt in range(max_retries):
            try:
                self._report_task_result(
                    result_id,
                    "downloading",
                    f"开始下载软件包 task_id={task_id} package_id={package_id}",
                    progress=0,
                    download_progress=0,
                )
                self._download_file(download_url, file_path, result_id, expected_hash)
                break
            except Exception as exc:
                print(f"[SoftwareManager] 下载失败 (attempt {attempt + 1}): {exc}")
                if attempt == max_retries - 1:
                    raise
                time.sleep(2 ** attempt)

        # 执行安装
        install_cmd = package_info.get("install_command") or task.get("install_command")
        install_cmd = self._build_package_command(install_cmd, file_path)
        print(f"[SoftwareManager] 执行安装: {install_cmd}")
        self._report_task_result(
            result_id,
            "installing",
            f"开始执行安装命令 task_id={task_id}",
            progress=80,
            download_progress=100,
            install_progress=0,
        )
        result = subprocess.run(
            install_cmd, shell=True, capture_output=True, text=True,
            errors="replace", timeout=int(task.get("timeout") or 600), check=False,
        )

        if result.returncode == 0:
            self._report_task_result(
                result_id,
                "success",
                "install_success",
                progress=100,
                download_progress=100,
                install_progress=100,
                stdout_log=self._truncate_log(result.stdout or "install_success"),
                stderr_log=self._truncate_log(result.stderr),
            )
        else:
            self._report_task_result(
                result_id, "failed",
                f"exit_code={result.returncode} stderr={result.stderr[:500]}",
                progress=100,
                download_progress=100,
                install_progress=100,
                stdout_log=self._truncate_log(result.stdout),
                stderr_log=self._truncate_log(result.stderr),
                error_message=f"安装命令退出码={result.returncode}",
            )

    def _do_uninstall(self, result_id, task_id, package_id: int, task: dict, package_info: dict):
        uninstall_cmd = package_info.get("uninstall_command") or task.get("uninstall_command")
        if not uninstall_cmd:
            self._report_task_result(result_id, "failed", f"no_uninstall_command package_id={package_id}")
            return

        print(f"[SoftwareManager] 执行卸载: {uninstall_cmd}")
        self._report_task_result(
            result_id,
            "installing",
            f"开始执行卸载命令 task_id={task_id}",
            progress=10,
            install_progress=10,
        )
        result = subprocess.run(
            uninstall_cmd, shell=True, capture_output=True, text=True,
            errors="replace", timeout=int(task.get("timeout") or 300), check=False,
        )

        if result.returncode == 0:
            self._report_task_result(
                result_id,
                "success",
                "uninstall_success",
                progress=100,
                install_progress=100,
                stdout_log=self._truncate_log(result.stdout or "uninstall_success"),
                stderr_log=self._truncate_log(result.stderr),
            )
        else:
            self._report_task_result(
                result_id, "failed",
                f"exit_code={result.returncode} stderr={result.stderr[:500]}",
                progress=100,
                install_progress=100,
                stdout_log=self._truncate_log(result.stdout),
                stderr_log=self._truncate_log(result.stderr),
                error_message=f"卸载命令退出码={result.returncode}",
            )

    def _download_file(self, url: str, file_path: Path, result_id: int | None = None, expected_hash: str = ""):
        headers = _software_headers()
        resume_pos = 0
        if file_path.exists():
            resume_pos = file_path.stat().st_size
            headers["Range"] = f"bytes={resume_pos}-"

        mode = "ab" if resume_pos > 0 else "wb"
        with requests.get(url, headers=headers, stream=True, timeout=120, verify=_agent_requests_verify()) as resp:
            if resp.status_code == 416 and file_path.exists():
                # 本地断点文件长度异常时删除后重新下载，避免无限 416。
                file_path.unlink()
                return self._download_file(url, file_path, result_id, expected_hash)
            resp.raise_for_status()
            if resume_pos > 0 and resp.status_code == 200:
                # 服务端未按 Range 返回 206 时必须重写文件，否则会追加出损坏包。
                resume_pos = 0
                mode = "wb"
            total = int(resp.headers.get("Content-Length", 0)) + resume_pos
            downloaded = resume_pos
            expected_hash = expected_hash or str(resp.headers.get("X-File-Hash") or "").strip()
            last_reported_progress = -1
            with open(file_path, mode) as f:
                for chunk in resp.iter_content(chunk_size=1024 * 1024):
                    if chunk:
                        f.write(chunk)
                        downloaded += len(chunk)
                        if total > 0 and result_id:
                            progress = int((downloaded / total) * 100)
                            if progress >= 100 or progress - last_reported_progress >= 10:
                                self._report_task_result(
                                    result_id,
                                    "downloading",
                                    f"下载进度 {progress}%",
                                    progress=min(progress, 80),
                                    download_progress=min(progress, 100),
                                )
                                last_reported_progress = progress
                            print(f"[SoftwareManager] 下载进度: {progress}%")

        # SHA256 校验
        sha256 = hashlib.sha256()
        with open(file_path, "rb") as f:
            for chunk in iter(lambda: f.read(4096), b""):
                sha256.update(chunk)
        actual_hash = sha256.hexdigest()
        if expected_hash and actual_hash.lower() != expected_hash.lower():
            raise RuntimeError(f"软件包 SHA256 校验失败 expected={expected_hash} actual={actual_hash}")
        print(f"[SoftwareManager] 下载完成 SHA256={actual_hash}")

    @staticmethod
    def _safe_file_name(file_name: str) -> str:
        # 只保留文件名，防止服务端数据异常导致写到下载目录之外。
        cleaned = Path(str(file_name)).name.strip()
        return cleaned or "package.bin"

    @staticmethod
    def _build_package_command(command: str | None, file_path: Path) -> str:
        # 安装命令支持显式占位符；没有命令时直接执行下载的软件包。
        quoted_path = f'"{file_path}"'
        if not command:
            return quoted_path
        install_command = str(command)
        for placeholder in ("{file_path}", "{package_path}", "%FILE_PATH%", "%PACKAGE_PATH%"):
            install_command = install_command.replace(f'"{placeholder}"', quoted_path)
            install_command = install_command.replace(f"'{placeholder}'", quoted_path)
            install_command = install_command.replace(placeholder, quoted_path)
        return install_command

    @staticmethod
    def _truncate_log(text: str | None, limit: int = 20000) -> str:
        # 限制单次回传日志长度，避免数据库字段和接口请求过大。
        if not text:
            return ""
        value = str(text)
        if len(value) <= limit:
            return value
        return value[-limit:]

    def _report_task_result(
        self,
        result_id: int | None,
        status: str,
        message: str,
        progress: int | None = None,
        download_progress: int | None = None,
        install_progress: int | None = None,
        stdout_log: str | None = None,
        stderr_log: str | None = None,
        error_message: str | None = None,
    ):
        if result_id is None:
            return
        try:
            if status == "completed":
                status = "success"
            url = urljoin(SOFTWARE_CONFIG["server_url"], f"/api/v1/software/agent/task-results/{result_id}")
            payload = {
                "asset_id": self.asset_id,
                "status": status,
                "completed_at": datetime.now().isoformat(),
            }
            if progress is not None:
                payload["progress"] = max(0, min(100, int(progress)))
            if download_progress is not None:
                payload["download_progress"] = max(0, min(100, int(download_progress)))
            if install_progress is not None:
                payload["install_progress"] = max(0, min(100, int(install_progress)))
            if status in {"failed", "timeout"}:
                payload["error_message"] = self._truncate_log(error_message or message, 1000)
            else:
                payload["stdout_log"] = self._truncate_log(stdout_log or message)
            if stdout_log is not None and "stdout_log" not in payload:
                payload["stdout_log"] = self._truncate_log(stdout_log)
            if stderr_log is not None:
                payload["stderr_log"] = self._truncate_log(stderr_log)

            resp = requests.put(url, json=payload, headers=_software_headers(), timeout=30, verify=_agent_requests_verify())
            if resp.status_code >= 400:
                print(f"[SoftwareManager] 结果上报 HTTP {resp.status_code}: {resp.text[:500]}")
        except Exception as exc:
            print(f"[SoftwareManager] 结果上报失败: {exc}")


# =============================================================================
# 远程桌面服务端
# =============================================================================

def _truncate_control_output(value: str | None, limit: int = 20000) -> str:
    text = str(value or "")
    if len(text) <= limit:
        return text
    return text[:limit] + "\n...[truncated]"


# =============================================================================
# 高权限命令白名单（P0-10）
# 结构化协议：{"zview_cmd": {"op": "restart|shutdown|script|raw", ...}}
# - restart/shutdown：delay 强制钳位 0~3600
# - script：base64 编码 PowerShell，长度上限 128KB
# - raw：自由命令，默认允许（ZVIEW_AGENT_ALLOW_RAW_COMMAND=0 可关闭），逐条告警日志
# - 兼容：旧平台裸 {"command": "..."} 按 raw 处理（同 raw 开关）
# =============================================================================

_RAW_COMMAND_ENV_NAME = "ZVIEW_AGENT_ALLOW_RAW_COMMAND"
_SCRIPT_B64_MAX_LEN = 128 * 1024


def _raw_command_allowed() -> bool:
    return str(get_env(_RAW_COMMAND_ENV_NAME, "1") or "1").strip().lower() not in ("0", "false", "no", "off")


def _clamp_timeout(value: Any, default: int = 60) -> int:
    try:
        timeout = int(value)
    except (TypeError, ValueError):
        timeout = default
    return max(1, min(timeout, 300))


def _dispatch_zview_cmd(zv: dict, operator: str) -> dict:
    op = str(zv.get("op") or "").strip().lower()

    if op in ("restart", "shutdown"):
        try:
            delay = int(zv.get("delay", 0) or 0)
        except (TypeError, ValueError):
            delay = 0
        delay = max(0, min(delay, 3600))
        flag = "/r" if op == "restart" else "/s"
        command = f"shutdown {flag} /t {delay} /f"
        print(f"[Command] whitelist op={op} delay={delay} operator={operator}")
        return execute_control_command(command, 15)

    if op == "script":
        encoded = str(zv.get("encoded") or "").strip()
        if not encoded or len(encoded) > _SCRIPT_B64_MAX_LEN:
            return {"success": False, "error": "script encoded payload missing or too large",
                    "stdout": "", "stderr": "", "returncode": None}
        try:
            base64.b64decode(encoded, validate=True)
        except Exception:
            return {"success": False, "error": "script encoded payload is not valid base64",
                    "stdout": "", "stderr": "", "returncode": None}
        command = f"powershell -NoProfile -ExecutionPolicy Bypass -EncodedCommand {encoded}"
        print(f"[Command] whitelist op=script len={len(encoded)} operator={operator}")
        return execute_control_command(command, _clamp_timeout(zv.get("timeout_seconds", 120), 120))

    if op == "raw":
        command = str(zv.get("command") or "").strip()
        if not command:
            return {"success": False, "error": "Command is required",
                    "stdout": "", "stderr": "", "returncode": None}
        if not _raw_command_allowed():
            print(f"[Command] raw command DENIED by ZVIEW_AGENT_ALLOW_RAW_COMMAND=0 operator={operator}")
            return {"success": False, "error": "Raw command execution is disabled on this agent",
                    "stdout": "", "stderr": "", "returncode": None}
        print(f"[Command] raw command executed (audit): operator={operator} command={command[:200]}")
        return execute_control_command(command, _clamp_timeout(zv.get("timeout_seconds", 60), 60))

    return {"success": False, "error": f"unknown zview_cmd op: {op}",
            "stdout": "", "stderr": "", "returncode": None}


def handle_control_command_payload(payload: dict) -> dict:
    """9001 /api/v1/command 统一入口：结构化白名单优先，裸 command 兼容。"""
    operator = str(payload.get("operator") or payload.get("requester") or "platform")
    zv = payload.get("zview_cmd")
    if isinstance(zv, dict):
        return _dispatch_zview_cmd(zv, operator)
    # 兼容旧平台：裸 command 字段按 raw 处理（同一开关管控）
    legacy_command = str(payload.get("command") or "").strip()
    if legacy_command:
        return _dispatch_zview_cmd({"op": "raw", "command": legacy_command,
                                    "timeout_seconds": payload.get("timeout_seconds", 60)}, operator)
    return {"success": False, "error": "command payload required (zview_cmd or command)",
            "stdout": "", "stderr": "", "returncode": None}


def execute_control_command(command: str, timeout_seconds: int = 60) -> dict:
    normalized_command = str(command or "").strip()
    if not normalized_command:
        return {
            "success": False,
            "error": "Command is required",
            "stdout": "",
            "stderr": "",
            "returncode": None,
        }

    timeout_seconds = max(1, min(int(timeout_seconds or 60), 300))
    try:
        result = subprocess.run(
            normalized_command,
            shell=True,
            capture_output=True,
            text=True,
            errors="replace",
            timeout=timeout_seconds,
            check=False,
        )
        return {
            "success": result.returncode == 0,
            "stdout": _truncate_control_output(result.stdout),
            "stderr": _truncate_control_output(result.stderr),
            "returncode": result.returncode,
            "error": "" if result.returncode == 0 else f"Command exited with code {result.returncode}",
        }
    except subprocess.TimeoutExpired as exc:
        return {
            "success": False,
            "stdout": _truncate_control_output(exc.stdout),
            "stderr": _truncate_control_output(exc.stderr),
            "returncode": None,
            "error": f"Command timed out after {timeout_seconds} seconds",
        }
    except Exception as exc:
        return {
            "success": False,
            "stdout": "",
            "stderr": "",
            "returncode": None,
            "error": f"Command execution failed: {type(exc).__name__}: {exc}",
        }


# V1.8.3 通用任务通道：注册 command handler（与控制通道同一安全模型——
# raw 命令受 ZVIEW_AGENT_ALLOW_RAW_COMMAND 门控，zv 结构化命令白名单分发）
from zvagent import jobs as _agent_jobs  # noqa: E402


def _job_command_handler(payload: dict) -> dict:
    return execute_control_command(
        str(payload.get("command") or ""),
        _clamp_timeout(payload.get("timeout_seconds"), 60),
    )


_agent_jobs.register_job_handler("command", _job_command_handler)


class AgentControlRequestHandler(BaseHTTPRequestHandler):
    server_version = "ZViewAgentControl/1.0"
    max_request_body_bytes = 1024 * 1024

    def log_message(self, format, *args):
        print(f"[AgentControl] {format % args}")

    def _send_json(self, status_code: int, payload: dict):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status_code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(body)

    def _is_authorized(self) -> bool:
        expected_token = str(CONFIG.get("token") or "").strip()
        if not expected_token:
            return False
        authorization = str(self.headers.get("Authorization") or "").strip()
        provided_token = authorization[7:].strip() if authorization.lower().startswith("bearer ") else ""
        return bool(provided_token) and hmac.compare_digest(provided_token, expected_token)

    def _read_json_body(self) -> dict:
        raw_length = self.headers.get("Content-Length") or "0"
        try:
            content_length = int(raw_length)
        except ValueError as exc:
            raise ValueError("Invalid Content-Length") from exc
        if content_length < 0 or content_length > self.max_request_body_bytes:
            raise ValueError("Request body is too large")
        raw_body = self.rfile.read(content_length) if content_length else b"{}"
        if not raw_body:
            return {}
        value = json.loads(raw_body.decode("utf-8"))
        if not isinstance(value, dict):
            raise ValueError("JSON body must be an object")
        return value

    def do_POST(self):
        if not self._is_authorized():
            self._send_json(401, {"success": False, "error": "Unauthorized"})
            return

        try:
            payload = self._read_json_body()
        except (UnicodeDecodeError, ValueError, json.JSONDecodeError) as exc:
            self._send_json(400, {"success": False, "error": str(exc)})
            return

        if self.path == "/api/v1/command":
            result = handle_control_command_payload(payload)
            self._send_json(200 if result["success"] else 400, result)
            return

        if self.path == "/api/v1/security-command":
            try:
                from security_manager import execute_security_command
                command_type = payload.get("command_type") or ""
                params = payload.get("params") or {}
                result = execute_security_command(command_type, params)
                self._send_json(200 if result.get("success") else 400, result)
            except Exception as exc:
                self._send_json(500, {"success": False, "error": str(exc)})
            return

        if self.path == "/api/v1/trigger-report":
            result = trigger_immediate_report()
            self._send_json(200 if result["success"] else 502, result)
            return

        self._send_json(404, {"success": False, "error": "Unknown control endpoint"})


class AgentControlServer:
    def __init__(self, host: str | None = None, port: int | None = None):
        host = host or get_env("ZVIEW_BIND_HOST", "0.0.0.0") or "0.0.0.0"  # P0-2: 可配置监听地址
        self.host = host
        self.port = int(port or CONFIG.get("control_port") or 9001)
        self.server: ThreadingHTTPServer | None = None
        self.thread: threading.Thread | None = None

    def start(self):
        if self.thread and self.thread.is_alive():
            return
        self.server = ThreadingHTTPServer((self.host, self.port), AgentControlRequestHandler)
        self.server.daemon_threads = True
        self.thread = threading.Thread(target=self.server.serve_forever, name="agent-control", daemon=True)
        self.thread.start()
        print(f"[AgentControl] Listening on http://{self.host}:{self.port}")

    def stop(self):
        if self.server:
            self.server.shutdown()
            self.server.server_close()
            self.server = None
        if self.thread:
            self.thread.join(timeout=2)
            self.thread = None


class StarletteWebSocketAdapter:
    """Adapt the native websockets connection to the engine's Starlette API."""

    def __init__(self, connection):
        self.connection = connection
        self.remote_address = connection.remote_address

    async def receive_text(self) -> str:
        message = await self.connection.recv()
        if isinstance(message, bytes):
            return message.decode("utf-8")
        return str(message)

    async def send_json(self, payload: dict):
        await self.connection.send(json.dumps(payload, ensure_ascii=False))

    async def send_bytes(self, data: bytes):
        await self.connection.send(data)

    async def close(self, code: int = 1000, reason: str = ""):
        await self.connection.close(code=code, reason=reason)


class RemoteDesktopServer:
    def __init__(self, host: str | None = None, port: int = 9000):
        host = host or get_env("ZVIEW_BIND_HOST", "0.0.0.0") or "0.0.0.0"  # P0-2: 可配置监听地址
        self.host = host
        self.port = port
        self.running = False
        self.server = None
        self.thread: threading.Thread | None = None

    @staticmethod
    def _log(message: str, exc: BaseException | None = None) -> None:
        lines = [f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] [RemoteDesktopServer] {message}"]
        if exc is not None:
            lines.append(traceback.format_exception(type(exc), exc, exc.__traceback__))
        try:
            log_dir = Path(os.environ.get("ProgramData", ".")) / "CMDB-Agent" / "logs"
            log_dir.mkdir(parents=True, exist_ok=True)
            with open(log_dir / "remote-desktop-server.log", "a", encoding="utf-8") as fh:
                fh.write("\n".join(lines) + "\n")
        except Exception:
            pass

    def start(self):
        if self.running:
            return
        self.running = True
        self.thread = threading.Thread(target=self.serve_blocking, name="rdp-server", daemon=True)
        self.thread.start()
        print(f"[RemoteDesktop] 服务端启动: ws://{self.host}:{self.port}/remote-desktop")

    def serve_blocking(self):
        """Run the websocket server loop in the calling thread until stopped."""
        if self.running:
            return
        self.running = True
        try:
            asyncio.set_event_loop(asyncio.new_event_loop())
            loop = asyncio.get_event_loop()
            loop.run_until_complete(self._serve())
        except Exception as exc:
            self._log("服务线程异常退出", exc)
            print(f"[RemoteDesktop] 服务端错误: {exc}")
        finally:
            self.running = False

    def stop(self):
        self.running = False
        if self.server:
            try:
                self.server.close()
            except Exception:
                pass

    def _run_server(self):
        self.serve_blocking()

    async def _serve(self):
        try:
            import websockets
        except ImportError:
            self._log("websockets 未安装，远程桌面不可用")
            print("[RemoteDesktop] websockets 未安装，远程桌面不可用")
            return

        self._log(f"websockets {getattr(websockets, '__version__', 'unknown')} 导入成功，准备监听 ws://{self.host}:{self.port}")

        try:
            apply_agent_firewall_whitelist(
                CONFIG.get("server_url", ""), logger=lambda m: self._log(m)
            )
        except Exception:
            pass

        async def handler(websocket, path=None):
            self._log(f"客户端连接: {websocket.remote_address}")
            try:
                session = self._create_session(StarletteWebSocketAdapter(websocket))
                await session.start()
            except Exception as exc:
                import traceback
                self._log(f"会话错误: {type(exc).__name__}: {exc}")
                self._log("traceback: " + traceback.format_exc()[-800:])
            finally:
                self._log(f"客户端断开: {websocket.remote_address}")

        try:
            self.server = await websockets.serve(
                handler, self.host, self.port,
                ping_interval=20, ping_timeout=10,
            )
            self._log(f"监听成功 ws://{self.host}:{self.port}，等待连接")
            await self.server.wait_closed()
            self._log("server.wait_closed 返回，服务端正常关闭")
        except Exception as exc:
            self._log("serve 失败", exc)
            print(f"[RemoteDesktop] serve 错误: {exc}")

    def _create_session(self, websocket):
        _load_cached_agent_policies()
        session_id = f"session_{int(time.time() * 1000)}_{uuid.uuid4().hex[:8]}"
        try:
            # 优先使用 v2 引擎
            from remote_desktop_engine_v2 import RemoteDesktopSession as V2Session
            return V2Session(websocket, session_id)
        except Exception as exc:
            print(f"[RemoteDesktop] v2 引擎加载失败 ({exc})，回退到 v1")
            from remote_desktop_engine import RemoteDesktopSession as V1Session
            return V1Session(websocket, session_id)


# =============================================================================
# 公开接口（入口文件依赖这些符号）
# =============================================================================

_server_instance: RemoteDesktopServer | None = None
_control_server_instance: AgentControlServer | None = None
_software_manager_instance: SoftwareManager | None = None


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


def start_software_management(asset_id: int):
    """启动软件管理（策略同步 + 任务轮询）。"""
    global _software_manager_instance
    if _software_manager_instance is not None:
        return
    _software_manager_instance = SoftwareManager(asset_id)
    _software_manager_instance.start()


_security_policy_sync_instance: "SecurityPolicySync | None" = None


class SecurityPolicySync:
    """安全策略自动轮询同步：定时从平台拉取绑定的安全策略并应用，回传执行结果。"""

    _LOG_PATH = Path(os.environ.get("ProgramData", ".")) / "CMDB-Agent" / "logs" / "security-sync.log"

    def __init__(self, asset_id: int):
        self.asset_id = asset_id
        self.running = False
        self.thread: threading.Thread | None = None

    def _log(self, msg: str):
        line = f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {msg}"
        print(f"[SecurityPolicySync] {msg}")
        try:
            self._LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
            with open(self._LOG_PATH, "a", encoding="utf-8") as f:
                f.write(line + "\n")
        except Exception:
            pass

    def start(self):
        if self.running:
            return
        self.running = True
        self.thread = threading.Thread(target=self._loop, name="security-policy-sync", daemon=True)
        self.thread.start()
        self._log(f"启动 (asset_id={self.asset_id})")

    def stop(self):
        self.running = False

    def _loop(self):
        interval = int(CONFIG.get("intervals", {}).get("security_policy_sync", 300) or 300)
        interval = max(60, min(interval, 3600))
        self._log(f"轮询间隔={interval}s")
        while self.running:
            try:
                self._sync_and_apply()
            except Exception as exc:
                self._log(f"同步失败: {exc}")
            time.sleep(interval)

    def _sync_and_apply(self):
        from security_manager import execute_security_command
        token = CONFIG.get("token") or ""
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        url = urljoin(_platform_base(), f"/api/v1/agent/security-policies?asset_id={self.asset_id}")
        self._log(f"拉取策略: {url}")
        resp = requests.get(url, headers=headers, timeout=30, verify=_agent_requests_verify())
        if resp.status_code != 200:
            self._log(f"拉取策略 HTTP {resp.status_code}")
            return
        data = resp.json()
        policies = data.get("policies") or []
        self._log(f"拉取到 {len(policies)} 条安全策略")
        if not policies:
            return
        for p in policies:
            try:
                self._apply_one(p, execute_security_command)
            except Exception as exc:
                self._log(f"应用策略 {p.get('id')} 失败: {exc}")

    def _apply_one(self, policy: dict, executor):
        ptype = policy.get("policy_type")
        config = policy.get("config") or {}
        policy_id = policy.get("id")
        scope_type = policy.get("scope_type", "asset")
        applied = 0
        failed = 0
        error_detail = None
        try:
            if ptype == "firewall":
                rules = config.get("rules") or []
                res = executor("firewall_apply", {"rules": rules})
                applied = res.get("applied", 0)
                failed = res.get("failed", 0)
                if not res.get("success"):
                    error_detail = res.get("error") or json.dumps(res.get("details", []))[:500]
            elif ptype == "usb":
                action = config.get("action", "block")
                cmd = "usb_block" if action == "block" else "usb_allow"
                res = executor(cmd, {})
                if not res.get("success"):
                    failed = 1
                    error_detail = res.get("error", "usb apply failed")
                else:
                    applied = 1
            else:
                error_detail = f"unknown policy_type: {ptype}"
                failed = 1
        except Exception as exc:
            failed = 1
            error_detail = str(exc)

        self._report_result(policy_id, scope_type, applied, failed, error_detail)

    def _report_result(self, policy_id, scope_type, applied, failed, error_detail):
        token = CONFIG.get("token") or ""
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        url = urljoin(_platform_base(), "/api/v1/agent/security-policy-result")
        status = "success" if failed == 0 and applied > 0 else ("partial" if applied > 0 else "failed")
        payload = {
            "policy_id": policy_id,
            "asset_id": self.asset_id,
            "scope_type": scope_type,
            "status": status,
            "applied_rules": applied,
            "failed_rules": failed,
            "error_detail": error_detail,
        }
        try:
            requests.post(url, json=payload, headers=headers, timeout=15, verify=_agent_requests_verify())
        except Exception as exc:
            print(f"[SecurityPolicySync] 回传结果失败: {exc}")

def apply_agent_firewall_whitelist(server_url: str, logger=None):
    """R2 防火墙白名单（可选，ZVIEW_FIREWALL_WHITELIST=1 启用）：
    仅允许平台主机与本机访问 Agent 9000/9001 端口。
    幂等：规则名固定，先删后建。失败不影响服务启动。
    """
    import subprocess
    import ipaddress
    from urllib.parse import urlparse
    try:
        if get_env("ZVIEW_FIREWALL_WHITELIST", "").strip() not in ("1", "true", "True"):
            return
        parsed = urlparse(server_url)
        platform_host = parsed.hostname or ""
        try:
            platform_ip = ipaddress.ip_address(platform_host).exploded
        except ValueError:
            import socket
            platform_ip = socket.gethostbyname(platform_host)
        allow_remoteip = f"{platform_ip},127.0.0.1"
        try:
            lan_net = ipaddress.ip_network(f"{platform_ip}/24", strict=False)
            if lan_net.prefixlen < 32:
                allow_remoteip += f",{lan_net.network_address}/{lan_net.prefixlen}"
        except ValueError:
            pass
        # 额外放行网段（跨网段观看端场景）：ZVIEW_EXTRA_FIREWALL_ALLOW="172.17.40.0/24,10.0.0.5"
        extra_allow = get_env("ZVIEW_EXTRA_FIREWALL_ALLOW", "").strip()
        if extra_allow:
            allow_remoteip += f",{extra_allow}"
            if logger:
                logger(f"agent firewall extra allow: {extra_allow}")
        RULE_ALLOW = "zv-agent-allow-platform"
        RULE_BLOCK = "zv-agent-block-others"
        for name in (RULE_ALLOW, RULE_BLOCK):
            subprocess.run(
                ["netsh", "advfirewall", "firewall", "delete", "rule", f"name={name}"],
                capture_output=True, timeout=15, check=False,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        subprocess.run(
            ["netsh", "advfirewall", "firewall", "add", "rule", f"name={RULE_ALLOW}",
             "dir=in", "action=allow", "protocol=TCP",
             "localport=9000,9001", f"remoteip={allow_remoteip}"],
            capture_output=True, timeout=15, check=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        subprocess.run(
            ["netsh", "advfirewall", "firewall", "add", "rule", f"name={RULE_BLOCK}",
             "dir=in", "action=block", "protocol=TCP", "localport=9000,9001"],
            capture_output=True, timeout=15, check=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        if logger:
            logger(f"agent firewall whitelist applied: allow={platform_ip},127.0.0.1 -> 9000,9001")
    except Exception as exc:
        if logger:
            logger(f"agent firewall whitelist failed (ignored): {exc}")


def start_security_policy_sync(asset_id: int):
    """启动安全策略自动轮询同步。"""
    global _security_policy_sync_instance
    if _security_policy_sync_instance is not None:
        return
    _security_policy_sync_instance = SecurityPolicySync(asset_id)
    _security_policy_sync_instance.start()


def start_remote_desktop_server(wait: bool = False):
    """启动远程桌面 WebSocket 服务端。

    wait=False 时在后台线程运行并立即返回；
    wait=True 时在调用线程内阻塞运行，直到服务端关闭。
    """
    global _server_instance
    if _server_instance is not None:
        return
    _server_instance = RemoteDesktopServer(port=9000)  # host 经 ZVIEW_BIND_HOST 可配置
    if wait:
        _server_instance.serve_blocking()
    else:
        _server_instance.start()


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
