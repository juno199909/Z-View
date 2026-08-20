# -*- coding: utf-8 -*-
"""终端发现服务（P1-01 从 assets_api 迁出）。"""
from __future__ import annotations

import ipaddress
import json
import os
import socket
import subprocess
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED
from datetime import datetime
from typing import Any, Dict, List, Optional

from fastapi import HTTPException
from mysql.connector import Error

from console_utils import safe_console_print
from zvplatform.constants import AGENT_INSTALL_STATUS_NOT_INSTALLED
from zvplatform.settings import get_settings
from zvplatform.db import create_connection, format_datetime
from zvplatform.models import DiscoverySNMPTarget

SNMP_IMPORT_ERROR = None
SNMP_API_MODE = None
try:
    import asyncio

    from pysnmp.hlapi.asyncio import (
        CommunityData,
        ContextData,
        ObjectIdentity,
        ObjectType,
        SnmpEngine,
        UdpTransportTarget,
        getCmd,
    )
    SNMP_API_MODE = "asyncio"
except ImportError:
    try:
        from pysnmp.hlapi import (
            CommunityData,
            ContextData,
            ObjectIdentity,
            ObjectType,
            SnmpEngine,
            UdpTransportTarget,
            getCmd,
        )
        SNMP_API_MODE = "sync"
    except Exception as snmp_import_error:
        SNMP_IMPORT_ERROR = str(snmp_import_error)
except Exception as snmp_import_error:
    SNMP_IMPORT_ERROR = str(snmp_import_error)

from zvplatform.settings import get_settings
_settings = get_settings()

DISCOVERY_TASKS: Dict[str, Dict[str, Any]] = {}
DISCOVERY_TASK_LOCK = threading.Lock()
PING_CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def discovery_duration_text(started_at: Optional[datetime], completed_at: Optional[datetime] = None) -> str:
    if not started_at:
        return "-"
    end_time = completed_at or datetime.now()
    elapsed = max(0, int((end_time - started_at).total_seconds()))
    minutes, seconds = divmod(elapsed, 60)
    hours, minutes = divmod(minutes, 60)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}"


def cleanup_discovery_tasks_locked():
    now_ts = time.time()
    removable = [
        task_id
        for task_id, task in DISCOVERY_TASKS.items()
        if task.get("completed_at_ts")
        and now_ts - float(task["completed_at_ts"]) > DISCOVERY_TASK_RETENTION_SECONDS
    ]
    for task_id in removable:
        DISCOVERY_TASKS.pop(task_id, None)

    if len(DISCOVERY_TASKS) <= DISCOVERY_MAX_TASKS:
        return

    ordered_tasks = sorted(
        DISCOVERY_TASKS.items(),
        key=lambda item: item[1].get("created_at_ts", 0),
    )
    excess = len(DISCOVERY_TASKS) - DISCOVERY_MAX_TASKS
    for task_id, _ in ordered_tasks[:excess]:
        DISCOVERY_TASKS.pop(task_id, None)


def serialize_discovery_task(task: Dict[str, Any]) -> Dict[str, Any]:
    total = int(task.get("total") or 0)
    current = int(task.get("current") or 0)
    progress = int(round((current / total) * 100)) if total > 0 else 0

    return {
        "task_id": task["task_id"],
        "type": task["type"],
        "target": task["target"],
        "progress": progress,
        "current": current,
        "total": total,
        "status": task["status"],
        "found": int(task.get("found") or 0),
        "failed": int(task.get("failed") or 0),
        "found_ips": list(task.get("found_ips") or []),
        "failed_targets": list(task.get("failed_targets") or []),
        "created_at": format_datetime(task.get("created_at")),
        "started_at": format_datetime(task.get("started_at")),
        "completed_at": format_datetime(task.get("completed_at")),
        "duration": discovery_duration_text(task.get("started_at"), task.get("completed_at")),
        "error": task.get("error"),
        "metadata": task.get("metadata") or {},
        "cancel_requested": bool(task.get("cancel_requested")),
    }


def create_discovery_task(task_type: str, target: str, total: int, metadata: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    now = datetime.now()
    task = {
        "task_id": f"discovery-{task_type}-{uuid.uuid4().hex[:16]}",
        "type": task_type,
        "target": target,
        "total": total,
        "current": 0,
        "found": 0,
        "failed": 0,
        "found_ips": [],
        "failed_targets": [],
        "status": "pending",
        "error": None,
        "cancel_requested": False,
        "metadata": metadata or {},
        "created_at": now,
        "created_at_ts": time.time(),
        "started_at": None,
        "completed_at": None,
        "completed_at_ts": None,
    }
    with DISCOVERY_TASK_LOCK:
        cleanup_discovery_tasks_locked()
        DISCOVERY_TASKS[task["task_id"]] = task
    return task


def update_discovery_task(task_id: str, **updates):
    with DISCOVERY_TASK_LOCK:
        task = DISCOVERY_TASKS.get(task_id)
        if not task:
            return
        task.update(updates)
        if task.get("completed_at") and not task.get("completed_at_ts"):
            task["completed_at_ts"] = time.time()


def append_discovery_failure(task: Dict[str, Any], result: Dict[str, Any], fallback_error: str) -> Dict[str, Any]:
    failed_targets = list(task.get("failed_targets") or [])
    failed_targets.append({
        "ip": result.get("ip"),
        "error": str(result.get("error") or fallback_error),
    })
    return {
        "failed": int(task.get("failed") or 0) + 1,
        "failed_targets": failed_targets[-200:],
    }


def mark_discovery_task_finished(task_id: str, status: str, error: Optional[str] = None):
    update_discovery_task(
        task_id,
        status=status,
        error=error,
        completed_at=datetime.now(),
        completed_at_ts=time.time(),
    )


def get_discovery_task(task_id: str) -> Optional[Dict[str, Any]]:
    with DISCOVERY_TASK_LOCK:
        cleanup_discovery_tasks_locked()
        task = DISCOVERY_TASKS.get(task_id)
        return dict(task) if task else None


def list_discovery_tasks() -> List[Dict[str, Any]]:
    with DISCOVERY_TASK_LOCK:
        cleanup_discovery_tasks_locked()
        ordered = sorted(
            DISCOVERY_TASKS.values(),
            key=lambda item: item.get("created_at_ts", 0),
            reverse=True,
        )
        return [serialize_discovery_task(dict(task)) for task in ordered]


def expand_discovery_targets(raw_items: List[str], max_targets: int = _settings.discovery.max_targets) -> List[str]:
    targets: List[str] = []
    seen = set()

    def add_ip(ip_text: str):
        try:
            normalized = str(ipaddress.ip_address(ip_text.strip()))
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=f"Invalid IP address: {ip_text}") from exc
        if normalized not in seen:
            seen.add(normalized)
            targets.append(normalized)
        if len(targets) > max_targets:
            raise HTTPException(status_code=400, detail=f"Discovery targets exceed limit {max_targets}")

    for raw in raw_items:
        for fragment in str(raw or "").replace("；", ",").replace("\n", ",").split(","):
            item = fragment.strip()
            if not item:
                continue

            if "/" in item:
                try:
                    network = ipaddress.ip_network(item, strict=False)
                except ValueError as exc:
                    raise HTTPException(status_code=400, detail=f"Invalid CIDR: {item}") from exc
                host_iter = list(network.hosts()) if network.num_addresses > 1 else [network.network_address]
                for address in host_iter:
                    add_ip(str(address))
                continue

            if "-" in item:
                start_text, end_text = [part.strip() for part in item.split("-", 1)]
                try:
                    start_ip = ipaddress.ip_address(start_text)
                    end_ip = ipaddress.ip_address(end_text)
                except ValueError as exc:
                    raise HTTPException(status_code=400, detail=f"Invalid IP range: {item}") from exc
                if start_ip.version != end_ip.version or int(start_ip) > int(end_ip):
                    raise HTTPException(status_code=400, detail=f"Invalid IP range: {item}")
                for value in range(int(start_ip), int(end_ip) + 1):
                    add_ip(str(ipaddress.ip_address(value)))
                continue

            add_ip(item)

    if not targets:
        raise HTTPException(status_code=400, detail="No valid discovery targets found")
    return targets


def detect_asset_type_from_text(raw_text: Optional[str]) -> str:
    text = (raw_text or "").lower()
    if not text:
        return "unknown"
    if any(keyword in text for keyword in ("switch", "catalyst", "s5700", "s5735", "h3c", "ruijie")):
        return "switch"
    if any(keyword in text for keyword in ("router", "route", "isr", "asr")):
        return "router"
    if any(keyword in text for keyword in ("server", "windows server", "linux", "ubuntu", "centos", "vmware")):
        return "server"
    if any(keyword in text for keyword in ("desktop", "workstation", "windows 10", "windows 11", "pc")):
        return "pc"
    return "unknown"


def detect_vendor_from_text(raw_text: Optional[str]) -> Optional[str]:
    text = (raw_text or "").lower()
    mapping = {
        "huawei": "Huawei",
        "cisco": "Cisco",
        "h3c": "H3C",
        "hp": "HP",
        "hewlett-packard": "HP",
        "dell": "Dell",
        "lenovo": "Lenovo",
        "ruijie": "Ruijie",
        "vmware": "VMware",
        "microsoft": "Microsoft",
    }
    for keyword, vendor in mapping.items():
        if keyword in text:
            return vendor
    return None


def resolve_hostname_for_ip(ip_address_text: str) -> Optional[str]:
    try:
        hostname, _, _ = socket.gethostbyaddr(ip_address_text)
        return hostname
    except Exception:
        return None


def upsert_discovered_asset(discovered: Dict[str, Any]) -> Optional[int]:
    conn = create_connection()
    if not conn:
        return None

    cursor = conn.cursor(dictionary=True)
    try:
        ip_address_text = str(discovered.get("ip_address") or "").strip()
        if not ip_address_text:
            return None

        hostname = str(discovered.get("hostname") or "").strip() or ip_address_text
        mac_address = str(discovered.get("mac_address") or "").strip() or None
        asset_type = str(discovered.get("asset_type") or "unknown").strip() or "unknown"
        manufacturer = str(discovered.get("manufacturer") or "").strip() or None
        model = str(discovered.get("model") or "").strip() or None
        os_type = str(discovered.get("os_type") or "").strip() or None
        os_version = str(discovered.get("os_version") or "").strip() or None
        serial_number = str(discovered.get("serial_number") or "").strip() or None

        if mac_address:
            cursor.execute("""
                SELECT id FROM assets
                WHERE deleted_at IS NULL
                  AND (ip_address = %s OR mac_address = %s)
                LIMIT 1
            """, (ip_address_text, mac_address))
        else:
            cursor.execute("""
                SELECT id FROM assets
                WHERE deleted_at IS NULL
                  AND ip_address = %s
                LIMIT 1
            """, (ip_address_text,))
        existing = cursor.fetchone()

        if existing:
            asset_id = int(existing["id"])
            update_fields = [
                "hostname = %s",
                "ip_address = %s",
                "status = 'online'",
                "last_seen = NOW()",
                "updated_at = NOW()",
            ]
            values: List[Any] = [hostname, ip_address_text]

            if mac_address:
                update_fields.append("mac_address = %s")
                values.append(mac_address)
            if asset_type:
                update_fields.append("asset_type = %s")
                values.append(asset_type)
            if manufacturer:
                update_fields.append("manufacturer = %s")
                values.append(manufacturer)
            if model:
                update_fields.append("model = %s")
                values.append(model)
            if os_type:
                update_fields.append("os_type = %s")
                values.append(os_type)
            if os_version:
                update_fields.append("os_version = %s")
                values.append(os_version)
            if serial_number:
                update_fields.append("serial_number = %s")
                values.append(serial_number)

            values.append(asset_id)
            cursor.execute(
                f"UPDATE assets SET {', '.join(update_fields)} WHERE id = %s",
                values,
            )
        else:
            cursor.execute("""
                INSERT INTO assets (
                    asset_type, hostname, ip_address, mac_address,
                    serial_number, manufacturer, model, os_type, os_version,
                    status, agent_install_status, last_seen, created_at, updated_at
                ) VALUES (
                    %s, %s, %s, %s,
                    %s, %s, %s, %s, %s,
                    'online', %s, NOW(), NOW(), NOW()
                )
            """, (
                asset_type,
                hostname,
                ip_address_text,
                mac_address,
                serial_number,
                manufacturer,
                model,
                os_type,
                os_version,
                AGENT_INSTALL_STATUS_NOT_INSTALLED,
            ))
            asset_id = int(cursor.lastrowid)

        conn.commit()
        return asset_id
    except Error as exc:
        conn.rollback()
        safe_console_print(f"[Discovery] Asset upsert failed for {discovered.get('ip_address')}: {exc}")
        return None
    finally:
        cursor.close()
        conn.close()


def ping_host(ip_address_text: str, timeout_ms: int) -> Dict[str, Any]:
    command = ["ping", "-n", "1", "-w", str(timeout_ms), ip_address_text]
    timeout_seconds = max(3, int(timeout_ms / 1000) + 2)
    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="ignore",
            timeout=timeout_seconds,
            creationflags=PING_CREATE_NO_WINDOW,
        )
        alive = result.returncode == 0
        hostname = resolve_hostname_for_ip(ip_address_text) if alive else None
        return {
            "ip": ip_address_text,
            "alive": alive,
            "hostname": hostname,
            "stdout": result.stdout,
            "stderr": result.stderr,
        }
    except subprocess.TimeoutExpired:
        return {"ip": ip_address_text, "alive": False, "error": "timeout"}
    except Exception as exc:
        return {"ip": ip_address_text, "alive": False, "error": str(exc)}


def snmp_collect_target(ip_address_text: str, community: str, version: int, timeout_seconds: int) -> Dict[str, Any]:
    if SNMP_IMPORT_ERROR:
        return {
            "ip": ip_address_text,
            "success": False,
            "error": f"SNMP runtime unavailable: {SNMP_IMPORT_ERROR}",
        }

    if version not in (1, 2):
        return {"ip": ip_address_text, "success": False, "error": f"Unsupported SNMP version: {version}"}

    try:
        if SNMP_API_MODE == "asyncio":
            async def run_async_query():
                transport = await UdpTransportTarget.create(
                    (ip_address_text, 161),
                    timeout=timeout_seconds,
                    retries=0,
                )
                return await getCmd(
                    SnmpEngine(),
                    CommunityData(community, mpModel=0 if version == 1 else 1),
                    transport,
                    ContextData(),
                    ObjectType(ObjectIdentity("1.3.6.1.2.1.1.1.0")),
                    ObjectType(ObjectIdentity("1.3.6.1.2.1.1.5.0")),
                    ObjectType(ObjectIdentity("1.3.6.1.2.1.1.2.0")),
                )

            error_indication, error_status, _, var_binds = asyncio.run(run_async_query())
        else:
            iterator = getCmd(
                SnmpEngine(),
                CommunityData(community, mpModel=0 if version == 1 else 1),
                UdpTransportTarget((ip_address_text, 161), timeout=timeout_seconds, retries=0),
                ContextData(),
                ObjectType(ObjectIdentity("1.3.6.1.2.1.1.1.0")),
                ObjectType(ObjectIdentity("1.3.6.1.2.1.1.5.0")),
                ObjectType(ObjectIdentity("1.3.6.1.2.1.1.2.0")),
            )
            error_indication, error_status, _, var_binds = next(iterator)
        if error_indication:
            return {"ip": ip_address_text, "success": False, "error": str(error_indication)}
        if error_status:
            return {"ip": ip_address_text, "success": False, "error": str(error_status)}

        values = [str(binding[1]) for binding in var_binds]
        sys_descr = values[0] if len(values) > 0 else ""
        sys_name = values[1] if len(values) > 1 else ""
        sys_object_id = values[2] if len(values) > 2 else ""
        hostname = sys_name.strip() or resolve_hostname_for_ip(ip_address_text) or ip_address_text
        manufacturer = detect_vendor_from_text(sys_descr)
        asset_type = detect_asset_type_from_text(sys_descr)

        return {
            "ip": ip_address_text,
            "success": True,
            "hostname": hostname,
            "manufacturer": manufacturer,
            "model": sys_descr[:255] if sys_descr else None,
            "asset_type": asset_type,
            "snmp": {
                "sys_descr": sys_descr,
                "sys_name": sys_name,
                "sys_object_id": sys_object_id,
            },
        }
    except StopIteration:
        return {"ip": ip_address_text, "success": False, "error": "No SNMP response"}
    except Exception as exc:
        return {"ip": ip_address_text, "success": False, "error": str(exc)}


def run_ping_discovery_task(task_id: str, targets: List[str], concurrency: int, timeout_ms: int):
    update_discovery_task(task_id, status="running", started_at=datetime.now())
    futures = {}

    try:
        max_workers = max(1, min(concurrency, 256))
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            for ip_address_text in targets:
                futures[executor.submit(ping_host, ip_address_text, timeout_ms)] = ip_address_text

            pending = set(futures.keys())
            while pending:
                task = get_discovery_task(task_id)
                if not task:
                    return
                if task.get("cancel_requested"):
                    mark_discovery_task_finished(task_id, "cancelled")
                    return

                done, pending = wait(pending, timeout=0.2, return_when=FIRST_COMPLETED)
                if not done:
                    continue

                for future in done:
                    result = future.result()
                    task = get_discovery_task(task_id)
                    if not task:
                        return

                    current = int(task.get("current") or 0) + 1
                    found = int(task.get("found") or 0)
                    found_ips = list(task.get("found_ips") or [])
                    failed = int(task.get("failed") or 0)
                    failed_targets = list(task.get("failed_targets") or [])

                    if result.get("alive"):
                        found += 1
                        found_ips.append(result["ip"])
                        upsert_discovered_asset({
                            "asset_type": "unknown",
                            "hostname": result.get("hostname") or result["ip"],
                            "ip_address": result["ip"],
                        })
                    else:
                        failure_update = append_discovery_failure(
                            task,
                            result,
                            "Host unreachable or ping timeout",
                        )
                        failed = failure_update["failed"]
                        failed_targets = failure_update["failed_targets"]

                    update_discovery_task(
                        task_id,
                        current=current,
                        found=found,
                        failed=failed,
                        found_ips=found_ips,
                        failed_targets=failed_targets,
                    )

        mark_discovery_task_finished(task_id, "completed")
    except Exception as exc:
        safe_console_print(f"[Discovery] Ping task failed {task_id}: {exc}")
        mark_discovery_task_finished(task_id, "failed", str(exc))


def run_snmp_discovery_task(task_id: str, targets: List[DiscoverySNMPTarget], version: int, timeout_seconds: int):
    update_discovery_task(task_id, status="running", started_at=datetime.now())
    futures = {}

    try:
        max_workers = max(1, min(len(targets), 64))
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            for target in targets:
                futures[
                    executor.submit(
                        snmp_collect_target,
                        target.ip,
                        target.community,
                        version,
                        timeout_seconds,
                    )
                ] = target.ip

            pending = set(futures.keys())
            while pending:
                task = get_discovery_task(task_id)
                if not task:
                    return
                if task.get("cancel_requested"):
                    mark_discovery_task_finished(task_id, "cancelled")
                    return

                done, pending = wait(pending, timeout=0.2, return_when=FIRST_COMPLETED)
                if not done:
                    continue

                for future in done:
                    result = future.result()
                    task = get_discovery_task(task_id)
                    if not task:
                        return

                    current = int(task.get("current") or 0) + 1
                    found = int(task.get("found") or 0)
                    found_ips = list(task.get("found_ips") or [])
                    failed = int(task.get("failed") or 0)
                    failed_targets = list(task.get("failed_targets") or [])

                    if result.get("success"):
                        found += 1
                        found_ips.append(result["ip"])
                        upsert_discovered_asset({
                            "asset_type": result.get("asset_type") or "unknown",
                            "hostname": result.get("hostname") or result["ip"],
                            "ip_address": result["ip"],
                            "manufacturer": result.get("manufacturer"),
                            "model": result.get("model"),
                        })
                    else:
                        failure_update = append_discovery_failure(
                            task,
                            result,
                            "No SNMP response received",
                        )
                        failed = failure_update["failed"]
                        failed_targets = failure_update["failed_targets"]

                    update_discovery_task(
                        task_id,
                        current=current,
                        found=found,
                        failed=failed,
                        found_ips=found_ips,
                        failed_targets=failed_targets,
                    )

        task = get_discovery_task(task_id)
        if task and task.get("found") == 0 and SNMP_IMPORT_ERROR:
            mark_discovery_task_finished(task_id, "failed", f"SNMP runtime unavailable: {SNMP_IMPORT_ERROR}")
            return
        mark_discovery_task_finished(task_id, "completed")
    except Exception as exc:
        safe_console_print(f"[Discovery] SNMP task failed {task_id}: {exc}")
        mark_discovery_task_finished(task_id, "failed", str(exc))


# ============================================================
# 数据模型
# ============================================================
