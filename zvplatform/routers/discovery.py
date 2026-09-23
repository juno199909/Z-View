# -*- coding: utf-8 -*-
"""终端发现路由（P1-01 从 assets_api 迁出）。"""
from __future__ import annotations

import ipaddress
import threading
from datetime import datetime

from fastapi import APIRouter, HTTPException, Query, Request
from mysql.connector import Error

from auth_utils import get_request_username
from zvplatform.constants import (
    AGENT_INSTALL_STATUS_NOT_INSTALLED,
    DISCOVERY_MAX_TARGETS,
)
from zvplatform.db import create_connection, format_datetime
from zvplatform.models import (
    DiscoveryImportRequest,
    DiscoveryPingRequest,
    DiscoverySNMPRequest,
)
from zvplatform.services.discovery_service import (
    SNMP_IMPORT_ERROR,
    create_discovery_task,
    expand_discovery_targets,
    get_discovery_task,
    list_discovery_tasks,
    run_ping_discovery_task,
    run_snmp_discovery_task,
    serialize_discovery_task,
    update_discovery_task,
)


router = APIRouter(prefix="/api/v1/discovery", tags=["discovery"])


@router.get("/recent")
def get_recent_discovery_scans(limit: int = Query(default=20, ge=1, le=100)):
    """获取最近扫描记录摘要"""
    tasks = list_discovery_tasks()
    scans = [
        {
            "task_id": task.get("task_id"),
            "created_at": task.get("created_at") or format_datetime(task.get("started_at")),
            "scan_type": task.get("type"),
            "ip_ranges": task.get("target"),
            "total": int(task.get("total") or 0),
            "online": int(task.get("found") or 0),
            "failed": int(task.get("failed") or 0),
            "status": "success" if task.get("status") == "completed" else "failed",
            "raw_status": task.get("status"),
        }
        for task in tasks
    ]
    scans.sort(key=lambda item: item.get("created_at") or "", reverse=True)
    return {"data": scans[:limit], "total": len(scans)}


@router.post("/import")
def import_discovered_asset(payload: DiscoveryImportRequest, request: Request):
    """将扫描发现的主机导入为资产"""
    ip_address = (payload.ip_address or "").strip()
    if not ip_address:
        raise HTTPException(status_code=400, detail="ip_address is required")
    try:
        ipaddress.ip_address(ip_address)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Invalid IP address: {ip_address}")

    conn = create_connection()
    if not conn:
        raise HTTPException(status_code=500, detail="Database connection failed")

    cursor = None
    try:
        cursor = conn.cursor(dictionary=True)
        operator_name = get_request_username(request, fallback="console")

        cursor.execute(
            """
            SELECT id FROM assets
            WHERE ip_address = %s AND deleted_at IS NULL
            LIMIT 1
            """,
            (ip_address,),
        )
        existing = cursor.fetchone()
        if existing:
            return {
                "already_exists": True,
                "id": existing["id"],
                "message": "Asset with this IP already exists",
            }

        cursor.execute(
            """
            INSERT INTO assets (
                asset_type, hostname, ip_address, mac_address, manufacturer,
                status, agent_install_status, asset_status, notes,
                created_at, updated_at
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, NOW(), NOW())
            """,
            (
                payload.device_type or "unknown",
                payload.hostname or ip_address,
                ip_address,
                payload.mac_address,
                payload.vendor,
                "unknown",
                AGENT_INSTALL_STATUS_NOT_INSTALLED,
                "in_stock",
                "Imported from network discovery",
            ),
        )
        asset_id = cursor.lastrowid
        conn.commit()

        return {
            "already_exists": False,
            "id": asset_id,
            "message": "Asset imported",
        }
    except Error as e:
        conn.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        if cursor:
            cursor.close()
        conn.close()


@router.get("/tasks")
def get_discovery_tasks():
    """获取资产发现任务列表"""
    tasks = list_discovery_tasks()
    return {"data": tasks, "total": len(tasks)}


@router.get("/{task_id}")
def get_discovery_task_detail(task_id: str):
    """获取资产发现任务详情"""
    task = get_discovery_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Discovery task not found")
    return serialize_discovery_task(task)


@router.post("/{task_id}/cancel")
def cancel_discovery_task(task_id: str):
    """取消资产发现任务"""
    task = get_discovery_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Discovery task not found")
    if task["status"] in {"completed", "failed", "cancelled"}:
        return {"message": "Task already finished", "task_id": task_id, "status": task["status"]}

    update_discovery_task(task_id, cancel_requested=True, status="cancelled", completed_at=datetime.now())
    return {"message": "Task cancellation requested", "task_id": task_id, "status": "cancelled"}


@router.post("/ping")
def start_ping_discovery(request: DiscoveryPingRequest):
    """启动 Ping 扫描任务"""
    targets = expand_discovery_targets(request.ip_ranges)
    task = create_discovery_task(
        "ping",
        ", ".join(request.ip_ranges[:3]) + (" ..." if len(request.ip_ranges) > 3 else ""),
        len(targets),
        {
            "concurrency": min(request.concurrency, 256),
            "timeout": request.timeout,
        },
    )

    worker = threading.Thread(
        target=run_ping_discovery_task,
        args=(task["task_id"], targets, request.concurrency, request.timeout),
        daemon=True,
        name=f"ping-discovery-{task['task_id']}",
    )
    worker.start()

    return {
        "message": "Ping discovery task started",
        "task_id": task["task_id"],
        "total_ips": len(targets),
        "status": "pending",
    }


@router.post("/snmp")
def start_snmp_discovery(request: DiscoverySNMPRequest):
    """启动 SNMP 采集任务"""
    if not request.targets:
        raise HTTPException(status_code=400, detail="SNMP targets are required")
    if len(request.targets) > DISCOVERY_MAX_TARGETS:
        raise HTTPException(status_code=400, detail=f"SNMP targets exceed limit {DISCOVERY_MAX_TARGETS}")
    if request.version not in (1, 2):
        raise HTTPException(status_code=400, detail="SNMP version must be 1 or 2")

    target_ips = [target.ip for target in request.targets]
    expand_discovery_targets(target_ips, max_targets=DISCOVERY_MAX_TARGETS)

    task = create_discovery_task(
        "snmp",
        ", ".join(target_ips[:3]) + (" ..." if len(target_ips) > 3 else ""),
        len(request.targets),
        {
            "version": request.version,
            "timeout": request.timeout,
        },
    )

    worker = threading.Thread(
        target=run_snmp_discovery_task,
        args=(task["task_id"], request.targets, request.version, request.timeout),
        daemon=True,
        name=f"snmp-discovery-{task['task_id']}",
    )
    worker.start()

    return {
        "message": "SNMP discovery task started",
        "task_id": task["task_id"],
        "total_targets": len(request.targets),
        "status": "pending",
        "snmp_available": SNMP_IMPORT_ERROR is None,
        "snmp_runtime_error": SNMP_IMPORT_ERROR,
    }
