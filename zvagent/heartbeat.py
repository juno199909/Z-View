# -*- coding: utf-8 -*-
"""Agent 心跳循环与状态上报（V1.8.2 迁入自 cmdb_agent_core.py）。

职责：资产注册（get_asset_id_from_server）、心跳主循环（策略应用/凭据签发/
ca-bundle 原子写/升级指令触发）、周期上报线程（system_status/hardware/
software/network）、立即上报（trigger_immediate_report）、自愈退出。
"""
from __future__ import annotations

import json
import os
import platform
import threading
import time
import traceback
from urllib.parse import urljoin

import psutil
import requests

from console_utils import safe_console_print
from zvagent import __version__ as AGENT_VERSION
from zvagent import jobs as _jobs
from zvagent.auth import (
    _AGENT_TLS_CACHE_PATH,
    _agent_headers,
    _agent_requests_verify,
    _device_credentials,
    _log_agent_error,
    _platform_base,
    _save_device_credentials,
    clear_device_credentials,
    invalidate_tls_cache,
)
from zvagent.collectors.software import _software_payload_hash, collect_software_list
from zvagent.collectors.system import collect_hardware_info, collect_system_status, get_primary_network_info
from zvagent.config import CONFIG, SOFTWARE_CONFIG, _CONFIG_CANDIDATES
from zvagent.policy import _apply_agent_policies, _current_interval
from zvagent.state import _AGENT_STATE
from zvagent.upgrade import (
    _UPGRADE_STATE,
    _UPGRADE_STATE_PATH,
    _get_last_upgrade_state,
    _upgrade_backoff_remaining,
    perform_self_upgrade,
    upgrade_state_busy,
)

print = safe_console_print


def _persist_agent_token(token: str) -> None:
    """V1.9.51 根因补链：把刷新后的 Agent 令牌持久化到 config.local.json。

    upgrade.py 的升级任务令牌来源是 CONFIG["token"]（安装时写入的静态值），
    平台轮换后失效 → ZViewUpdater 下载 401。心跳 self-heal 刷新内存的同时
    落盘（json.dump 无 BOM UTF8，写临时文件后原子替换），重启后仍有效。
    落盘目标取配置加载的最高优先级候选（frozen = ProgramData config.local.json）。
    """
    try:
        config_path = _CONFIG_CANDIDATES[0]
        data: dict = {}
        if config_path.exists():
            try:
                data = json.loads(config_path.read_text(encoding="utf-8-sig"))
            except Exception:
                data = {}
        if not isinstance(data, dict):
            data = {}
        if data.get("token") == token:
            return
        data["token"] = token
        config_path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = config_path.with_name(config_path.name + ".tmp")
        with open(tmp_path, "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False, indent=2)
        os.replace(str(tmp_path), str(config_path))
    except Exception as exc:
        safe_console_print(f"[Software] agent token persist failed: {exc}")


def get_asset_id_from_server() -> int | None:
    ip, mac = get_primary_network_info()
    _AGENT_STATE["main_ip"] = ip
    _AGENT_STATE["main_mac"] = mac

    hardware = collect_hardware_info()
    payload = {
        "hostname": _AGENT_STATE["hostname"],
        "ip_address": ip,
        "mac_address": mac,
        "os_info": hardware.get("os_info", ""),
        "manufacturer": hardware.get("manufacturer", ""),
        "model": hardware.get("model", ""),
        "serial_number": hardware.get("serial_number", ""),
        "cpu_info": hardware.get("cpu_info", ""),
        "memory_total_mb": hardware.get("memory_total_mb", 0),
        "status": "online",
        "agent_version": AGENT_VERSION,
        "agent_install_status": "installed",
    }

    try:
        url = urljoin(_platform_base(), "/api/v1/agent/heartbeat")
        resp = requests.post(url, json=payload, headers=_agent_headers(), timeout=30, verify=_agent_requests_verify())
        data = resp.json()
        if resp.status_code in (200, 201) and isinstance(data, dict):
            asset_id = data.get("asset_id") or data.get("id")
            if asset_id:
                _AGENT_STATE["asset_id"] = int(asset_id)
                print(f"[Agent] Asset ID: {_AGENT_STATE['asset_id']}")
                return _AGENT_STATE["asset_id"]
        # P1-03：非 200 详情落盘 + 401 凭据失效自动回退全局 token 重试
        detail = f"register HTTP {resp.status_code}: {resp.text[:200]}"
        _log_agent_error(detail)
        if resp.status_code == 401 and _device_credentials().get("device_secret"):
            print("[Auth] zv1 credential rejected (401); clearing and retrying with global token")
            clear_device_credentials()
            resp = requests.post(url, json=payload, headers=_agent_headers(), timeout=30, verify=_agent_requests_verify())
            data = resp.json()
            if resp.status_code in (200, 201) and isinstance(data, dict):
                asset_id = data.get("asset_id") or data.get("id")
                # P1-03/1.7.3：全局重试时平台会重发设备凭据（allow_rotate），保存以恢复 zv1 通道
                credential_info = data.get("agent_credential")
                if isinstance(credential_info, dict) and credential_info.get("agent_id") and credential_info.get("device_secret"):
                    try:
                        _save_device_credentials(
                            int(credential_info["agent_id"]),
                            str(credential_info["device_secret"]),
                        )
                        _device_credentials._cache = {  # type: ignore[attr-defined]
                            "agent_id": int(credential_info["agent_id"]),
                            "device_secret": str(credential_info["device_secret"]),
                        }
                        print(f"[Auth] credential re-issued and enabled (agent_id={credential_info['agent_id']})")
                    except Exception as exc:
                        print(f"[Auth] credential re-save failed: {exc}")
                if asset_id:
                    _AGENT_STATE["asset_id"] = int(asset_id)
                    print(f"[Agent] Asset ID (global retry): {_AGENT_STATE['asset_id']}")
                    return _AGENT_STATE["asset_id"]
            _log_agent_error(f"global token retry HTTP {resp.status_code}")
    except Exception as exc:
        exc_str = str(exc).lower()
        if "ssl" in exc_str or "certificate" in exc_str or "tlsv" in exc_str:
            print("[Agent] SSL error during registration; invalidating TLS cache")
            invalidate_tls_cache()
        detail = f"register exception {type(exc).__name__}: {exc}"
        print(f"[Agent] 获取 asset_id 失败: {exc}")
        _log_agent_error(detail + "\n" + traceback.format_exc()[-800:])

    return None


# =============================================================================
# 心跳上报线程
# =============================================================================



# ============================================================
# Agent 自动升级（R13）+ 升级状态机（P0-06）
# 状态：CHECK → DOWNLOAD → VERIFY_HASH → VERIFY_SIGNATURE → BACKUP
#       → INSTALL(bat) → START → HEALTH_CHECK → COMMIT
def _check_heartbeat_self_heal(consecutive_failures: int) -> None:
    """P0-06：心跳连续失败达到阈值时退出进程，由服务重启 worker（自愈）。"""
    if consecutive_failures >= 10:
        print(f"[SelfHeal] heartbeat failed {consecutive_failures} consecutive times; exiting for worker restart")
        os._exit(1)


def _heartbeat_loop():
    consecutive_failures = 0  # P0-06：连续失败自愈计数
    print("[V199MARKER] heartbeat loop marker active")
    pending_job_results: list[dict] = []  # V1.8.3：任务结果暂存，随下次心跳上报
    while _AGENT_STATE["running"]:
        try:
            asset_id = _AGENT_STATE.get("asset_id")
            if not asset_id:
                # 尝试重新注册
                asset_id = get_asset_id_from_server()
                if not asset_id:
                    consecutive_failures += 1
                    _check_heartbeat_self_heal(consecutive_failures)
                    time.sleep(_current_interval("heartbeat", 30))
                    continue

            ip, mac = get_primary_network_info()
            status = collect_system_status()
            payload = {
                "asset_id": asset_id,
                "hostname": _AGENT_STATE["hostname"],
                "ip_address": ip,
                "mac_address": mac,
                "cpu_usage": status.get("cpu_percent", 0),
                "memory_usage": status.get("memory_percent", 0),
                "disk_usage": status.get("disk_percent", 0),
                # V1.9.21：逐盘明细（服务端 agent_heartbeat.disk_info 入库 + 详情接口已支持）
                "disk_info": status.get("disks", []),
                "process_count": len(psutil.pids()),
                "logged_users": os.getlogin() if hasattr(os, "getlogin") else "",
                "status": "online",
                "agent_version": AGENT_VERSION,
            }
            # P0-06 + V1.7.0：升级状态随心跳上报 —— 进行中报实时阶段，空闲报最近终态
            last_upgrade_state = None  # 防止 in_progress 分支跳过赋值后下方引用 NameError
            if _UPGRADE_STATE.get("in_progress"):
                payload["agent_upgrade_state"] = {
                    "stage": _UPGRADE_STATE.get("stage") or "RUNNING",
                    "server_upgrade_id": _UPGRADE_STATE.get("server_upgrade_id"),
                    "upgrade_id": _UPGRADE_STATE.get("upgrade_id"),
                    "from_version": AGENT_VERSION,
                    "to_version": _UPGRADE_STATE.get("to_version"),
                }
            else:
                last_upgrade_state = _get_last_upgrade_state()
                if last_upgrade_state:
                    payload["agent_upgrade_state"] = last_upgrade_state
            # V1.8.3：上轮执行的任务结果随本次心跳上报
            # V1.9.x deferred：长任务（补丁安装）完成结果并入上报
            pending_job_results.extend(_jobs.take_deferred_results())
            if pending_job_results:
                payload["job_results"] = pending_job_results
                pending_job_results = []  # 移交 payload，清空暂存避免重复

            url = urljoin(_platform_base(), "/api/v1/agent/heartbeat")
            resp = requests.post(url, json=payload, headers=_agent_headers(), timeout=30, verify=_agent_requests_verify())
            if resp.status_code in (200, 201):
                applied = None
                try:
                    body = resp.json()
                except Exception:
                    body = None
                if isinstance(body, dict):
                    applied = _apply_agent_policies(body.get("policies"))
                    # P1-软件仓库自愈：平台在心跳响应中下发当前 Agent 令牌，
                    # 配置漂移（安装时的旧 token vs 平台轮换后的新 token）时自动纠正，
                    # 软件任务轮询/下载通道即时恢复，无需人工改配置或重装。
                    # V1.9.51 根因补链：同步刷新 CONFIG["token"]（升级任务令牌来源）
                    # 并持久化到 config.local.json，否则平台轮换后 ZViewUpdater
                    # 用安装时的静态令牌下载 → 401 反复回滚。
                    try:
                        platform_agent_token = str(body.get("agent_token") or "").strip()
                        if (platform_agent_token
                                and (platform_agent_token != SOFTWARE_CONFIG.get("token")
                                     or platform_agent_token != CONFIG.get("token"))):
                            CONFIG["token"] = platform_agent_token
                            SOFTWARE_CONFIG["token"] = platform_agent_token
                            _persist_agent_token(platform_agent_token)
                            safe_console_print("[Software] agent token refreshed from heartbeat (memory + config.local.json)")
                    except Exception as exc:
                        safe_console_print(f"[Software] token refresh failed: {exc}")
                    # V1.8.3 通用任务通道：执行心跳下发的任务，结果随下次心跳上报
                    try:
                        pending_jobs = body.get("jobs")
                        safe_console_print(f"[V1910DBG] heartbeat body keys={sorted(body.keys())[:12]} "
                                           f"jobs={len(pending_jobs or [])}")
                        if isinstance(pending_jobs, list) and pending_jobs:
                            safe_console_print(f"[V1910DBG] executing {len(pending_jobs)} jobs now")
                            job_results = _jobs.execute_pending_jobs(pending_jobs)
                            safe_console_print(f"[V1910DBG] jobs executed, results={len(job_results)}")
                            if job_results:
                                pending_job_results.extend(job_results)
                    except Exception as exc:
                        safe_console_print(f"[V1910DBG] jobs dispatch EXCEPTION: {type(exc).__name__}: {exc}")
                    # 一机一密（P0-01）：保存平台签发的设备凭据，后续请求改用 zv1 token
                    credential_info = body.get("agent_credential")
                    if isinstance(credential_info, dict) and credential_info.get("agent_id") and credential_info.get("device_secret"):
                        try:
                            _save_device_credentials(
                                int(credential_info["agent_id"]),
                                str(credential_info["device_secret"]),
                            )
                            _device_credentials._cache = {  # type: ignore[attr-defined]
                                "agent_id": int(credential_info["agent_id"]),
                                "device_secret": str(credential_info["device_secret"]),
                            }
                            print(f"[Auth] 设备凭据已签发并启用 (agent_id={credential_info['agent_id']})")
                        except Exception as exc:
                            print(f"[Auth] 设备凭据启用失败: {exc}")
                    # P0-02：保存平台证书公钥（ca-bundle），TLS 校验用
                    ca_pem = body.get("agent_ca_bundle_pem")
                    if isinstance(ca_pem, str) and "BEGIN CERTIFICATE" in ca_pem:
                        try:
                            ca_path = _AGENT_TLS_CACHE_PATH.parent / "ca-bundle.pem"
                            ca_path.parent.mkdir(parents=True, exist_ok=True)
                            # V1.5 加固：原子写。2026-09-09 事故：非原子写被截断成 0 字节，
                            # TLS 心跳拿空 bundle 校验必然 SSLError(136)，且刷新依赖成功心跳，
                            # 形成死锁，终端掉线 19 小时。
                            ca_tmp = ca_path.with_suffix(".pem.tmp")
                            ca_tmp.write_text(ca_pem, encoding="utf-8")
                            os.replace(str(ca_tmp), str(ca_path))
                        except Exception as exc:
                            print(f"[TLS] ca-bundle 保存失败: {exc}")
                    # 自动升级（R13）：平台在心跳响应中携带 upgrade 指令
                    upgrade_info = body.get("upgrade")
                    if isinstance(upgrade_info, dict) and upgrade_info.get("version") and upgrade_info.get("sha256"):
                        _UPGRADE_STATE["package_type"] = str(upgrade_info.get("package_type") or "exe")
                        # V1.7.0：携带服务端事务 ID，贯穿状态文件与终态上报
                        _UPGRADE_STATE["server_upgrade_id"] = str(upgrade_info.get("upgrade_id") or "")
                        # V1.5 重试限制：目标版本处于退避期内则静默跳过（每小时最多提示一次）
                        backoff_remaining = _upgrade_backoff_remaining(str(upgrade_info["version"]))
                        if backoff_remaining > 0:
                            last_note = float(_UPGRADE_STATE.get("backoff_note_at") or 0)
                            if time.time() - last_note > 3600:
                                _UPGRADE_STATE["backoff_note_at"] = time.time()
                                print(f"[Upgrade] target {upgrade_info['version']} backoff {int(backoff_remaining)}s remaining; skip")
                        elif not _UPGRADE_STATE.get("in_progress"):
                            # V1.9.51 跨进程互斥：updater 独立进程运行期间
                            # （upgrade-state.json 处于未决阶段）跳过本次触发，
                            # 防止并发拉起第二个升级进程互踩。
                            if upgrade_state_busy():
                                print("[Upgrade] upgrade-state.json in-progress; skip this trigger")
                            else:
                                _UPGRADE_STATE["in_progress"] = True
                                try:
                                    threading.Thread(
                                        target=perform_self_upgrade,
                                        args=(upgrade_info["version"], upgrade_info["sha256"]),
                                        daemon=True,
                                        name="agent-self-upgrade",
                                    ).start()
                                except Exception as exc:
                                    print(f"[Upgrade] trigger failed: {exc}")
                                    _UPGRADE_STATE["in_progress"] = False
                consecutive_failures = 0
                print(f"[Heartbeat] OK (asset_id={asset_id})")
                # V1.6.0：心跳成功即写入健康标记，ZViewUpdater 健康检查据此判定 COMMIT
                try:
                    import cmdb_agent_layout as _layout_health
                    _layout_health.write_upgrade_health(AGENT_VERSION, True)
                except Exception:
                    pass
                if applied:
                    print(f"[Heartbeat] 平台策略已更新: {applied}")
                if last_upgrade_state and last_upgrade_state.get("stage") in ("COMMIT", "ROLLBACK", "FAILED"):
                    # 上报成功后标记 REPORTED，避免每轮重复上报
                    try:
                        last_upgrade_state["stage"] = "REPORTED"
                        _UPGRADE_STATE_PATH.write_text(
                            json.dumps(last_upgrade_state, ensure_ascii=False, indent=2), encoding="utf-8"
                        )
                    except Exception:
                        pass
            else:
                consecutive_failures += 1
                print(f"[Heartbeat] HTTP {resp.status_code}: {resp.text[:200]}")
        except Exception as exc:
            consecutive_failures += 1
            exc_str = str(exc).lower()
            if "ssl" in exc_str or "certificate" in exc_str or "tlsv" in exc_str:
                print("[Heartbeat] SSL error detected; invalidating TLS cache")
                invalidate_tls_cache()
            print(f"[Heartbeat] 错误: {exc}")

        # P0-06：连续失败自愈（10 次 ≈ 5 分钟）—— 退出由服务重启 worker
        if consecutive_failures >= 10:
            print(f"[SelfHeal] heartbeat failed {consecutive_failures} consecutive times; exiting for worker restart")
            os._exit(1)

        time.sleep(_current_interval("heartbeat", 30))


def _system_status_loop():
    """Compatibility no-op: heartbeat already carries the live metrics."""
    return


def _hardware_report_loop():
    """终端接入后立即上报一次完整硬件信息，之后按平台策略周期上报。

    复用 trigger_immediate_report 的心跳通道（服务端心跳接口会更新
    os/cpu/memory 等静态字段）；上报成功才计入周期，失败时每 60 秒重试。
    """
    last_run = 0.0
    while _AGENT_STATE["running"]:
        now = time.time()
        if now - last_run < _current_interval("hardware", 86400):
            time.sleep(60)
            continue

        result = trigger_immediate_report()
        if result.get("success"):
            print(f"[Hardware] 硬件信息上报成功 (asset_id={result.get('asset_id')})")
            last_run = now
        else:
            print(f"[Hardware] 硬件信息上报失败: {result.get('error')}")

        time.sleep(60)


_SOFTWARE_FULL_SYNC_INTERVAL = 6 * 3600  # 无变化时也定期全量上报，兜底数据一致性
_SOFTWARE_REPORT_STATE = {"hash": None, "last_full_sync": 0.0}


def _software_report_loop():
    while _AGENT_STATE["running"]:
        try:
            asset_id = _AGENT_STATE.get("asset_id")
            if not asset_id:
                time.sleep(_current_interval("software", 120))
                continue

            software = collect_software_list()
            payload_hash = _software_payload_hash(software)
            now = time.time()
            unchanged = payload_hash == _SOFTWARE_REPORT_STATE["hash"]
            full_sync_due = now - _SOFTWARE_REPORT_STATE["last_full_sync"] >= _SOFTWARE_FULL_SYNC_INTERVAL
            if unchanged and not full_sync_due:
                # 清单无变化：跳过上报（省 17KB+/次的传输与服务端整表重写）
                time.sleep(_current_interval("software", 120))
                continue

            ip, mac = get_primary_network_info()
            payload = {
                "asset_id": asset_id,
                "hostname": _AGENT_STATE["hostname"],
                "ip_address": ip,
                "mac_address": mac,
                "status": "online",
                "report_type": "software",
                "agent_version": AGENT_VERSION,
                "software_hash": payload_hash,
                "software_list": [
                    {
                        "name": item.get("name"),
                        "version": item.get("version"),
                        "vendor": item.get("publisher") or item.get("vendor"),
                        "install_date": item.get("install_date"),
                        # V1.9.21：键名修正——此前数值塞进 "size" 键（历史遗留），服务端
                        # 需做数值/字符串双兼容解析；现直接用语义正确的 "size_mb"
                        "size_mb": item.get("size_mb") or item.get("size"),
                    }
                    for item in software
                    if item.get("name")
                ],
            }

            url = urljoin(_platform_base(), "/api/v1/agent/heartbeat")
            resp = requests.post(url, json=payload, headers=_agent_headers(), timeout=60, verify=_agent_requests_verify())
            if resp.status_code in (200, 201):
                _SOFTWARE_REPORT_STATE["hash"] = payload_hash
                _SOFTWARE_REPORT_STATE["last_full_sync"] = now
                print(f"[Software] 上报 {len(software)} 个软件 ({'full-sync' if unchanged else 'changed'})")
            else:
                print(f"[Software] HTTP {resp.status_code}")
        except Exception as exc:
            print(f"[Software] 错误: {exc}")

        time.sleep(_current_interval("software", 120))


def _network_report_loop():
    """网络状态/流量/质量采集上报（复用心跳通道，独立低频线程，10s 默认）。

    采集→计算→上报分离：NetworkCollector 只产出快照，本循环仅负责上报；
    连续失败静默降级（打印前 3 次），不影响心跳与远控。
    """
    try:
        import network_monitor
    except Exception as exc:
        print(f"[Network] network_monitor 不可用，网络采集禁用: {exc}")
        return

    interval = _current_interval("network", 10)
    collector = network_monitor.NetworkCollector(interval=interval)
    consecutive_failures = 0

    while _AGENT_STATE["running"]:
        try:
            asset_id = _AGENT_STATE.get("asset_id")
            if not asset_id:
                time.sleep(5)
                continue

            snapshot = collector.snapshot()
            if snapshot:
                ip, mac = get_primary_network_info()
                payload = {
                    "asset_id": asset_id,
                    "hostname": _AGENT_STATE["hostname"],
                    "ip_address": ip,
                    "mac_address": mac,
                    "status": "online",
                    "report_type": "network",
                    "agent_version": AGENT_VERSION,
                    "network": network_monitor.snapshot_to_payload(snapshot),
                }
                url = urljoin(_platform_base(), "/api/v1/agent/heartbeat")
                resp = requests.post(
                    url, json=payload, headers=_agent_headers(), timeout=20, verify=_agent_requests_verify()
                )
                if resp.status_code in (200, 201):
                    consecutive_failures = 0
                else:
                    consecutive_failures += 1
                    if consecutive_failures <= 3:
                        print(f"[Network] HTTP {resp.status_code}: {resp.text[:120]}")
        except Exception as exc:
            consecutive_failures += 1
            if consecutive_failures <= 3:
                print(f"[Network] 采集上报错误: {exc}")

        time.sleep(_current_interval("network", 10))


# =============================================================================
# 软件管理 - 策略同步 + 任务轮询
# =============================================================================



def trigger_immediate_report(payload: dict | None = None) -> dict:
    """立即上报（硬件/系统状态快照）。

    V1.9.23 修复：通用任务通道以 handler(payload) 调用 report 任务，此前函数
    不接受参数导致每个 report 任务必然 TypeError 失败（任务通道上线以来
    report 类型从未成功过）。payload 为任务载荷，正常触发路径传 None。
    """
    asset_id = get_asset_id_from_server()
    if not asset_id:
        return {
            "success": False,
            "error": "Unable to register asset with platform",
        }

    ip_address, mac_address = get_primary_network_info()
    hardware = collect_hardware_info()
    system_status = collect_system_status()
    disk_total = sum(
        float(item.get("total_mb") or 0)
        for item in hardware.get("disk_info") or []
    )
    payload = {
        "asset_id": asset_id,
        "hostname": _AGENT_STATE["hostname"],
        "ip_address": ip_address,
        "mac_address": mac_address,
        "status": "online",
        "report_type": "triggered_report",
        "agent_version": AGENT_VERSION,
        "os_type": hardware.get("os_info") or platform.platform(),
        "os_version": hardware.get("os_version") or platform.version(),
        "cpu_cores": psutil.cpu_count(logical=False) or psutil.cpu_count() or 0,
        "memory_total": system_status.get("memory_total_mb") or hardware.get("memory_total_mb") or 0,
        # V1.9.22 修复：disk_info 的 total_mb 是 MB，此前直接求和上报被服务端按
        # GB 语义存入 assets.disk_gb → 资产档案显示"562863 GB"。现除以 1024 转 GB。
        "disk_total": round(disk_total / 1024, 1),
        "serial_number": hardware.get("serial_number") or "",
        "manufacturer": hardware.get("manufacturer") or "",
        "model": hardware.get("model") or "",
        # V1.9.34：GPU / 主板 / BIOS（P1-①）
        "gpu_name": hardware.get("gpu_name") or "",
        "gpu_memory_mb": hardware.get("gpu_memory_mb") or 0,
        "motherboard": hardware.get("motherboard") or "",
        "bios_vendor": hardware.get("bios_vendor") or "",
        "bios_version": hardware.get("bios_version") or "",
        "bios_date": hardware.get("bios_date") or "",
    }

    try:
        response = requests.post(
            urljoin(_platform_base(), "/api/v1/agent/heartbeat"),
            json=payload,
            headers=_agent_headers(),
            timeout=30,
            verify=_agent_requests_verify(),
        )
        response.raise_for_status()
        response_body = response.json() if response.content else {}
    except Exception as exc:
        return {
            "success": False,
            "asset_id": asset_id,
            "error": f"Immediate report failed: {type(exc).__name__}: {exc}",
        }

    return {
        "success": True,
        "asset_id": asset_id,
        "message": "Immediate report completed",
        "result": response_body,
    }


# V1.8.3 通用任务通道：注册 report handler（定义于本模块，注册须在其定义之后）
_jobs.register_job_handler("report", trigger_immediate_report)


