# -*- coding: utf-8 -*-
"""Agent 心跳路由（P1-01 收官：heartbeat 本体从 assets_api 迁出）。

保留域依赖（设备凭据/资产识别 helpers 仍在 assets_api）通过函数内
运行时导入；其余依赖均为 zvplatform/auth_utils 直连。
"""
from __future__ import annotations

from datetime import datetime
import json
import os
import time
import traceback

from fastapi import APIRouter, HTTPException, Request
from mysql.connector import Error

from agent_upgrade_api import get_latest_upgrade, record_agent_version
from auth_utils import normalize_actor_name, require_agent_request
from console_utils import safe_console_print
from zvplatform.db import format_datetime
from zvplatform.models import SystemActivityLogCreate
from zvplatform.repositories.log_repository import insert_system_activity_log
from zvplatform.constants import (
    AGENT_INSTALL_STATUS_INSTALLED,
    AGENT_INSTALL_STATUS_NOT_INSTALLED,
)
from zvplatform.services.agent_policy_service import load_agent_policies


router = APIRouter(tags=["agent-heartbeat"])

# V1.7.0 服务端升级熔断（内存态）：asset_id -> {to_version, count, last_at, until}
_UPGRADE_FAILURE_BREAKER = {}
_UPGRADE_BREAKER_THRESHOLD = 3
_UPGRADE_BREAKER_COOLDOWN_SECONDS = 1800


@router.post("/api/v1/agent/heartbeat")
def agent_heartbeat(data: dict, request: Request):
    """接收Agent上报的心跳数据"""
    agent_auth = require_agent_request(request)
    agent_auth_type = str(agent_auth.get("agent_auth_type") or "global")
    authenticated_agent_id = agent_auth.get("agent_id")

    # 设备凭据绑定校验：zv1 凭据只能上报自己的资产
    import assets_api  # noqa: F401  (__file__ 锚定)
    from assets_api import (
        _agent_version_tuple,
        _issue_agent_device_credential,
        ensure_agent_credentials_table,
        fetch_asset_row,
        get_db_connection,
        record_asset_changes,
    )
    conn = get_db_connection()
    if not conn:
        raise HTTPException(status_code=500, detail="Database connection failed")

    try:
        cursor = conn.cursor(dictionary=True)
        before_asset_state = None

        def normalize_text(value, invalid_values=None):
            if value is None:
                return None
            value = str(value).strip()
            if not value:
                return None
            invalid_set = {
                "unknown",
                "n/a",
                "none",
                "null",
                "-",
                "default string",
                "to be filled by o.e.m.",
                "system product name",
                "system manufacturer",
                "system serial number",
            }
            if invalid_values:
                invalid_set.update({str(item).strip().lower() for item in invalid_values if str(item).strip()})
            if value.lower() in invalid_set:
                return None
            return value

        def normalize_positive_int(value):
            try:
                normalized = int(value)
            except (TypeError, ValueError):
                return None
            return normalized if normalized > 0 else None

        def normalize_dns_servers(value):
            if value is None:
                return None
            if isinstance(value, str):
                stripped = value.strip()
                if not stripped:
                    return None
                if stripped.startswith("["):
                    try:
                        decoded = json.loads(stripped)
                    except Exception:
                        decoded = None
                    if isinstance(decoded, list):
                        value = decoded
                    elif isinstance(decoded, str):
                        value = [decoded]
                    else:
                        value = [stripped]
                else:
                    value = [stripped]
            if isinstance(value, list):
                normalized = [str(item).strip() for item in value if normalize_text(item)]
                return normalized or None
            text_value = normalize_text(value)
            return [text_value] if text_value else None

        def build_asset_identifier_clauses(hostname_value, ip_value, mac_value, serial_value):
            clauses = []
            params = []

            if mac_value:
                clauses.append("mac_address = %s")
                params.append(mac_value)
            if serial_value:
                clauses.append("serial_number = %s")
                params.append(serial_value)
            if hostname_value:
                clauses.append("hostname = %s")
                params.append(hostname_value)
            if ip_value:
                clauses.append("ip_address = %s")
                params.append(ip_value)

            if not clauses and hostname_value and ip_value:
                clauses.append("(hostname = %s AND ip_address = %s)")
                params.extend([hostname_value, ip_value])

            return clauses, params

        def calculate_asset_match_score(asset_row, hostname_value, ip_value, mac_value, serial_value):
            score = 0
            if mac_value and asset_row.get("mac_address") == mac_value:
                score += 100
            if serial_value and asset_row.get("serial_number") == serial_value:
                score += 80
            if hostname_value and asset_row.get("hostname") == hostname_value:
                score += 20
            if ip_value and asset_row.get("ip_address") == ip_value:
                score += 10

            metadata_fields = (
                "os_type",
                "os_version",
                "cpu_cores",
                "memory_mb",
                "disk_gb",
                "serial_number",
                "manufacturer",
                "model",
                "gateway",
                "dns_servers",
            )
            for field_name in metadata_fields:
                field_value = asset_row.get(field_name)
                if field_name in {"cpu_cores", "memory_mb", "disk_gb"}:
                    if normalize_positive_int(field_value) is not None:
                        score += 1
                elif field_name == "dns_servers":
                    if normalize_dns_servers(field_value):
                        score += 1
                elif normalize_text(field_value) is not None:
                    score += 1

            return score

        def select_best_asset_candidate(rows, hostname_value, ip_value, mac_value, serial_value):
            if not rows:
                return None
            return max(
                rows,
                key=lambda row: (
                    calculate_asset_match_score(row, hostname_value, ip_value, mac_value, serial_value),
                    row.get("last_seen") or datetime.min,
                    row.get("updated_at") or datetime.min,
                    -(row.get("id") or 0),
                ),
            )

        def load_matching_asset_candidates(include_deleted, exclude_asset_id=None):
            if not identifier_clauses:
                return []

            delete_clause = "deleted_at IS NOT NULL" if include_deleted else "deleted_at IS NULL"
            query = f"""
                SELECT id, hostname, ip_address, mac_address, serial_number,
                       os_type, os_version, cpu_cores, memory_mb, disk_gb,
                       manufacturer, model, gateway, dns_servers,
                       last_seen, updated_at, deleted_at
                FROM assets
                WHERE {delete_clause}
                  AND ({' OR '.join(identifier_clauses)})
            """
            params = list(identifier_params)
            if exclude_asset_id is not None:
                query += " AND id <> %s"
                params.append(exclude_asset_id)

            cursor.execute(query, tuple(params))
            return cursor.fetchall()

        def build_missing_field_backfill(target_row, report_values, donor_row):
            field_resolvers = {
                "os_type": normalize_text,
                "os_version": normalize_text,
                "cpu_cores": normalize_positive_int,
                "memory_mb": normalize_positive_int,
                "disk_gb": normalize_positive_int,
                "serial_number": normalize_text,
                "manufacturer": normalize_text,
                "model": normalize_text,
                "gateway": normalize_text,
                "dns_servers": normalize_dns_servers,
            }

            backfill_values = {}
            for field_name, resolver in field_resolvers.items():
                incoming_value = report_values.get(field_name)
                if resolver(incoming_value) is not None:
                    continue

                current_value = target_row.get(field_name) if target_row else None
                if resolver(current_value) is not None:
                    continue

                donor_value = donor_row.get(field_name) if donor_row else None
                normalized_donor = resolver(donor_value)
                if normalized_donor is not None:
                    backfill_values[field_name] = normalized_donor

            return backfill_values

        def resolve_serial_update_conflict(target_asset_id, serial_value):
            if not serial_value:
                return None

            cursor.execute("""
                SELECT id, deleted_at
                FROM assets
                WHERE serial_number = %s AND id <> %s
                ORDER BY deleted_at IS NULL DESC, id ASC
            """, (serial_value, target_asset_id))
            conflicts = cursor.fetchall()
            if not conflicts:
                return serial_value

            active_conflict_ids = [row["id"] for row in conflicts if row.get("deleted_at") is None]
            if active_conflict_ids:
                safe_console_print(
                    f"[Heartbeat] Skip serial update due to active conflict: target={target_asset_id}, "
                    f"serial={serial_value}, conflict_ids={active_conflict_ids}"
                )
                return None

            deleted_conflict_ids = [row["id"] for row in conflicts]
            cursor.execute("""
                UPDATE assets
                SET serial_number = NULL, updated_at = NOW()
                WHERE serial_number = %s AND id <> %s AND deleted_at IS NOT NULL
            """, (serial_value, target_asset_id))
            safe_console_print(
                f"[Heartbeat] Released serial conflict from deleted assets: target={target_asset_id}, "
                f"serial={serial_value}, donor_ids={deleted_conflict_ids}"
            )
            return serial_value

        # 提取基本信息
        hostname = data.get('hostname')
        ip_address = data.get('ip_address')
        mac_address = data.get('mac_address')
        report_type = data.get('report_type', 'heartbeat')
        serial_number = normalize_text(data.get("serial_number"))

        safe_console_print(f"[Heartbeat] Received report: hostname={hostname}, type={report_type}")

        if not hostname or not ip_address or not mac_address:
            raise HTTPException(status_code=400, detail="Missing required fields: hostname, ip_address, mac_address")

        identifier_clauses, identifier_params = build_asset_identifier_clauses(
            hostname,
            ip_address,
            mac_address,
            serial_number,
        )

        asset = None
        if identifier_clauses:
            asset_candidates = load_matching_asset_candidates(include_deleted=False)
            asset = select_best_asset_candidate(
                asset_candidates,
                hostname,
                ip_address,
                mac_address,
                serial_number,
            )

        if asset:
            asset_id = asset['id']
            before_asset_state = fetch_asset_row(cursor, asset_id, include_deleted=True)
            # 更新资产基本信息
            cursor.execute("""
                UPDATE assets SET
                    hostname = %s,
                    ip_address = %s,
                    mac_address = %s,
                    last_seen = NOW(),
                    status = 'online',
                    agent_install_status = %s,
                    agent_version = COALESCE(%s, agent_version),
                    updated_at = NOW()
                WHERE id = %s
            """, (
                hostname,
                ip_address,
                mac_address,
                AGENT_INSTALL_STATUS_INSTALLED,
                data.get("agent_version"),
                asset_id
            ))
            safe_console_print(
                f"[Heartbeat] Matched active asset: asset_id={asset_id}, hostname={hostname}, ip={ip_address}"
            )
        else:
            restored_asset = select_best_asset_candidate(
                load_matching_asset_candidates(include_deleted=True),
                hostname,
                ip_address,
                mac_address,
                serial_number,
            )

            if restored_asset:
                asset_id = restored_asset["id"]
                before_asset_state = fetch_asset_row(cursor, asset_id, include_deleted=True)
                cursor.execute("""
                    UPDATE assets SET
                        deleted_at = NULL,
                        hostname = %s,
                        ip_address = %s,
                        mac_address = %s,
                        last_seen = NOW(),
                        status = 'online',
                        agent_install_status = %s,
                        agent_version = COALESCE(%s, agent_version),
                        updated_at = NOW()
                    WHERE id = %s
                """, (
                    hostname,
                    ip_address,
                    mac_address,
                    AGENT_INSTALL_STATUS_INSTALLED,
                    data.get("agent_version"),
                    asset_id
                ))
                asset = {
                    **restored_asset,
                    "deleted_at": None,
                    "hostname": hostname,
                    "ip_address": ip_address,
                    "mac_address": mac_address,
                }
                safe_console_print(
                    f"[Heartbeat] Reactivated deleted asset: restored_id={asset_id}, hostname={hostname}, ip={ip_address}"
                )
            else:
                # 创建新资产 - 修复asset_type必须是enum中的值
                cursor.execute("""
                    INSERT INTO assets (
                        asset_type, hostname, ip_address, mac_address,
                        status, agent_install_status, agent_version, last_seen, created_at, updated_at
                    ) VALUES ('pc', %s, %s, %s, 'online', %s, %s, NOW(), NOW(), NOW())
                """, (hostname, ip_address, mac_address, AGENT_INSTALL_STATUS_INSTALLED, data.get("agent_version")))
                asset_id = cursor.lastrowid
                asset = {
                    "id": asset_id,
                    "hostname": hostname,
                    "ip_address": ip_address,
                    "mac_address": mac_address,
                    "serial_number": None,
                    "os_type": None,
                    "os_version": None,
                    "cpu_cores": None,
                    "memory_mb": None,
                    "disk_gb": None,
                    "manufacturer": None,
                    "model": None,
                    "gateway": None,
                    "dns_servers": None,
                    "last_seen": None,
                    "updated_at": None,
                    "deleted_at": None,
                }
                safe_console_print(
                    f"[Heartbeat] Created new asset: asset_id={asset_id}, hostname={hostname}, ip={ip_address}"
                )

        asset_update_fields = []
        asset_update_values = []

        normalized_report_values = {
            "os_type": normalize_text(data.get("os_type")),
            "os_version": normalize_text(data.get("os_version")),
            "cpu_cores": normalize_positive_int(data.get("cpu_cores")),
            "memory_mb": normalize_positive_int(data.get("memory_total")),
            "disk_gb": normalize_positive_int(data.get("disk_total")),
            "serial_number": serial_number,
            "manufacturer": normalize_text(data.get("manufacturer")),
            "model": normalize_text(data.get("model")),
            "gateway": normalize_text(data.get("gateway")),
        }

        for field_name, field_value in normalized_report_values.items():
            if field_value is not None:
                asset_update_fields.append(f"{field_name} = %s")
                asset_update_values.append(field_value)

        dns_servers = normalize_dns_servers(data.get("dns_servers"))
        normalized_report_values["dns_servers"] = dns_servers
        if dns_servers is not None:
            asset_update_fields.append("dns_servers = %s")
            asset_update_values.append(json.dumps(dns_servers))

        donor_asset = None
        if identifier_clauses:
            donor_candidates = load_matching_asset_candidates(
                include_deleted=True,
                exclude_asset_id=asset_id,
            )
            donor_asset = select_best_asset_candidate(
                donor_candidates,
                hostname,
                ip_address,
                mac_address,
                serial_number,
            )

        if donor_asset:
            backfill_values = build_missing_field_backfill(asset, normalized_report_values, donor_asset)
            for field_name, field_value in backfill_values.items():
                if field_name == "dns_servers":
                    asset_update_fields.append("dns_servers = %s")
                    asset_update_values.append(json.dumps(field_value))
                else:
                    asset_update_fields.append(f"{field_name} = %s")
                    asset_update_values.append(field_value)
            if backfill_values:
                safe_console_print(
                    f"[Heartbeat] Backfilled asset metadata: target={asset_id}, donor={donor_asset['id']}, "
                    f"fields={','.join(sorted(backfill_values.keys()))}"
                )

        serial_field_indexes = [index for index, field in enumerate(asset_update_fields) if field == "serial_number = %s"]
        if serial_field_indexes:
            serial_index = serial_field_indexes[-1]
            resolved_serial = resolve_serial_update_conflict(asset_id, asset_update_values[serial_index])
            if resolved_serial is None:
                del asset_update_fields[serial_index]
                del asset_update_values[serial_index]
            else:
                asset_update_values[serial_index] = resolved_serial

        if asset_update_fields:
            asset_update_fields.append("updated_at = NOW()")
            cursor.execute(f"""
                UPDATE assets SET
                    {", ".join(asset_update_fields)}
                WHERE id = %s
            """, (*asset_update_values, asset_id))
        after_asset_state = fetch_asset_row(cursor, asset_id, include_deleted=True)
        record_asset_changes(
            cursor,
            asset_id,
            before_asset_state,
            after_asset_state,
            field_names=[
                "hostname",
                "ip_address",
                "mac_address",
                "serial_number",
                "manufacturer",
                "model",
                "os_type",
                "os_version",
                "cpu_cores",
                "memory_mb",
                "disk_gb",
                "status",
                "agent_install_status",
                "gateway",
                "dns_servers",
                "last_seen",
                "deleted_at",
            ],
            change_type="agent_report",
            source_type="agent",
            operator_name=normalize_actor_name(f"agent:{asset_id}", fallback="agent"),
            details={"report_type": report_type},
        )

        # 根据report_type处理不同类型的数据
        if report_type in ['heartbeat', 'system_status']:
            # Keep older Agents that still send *_percent fields compatible
            # with the current agent_heartbeat table column names.
            cpu_usage = data.get('cpu_usage', data.get('cpu_percent', 0))
            memory_usage = data.get('memory_usage', data.get('memory_percent', 0))
            disk_usage = data.get('disk_usage', data.get('disk_percent', 0))
            # 插入心跳记录
            cursor.execute("""
                INSERT INTO agent_heartbeat (
                    asset_id, cpu_usage, memory_usage, disk_usage,
                    process_count, logged_users, disk_info, heartbeat_time, created_at
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, NOW(), NOW())
            """, (
                asset_id,
                cpu_usage,
                memory_usage,
                disk_usage,
                data.get('process_count', 0),
                data.get('logged_users', ''),
                json.dumps(data.get('disk_info', [])) if data.get('disk_info') else None
            ))

        elif report_type == 'hardware':
            # 保存磁盘详情
            if data.get('disk_info'):
                cursor.execute("""
                    UPDATE agent_heartbeat SET
                        disk_info = %s
                    WHERE asset_id = %s
                    ORDER BY heartbeat_time DESC
                    LIMIT 1
                """, (json.dumps(data.get('disk_info')), asset_id))

        elif report_type == 'network':
            # 网络监控上报：实时状态 + 分钟粒度历史（独立于心跳核心数据，fail-safe）
            try:
                from zvplatform.services.network_service import ingest_network_report
                ingest_network_report(cursor, conn, asset_id, data.get('network') or {})
                safe_console_print(f"[Heartbeat] Network report ingested: asset_id={asset_id}")
            except Exception as net_exc:
                safe_console_print(f"[Heartbeat] Network ingest failed (ignored): {net_exc}")

        elif report_type == 'patches':
            # Patch Management Phase 1：补丁状态存储（replace-per-report，fail-safe）
            try:
                patch_status = data.get('patch_status') or {}
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS agent_patches (
                        asset_id INT PRIMARY KEY,
                        pending_count INT NOT NULL DEFAULT 0,
                        reboot_required TINYINT(1) NOT NULL DEFAULT 0,
                        last_scan DATETIME NULL,
                        patches JSON NULL,
                        error VARCHAR(500) NULL,
                        updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
                    )
                """)
                pending = patch_status.get('pending') or []
                if isinstance(pending, dict):
                    pending = [pending]
                cursor.execute("""
                    REPLACE INTO agent_patches
                        (asset_id, pending_count, reboot_required, last_scan, patches, error)
                    VALUES (%s, %s, %s, %s, %s, %s)
                """, (
                    asset_id,
                    int(patch_status.get('pending_count') or 0),
                    1 if patch_status.get('reboot_required') else 0,
                    patch_status.get('last_scan') or None,
                    json.dumps(pending, ensure_ascii=False) if pending else None,
                    str(patch_status.get('error') or '')[:500] or None,
                ))
                safe_console_print(f"[Heartbeat] Patch report ingested: asset_id={asset_id} "
                                   f"pending={patch_status.get('pending_count')}")
            except Exception as patch_exc:
                safe_console_print(f"[Heartbeat] Patch ingest failed (ignored): {patch_exc}")

        elif report_type == 'software':
            # 更新软件清单
            software_list = data.get('software_list', [])

            safe_console_print(f"[Heartbeat] Received software inventory count={len(software_list)}")

            # 删除旧的软件记录
            cursor.execute("DELETE FROM asset_software WHERE asset_id = %s", (asset_id,))

            # 插入新的软件记录
            success_count = 0
            error_count = 0

            for software in software_list:
                try:
                    # 处理size字段：
                    # - Agent 采集端（zvagent/collectors/software.py）产出 "size_mb" 数值
                    # - 但上报 payload（zvagent/heartbeat.py:365）把数值塞进 "size" 键
                    #   （历史遗留键名）——因此这里依次兼容：size_mb 数值 → size 数值 →
                    #   size 带单位字符串（"1.5 MB"/"512 KB"/"2 GB"，最旧格式）
                    size_mb = 0.0
                    raw = software.get('size_mb')
                    if raw is None:
                        raw = software.get('size')
                    if raw is not None:
                        if isinstance(raw, (int, float)):
                            size_mb = float(raw) or 0.0
                        else:
                            size_str = str(raw)
                            try:
                                if 'GB' in size_str:
                                    size_mb = float(size_str.replace('GB', '').strip()) * 1024
                                elif 'MB' in size_str:
                                    size_mb = float(size_str.replace('MB', '').strip())
                                elif 'KB' in size_str:
                                    size_mb = float(size_str.replace('KB', '').strip()) / 1024
                                else:
                                    size_mb = float(size_str) or 0.0
                            except (ValueError, TypeError):
                                size_mb = 0.0

                    # 限制字段长度，避免数据库错误
                    software_name = (software.get('name') or '')[:255]
                    version = (software.get('version') or '')[:100]
                    vendor = (software.get('vendor') or '')[:255]
                    category = (software.get('category') or '')[:100]
                    install_date = software.get('install_date')

                    if not software_name:  # 跳过空名称
                        continue

                    cursor.execute("""
                        INSERT INTO asset_software (
                            asset_id, software_name, version, vendor, category,
                            install_date, size_mb, created_at
                        ) VALUES (%s, %s, %s, %s, %s, %s, %s, NOW())
                    """, (
                        asset_id,
                        software_name,
                        version,
                        vendor,
                        category or None,
                        install_date,
                        size_mb
                    ))
                    success_count += 1

                except Exception as e:
                    error_count += 1
                    if error_count <= 3:  # 只打印前3个错误
                        safe_console_print(f"[Heartbeat] Software insert failed: name={software.get('name', 'Unknown')} error={str(e)[:100]}")

            safe_console_print(f"[Heartbeat] Software sync complete: success={success_count}, failed={error_count}")

        conn.commit()

        # 一机一密（P0-01）：
        # - 设备凭据心跳 → 校验 agent_id 与本次上报资产一致
        # - 全局 token 心跳且未注册设备凭据 → 签发并随响应一次性下发
        ensure_agent_credentials_table(conn)
        heartbeat_credential = None
        if agent_auth_type == "device":
            if authenticated_agent_id != asset_id:
                conn.rollback()
                raise HTTPException(status_code=403, detail="Agent credential does not match this asset")
        else:
            try:
                # 1.6.0+ Agent 具备凭据保存能力：active 行存在也轮换重发（灰度断点修复）
                allow_rotate = _agent_version_tuple(data.get("agent_version")) >= (1, 6, 0)
                heartbeat_credential = _issue_agent_device_credential(asset_id, cursor, allow_rotate=allow_rotate)
                conn.commit()
            except Error as cred_err:
                conn.rollback()
                safe_console_print(f"[Heartbeat] device credential issue failed: {cred_err}")

        # P0-06 + V1.7.0：Agent 上报升级状态 → 审计日志 + 事务历史全阶段记录
        upgrade_state = data.get("agent_upgrade_state")
        if isinstance(upgrade_state, dict) and upgrade_state.get("stage"):
            stage = str(upgrade_state.get("stage"))
            # V1.7.0 服务端熔断：同一资产对同一目标版本连续失败达阈值后暂停下发，
            # 防止旧版 Agent 在无法完成迁移时陷入"下载→回滚"死循环
            # （2026-09-10 2213 事件：旧 updater 401 → 10s 一次 ROLLBACK 刷屏）。
            # 内存态，后端重启即清零；COMMITTED 或目标版本变化自动解除。
            if stage in ("ROLLBACK", "FAILED"):
                to_version = str(upgrade_state.get("to_version") or "")
                breaker = _UPGRADE_FAILURE_BREAKER.setdefault(asset_id, {"to_version": to_version, "count": 0})
                if breaker.get("to_version") == to_version:
                    breaker["count"] = int(breaker.get("count") or 0) + 1
                    breaker["last_at"] = time.time()
                    if int(breaker["count"]) >= _UPGRADE_BREAKER_THRESHOLD:
                        breaker["until"] = time.time() + _UPGRADE_BREAKER_COOLDOWN_SECONDS
                        safe_console_print(
                            f"[Heartbeat] upgrade breaker OPEN for asset {asset_id} "
                            f"(target {to_version}, {breaker['count']} failures, "
                            f"cooldown {_UPGRADE_BREAKER_COOLDOWN_SECONDS}s)")
                else:
                    _UPGRADE_FAILURE_BREAKER[asset_id] = {"to_version": to_version, "count": 1, "last_at": time.time()}
            elif stage == "COMMIT":
                _UPGRADE_FAILURE_BREAKER.pop(asset_id, None)
            try:
                from agent_upgrade_api import record_agent_upgrade_state
                normalized = {"COMMIT": "COMMITTED", "COMMITTED": "COMMITTED",
                              "ROLLBACK": "ROLLBACK", "FAILED": "FAILED"}.get(stage, "RUNNING")
                record_agent_upgrade_state(
                    conn, asset_id,
                    str(upgrade_state.get("server_upgrade_id") or upgrade_state.get("upgrade_id") or "UPG-unknown"),
                    normalized,
                    from_version=upgrade_state.get("from_version"),
                    to_version=upgrade_state.get("to_version"),
                    detail=upgrade_state,
                )
            except Exception:
                pass
            if stage in ("COMMIT", "ROLLBACK", "FAILED"):
                try:
                    insert_system_activity_log(cursor, SystemActivityLogCreate(
                        source_type="agent",
                        module="agent_upgrade",
                        category="security",
                        action=f"agent_upgrade_{stage.lower()}",
                        level="info" if stage == "COMMIT" else "warning",
                        result="success" if stage == "COMMIT" else "failed",
                        asset_id=asset_id,
                        hostname=data.get("hostname"),
                        operator_name="agent-self-upgrade",
                        title=f"Agent 自动升级终态: {stage}",
                        message=(f"{upgrade_state.get('from_version')} -> {upgrade_state.get('to_version')} "
                                 f"failure={upgrade_state.get('failure_reason')}"),
                        details=upgrade_state,
                    ))
                except Exception:
                    pass

        # 自动升级（R13）：记录资产版本，版本落后于平台最新版时在响应中携带升级指令
        record_agent_version(asset_id, data.get("agent_version"))
        heartbeat_response = {
            "status": "success",
            "asset_id": asset_id,
            "message": f"Heartbeat received: {report_type}",
            "policies": load_agent_policies(),
        }
        if heartbeat_credential:
            heartbeat_response["agent_credential"] = heartbeat_credential
        # V1.8.3 通用任务通道：仅在主心跳下发 pending 任务（Agent 主循环处理响应体；
        # network/hardware/software 上报循环不读响应体，任务会被丢弃）
        if report_type in ('heartbeat', 'system_status'):
            try:
                from zvplatform.routers.agent_jobs import fetch_pending_jobs
                pending_jobs = fetch_pending_jobs(conn, asset_id)
                if pending_jobs:
                    safe_console_print(f"[Heartbeat][DBG] jobs dispatched to asset {asset_id}: "
                                       f"{[j.get('job_id') for j in pending_jobs]}")
                    heartbeat_response["jobs"] = pending_jobs
            except Exception as jobs_exc:
                safe_console_print(f"[Heartbeat] jobs dispatch failed: {jobs_exc}")
        # V1.8.3 通用任务通道：Agent 上报任务执行结果 → 事务状态记录（终态审计）
        job_results = data.get("job_results")
        if isinstance(job_results, list) and job_results:
            safe_console_print(f"[Heartbeat][DBG] job_results received from asset {asset_id}: "
                               f"{json.dumps(job_results, ensure_ascii=False)[:300]}")
            try:
                from zvplatform.routers.agent_jobs import record_job_state
                for job_result in job_results:
                    if isinstance(job_result, dict) and job_result.get("job_id"):
                        record_job_state(
                            conn, str(job_result["job_id"]),
                            str(job_result.get("state") or "succeeded"),
                            job_result.get("result"),
                        )
            except Exception as jobs_exc:
                safe_console_print(f"[Heartbeat] job_results record failed: {jobs_exc}")
        # P0-02：下发平台证书公钥，Agent 存为 ca-bundle 用于 TLS 校验
        try:
            _ca_pem_path = os.path.join(os.path.dirname(os.path.abspath(assets_api.__file__)),
                                        "frontend", "certs", "zview-cert.pem")
            if os.path.exists(_ca_pem_path):
                with open(_ca_pem_path, encoding="utf-8") as _fh:
                    heartbeat_response["agent_ca_bundle_pem"] = _fh.read()
        except Exception:
            pass
        latest_upgrade = get_latest_upgrade()
        reported_version = str(data.get("agent_version") or "").strip()
        # 旧版 Agent 心跳无 version 字段（reported_version 为空）也下发升级指令，
        # 用于引导存量终端到含自动升级逻辑的版本。
        # 已知版本仅在"落后于最新包"时下发：manifest 回退到旧版本时严禁降级，
        # 否则 Agent 每次心跳重试下载/安装，反复重启并占满磁盘（2026-09-09 事故）
        if latest_upgrade.get("version"):
            should_upgrade = not reported_version
            if not should_upgrade:
                try:
                    should_upgrade = _agent_version_tuple(reported_version) < _agent_version_tuple(
                        latest_upgrade["version"]
                    )
                except Exception:
                    should_upgrade = reported_version != latest_upgrade["version"]
            # V1.7.0：desired/reported/upgrade_state 协议 —— desired_version 显式下发，
            # 升级指令带事务 ID（按目标版本确定性生成，重发幂等）；
            # 服务端熔断开启期间不下发指令（防止旧版 Agent 迁移死循环刷屏）
            heartbeat_response["desired_version"] = latest_upgrade["version"]
            # V1.7.0 收敛：Agent 版本已达 desired → 未完结事务标 COMMITTED
            # （updater 旧版不上报终态，版本到位即最强 COMMIT 证据）
            if reported_version and _agent_version_tuple(reported_version) >= _agent_version_tuple(
                latest_upgrade["version"]
            ):
                try:
                    from agent_upgrade_api import close_reached_upgrade
                    close_reached_upgrade(conn, asset_id, latest_upgrade["version"])
                except Exception:
                    pass
            breaker = _UPGRADE_FAILURE_BREAKER.get(asset_id)
            if breaker:
                until = float(breaker.get("until") or 0)
                if breaker.get("to_version") != latest_upgrade["version"]:
                    _UPGRADE_FAILURE_BREAKER.pop(asset_id, None)  # 目标版本已变化，重新计数
                elif until and time.time() < until:
                    return heartbeat_response  # 熔断中：不下发指令
                elif until:
                    _UPGRADE_FAILURE_BREAKER.pop(asset_id, None)  # 冷却结束，恢复下发
            if should_upgrade:
                dispatch_upgrade_id = f"UPG-{latest_upgrade['version']}"
                heartbeat_response["upgrade"] = {
                    "upgrade_id": dispatch_upgrade_id,
                    "version": latest_upgrade["version"],
                    "sha256": latest_upgrade.get("sha256"),
                    "package_type": latest_upgrade.get("package_type") or "exe",
                }
                try:
                    from agent_upgrade_api import record_agent_upgrade_state
                    record_agent_upgrade_state(
                        conn, asset_id, dispatch_upgrade_id, "DISPATCHED",
                        from_version=reported_version or None,
                        to_version=latest_upgrade["version"],
                        detail={"package_type": latest_upgrade.get("package_type") or "exe"},
                    )
                except Exception:
                    pass
        return heartbeat_response

    except HTTPException:
        raise
    except Exception as e:
        conn.rollback()
        import traceback
        error_detail = f"{str(e)}\n{traceback.format_exc()}"
        safe_console_print(f"[Heartbeat] Processing failed: {error_detail}")
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        cursor.close()
        conn.close()


# ============================================================
# 分组管理接口
# ============================================================


# ============================================================
# 软件管理接口
# ============================================================

