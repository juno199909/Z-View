# -*- coding: utf-8 -*-
"""资产 CRUD 路由（1.9.55 自 assets_api 迁入 #16 模块化第六批，逻辑逐字保留）。

共享助手经 bind_helpers 注入（assets_api 尾部调用），
后续批次逐步下沉到 zvplatform/repositories。
"""
import csv
import io
import json
import re
import ipaddress
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from fastapi import APIRouter, HTTPException, Query, Request, Response
from pydantic import BaseModel, Field
from mysql.connector import Error

from zvplatform.common import compute_health_score, safe_float

from auth_utils import require_request_permission, get_request_username, get_user_scoped_group_ids
from console_utils import safe_console_print
from zvplatform.db import create_connection as get_db_connection, format_datetime
from zvplatform.constants import (
    AGENT_INSTALL_STATUS_INSTALLED,
    AGENT_INSTALL_STATUS_NOT_INSTALLED,
    ALERT_ONLINE_SECONDS,
)
from zvplatform.repositories.asset_repository import (
    fetch_asset_row,
    record_asset_changes,
)
from zvplatform.repositories.log_repository import insert_system_activity_log
from zvplatform.models import SystemActivityLogCreate

router = APIRouter()

_injected = {}


def bind_helpers(**kwargs):
    """assets_api 尾部注入共享助手（get_asset_agent_target/proxy_agent_json_request/
    record_system_activity_log/AGENT_CONTROL_PORT）。"""
    _injected.update(kwargs)
    globals().update(kwargs)


class AssetStats(BaseModel):
    total: int
    online: int
    offline: int
    unknown: int


class Asset(BaseModel):
    id: Optional[int] = None
    group_id: Optional[int] = None
    asset_type: Optional[str] = None
    hostname: Optional[str] = None
    ip_address: Optional[str] = None
    mac_address: Optional[str] = None
    serial_number: Optional[str] = None
    manufacturer: Optional[str] = None
    model: Optional[str] = None
    os_type: Optional[str] = None
    os_version: Optional[str] = None
    cpu_cores: Optional[int] = None
    memory_mb: Optional[int] = None
    disk_gb: Optional[int] = None
    status: Optional[str] = None
    agent_install_status: Optional[str] = None
    last_seen: Optional[str] = None
    location: Optional[str] = None
    owner: Optional[str] = None
    purchase_date: Optional[str] = None
    purchase_price: Optional[float] = None
    supplier: Optional[str] = None
    contract_no: Optional[str] = None
    warranty_start: Optional[str] = None
    warranty_end: Optional[str] = None
    warranty_provider: Optional[str] = None
    deployment_date: Optional[str] = None
    asset_status: Optional[str] = None
    user_name: Optional[str] = None
    department: Optional[str] = None
    retire_date: Optional[str] = None
    retire_reason: Optional[str] = None
    notes: Optional[str] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None


class AssetCommandRequest(BaseModel):
    command: str = Field(..., min_length=1)
    operator: Optional[str] = None
    requester: Optional[str] = None


class AssetTriggerReportRequest(BaseModel):
    operator: Optional[str] = None
    requester: Optional[str] = None



def build_asset_filters(
    asset_type: Optional[str] = None,
    status: Optional[str] = None,
    group_id: Optional[int] = None,
    keyword: Optional[str] = None,
    alias: str = "a"
) -> Tuple[List[str], List[Any]]:
    """构建资产列表/统计通用筛选条件，确保在线口径一致。"""
    where_clauses = [f"{alias}.deleted_at IS NULL"]
    params: List[Any] = []

    if asset_type:
        where_clauses.append(f"{alias}.asset_type = %s")
        params.append(asset_type)

    if status:
        if status == "online":
            where_clauses.append(
                f"{alias}.last_seen IS NOT NULL "
                f"AND TIMESTAMPDIFF(SECOND, {alias}.last_seen, NOW()) <= %s"
            )
            params.append(ALERT_ONLINE_SECONDS)
        elif status == "offline":
            where_clauses.append(
                f"({alias}.last_seen IS NULL "
                f"OR TIMESTAMPDIFF(SECOND, {alias}.last_seen, NOW()) > %s)"
            )
            params.append(ALERT_ONLINE_SECONDS)
        else:
            where_clauses.append(f"{alias}.status = %s")
            params.append(status)

    if group_id is not None:
        where_clauses.append(f"{alias}.group_id = %s")
        params.append(group_id)

    if keyword:
        keyword_like = f"%{keyword}%"
        where_clauses.append(
            f"({alias}.hostname LIKE %s OR {alias}.ip_address LIKE %s OR {alias}.mac_address LIKE %s)"
        )
        params.extend([keyword_like, keyword_like, keyword_like])

    return where_clauses, params


@router.get("/api/v1/assets/stats")
def get_assets_stats(
    asset_type: Optional[str] = Query(None),
    status: Optional[str] = Query(None),
    group_id: Optional[int] = Query(None),
    keyword: Optional[str] = Query(None)
):
    """获取资产统计（支持与列表同口径筛选和实时在线判定）"""
    conn = get_db_connection()
    if not conn:
        raise HTTPException(status_code=500, detail="Database connection failed")

    try:
        cursor = conn.cursor(dictionary=True)
        ensure_asset_changes_table(conn)
        before_asset_state = None
        where_clauses, params = build_asset_filters(asset_type, status, group_id, keyword)
        where_sql = " AND ".join(where_clauses)

        cursor.execute(f"SELECT COUNT(*) AS total FROM assets a WHERE {where_sql}", params)
        total = (cursor.fetchone() or {}).get("total", 0) or 0

        cursor.execute(f"""
            SELECT
                SUM(CASE
                    WHEN a.last_seen IS NOT NULL
                     AND TIMESTAMPDIFF(SECOND, a.last_seen, NOW()) <= %s
                    THEN 1 ELSE 0
                END) AS online,
                SUM(CASE
                    WHEN a.last_seen IS NULL
                     OR TIMESTAMPDIFF(SECOND, a.last_seen, NOW()) > %s
                    THEN 1 ELSE 0
                END) AS offline
            FROM assets a
            WHERE {where_sql}
        """, [ALERT_ONLINE_SECONDS, ALERT_ONLINE_SECONDS, *params])
        status_row = cursor.fetchone() or {}
        online = status_row.get("online", 0) or 0
        offline = status_row.get("offline", 0) or 0
        unknown = 0

        cursor.execute(f"""
            SELECT
                CASE
                    WHEN a.last_seen IS NULL THEN 'offline'
                    WHEN TIMESTAMPDIFF(SECOND, a.last_seen, NOW()) <= %s THEN 'online'
                    ELSE 'offline'
                END AS real_status,
                CASE
                    WHEN TIMESTAMPDIFF(SECOND, a.last_seen, NOW()) <= %s THEN h.cpu_usage
                    ELSE NULL
                END AS cpu_usage,
                CASE
                    WHEN TIMESTAMPDIFF(SECOND, a.last_seen, NOW()) <= %s THEN h.memory_usage
                    ELSE NULL
                END AS memory_usage,
                CASE
                    WHEN TIMESTAMPDIFF(SECOND, a.last_seen, NOW()) <= %s THEN h.disk_usage
                    ELSE NULL
                END AS disk_usage
            FROM assets a
            LEFT JOIN agent_heartbeat h ON h.id = (
                SELECT h2.id
                FROM agent_heartbeat h2
                WHERE h2.asset_id = a.id
                ORDER BY h2.heartbeat_time DESC,
                         CASE
                             WHEN COALESCE(h2.disk_info, '') <> ''
                               OR COALESCE(h2.logged_users, '') <> ''
                               OR COALESCE(h2.process_count, 0) > 0
                               OR COALESCE(h2.cpu_usage, 0) <> 0
                               OR COALESCE(h2.memory_usage, 0) <> 0
                               OR COALESCE(h2.disk_usage, 0) <> 0
                             THEN 0 ELSE 1
                         END,
                         h2.id DESC
                LIMIT 1
            )
            WHERE {where_sql}
        """, [ALERT_ONLINE_SECONDS, ALERT_ONLINE_SECONDS, ALERT_ONLINE_SECONDS, ALERT_ONLINE_SECONDS, *params])
        risk = 0
        for row in cursor.fetchall():
            score = compute_health_score(
                row.get("real_status"),
                safe_float(row.get("cpu_usage")),
                safe_float(row.get("memory_usage")),
                safe_float(row.get("disk_usage"))
            )
            if score is not None and 0 < score < 60:
                risk += 1

        cursor.execute(f"""
            SELECT COALESCE(a.asset_type, 'unknown') AS asset_type, COUNT(*) AS count
            FROM assets a
            WHERE {where_sql}
            GROUP BY COALESCE(a.asset_type, 'unknown')
        """, params)
        by_type = {}
        for row in cursor.fetchall():
            by_type[row["asset_type"]] = row["count"]

        cursor.execute(f"""
            SELECT COALESCE(g.name, '未分组') AS group_name, COUNT(*) AS count
            FROM assets a
            LEFT JOIN asset_groups g ON a.group_id = g.id
            WHERE {where_sql}
            GROUP BY COALESCE(g.name, '未分组')
            ORDER BY count DESC, group_name
        """, params)
        by_group = {}
        for row in cursor.fetchall():
            by_group[row["group_name"]] = row["count"]

        return {
            "total": total,
            "online": online,
            "offline": offline,
            "unknown": unknown,
            "risk": risk,
            "by_type": by_type,
            "by_group": by_group
        }

    except Error as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        cursor.close()
        conn.close()


@router.get("/api/v1/assets")
def get_assets(
    request: Request,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    asset_type: Optional[str] = Query(None),
    status: Optional[str] = Query(None),
    group_id: Optional[int] = Query(None),
    keyword: Optional[str] = Query(None)
):
    """获取资产列表"""
    conn = get_db_connection()
    if not conn:
        raise HTTPException(status_code=500, detail="Database connection failed")

    try:
        cursor = conn.cursor(dictionary=True)

        where_clauses, params = build_asset_filters(asset_type, status, group_id, keyword)
        # Scoped RBAC：限定用户可见分组（未分组资产对受限用户不可见）
        scoped_group_ids = get_user_scoped_group_ids(getattr(request.state, "auth_user", None))
        if scoped_group_ids is not None:
            placeholders = ",".join(["%s"] * len(scoped_group_ids))
            where_clauses.append(f"a.group_id IN ({placeholders})")
            params.extend(scoped_group_ids)
        where_sql = " AND ".join(where_clauses)

        # 查询总数
        count_sql = f"SELECT COUNT(*) as total FROM assets a WHERE {where_sql}"
        cursor.execute(count_sql, params)
        total = cursor.fetchone()['total']

        # 查询数据（关联最新心跳信息和分组信息，实时计算在线状态）
        offset = (page - 1) * page_size
        data_sql = f"""
            SELECT a.id, a.asset_type, a.hostname, a.ip_address, a.mac_address,
                   a.serial_number, a.manufacturer, a.model, a.os_type, a.os_version,
                   a.cpu_cores, a.memory_mb, a.disk_gb, a.last_seen,
                   a.agent_install_status,
                   a.agent_version,
                   a.location, a.owner, a.group_id, a.created_at, a.updated_at,
                   g.name as group_name,
                   CASE
                       WHEN a.last_seen IS NULL THEN 'offline'
                       WHEN TIMESTAMPDIFF(SECOND, a.last_seen, NOW()) <= %s THEN 'online'
                       ELSE 'offline'
                   END as real_status,
                   CASE
                       WHEN TIMESTAMPDIFF(SECOND, a.last_seen, NOW()) <= %s THEN h.cpu_usage
                       ELSE NULL
                   END as cpu_usage,
                   CASE
                       WHEN TIMESTAMPDIFF(SECOND, a.last_seen, NOW()) <= %s THEN h.memory_usage
                       ELSE NULL
                   END as memory_usage,
                   CASE
                       WHEN TIMESTAMPDIFF(SECOND, a.last_seen, NOW()) <= %s THEN h.disk_usage
                       ELSE NULL
                   END as disk_usage,
                   CASE
                       WHEN TIMESTAMPDIFF(SECOND, a.last_seen, NOW()) <= %s THEN h.logged_users
                       ELSE NULL
                   END as logged_users,
                   CASE
                       WHEN TIMESTAMPDIFF(SECOND, a.last_seen, NOW()) <= %s THEN h.heartbeat_time
                       ELSE NULL
                   END as heartbeat_time
            FROM assets a
            LEFT JOIN asset_groups g ON a.group_id = g.id
            LEFT JOIN agent_heartbeat h ON h.id = (
                -- V1.9.21 性能修复：此前相关子查询按 heartbeat_time DESC + 6 个
                -- COALESCE 表达式排序（索引失效，每行资产对 8000+ 条历史心跳
                -- filesort，33 万行表实测列表 SQL ~500ms）。id 自增即时间序，
                -- MAX(h2.id) 走 (asset_id, id) 复合索引直接定位，实测 50ms。
                -- 1.9.21 起每条心跳都带 disk_info，原 CASE"优先有明细的心跳"
                -- 已无存在必要；旧版空心跳由前端 diskList 空回退综合值兜底。
                SELECT MAX(h2.id) FROM agent_heartbeat h2 WHERE h2.asset_id = a.id
            )
            WHERE {where_sql}
            ORDER BY a.id DESC
            LIMIT %s OFFSET %s
        """
        cursor.execute(data_sql, [
            ALERT_ONLINE_SECONDS,
            ALERT_ONLINE_SECONDS,
            ALERT_ONLINE_SECONDS,
            ALERT_ONLINE_SECONDS,
            ALERT_ONLINE_SECONDS,
            ALERT_ONLINE_SECONDS,
            *params,
            page_size,
            offset
        ])
        assets = cursor.fetchall()

        # 格式化日期和实时状态
        for asset in assets:
            # 使用实时计算的状态
            asset['status'] = asset['real_status']

            if asset.get('last_seen'):
                asset['last_seen'] = asset['last_seen'].strftime('%Y-%m-%d %H:%M:%S')
            if asset.get('created_at'):
                asset['created_at'] = asset['created_at'].strftime('%Y-%m-%d %H:%M:%S')
            if asset.get('updated_at'):
                asset['updated_at'] = asset['updated_at'].strftime('%Y-%m-%d %H:%M:%S')
            if asset.get('heartbeat_time'):
                asset['heartbeat_time'] = asset['heartbeat_time'].strftime('%Y-%m-%d %H:%M:%S')

        return {
            "data": assets,
            "total": total,
            "page": page,
            "page_size": page_size
        }

    except Error as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        cursor.close()
        conn.close()


@router.get("/api/v1/assets/options")
def list_asset_options():
    """终端下拉选项（策略下发/绑定等场景）：全量终端 id/hostname/ip/type，不分页。

    注意：必须声明在 /api/v1/assets/{asset_id} 之前，避免被参数路由拦截。
    """
    conn = get_db_connection()
    if not conn:
        raise HTTPException(status_code=500, detail="Database connection failed")

    try:
        cursor = conn.cursor(dictionary=True)
        cursor.execute(
            "SELECT id, hostname, ip_address, asset_type FROM assets "
            "WHERE deleted_at IS NULL ORDER BY hostname, id LIMIT 2000"
        )
        rows = cursor.fetchall()
        return {"data": rows, "total": len(rows)}
    finally:
        cursor.close()
        conn.close()


@router.get("/api/v1/assets/export")
def export_assets(
    asset_type: Optional[str] = Query(None),
    status: Optional[str] = Query(None),
    group_id: Optional[int] = Query(None),
    keyword: Optional[str] = Query(None)
):
    """导出资产列表 CSV"""
    conn = get_db_connection()
    if not conn:
        raise HTTPException(status_code=500, detail="Database connection failed")

    asset_type_labels = {
        "server": "服务器",
        "switch": "交换机",
        "router": "路由器",
        "pc": "PC终端",
        "unknown": "未知",
    }
    status_labels = {
        "online": "在线",
        "offline": "离线",
        "degraded": "降级",
        "unknown": "未知",
    }

    try:
        cursor = conn.cursor(dictionary=True)

        where_clauses, params = build_asset_filters(asset_type, status, group_id, keyword)
        where_sql = " AND ".join(where_clauses)
        cursor.execute(f"""
            SELECT
                a.id,
                a.asset_type,
                a.hostname,
                a.ip_address,
                a.mac_address,
                a.manufacturer,
                a.model,
                a.os_type,
                a.os_version,
                a.location,
                a.owner,
                a.agent_install_status,
                a.last_seen,
                g.name AS group_name,
                CASE
                    WHEN a.last_seen IS NULL THEN 'offline'
                    WHEN TIMESTAMPDIFF(SECOND, a.last_seen, NOW()) <= %s THEN 'online'
                    ELSE 'offline'
                END AS real_status
            FROM assets a
            LEFT JOIN asset_groups g ON g.id = a.group_id
            WHERE {where_sql}
            ORDER BY a.id DESC
        """, [ALERT_ONLINE_SECONDS, *params])
        rows = cursor.fetchall()

        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow([
            "资产ID",
            "主机名",
            "IP地址",
            "MAC地址",
            "资产类型",
            "状态",
            "Agent安装状态",
            "分组",
            "厂商",
            "型号",
            "操作系统",
            "位置",
            "负责人",
            "最后在线时间",
        ])

        for row in rows:
            os_display = " ".join(part for part in [row.get("os_type"), row.get("os_version")] if part).strip()
            writer.writerow([
                row.get("id"),
                row.get("hostname") or "",
                row.get("ip_address") or "",
                row.get("mac_address") or "",
                asset_type_labels.get(row.get("asset_type"), row.get("asset_type") or ""),
                status_labels.get(row.get("real_status"), row.get("real_status") or ""),
                "已安装" if row.get("agent_install_status") == AGENT_INSTALL_STATUS_INSTALLED else "未安装",
                row.get("group_name") or "未分组",
                row.get("manufacturer") or "",
                row.get("model") or "",
                os_display,
                row.get("location") or "",
                row.get("owner") or "",
                format_datetime(row.get("last_seen")) or "",
            ])

        filename = f"assets-{datetime.now().strftime('%Y%m%d-%H%M%S')}.csv"
        csv_content = output.getvalue()
        output.close()

        return Response(
            content=csv_content.encode("utf-8-sig"),
            media_type="text/csv; charset=utf-8",
            headers={
                "Content-Disposition": f'attachment; filename="{filename}"'
            },
        )
    except Error as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        cursor.close()
        conn.close()


@router.get("/api/v1/assets/{asset_id}")
def get_asset(asset_id: int):
    """Get a single asset."""
    conn = get_db_connection()
    if not conn:
        raise HTTPException(status_code=500, detail="Database connection failed")

    try:
        cursor = conn.cursor(dictionary=True)
        cursor.execute("""
            SELECT a.*,
                   CASE
                       WHEN a.last_seen IS NULL THEN 'offline'
                       WHEN TIMESTAMPDIFF(SECOND, a.last_seen, NOW()) <= 90 THEN 'online'
                       ELSE 'offline'
                   END AS real_status
            FROM assets a
            WHERE a.id = %s AND a.deleted_at IS NULL
        """, (asset_id,))

        asset = cursor.fetchone()
        if not asset:
            raise HTTPException(status_code=404, detail="Asset not found")

        asset['status'] = asset.get('real_status') or asset.get('status')
        for field_name in (
            'last_seen', 'created_at', 'updated_at',
            'purchase_date', 'warranty_start', 'warranty_end',
            'deployment_date', 'retire_date',
        ):
            if asset.get(field_name):
                asset[field_name] = format_datetime(asset[field_name])
        return asset
    except Error as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        cursor.close()
        conn.close()


ASSET_TYPE_CHOICES = ("switch", "router", "server", "pc", "unknown")
ASSET_STATUS_CHOICES = ("online", "offline", "unknown")


@router.post("/api/v1/assets")
def create_asset(asset: Asset, request: Request):
    """Create asset."""
    if asset.asset_type and asset.asset_type not in ASSET_TYPE_CHOICES:
        raise HTTPException(
            status_code=422,
            detail=f"Invalid asset_type: {asset.asset_type}. Allowed: {', '.join(ASSET_TYPE_CHOICES)}",
        )
    if asset.status and asset.status not in ASSET_STATUS_CHOICES:
        raise HTTPException(
            status_code=422,
            detail=f"Invalid status: {asset.status}. Allowed: {', '.join(ASSET_STATUS_CHOICES)}",
        )
    if asset.ip_address:
        try:
            ipaddress.ip_address(asset.ip_address)
        except ValueError:
            raise HTTPException(
                status_code=422,
                detail=f"Invalid ip_address: {asset.ip_address}",
            )

    conn = get_db_connection()
    if not conn:
        raise HTTPException(status_code=500, detail="Database connection failed")

    try:
        cursor = conn.cursor(dictionary=True)
        operator_name = get_request_username(request, fallback="console")

        if asset.ip_address:
            cursor.execute(
                "SELECT id FROM assets WHERE ip_address = %s AND deleted_at IS NULL LIMIT 1",
                (asset.ip_address,),
            )
            existing = cursor.fetchone()
            if existing:
                raise HTTPException(
                    status_code=409,
                    detail=f"Asset with IP {asset.ip_address} already exists (id={existing['id']})",
                )

        cursor.execute("""
            INSERT INTO assets (
                asset_type, hostname, ip_address, mac_address,
                serial_number, manufacturer, model, os_type, os_version,
                cpu_cores, memory_mb, disk_gb, status, agent_install_status,
                location, owner, group_id,
                purchase_date, purchase_price, supplier, contract_no,
                warranty_start, warranty_end, warranty_provider,
                deployment_date, asset_status, user_name, department,
                retire_date, retire_reason, notes,
                created_at, updated_at
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, NOW(), NOW())
        """, (
            asset.asset_type, asset.hostname, asset.ip_address, asset.mac_address,
            asset.serial_number, asset.manufacturer, asset.model, asset.os_type, asset.os_version,
            asset.cpu_cores, asset.memory_mb, asset.disk_gb, asset.status or 'unknown',
            asset.agent_install_status or AGENT_INSTALL_STATUS_NOT_INSTALLED,
            asset.location, asset.owner, asset.group_id,
            asset.purchase_date, asset.purchase_price, asset.supplier, asset.contract_no,
            asset.warranty_start, asset.warranty_end, asset.warranty_provider,
            asset.deployment_date, asset.asset_status or 'in_stock', asset.user_name, asset.department,
            asset.retire_date, asset.retire_reason, asset.notes
        ))

        asset_id = cursor.lastrowid
        after_asset = fetch_asset_row(cursor, asset_id, include_deleted=True)
        record_asset_changes(
            cursor,
            asset_id,
            None,
            after_asset,
            field_names=[
                "asset_type", "hostname", "ip_address", "mac_address", "serial_number",
                "manufacturer", "model", "os_type", "os_version", "cpu_cores",
                "memory_mb", "disk_gb", "status", "agent_install_status", "location",
                "owner", "group_id", "purchase_date", "purchase_price", "supplier",
                "contract_no", "warranty_start", "warranty_end", "warranty_provider",
                "deployment_date", "asset_status", "user_name", "department",
                "retire_date", "retire_reason", "notes",
            ],
            change_type="create",
            source_type="manual",
            operator_name=operator_name,
        )
        conn.commit()
        return {"id": asset_id, "message": "Asset created successfully"}
    except Error as e:
        conn.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        cursor.close()
        conn.close()


@router.put("/api/v1/assets/{asset_id}")
def update_asset(asset_id: int, data: dict, request: Request):
    """Update asset."""
    conn = get_db_connection()
    if not conn:
        raise HTTPException(status_code=500, detail="Database connection failed")

    try:
        cursor = conn.cursor(dictionary=True)
        operator_name = get_request_username(request, fallback="console")

        cursor.execute("SELECT id FROM assets WHERE id = %s AND deleted_at IS NULL", (asset_id,))
        if not cursor.fetchone():
            raise HTTPException(status_code=404, detail="Asset not found")
        before_asset = fetch_asset_row(cursor, asset_id, include_deleted=True)

        update_fields = []
        values = []
        allowed_fields = {
            'asset_type': 'asset_type',
            'hostname': 'hostname',
            'ip_address': 'ip_address',
            'mac_address': 'mac_address',
            'serial_number': 'serial_number',
            'manufacturer': 'manufacturer',
            'model': 'model',
            'os_type': 'os_type',
            'os_version': 'os_version',
            'cpu_cores': 'cpu_cores',
            'memory_mb': 'memory_mb',
            'disk_gb': 'disk_gb',
            'status': 'status',
            'location': 'location',
            'owner': 'owner',
            'group_id': 'group_id',
            'purchase_date': 'purchase_date',
            'purchase_price': 'purchase_price',
            'supplier': 'supplier',
            'contract_no': 'contract_no',
            'warranty_start': 'warranty_start',
            'warranty_end': 'warranty_end',
            'warranty_provider': 'warranty_provider',
            'deployment_date': 'deployment_date',
            'asset_status': 'asset_status',
            'user_name': 'user_name',
            'department': 'department',
            'retire_date': 'retire_date',
            'retire_reason': 'retire_reason',
            'notes': 'notes',
        }

        for key, value in data.items():
            if key in allowed_fields:
                if key == "asset_type" and value and value not in ASSET_TYPE_CHOICES:
                    raise HTTPException(
                        status_code=422,
                        detail=f"Invalid asset_type: {value}. Allowed: {', '.join(ASSET_TYPE_CHOICES)}",
                    )
                if key == "status" and value and value not in ASSET_STATUS_CHOICES:
                    raise HTTPException(
                        status_code=422,
                        detail=f"Invalid status: {value}. Allowed: {', '.join(ASSET_STATUS_CHOICES)}",
                    )
                if key == "ip_address" and value:
                    try:
                        ipaddress.ip_address(value)
                    except ValueError:
                        raise HTTPException(
                            status_code=422,
                            detail=f"Invalid ip_address: {value}",
                        )
                    cursor.execute(
                        "SELECT id FROM assets WHERE ip_address = %s AND id != %s AND deleted_at IS NULL LIMIT 1",
                        (value, asset_id),
                    )
                    if cursor.fetchone():
                        raise HTTPException(
                            status_code=409,
                            detail=f"Asset with IP {value} already exists",
                        )
                update_fields.append(f"{allowed_fields[key]} = %s")
                values.append(None if value == '' else value)

        if not update_fields:
            raise HTTPException(status_code=400, detail="No valid fields to update")

        update_fields.append("updated_at = NOW()")
        values.append(asset_id)

        sql = f"UPDATE assets SET {', '.join(update_fields)} WHERE id = %s"
        cursor.execute(sql, tuple(values))
        after_asset = fetch_asset_row(cursor, asset_id, include_deleted=True)
        record_asset_changes(
            cursor,
            asset_id,
            before_asset,
            after_asset,
            field_names=list(allowed_fields.keys()),
            change_type="update",
            source_type="manual",
            operator_name=operator_name,
        )
        conn.commit()
        return {"message": "Asset updated successfully"}
    except HTTPException:
        raise
    except Error as e:
        conn.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        cursor.close()
        conn.close()


# ============================================================
# 资产生命周期状态机（P1）：in_stock 入库 → deployed 在用 ⇄ repairing 维修 → retired 退役（终态）
# ============================================================

ASSET_LIFECYCLE_TRANSITIONS = {
    "in_stock": ("deployed", "retired"),
    "deployed": ("repairing", "in_stock", "retired"),
    "repairing": ("deployed", "retired"),
    "retired": (),
}

ASSET_LIFECYCLE_LABELS = {
    "in_stock": "入库",
    "deployed": "在用",
    "repairing": "维修",
    "retired": "退役",
}


class AssetLifecycleRequest(BaseModel):
    status: str
    note: Optional[str] = None


@router.get("/api/v1/assets/{asset_id}/lifecycle")
def get_asset_lifecycle(asset_id: int, request: Request):
    """当前生命周期状态与允许的下一状态列表。"""
    conn = get_db_connection()
    if not conn:
        raise HTTPException(status_code=500, detail="Database connection failed")
    cursor = conn.cursor(dictionary=True)
    try:
        cursor.execute("SELECT asset_status FROM assets WHERE id=%s AND deleted_at IS NULL", (asset_id,))
        row = cursor.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Asset not found")
        current = str(row.get("asset_status") or "in_stock")
        return {
            "asset_id": asset_id,
            "current": current,
            "label": ASSET_LIFECYCLE_LABELS.get(current, current),
            "allowed_next": list(ASSET_LIFECYCLE_TRANSITIONS.get(current, ())),
        }
    finally:
        cursor.close()
        conn.close()


@router.put("/api/v1/assets/{asset_id}/lifecycle")
def change_asset_lifecycle(asset_id: int, payload: AssetLifecycleRequest, request: Request):
    """生命周期流转：校验状态机迁移、落库、写资产变更与审计日志。"""
    target = str(payload.status or "").strip()
    if target not in ASSET_LIFECYCLE_LABELS:
        raise HTTPException(status_code=422, detail="无效的生命周期状态")
    conn = get_db_connection()
    if not conn:
        raise HTTPException(status_code=500, detail="Database connection failed")
    cursor = conn.cursor(dictionary=True)
    try:
        cursor.execute(
            "SELECT id, hostname, asset_status, deployment_date FROM assets WHERE id=%s AND deleted_at IS NULL",
            (asset_id,),
        )
        asset = cursor.fetchone()
        if not asset:
            raise HTTPException(status_code=404, detail="Asset not found")
        current = str(asset.get("asset_status") or "in_stock")
        allowed = ASSET_LIFECYCLE_TRANSITIONS.get(current, ())
        if target not in allowed:
            raise HTTPException(
                status_code=422,
                detail=f"不允许从 {ASSET_LIFECYCLE_LABELS.get(current, current)} 流转到 "
                       f"{ASSET_LIFECYCLE_LABELS.get(target, target)}",
            )

        update_fields = ["asset_status=%s"]
        values: list = [target]
        if target == "retired":
            update_fields.append("retire_date=CURDATE()")
            update_fields.append("retire_reason=%s")
            values.append(str(payload.note or "").strip() or "生命周期退役")
        if target == "deployed" and not asset.get("deployment_date"):
            update_fields.append("deployment_date=CURDATE()")
        values.append(asset_id)
        cursor.execute(f"UPDATE assets SET {', '.join(update_fields)} WHERE id=%s", tuple(values))

        after_asset = fetch_asset_row(cursor, asset_id, include_deleted=False)
        record_asset_changes(
            cursor,
            asset_id,
            asset,
            after_asset,
            field_names=("asset_status", "retire_date", "retire_reason", "deployment_date"),
            change_type="lifecycle",
            source_type="manual",
            operator_name=get_request_username(request),
        )
        conn.commit()

        operator = get_request_username(request)
        transition_note = str(payload.note or "").strip()
        record_system_activity_log(SystemActivityLogCreate(
            source_type="platform",
            module="asset",
            category="lifecycle",
            action="lifecycle_change",
            level="info" if target != "retired" else "warning",
            result="success",
            asset_id=asset_id,
            hostname=asset.get("hostname"),
            operator_name=operator,
            title=f"资产生命周期流转: {ASSET_LIFECYCLE_LABELS.get(current, current)} → {ASSET_LIFECYCLE_LABELS.get(target)}",
            message=(f"{ASSET_LIFECYCLE_LABELS.get(current, current)} → "
                     f"{ASSET_LIFECYCLE_LABELS.get(target, target)}") + (f"，备注: {transition_note}" if transition_note else ""),
        ))
        return {
            "message": "生命周期已更新",
            "asset_id": asset_id,
            "current": target,
            "label": ASSET_LIFECYCLE_LABELS.get(target),
            "allowed_next": list(ASSET_LIFECYCLE_TRANSITIONS.get(target, ())),
        }
    except HTTPException:
        raise
    except Error as e:
        conn.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        cursor.close()
        conn.close()


# ============================================================
# Agent Fleet 健康度（P1）：Agent 安装/在线/版本分布/异常聚合
# ============================================================

@router.get("/api/v1/agent-fleet/health")
def agent_fleet_health(request: Request):
    """Agent 队列健康度：安装与在线概况、版本分布、落后与心跳异常清单。"""
    conn = get_db_connection()
    if not conn:
        raise HTTPException(status_code=500, detail="Database connection failed")
    cursor = conn.cursor(dictionary=True)
    try:
        cursor.execute("""
            SELECT id, hostname, ip_address, agent_install_status, agent_version,
                   TIMESTAMPDIFF(SECOND, last_seen, NOW()) AS seconds_since_seen
            FROM assets WHERE deleted_at IS NULL
        """)
        rows = cursor.fetchall()

        total = len(rows)
        installed = [r for r in rows if r.get("agent_install_status") == "installed"]
        online = [r for r in rows if r.get("seconds_since_seen") is not None
                  and r["seconds_since_seen"] <= ALERT_ONLINE_SECONDS]
        not_installed = [r for r in rows if r.get("agent_install_status") != "installed"]

        version_counts: Dict[str, int] = {}
        for r in installed:
            version = str(r.get("agent_version") or "unknown")
            version_counts[version] = version_counts.get(version, 0) + 1
        latest_version = max(version_counts, key=lambda v: tuple(int(x) for x in re.findall(r"\d+", v)) if re.findall(r"\d+", v) else (0,)) \
            if version_counts else None

        anomalies = []
        for r in installed:
            seen = r.get("seconds_since_seen")
            version = str(r.get("agent_version") or "unknown")
            if seen is None:
                anomalies.append({
                    "asset_id": r["id"], "hostname": r.get("hostname"),
                    "ip_address": r.get("ip_address"),
                    "type": "never_seen",
                    "detail": "Agent 已安装但从未上报心跳",
                })
            elif seen > max(ALERT_ONLINE_SECONDS * 3, 300):
                anomalies.append({
                    "asset_id": r["id"], "hostname": r.get("hostname"),
                    "ip_address": r.get("ip_address"),
                    "type": "stale_heartbeat",
                    "detail": f"心跳中断 {seen // 60} 分钟",
                })
            if latest_version and version != latest_version and version != "unknown":
                anomalies.append({
                    "asset_id": r["id"], "hostname": r.get("hostname"),
                    "ip_address": r.get("ip_address"),
                    "type": "version_lag",
                    "detail": f"Agent {version} 落后于最新 {latest_version}",
                })

        return {
            "summary": {
                "total": total,
                "online": len(online),
                "offline": total - len(online),
                "installed": len(installed),
                "not_installed": len(not_installed),
                "anomaly_count": len(anomalies),
            },
            "versions": version_counts,
            "latest_version": latest_version,
            "anomalies": anomalies,
        }
    finally:
        cursor.close()
        conn.close()


# 硬删除时随资产一并清除的关联数据表（V1.9.23 用户决策：手动删除 = 彻底删除）。
# system_activity_logs 为全局审计，不在清除范围内；删除动作本身写入该表。
ASSET_HARD_DELETE_TABLES = (
    "agent_credentials", "agent_heartbeat", "agent_jobs", "agent_patches",
    "agent_tokens", "agent_upgrade_history", "alert_records", "alerts",
    "asset_changes", "asset_software", "batch_operation_results",
    "file_anomaly_events", "incidents", "network_interfaces",
    "network_quality_stats", "network_state", "network_traffic_stats",
    "process_launch_logs", "raw_data", "remote_sessions", "security_events",
    "security_policy_exec_results", "software_compliance_results",
    "software_policy_logs", "software_task_results", "usb_devices",
    "usb_events", "file_protect_baselines",
)


def _hard_delete_asset_data(cursor, asset_id: int) -> int:
    """硬删除资产的全部关联数据，返回删除总行数。单表失败不影响其余表。"""
    total = 0
    for table in ASSET_HARD_DELETE_TABLES:
        try:
            cursor.execute(f"DELETE FROM {table} WHERE asset_id = %s", (asset_id,))
            total += max(cursor.rowcount, 0)
        except Exception as exc:
            safe_console_print(f"[Asset] hard-delete {table} asset_id={asset_id} failed: {exc}")
    return total


def _audit_asset_delete(cursor, asset_id: int, hostname, ip_address, operator, message: str) -> None:
    """资产删除写入操作日志（P0 审计：删除为高风险操作，须可追溯）。"""
    try:
        payload = SystemActivityLogCreate(
            source_type="console",
            module="assets",
            category="资产管理",
            action="delete",
            level="warning",
            asset_id=asset_id,
            hostname=hostname,
            ip_address=ip_address,
            operator_name=operator,
            title="资产删除",
            message=message,
        )
        insert_system_activity_log(cursor, payload)
    except Exception as exc:
        safe_console_print(f"[Asset] audit log failed asset_id={asset_id}: {exc}")


@router.delete("/api/v1/assets/{asset_id}")
def delete_asset(asset_id: int, request: Request, confirm_text: Optional[str] = Query(None)):
    """删除资产（硬删除，V1.9.23 用户决策）。

    需携带 confirm_text="确认删除"（二次确认，防误删/防误调用）。
    资产行与全部关联数据（心跳/软件清单/告警/事件/远控会话/设备凭据等）
    一并物理删除，删除动作写入操作日志（审计）。终端若仍装有 Agent，
    下个心跳将按新资产自动重建。
    """
    if confirm_text != "确认删除":
        raise HTTPException(status_code=400, detail='请输入"确认删除"以继续删除')
    operator = None
    try:
        operator = get_request_username(request)
    except Exception:
        operator = None
    conn = get_db_connection()
    if not conn:
        raise HTTPException(status_code=500, detail="Database connection failed")

    cursor = None
    try:
        cursor = conn.cursor(dictionary=True)
        cursor.execute("SELECT id, hostname, ip_address FROM assets WHERE id = %s", (asset_id,))
        asset = cursor.fetchone()
        if not asset:
            raise HTTPException(status_code=404, detail="Asset not found")
        cursor.close()

        cursor = conn.cursor()
        deleted_rows = _hard_delete_asset_data(cursor, asset_id)
        cursor.execute("DELETE FROM assets WHERE id = %s", (asset_id,))
        deleted_rows += max(cursor.rowcount, 0)
        _audit_asset_delete(
            cursor, asset_id, asset.get("hostname"), asset.get("ip_address"),
            operator, f"硬删除资产 {asset.get('hostname')}(id={asset_id})，含关联数据 {deleted_rows} 行",
        )
        conn.commit()

        safe_console_print(f"[Asset] Hard-deleted asset_id={asset_id} hostname={asset.get('hostname')} rows={deleted_rows}")

        return {"message": "Asset deleted successfully (hard delete)", "deleted_rows": deleted_rows}

    except HTTPException:
        conn.rollback()
        raise
    except Error as e:
        conn.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        if cursor:
            cursor.close()
        conn.close()


@router.post("/api/v1/assets/batch-delete")
def batch_delete_assets(request: dict):
    """批量删除资产（硬删除，V1.9.23 用户决策，语义与单个删除一致）"""
    ids = request.get('ids', [])

    if not ids:
        raise HTTPException(status_code=400, detail="No asset IDs provided")

    # V1.9.23：二次确认（与单个删除一致），防误删/防误调用
    if request.get('confirm_text') != "确认删除":
        raise HTTPException(status_code=400, detail='请输入"确认删除"以继续删除')

    operator = None
    try:
        operator = request.get('operator')
    except Exception:
        operator = None

    conn = get_db_connection()
    if not conn:
        raise HTTPException(status_code=500, detail="Database connection failed")

    cursor = None
    try:
        cursor = conn.cursor(dictionary=True)
        placeholders = ','.join(['%s'] * len(ids))
        cursor.execute(
            f"SELECT id, hostname, ip_address FROM assets WHERE id IN ({placeholders})",
            tuple(ids)
        )
        assets = cursor.fetchall()
        if not assets:
            raise HTTPException(status_code=404, detail="No matching assets")
        cursor.close()

        cursor = conn.cursor()
        deleted_rows = 0
        deleted_assets = []
        for asset in assets:
            deleted_rows += _hard_delete_asset_data(cursor, asset["id"])
            cursor.execute("DELETE FROM assets WHERE id = %s", (asset["id"],))
            deleted_rows += max(cursor.rowcount, 0)
            deleted_assets.append(f"{asset.get('hostname')}(id={asset['id']})")
            _audit_asset_delete(
                cursor, asset["id"], asset.get("hostname"), asset.get("ip_address"),
                operator, f"批量硬删除资产 {asset.get('hostname')}(id={asset['id']})",
            )
        conn.commit()

        safe_console_print(f"[Asset] Batch hard-deleted {len(assets)} assets, rows={deleted_rows}")

        return {
            "message": f"Successfully hard-deleted {len(assets)} assets",
            "deleted_count": len(assets),
            "deleted_rows": deleted_rows,
        }

    except HTTPException:
        conn.rollback()
        raise
    except Error as e:
        conn.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        if cursor:
            cursor.close()
        conn.close()


@router.get("/api/v1/assets/{asset_id}/detail")
def get_asset_detail(asset_id: int):
    """Get terminal detail info."""
    conn = get_db_connection()
    if not conn:
        raise HTTPException(status_code=500, detail="Database connection failed")

    try:
        cursor = conn.cursor(dictionary=True)
        cursor.execute("""
            SELECT a.*,
                   CASE
                       WHEN a.last_seen IS NULL THEN 'offline'
                       WHEN TIMESTAMPDIFF(SECOND, a.last_seen, NOW()) <= 90 THEN 'online'
                       ELSE 'offline'
                   END as real_status
            FROM assets a
            WHERE a.id = %s AND a.deleted_at IS NULL
        """, (asset_id,))
        asset = cursor.fetchone()
        if not asset:
            raise HTTPException(status_code=404, detail="Asset not found")

        asset['status'] = asset.get('real_status') or asset.get('status')
        for field_name in ('last_seen', 'created_at', 'updated_at', 'purchase_date', 'warranty_start', 'warranty_end', 'deployment_date', 'retire_date'):
            if asset.get(field_name):
                asset[field_name] = format_datetime(asset[field_name])

        cursor.execute("""
            SELECT cpu_usage, memory_usage, disk_usage, disk_info,
                   process_count, logged_users, heartbeat_time
            FROM agent_heartbeat
            WHERE asset_id = %s
            ORDER BY heartbeat_time DESC
            LIMIT 1
        """, (asset_id,))
        heartbeat = cursor.fetchone()
        if heartbeat and heartbeat.get('heartbeat_time'):
            heartbeat['heartbeat_time'] = heartbeat['heartbeat_time'].strftime('%Y-%m-%d %H:%M:%S')
            if heartbeat.get('disk_info'):
                try:
                    heartbeat['disk_info'] = json.loads(heartbeat['disk_info'])
                except Exception:
                    heartbeat['disk_info'] = []

        cursor.execute("""
            SELECT software_name, version, vendor, install_date
            FROM asset_software
            WHERE asset_id = %s
            ORDER BY software_name
        """, (asset_id,))
        software_list = cursor.fetchall()

        cursor.execute("""
            SELECT cpu_usage, memory_usage, disk_usage, heartbeat_time
            FROM agent_heartbeat
            WHERE asset_id = %s
            ORDER BY heartbeat_time DESC
            LIMIT 10
        """, (asset_id,))
        heartbeat_history = cursor.fetchall()
        for h in heartbeat_history:
            if h.get('heartbeat_time'):
                h['heartbeat_time'] = h['heartbeat_time'].strftime('%Y-%m-%d %H:%M:%S')

        return {
            "asset": asset,
            "heartbeat": heartbeat,
            "software_list": software_list,
            "heartbeat_history": heartbeat_history
        }
    except Error as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        cursor.close()
        conn.close()


@router.post("/api/v1/assets/{asset_id}/remote-control")
def remote_control(asset_id: int, command: dict):
    """远程桌面连接准备接口，给旧前端调用保留兼容返回。"""
    action = str((command or {}).get("action") or "connect").strip().lower()
    allowed_actions = {"connect", "remote_desktop", "status"}
    if action not in allowed_actions:
        raise HTTPException(status_code=400, detail="不支持的远程控制动作")

    conn = get_db_connection()
    if not conn:
        raise HTTPException(status_code=500, detail="Database connection failed")

    cursor = None
    try:
        cursor = conn.cursor(dictionary=True)
        asset = get_asset_agent_target(cursor, asset_id)
        can_connect = True
        status_message = "ready"
        if not str(asset.get("ip_address") or "").strip():
            can_connect = False
            status_message = "missing_ip_address"
        elif asset.get("agent_install_status") != AGENT_INSTALL_STATUS_INSTALLED:
            can_connect = False
            status_message = "agent_not_installed"
        elif asset.get("resolved_status") != "online":
            can_connect = False
            status_message = "asset_offline"
        return {
            "message": "远程桌面连接已就绪",
            "asset_id": asset_id,
            "action": action,
            "hostname": asset.get("hostname"),
            "ip_address": asset.get("ip_address"),
            "resolved_status": asset.get("resolved_status"),
            "agent_install_status": asset.get("agent_install_status"),
            "can_connect": can_connect,
            "status_message": status_message,
            "proxy_ws_path": f"/api/v1/assets/{asset_id}/remote-desktop/ws",
            "agent_ws_port": 9000,
            "agent_control_port": AGENT_CONTROL_PORT,
        }
    finally:
        if cursor:
            cursor.close()
        conn.close()


@router.get("/api/v1/assets/{asset_id}/status")
def get_asset_status(asset_id: int):
    """Get current status overview for a single asset."""
    conn = get_db_connection()
    if not conn:
        raise HTTPException(status_code=500, detail="Database connection failed")
    try:
        cursor = conn.cursor(dictionary=True)
        cursor.execute("""
            SELECT a.id, a.status, a.last_seen,
                   CASE
                       WHEN a.last_seen IS NULL THEN 'offline'
                       WHEN TIMESTAMPDIFF(SECOND, a.last_seen, NOW()) <= 90 THEN 'online'
                       ELSE 'offline'
                   END as current_status,
                   a.agent_install_status
            FROM assets a
            WHERE a.id = %s AND a.deleted_at IS NULL
        """, (asset_id,))
        status = cursor.fetchone()
        if not status:
            raise HTTPException(status_code=404, detail="Asset not found")
        cursor.execute("""
            SELECT cpu_usage, memory_usage, disk_usage, heartbeat_time
            FROM agent_heartbeat
            WHERE asset_id = %s
            ORDER BY heartbeat_time DESC
            LIMIT 1
        """, (asset_id,))
        heartbeat = cursor.fetchone()
        if heartbeat and heartbeat.get('heartbeat_time'):
            heartbeat['heartbeat_time'] = heartbeat['heartbeat_time'].strftime('%Y-%m-%d %H:%M:%S')
        status['heartbeat'] = heartbeat
        return status
    except Error as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        cursor.close()
        conn.close()


@router.get("/api/v1/assets/{asset_id}/status/history")
def get_asset_status_history(asset_id: int, limit: int = 20):
    """Get recent heartbeat history for an asset."""
    limit = max(1, min(int(limit or 20), 200))
    conn = get_db_connection()
    if not conn:
        raise HTTPException(status_code=500, detail="Database connection failed")

    try:
        cursor = conn.cursor(dictionary=True)
        # 多取一行用于计算相邻心跳间隔（判断离线段），返回时剔除
        cursor.execute("""
            SELECT cpu_usage, memory_usage, disk_usage, process_count,
                   logged_users, heartbeat_time
            FROM agent_heartbeat
            WHERE asset_id = %s
            ORDER BY heartbeat_time DESC
            LIMIT %s
        """, (asset_id, limit + 1))
        rows = cursor.fetchall()
        # V1.9.20 修复：此前不返回 status 字段，前端颜色映射 fallback 全灰。
        # 状态推断：相邻心跳间隔 <=90s（正常 30s 心跳的 3 倍）视为连续在线；
        # 间隔超限 = 两次心跳之间有离线时段，离线前最后一次心跳标 offline；
        # 最旧一行视为 online（心跳成功即在线）；最新一行若距 NOW() 超 90s
        # 也已离线。
        if rows:
            now_dt = datetime.now()
            for i, h in enumerate(rows):
                h_dt = h.get('heartbeat_time')
                if not h_dt:
                    h['status'] = 'online'
                    continue
                gap = None
                if i + 1 < len(rows):
                    prev_dt = rows[i + 1].get('heartbeat_time')
                    if prev_dt:
                        gap = (h_dt - prev_dt).total_seconds()
                else:
                    gap = (now_dt - h_dt).total_seconds()
                if gap is not None and gap > 90:
                    h['status'] = 'offline'
                else:
                    h['status'] = 'online'
                if h.get('heartbeat_time'):
                    h['heartbeat_time'] = h_dt.strftime('%Y-%m-%d %H:%M:%S')
        out_rows = rows[:limit]
        return {"data": out_rows, "total": len(out_rows)}
    except Error as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        cursor.close()
        conn.close()


@router.get("/api/v1/assets/{asset_id}/changes")
def get_asset_changes_route(asset_id: int, page: int = 1, page_size: int = 20):
    """Get change history for an asset."""
    page = max(1, int(page or 1))
    page_size = max(1, min(int(page_size or 20), 200))
    offset = (page - 1) * page_size
    conn = get_db_connection()
    if not conn:
        raise HTTPException(status_code=500, detail="Database connection failed")
    try:
        cursor = conn.cursor(dictionary=True)
        cursor.execute("SELECT COUNT(*) AS total FROM asset_changes WHERE asset_id = %s", (asset_id,))
        total = cursor.fetchone().get('total', 0) or 0
        cursor.execute("""
            SELECT id, change_type, field_name, old_value, new_value,
                   source_type, operator_name, created_at
            FROM asset_changes
            WHERE asset_id = %s
            -- ORDER BY 用 changed_at（与 created_at 值恒等）：命中复合索引
            -- idx_asset_id(asset_id, changed_at)，避免每资产 filesort
            ORDER BY changed_at DESC
            LIMIT %s OFFSET %s
        """, (asset_id, page_size, offset))
        rows = cursor.fetchall()
        for r in rows:
            if r.get('created_at'):
                r['created_at'] = r['created_at'].strftime('%Y-%m-%d %H:%M:%S')
        return {"data": rows, "total": total}
    except Error as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        cursor.close()
        conn.close()


@router.get("/api/v1/assets/{asset_id}/uptime")
def get_asset_uptime_route(asset_id: int, days: int = 7):
    """Get uptime summary for an asset over recent days."""
    days = max(1, min(int(days or 7), 90))
    conn = get_db_connection()
    if not conn:
        raise HTTPException(status_code=500, detail="Database connection failed")
    try:
        cursor = conn.cursor(dictionary=True)
        # Get last_seen to determine if asset is online now
        cursor.execute(
            "SELECT last_seen FROM assets WHERE id = %s",
            (asset_id,)
        )
        row = cursor.fetchone()
        last_seen = row.get('last_seen') if row else None
        is_online = bool(last_seen and (datetime.now() - last_seen).total_seconds() <= 90)
        # Query recent heartbeats for the period (used as "online windows" basis)
        cursor.execute("""
            SELECT heartbeat_time
            FROM agent_heartbeat
            WHERE asset_id = %s
              AND heartbeat_time >= (NOW() - INTERVAL %s DAY)
            ORDER BY heartbeat_time ASC
        """, (asset_id, days))
        rows = cursor.fetchall()
        total_windows = len(rows)
        # assume all heartbeat samples mean online; fallback to "no data" when none
        online_windows = total_windows
        availability_percent = 100.0 if total_windows else 0.0
        # current uptime text
        current_uptime_text = "-"
        if is_online and last_seen:
            delta = datetime.now() - last_seen
            secs = int(delta.total_seconds())
            if secs < 86400:
                h, rem = divmod(secs, 3600)
                m, s = divmod(rem, 60)
                current_uptime_text = f"{h}时 {m}分 {s}秒"
        return {
            "days": days,
            "total_windows": total_windows,
            "online_windows": online_windows,
            "availability_percent": availability_percent,
            "current_uptime_text": current_uptime_text
        }
    except Error as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        cursor.close()
        conn.close()


@router.post("/api/v1/assets/{asset_id}/command")
def execute_asset_command(asset_id: int, payload: AssetCommandRequest, request: Request):
    # P0-10：自由命令需要显式 automation:execute 权限（viewer 天然拒绝）
    require_request_permission(getattr(request.state, "auth_user", None), request.url.path, request.method)
    conn = get_db_connection()
    if not conn:
        raise HTTPException(status_code=500, detail="Database connection failed")

    cursor = None
    try:
        cursor = conn.cursor(dictionary=True)
        asset = get_asset_agent_target(cursor, asset_id)
        requester = get_request_username(request, fallback=payload.requester or payload.operator or "console")
        request_payload = {
            "zview_cmd": {"op": "raw", "command": payload.command, "timeout_seconds": 60},
            "operator": requester,
        }
        result = proxy_agent_json_request(
            asset,
            "/api/v1/command",
            payload=request_payload,
            timeout_seconds=60,
        )
        # 双侧审计：平台侧落 system_activity_logs（Agent 侧另有逐条告警日志）
        try:
            insert_system_activity_log(cursor, SystemActivityLogCreate(
                source_type="platform",
                module="remote_command",
                category="operation",
                action="asset_command",
                level="warning",
                result="success" if result.get("success") else "failed",
                asset_id=asset_id,
                hostname=asset.get("hostname"),
                ip_address=asset.get("ip_address"),
                operator_name=requester,
                title="远程命令执行",
                message=str(payload.command or "")[:2000],
            ))
            conn.commit()
        except Exception:
            conn.rollback()
        return result
    finally:
        if cursor:
            cursor.close()
        conn.close()


@router.post("/api/v1/assets/{asset_id}/trigger-report")
def trigger_asset_report(
    asset_id: int,
    request: Request,
    payload: Optional[AssetTriggerReportRequest] = None,
):
    """通过平台代理触发单终端立即上报"""
    conn = get_db_connection()
    if not conn:
        raise HTTPException(status_code=500, detail="Database connection failed")

    cursor = None
    try:
        cursor = conn.cursor(dictionary=True)
        asset = get_asset_agent_target(cursor, asset_id)
        payload = payload or AssetTriggerReportRequest()
        requester = get_request_username(request, fallback=payload.requester or payload.operator or "console")
        request_payload = {
            "operator": requester,
            "requester": requester,
        }
        return proxy_agent_json_request(
            asset,
            "/api/v1/trigger-report",
            payload=request_payload,
            timeout_seconds=15,
        )
    finally:
        if cursor:
            cursor.close()
        conn.close()

