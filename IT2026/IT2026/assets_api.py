"""
Assets API - 资产管理接口
使用FastAPI实现资产的增删改查
"""

import asyncio
import base64
import csv
import http.client
import io
import ipaddress
import json
import os
import socket
import subprocess
import threading
import time
import uuid
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from datetime import date, datetime, timedelta
from urllib.parse import urlencode, urlparse

import requests
import websockets
from fastapi import FastAPI, HTTPException, Query, Request, Response, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from starlette.websockets import WebSocketState
from typing import List, Optional, Dict, Any, Tuple
import mysql.connector
from mysql.connector import Error
from websockets.exceptions import ConnectionClosed

from auth_utils import (
    authenticate_username_password,
    change_password,
    extract_bearer_token,
    get_expected_agent_token,
    get_auth_profile,
    require_agent_request,
    get_request_username,
    is_exempt_path,
    normalize_actor_name,
    require_request_permission,
    user_has_permission,
    issue_access_token,
    verify_agent_token,
    verify_access_token,
)
from console_utils import enable_utf8_stdio, safe_console_print
from config_utils import get_cors_middleware_options, get_db_config, get_env

SNMP_API_MODE = None

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
    SNMP_API_MODE = "legacy"
    SNMP_IMPORT_ERROR = None
except Exception:
    try:
        from pysnmp.hlapi.asyncio import (
            CommunityData,
            ContextData,
            ObjectIdentity,
            ObjectType,
            SnmpEngine,
            UdpTransportTarget,
            get_cmd as getCmd,
        )
        SNMP_API_MODE = "asyncio"
        SNMP_IMPORT_ERROR = None
    except Exception as exc:  # pragma: no cover - runtime environment dependent
        CommunityData = None
        ContextData = None
        ObjectIdentity = None
        ObjectType = None
        SnmpEngine = None
        UdpTransportTarget = None
        getCmd = None
        SNMP_IMPORT_ERROR = str(exc)


enable_utf8_stdio()

app = FastAPI(title="Z-View Assets API", version="1.0.0")

# 配置CORS
app.add_middleware(
    CORSMiddleware,
    **get_cors_middleware_options(),
)

AUTH_EXEMPTIONS = (
    {"path": "/api/v1/auth/login", "methods": ["POST"]},
    {"path": "/api/v1/agent/heartbeat", "methods": ["POST"]},
    {"path": "/api/v1/logs", "methods": ["POST"]},
)

# 数据库配置
DB_CONFIG = get_db_config()

ALERT_ONLINE_SECONDS = 90
ALERT_OFFLINE_SECONDS = 180
STATUS_RECONCILE_INTERVAL_SECONDS = 30
AGENT_CONTROL_PORT = int(get_env("ZVIEW_AGENT_CONTROL_PORT", "9001") or "9001")
ALERT_THRESHOLDS = {
    'cpu': {'warning': 80.0, 'critical': 90.0},
    'memory': {'warning': 90.0, 'critical': 95.0},
    'disk': {'warning': 90.0, 'critical': 95.0},
    'health': {'warning': 60.0, 'critical': 40.0}
}
ALERT_TYPE_LABELS = {
    'cpu': 'CPU使用率',
    'memory': '内存使用率',
    'disk': '磁盘使用率',
    'offline': '终端离线',
    'health': '健康度'
}
DISCOVERY_TASK_RETENTION_SECONDS = 24 * 60 * 60
DISCOVERY_MAX_TASKS = 100
DISCOVERY_MAX_TARGETS = 4096
DISCOVERY_TASKS: Dict[str, Dict[str, Any]] = {}
DISCOVERY_TASK_LOCK = threading.Lock()
PING_CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
STATUS_RECONCILE_LOCK = threading.Lock()
STATUS_RECONCILE_THREAD = None
STATUS_RECONCILE_STARTED = False
AGENT_INSTALL_STATUS_INSTALLED = "installed"
AGENT_INSTALL_STATUS_NOT_INSTALLED = "not_installed"


class SystemActivityLogCreate(BaseModel):
    source_type: str = "agent"
    module: str
    category: Optional[str] = None
    action: str
    level: str = "info"
    result: Optional[str] = None
    asset_id: Optional[int] = None
    hostname: Optional[str] = None
    ip_address: Optional[str] = None
    operator_name: Optional[str] = None
    session_id: Optional[str] = None
    title: Optional[str] = None
    message: str
    event_time: Optional[datetime] = None
    details: Optional[Any] = None
    stdout_log: Optional[str] = None
    stderr_log: Optional[str] = None


class LoginRequest(BaseModel):
    username: str
    password: str


class ChangePasswordRequest(BaseModel):
    current_password: str = Field(..., min_length=1)
    new_password: str = Field(..., min_length=6)


class BatchExecuteRequest(BaseModel):
    operation_type: str
    terminal_ids: List[int]
    parameters: Dict[str, Any] = Field(default_factory=dict)
    operator_name: Optional[str] = "console"


class DiscoveryPingRequest(BaseModel):
    ip_ranges: List[str] = Field(default_factory=list)
    concurrency: int = Field(default=100, ge=1, le=1000)
    timeout: int = Field(default=3000, ge=500, le=10000)


class DiscoverySNMPTarget(BaseModel):
    ip: str
    community: str = "public"


class DiscoverySNMPRequest(BaseModel):
    targets: List[DiscoverySNMPTarget] = Field(default_factory=list)
    version: int = Field(default=2)
    timeout: int = Field(default=5, ge=1, le=30)


class AlertBatchResolveRequest(BaseModel):
    ids: List[int] = Field(default_factory=list)
    resolved_by: Optional[str] = "console"


class AssetCommandRequest(BaseModel):
    command: str = Field(..., min_length=1)
    operator: Optional[str] = None
    requester: Optional[str] = None


class AssetTriggerReportRequest(BaseModel):
    operator: Optional[str] = None
    requester: Optional[str] = None


@app.middleware("http")
async def auth_middleware(request: Request, call_next):
    if is_exempt_path(request.url.path, request.method, AUTH_EXEMPTIONS):
        return await call_next(request)

    token = extract_bearer_token(request)
    auth_user = verify_access_token(token)
    if not auth_user:
        return JSONResponse(
            status_code=401,
            content={"detail": "Unauthorized"},
        )

    try:
        require_request_permission(auth_user, request.url.path, request.method)
    except HTTPException as exc:
        return JSONResponse(
            status_code=exc.status_code,
            content={"detail": exc.detail},
        )

    request.state.auth_user = auth_user
    return await call_next(request)


def get_db_connection():
    """获取数据库连接"""
    try:
        conn = mysql.connector.connect(**DB_CONFIG)
        # 设置会话时区为北京时间
        cursor = conn.cursor()
        cursor.execute("SET time_zone = '+8:00'")
        cursor.close()
        return conn
    except Error as e:
        safe_console_print(f"[DB] Connection failed: {e}")
        return None


def format_datetime(value):
    """统一格式化时间"""
    if not value:
        return None
    if isinstance(value, datetime):
        return value.strftime('%Y-%m-%d %H:%M:%S')
    if isinstance(value, date):
        return value.strftime('%Y-%m-%d')
    return str(value)


def parse_json_field(value):
    """尽量将 JSON 字段恢复为结构化对象，失败时保留原值。"""
    if value in (None, "", b""):
        return None
    if isinstance(value, (dict, list)):
        return value
    if isinstance(value, bytes):
        value = value.decode("utf-8", errors="ignore")
    if not isinstance(value, str):
        return value
    try:
        return json.loads(value)
    except (TypeError, ValueError, json.JSONDecodeError):
        return value


def resolve_asset_online_status(asset: Dict[str, Any]) -> str:
    last_seen = asset.get("last_seen")
    if isinstance(last_seen, datetime):
        age_seconds = (datetime.now() - last_seen).total_seconds()
        return "online" if age_seconds <= ALERT_ONLINE_SECONDS else "offline"

    status = str(asset.get("status") or "").strip().lower()
    return status or "unknown"


def get_asset_agent_target(cursor, asset_id: int) -> Dict[str, Any]:
    cursor.execute(
        """
        SELECT id, hostname, ip_address, status, agent_install_status, last_seen
        FROM assets
        WHERE id = %s AND deleted_at IS NULL
        LIMIT 1
        """,
        (asset_id,),
    )
    asset = cursor.fetchone()
    if not asset:
        raise HTTPException(status_code=404, detail="Asset not found")

    asset["resolved_status"] = resolve_asset_online_status(asset)
    return asset


def normalize_history_value(value: Any) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, datetime):
        return format_datetime(value)
    if isinstance(value, (dict, list)):
        try:
            return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
        except Exception:
            return str(value)
    if isinstance(value, bytes):
        value = value.decode("utf-8", errors="ignore")
    if isinstance(value, bool):
        return "1" if value else "0"
    text = str(value).strip()
    return text if text else None


def ensure_asset_changes_table(conn):
    cursor = conn.cursor()
    try:
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS asset_changes (
                id BIGINT PRIMARY KEY AUTO_INCREMENT,
                asset_id BIGINT NOT NULL,
                change_type VARCHAR(50) NOT NULL,
                field_name VARCHAR(100) NOT NULL,
                old_value LONGTEXT NULL,
                new_value LONGTEXT NULL,
                source_type VARCHAR(50) NOT NULL DEFAULT 'platform',
                operator_name VARCHAR(120) NULL,
                details_json LONGTEXT NULL,
                created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                INDEX idx_asset_changes_asset_id (asset_id),
                INDEX idx_asset_changes_change_type (change_type),
                INDEX idx_asset_changes_field_name (field_name),
                INDEX idx_asset_changes_created_at (created_at)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='资产变更历史表'
            """
        )
        conn.commit()
    finally:
        cursor.close()


def record_asset_change_entry(
    cursor,
    asset_id: int,
    field_name: str,
    old_value: Any,
    new_value: Any,
    *,
    change_type: str,
    source_type: str = "platform",
    operator_name: Optional[str] = None,
    details: Optional[Dict[str, Any]] = None,
) -> bool:
    normalized_old = normalize_history_value(old_value)
    normalized_new = normalize_history_value(new_value)
    if normalized_old == normalized_new:
        return False

    details_json = None
    if details is not None:
        try:
            details_json = json.dumps(details, ensure_ascii=False, default=str)
        except Exception:
            details_json = json.dumps({"value": str(details)}, ensure_ascii=False)

    cursor.execute(
        """
        INSERT INTO asset_changes (
            asset_id, change_type, field_name, old_value, new_value,
            source_type, operator_name, details_json, created_at
        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, NOW())
        """,
        (
            asset_id,
            change_type,
            field_name,
            normalized_old,
            normalized_new,
            source_type,
            operator_name,
            details_json,
        ),
    )
    return True


def record_asset_changes(
    cursor,
    asset_id: int,
    before_row: Optional[Dict[str, Any]],
    after_row: Optional[Dict[str, Any]],
    *,
    field_names: List[str],
    change_type: str,
    source_type: str = "platform",
    operator_name: Optional[str] = None,
    details: Optional[Dict[str, Any]] = None,
) -> int:
    if not after_row:
        return 0

    affected = 0
    before_row = before_row or {}
    for field_name in field_names:
        if field_name not in after_row:
            continue
        if record_asset_change_entry(
            cursor,
            asset_id,
            field_name,
            before_row.get(field_name),
            after_row.get(field_name),
            change_type=change_type,
            source_type=source_type,
            operator_name=operator_name,
            details=details,
        ):
            affected += 1
    return affected


def fetch_asset_row(cursor, asset_id: int, *, include_deleted: bool = False) -> Optional[Dict[str, Any]]:
    where_clause = "id = %s"
    if not include_deleted:
        where_clause += " AND deleted_at IS NULL"
    cursor.execute(
        f"""
        SELECT *
        FROM assets
        WHERE {where_clause}
        LIMIT 1
        """,
        (asset_id,),
    )
    return cursor.fetchone()


def format_duration_text(total_seconds: Optional[int]) -> str:
    try:
        seconds = max(0, int(total_seconds or 0))
    except (TypeError, ValueError):
        return "-"
    hours, remainder = divmod(seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    return f"{hours}h {minutes}m {seconds}s"


def estimate_online_seconds_from_heartbeats(
    heartbeat_rows: List[Dict[str, Any]],
    *,
    window_end: Optional[datetime] = None,
    max_gap_seconds: int = ALERT_OFFLINE_SECONDS,
) -> int:
    heartbeat_times = [
        row.get("heartbeat_time")
        for row in heartbeat_rows
        if isinstance(row.get("heartbeat_time"), datetime)
    ]
    if not heartbeat_times:
        return 0

    sorted_times = sorted(heartbeat_times)
    online_seconds = 0
    for index, current_time in enumerate(sorted_times):
        next_time = sorted_times[index + 1] if index + 1 < len(sorted_times) else (window_end or datetime.now())
        delta_seconds = max(0, int((next_time - current_time).total_seconds()))
        online_seconds += min(delta_seconds, max_gap_seconds)
    return online_seconds


def get_asset_uptime_seconds(asset_row: Dict[str, Any], heartbeat_row: Optional[Dict[str, Any]] = None) -> int:
    if not asset_row:
        return 0

    last_seen = asset_row.get("last_seen")
    if not isinstance(last_seen, datetime):
        return 0

    resolved_status = resolve_asset_online_status(asset_row)
    if resolved_status != "online":
        return 0

    try:
        return max(0, int((datetime.now() - last_seen).total_seconds()))
    except Exception:
        return 0


def serialize_asset_change_row(row: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "id": row.get("id"),
        "asset_id": row.get("asset_id"),
        "change_type": row.get("change_type"),
        "field_name": row.get("field_name"),
        "old_value": parse_json_field(row.get("old_value")),
        "new_value": parse_json_field(row.get("new_value")),
        "source_type": row.get("source_type") or "platform",
        "operator_name": row.get("operator_name"),
        "details": parse_json_field(row.get("details_json")),
        "created_at": format_datetime(row.get("created_at")),
    }


def build_agent_auth_headers(extra_headers: Optional[Dict[str, str]] = None) -> Dict[str, str]:
    headers: Dict[str, str] = {}
    token = str(get_expected_agent_token() or "").strip()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if extra_headers:
        headers.update(extra_headers)
    return headers


def proxy_agent_json_request(
    asset: Dict[str, Any],
    path: str,
    payload: Optional[Dict[str, Any]] = None,
    timeout_seconds: int = 10,
) -> JSONResponse:
    ip_address = str(asset.get("ip_address") or "").strip()
    if not ip_address:
        raise HTTPException(status_code=400, detail="Asset IP address is missing")

    if asset.get("agent_install_status") != AGENT_INSTALL_STATUS_INSTALLED:
        raise HTTPException(status_code=409, detail="Agent is not installed on target asset")

    if asset.get("resolved_status") != "online":
        raise HTTPException(status_code=409, detail="Target asset is offline")

    request_body = json.dumps(payload or {})
    response = None
    raw_body = ""
    try:
        connection = http.client.HTTPConnection(ip_address, AGENT_CONTROL_PORT, timeout=timeout_seconds)
        try:
            connection.request(
                "POST",
                path,
                body=request_body,
                headers=build_agent_auth_headers({
                    "Content-Type": "application/json",
                    "Connection": "close",
                }),
            )
            response = connection.getresponse()
            raw_body = response.read().decode("utf-8", errors="ignore")
        finally:
            connection.close()
    except OSError as exc:
        raise HTTPException(
            status_code=502,
            detail=f"Agent request failed: {type(exc).__name__}: {exc}",
        ) from exc

    try:
        body = json.loads(raw_body) if raw_body else {}
    except ValueError:
        body = {
            "success": 200 <= response.status < 300,
            "message": raw_body.strip() or "Agent returned non-JSON response",
        }

    if isinstance(body, dict):
        body.setdefault("asset_id", asset.get("id"))
        body.setdefault("hostname", asset.get("hostname"))
        body.setdefault("ip_address", ip_address)

    if response.status in (401, 403):
        detail_message = ""
        if isinstance(body, dict):
            detail_message = str(
                body.get("detail")
                or body.get("message")
                or body.get("error")
                or ""
            ).strip()

        return JSONResponse(
            status_code=502,
            content={
                "success": False,
                "message": "Agent rejected platform request: authentication mismatch",
                "detail": detail_message or "Agent control-plane authentication failed",
                "upstream_status": response.status,
                "asset_id": asset.get("id"),
                "hostname": asset.get("hostname"),
                "ip_address": ip_address,
            },
        )

    return JSONResponse(status_code=response.status, content=body)


def authenticate_websocket_request(websocket: WebSocket) -> Optional[Dict[str, Any]]:
    token = str(websocket.query_params.get("token") or "").strip()
    if not token:
        return None
    return verify_access_token(token)


def get_remote_desktop_requester(websocket: WebSocket, auth_user: Optional[Dict[str, Any]] = None) -> str:
    if auth_user and auth_user.get("username"):
        return normalize_actor_name(auth_user["username"], fallback="console")
    requester = str(websocket.query_params.get("requester") or "").strip()
    return normalize_actor_name(requester, fallback="console")


async def close_browser_websocket(
    websocket: WebSocket,
    code: int = 1011,
    reason: str = "",
) -> None:
    code = normalize_browser_websocket_close_code(code)
    try:
        await websocket.close(code=code, reason=reason[:120] if reason else None)
    except RuntimeError:
        pass
    except Exception:
        pass


async def close_upstream_websocket(
    upstream_socket,
    *,
    code: int = 1000,
    reason: str = "",
) -> None:
    try:
        if getattr(upstream_socket, "closed", False):
            return
    except Exception:
        pass

    try:
        await upstream_socket.close(code=code, reason=reason[:120] if reason else "")
    except TypeError:
        try:
            await upstream_socket.close()
        except Exception:
            pass
    except Exception:
        pass


async def send_browser_session_error(
    websocket: WebSocket,
    message: str,
    *,
    code: int = 1011,
) -> None:
    safe_message = str(message or "Remote desktop session failed")[:240]

    try:
        if websocket.application_state == WebSocketState.CONNECTING:
            await websocket.accept()
    except Exception:
        await close_browser_websocket(websocket, code=code, reason=safe_message)
        return

    try:
        if websocket.application_state == WebSocketState.CONNECTED:
            await websocket.send_text(
                json.dumps(
                    {
                        "type": "session_error",
                        "message": safe_message,
                    },
                    ensure_ascii=False,
                )
            )
    except Exception:
        pass

    await close_browser_websocket(websocket, code=code, reason=safe_message)


async def relay_browser_to_agent(websocket: WebSocket, upstream_socket, *, asset_id: int) -> None:
    should_close_upstream = False
    try:
        while True:
            message = await websocket.receive()
            message_type = message.get("type")

            if message_type == "websocket.disconnect":
                should_close_upstream = True
                safe_console_print(f"[RemoteDesktopProxy] asset={asset_id} browser websocket disconnected")
                return "browser_disconnected"

            text_data = message.get("text")
            if text_data is not None:
                await upstream_socket.send(text_data)
                continue

            binary_data = message.get("bytes")
            if binary_data is not None:
                await upstream_socket.send(binary_data)
    except WebSocketDisconnect:
        should_close_upstream = True
        safe_console_print(f"[RemoteDesktopProxy] asset={asset_id} browser websocket disconnect exception")
        return "browser_disconnect_exception"
    except asyncio.CancelledError:
        safe_console_print(f"[RemoteDesktopProxy] asset={asset_id} browser relay cancelled")
        raise
    except Exception as exc:
        should_close_upstream = True
        safe_console_print(f"[RemoteDesktopProxy] asset={asset_id} browser relay failed: {exc}")
        raise
    finally:
        if should_close_upstream:
            await close_upstream_websocket(
                upstream_socket,
                code=1000,
                reason="browser_disconnected",
            )


async def relay_agent_to_browser(websocket: WebSocket, upstream_socket, *, asset_id: int) -> None:
    close_code = 1000
    close_reason = ""
    outcome = "upstream_stream_ended"
    try:
        async for payload in upstream_socket:
            if isinstance(payload, bytes):
                await websocket.send_bytes(payload)
            else:
                await websocket.send_text(payload)
        safe_console_print(f"[RemoteDesktopProxy] asset={asset_id} upstream stream ended")
    except ConnectionClosed as exc:
        upstream_code = getattr(exc, "code", None)
        upstream_reason = str(getattr(exc, "reason", "") or "").strip()
        close_code = normalize_browser_websocket_close_code(
            upstream_code,
            default=1000 if upstream_code == 1000 else 1011,
        )
        close_reason = upstream_reason
        outcome = f"upstream_closed:{upstream_code or 'unknown'}"
        safe_console_print(
            f"[RemoteDesktopProxy] asset={asset_id} upstream closed: code={upstream_code} "
            f"mapped_code={close_code} reason={upstream_reason or '-'}"
        )
    except asyncio.CancelledError:
        safe_console_print(f"[RemoteDesktopProxy] asset={asset_id} upstream relay cancelled")
        raise
    except Exception as exc:
        close_code = 1011
        close_reason = "remote_desktop_upstream_closed"
        outcome = "upstream_relay_failed"
        safe_console_print(f"[RemoteDesktopProxy] asset={asset_id} relay upstream failed: {exc}")
    finally:
        await close_browser_websocket(websocket, code=close_code, reason=close_reason)
    return outcome


def log_remote_desktop_task_result(asset_id: int, task_name: str, task: asyncio.Task) -> None:
    if task.cancelled():
        safe_console_print(f"[RemoteDesktopProxy] asset={asset_id} task={task_name} cancelled")
        return

    exc = task.exception()
    if exc is None:
        safe_console_print(f"[RemoteDesktopProxy] asset={asset_id} task={task_name} completed")
        return

    safe_console_print(
        f"[RemoteDesktopProxy] asset={asset_id} task={task_name} failed: "
        f"{type(exc).__name__}: {exc}"
    )


def get_remote_desktop_task_name(
    browser_to_agent_task: asyncio.Task,
    agent_to_browser_task: asyncio.Task,
    task: asyncio.Task,
) -> str:
    if task is browser_to_agent_task:
        return "browser_to_agent"
    if task is agent_to_browser_task:
        return "agent_to_browser"
    return "unknown"


def normalize_browser_websocket_close_code(code, default: int = 1011) -> int:
    try:
        normalized = int(code)
    except (TypeError, ValueError):
        return default

    if normalized in {1005, 1006, 1015}:
        return default

    if 1000 <= normalized <= 1014:
        return normalized

    if 3000 <= normalized <= 4999:
        return normalized

    return default


def safe_float(value):
    """安全转换浮点数"""
    if value is None:
        return None
    try:
        return round(float(value), 2)
    except (TypeError, ValueError):
        return None


def compute_health_score(status: Optional[str], cpu_usage, memory_usage, disk_usage):
    """按终端概览一致的规则计算健康度"""
    if status != 'online':
        return 0

    metrics = [cpu_usage, memory_usage, disk_usage]
    if all(metric is None for metric in metrics):
        return None

    score = 40

    if cpu_usage is not None:
        if cpu_usage < 70:
            score += 20
        elif cpu_usage < 80:
            score += 10
        elif cpu_usage < 90:
            score += 5
    else:
        score += 20

    if memory_usage is not None:
        if memory_usage < 80:
            score += 20
        elif memory_usage < 90:
            score += 10
        elif memory_usage < 95:
            score += 5
    else:
        score += 20

    if disk_usage is not None:
        if disk_usage < 85:
            score += 20
        elif disk_usage < 90:
            score += 10
        elif disk_usage < 95:
            score += 5
    else:
        score += 20

    return score


def reconcile_asset_statuses() -> Dict[str, int]:
    """按 last_seen 实时回写资产状态，避免 status 列长期滞后。"""
    conn = get_db_connection()
    if not conn:
        return {"online_updated": 0, "offline_updated": 0}

    cursor = conn.cursor()
    try:
        cursor.execute("""
            UPDATE assets
            SET status = 'online', updated_at = NOW()
            WHERE deleted_at IS NULL
              AND last_seen IS NOT NULL
              AND TIMESTAMPDIFF(SECOND, last_seen, NOW()) <= %s
              AND (status IS NULL OR status <> 'online')
        """, (ALERT_ONLINE_SECONDS,))
        online_updated = cursor.rowcount or 0

        cursor.execute("""
            UPDATE assets
            SET status = 'offline', updated_at = NOW()
            WHERE deleted_at IS NULL
              AND (last_seen IS NULL OR TIMESTAMPDIFF(SECOND, last_seen, NOW()) > %s)
              AND (status IS NULL OR status <> 'offline')
        """, (ALERT_ONLINE_SECONDS,))
        offline_updated = cursor.rowcount or 0

        conn.commit()
        return {
            "online_updated": int(online_updated),
            "offline_updated": int(offline_updated),
        }
    except Error as e:
        conn.rollback()
        safe_console_print(f"[StatusReconcile] Reconcile failed: {e}")
        return {"online_updated": 0, "offline_updated": 0}
    finally:
        cursor.close()
        conn.close()


def status_reconcile_loop():
    """后台状态对账线程。"""
    safe_console_print(
        f"[StatusReconcile] Worker started; interval={STATUS_RECONCILE_INTERVAL_SECONDS}s "
        f"online_threshold={ALERT_ONLINE_SECONDS}s"
    )

    while True:
        try:
            result = reconcile_asset_statuses()
            if result["online_updated"] or result["offline_updated"]:
                safe_console_print(
                    "[StatusReconcile] Status synced: "
                    f"online_updated={result['online_updated']} "
                    f"offline_updated={result['offline_updated']}"
                )
        except Exception as exc:
            safe_console_print(f"[StatusReconcile] Worker error: {exc}")
        time.sleep(STATUS_RECONCILE_INTERVAL_SECONDS)


def ensure_status_reconcile_worker_started():
    """确保状态对账线程只启动一次。"""
    global STATUS_RECONCILE_THREAD, STATUS_RECONCILE_STARTED

    with STATUS_RECONCILE_LOCK:
        if STATUS_RECONCILE_STARTED and STATUS_RECONCILE_THREAD and STATUS_RECONCILE_THREAD.is_alive():
            return

        reconcile_asset_statuses()

        STATUS_RECONCILE_THREAD = threading.Thread(
            target=status_reconcile_loop,
            daemon=True,
            name="asset-status-reconcile",
        )
        STATUS_RECONCILE_THREAD.start()
        STATUS_RECONCILE_STARTED = True


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


def ensure_alerts_table(conn):
    """确保告警表存在"""
    cursor = conn.cursor()
    try:
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS alerts (
                id BIGINT PRIMARY KEY AUTO_INCREMENT,
                asset_id BIGINT NOT NULL,
                alert_type VARCHAR(50) NOT NULL,
                severity VARCHAR(20) NOT NULL DEFAULT 'warning',
                status VARCHAR(20) NOT NULL DEFAULT 'active',
                message VARCHAR(500) NOT NULL,
                current_value DECIMAL(10,2) NULL,
                threshold_value DECIMAL(10,2) NULL,
                details_json JSON NULL,
                active_fingerprint VARCHAR(255) NULL,
                first_triggered_at DATETIME NOT NULL,
                last_seen_at DATETIME NOT NULL,
                resolved_at DATETIME NULL,
                resolved_by VARCHAR(100) NULL,
                created_at DATETIME NOT NULL,
                updated_at DATETIME NOT NULL,
                INDEX idx_alert_status (status),
                INDEX idx_alert_asset (asset_id),
                INDEX idx_alert_type (alert_type),
                INDEX idx_alert_last_seen (last_seen_at),
                UNIQUE KEY uk_alert_active_fingerprint (active_fingerprint)
            )
        """)
        conn.commit()
    finally:
        cursor.close()


def ensure_system_activity_logs_table(conn):
    """确保统一运行时日志表存在。"""
    cursor = conn.cursor()
    try:
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS system_activity_logs (
                id BIGINT PRIMARY KEY AUTO_INCREMENT,
                source_type VARCHAR(50) NOT NULL DEFAULT 'agent',
                module VARCHAR(100) NOT NULL,
                category VARCHAR(100) NULL,
                action VARCHAR(100) NOT NULL,
                level VARCHAR(20) NOT NULL DEFAULT 'info',
                result VARCHAR(50) NULL,
                asset_id BIGINT NULL,
                hostname VARCHAR(255) NULL,
                ip_address VARCHAR(64) NULL,
                operator_name VARCHAR(100) NULL,
                session_id VARCHAR(100) NULL,
                title VARCHAR(255) NULL,
                message TEXT NOT NULL,
                details_json JSON NULL,
                stdout_log MEDIUMTEXT NULL,
                stderr_log MEDIUMTEXT NULL,
                event_time DATETIME NOT NULL,
                created_at DATETIME NOT NULL,
                INDEX idx_system_activity_event_time (event_time),
                INDEX idx_system_activity_module (module),
                INDEX idx_system_activity_asset (asset_id),
                INDEX idx_system_activity_source_type (source_type)
            )
        """)
        conn.commit()
    finally:
        cursor.close()


def ensure_batch_tables(conn):
    """确保批量操作主表和结果表存在。"""
    cursor = conn.cursor()
    try:
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS batch_operations (
                id BIGINT UNSIGNED PRIMARY KEY AUTO_INCREMENT,
                operation_type VARCHAR(50) NOT NULL,
                operator_name VARCHAR(100) NULL,
                parameters_json JSON NULL,
                parameters_text TEXT NULL,
                target_count INT NOT NULL DEFAULT 0,
                success_count INT NOT NULL DEFAULT 0,
                failed_count INT NOT NULL DEFAULT 0,
                created_at DATETIME NOT NULL,
                completed_at DATETIME NULL,
                INDEX idx_batch_operations_created_at (created_at)
            )
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS batch_operation_results (
                id BIGINT UNSIGNED PRIMARY KEY AUTO_INCREMENT,
                operation_id BIGINT UNSIGNED NOT NULL,
                asset_id BIGINT UNSIGNED NULL,
                hostname VARCHAR(255) NULL,
                ip_address VARCHAR(64) NULL,
                status VARCHAR(20) NOT NULL DEFAULT 'pending',
                command_text TEXT NULL,
                stdout_log MEDIUMTEXT NULL,
                stderr_log MEDIUMTEXT NULL,
                output_text MEDIUMTEXT NULL,
                returncode INT NULL,
                error_message TEXT NULL,
                executed_at DATETIME NULL,
                created_at DATETIME NOT NULL,
                INDEX idx_batch_operation_results_operation (operation_id),
                INDEX idx_batch_operation_results_asset (asset_id),
                CONSTRAINT fk_batch_operation_results_operation
                    FOREIGN KEY (operation_id) REFERENCES batch_operations(id)
                    ON DELETE CASCADE
            )
        """)
        cursor.execute("""
            SELECT
                table_name,
                column_name,
                column_type,
                is_nullable
            FROM information_schema.columns
            WHERE table_schema = %s
              AND table_name IN ('batch_operations', 'batch_operation_results')
        """, (DB_CONFIG["database"],))
        existing_columns = {
            (row[0], row[1]): {
                "column_type": (row[2] or "").lower(),
                "is_nullable": row[3],
            }
            for row in cursor.fetchall()
        }

        operation_column_sql = {
            "operator_name": "ALTER TABLE batch_operations ADD COLUMN operator_name VARCHAR(100) NULL AFTER operation_type",
            "parameters_json": "ALTER TABLE batch_operations ADD COLUMN parameters_json JSON NULL AFTER operator_name",
            "parameters_text": "ALTER TABLE batch_operations ADD COLUMN parameters_text TEXT NULL AFTER parameters_json",
            "target_count": "ALTER TABLE batch_operations ADD COLUMN target_count INT NOT NULL DEFAULT 0 AFTER parameters_text",
            "success_count": "ALTER TABLE batch_operations MODIFY COLUMN success_count INT NOT NULL DEFAULT 0",
            "failed_count": "ALTER TABLE batch_operations MODIFY COLUMN failed_count INT NOT NULL DEFAULT 0",
            "completed_at": "ALTER TABLE batch_operations ADD COLUMN completed_at DATETIME NULL AFTER created_at",
        }
        for column_name, sql in operation_column_sql.items():
            if ("batch_operations", column_name) not in existing_columns:
                cursor.execute(sql)

        if ("batch_operations", "success_count") in existing_columns:
            success_meta = existing_columns[("batch_operations", "success_count")]
            if success_meta["is_nullable"] == "YES":
                cursor.execute("""
                    UPDATE batch_operations
                    SET success_count = 0
                    WHERE success_count IS NULL
                """)
                cursor.execute("ALTER TABLE batch_operations MODIFY COLUMN success_count INT NOT NULL DEFAULT 0")

        if ("batch_operations", "failed_count") in existing_columns:
            failed_meta = existing_columns[("batch_operations", "failed_count")]
            if failed_meta["is_nullable"] == "YES":
                cursor.execute("""
                    UPDATE batch_operations
                    SET failed_count = 0
                    WHERE failed_count IS NULL
                """)
                cursor.execute("ALTER TABLE batch_operations MODIFY COLUMN failed_count INT NOT NULL DEFAULT 0")

        if ("batch_operations", "parameters") in existing_columns:
            cursor.execute("""
                UPDATE batch_operations
                SET parameters_text = COALESCE(parameters_text, parameters)
                WHERE parameters IS NOT NULL
                  AND (parameters_text IS NULL OR parameters_text = '')
            """)

        result_column_sql = {
            "hostname": "ALTER TABLE batch_operation_results ADD COLUMN hostname VARCHAR(255) NULL AFTER asset_id",
            "ip_address": "ALTER TABLE batch_operation_results ADD COLUMN ip_address VARCHAR(64) NULL AFTER hostname",
            "command_text": "ALTER TABLE batch_operation_results ADD COLUMN command_text TEXT NULL AFTER status",
            "stdout_log": "ALTER TABLE batch_operation_results ADD COLUMN stdout_log MEDIUMTEXT NULL AFTER command_text",
            "stderr_log": "ALTER TABLE batch_operation_results ADD COLUMN stderr_log MEDIUMTEXT NULL AFTER stdout_log",
            "output_text": "ALTER TABLE batch_operation_results ADD COLUMN output_text MEDIUMTEXT NULL AFTER stderr_log",
            "returncode": "ALTER TABLE batch_operation_results ADD COLUMN returncode INT NULL AFTER output_text",
            "error_message": "ALTER TABLE batch_operation_results ADD COLUMN error_message TEXT NULL AFTER returncode",
            "created_at": "ALTER TABLE batch_operation_results ADD COLUMN created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP AFTER executed_at",
        }
        for column_name, sql in result_column_sql.items():
            if ("batch_operation_results", column_name) not in existing_columns:
                cursor.execute(sql)

        asset_meta = existing_columns.get(("batch_operation_results", "asset_id"))
        if asset_meta and asset_meta["is_nullable"] == "NO":
            cursor.execute("ALTER TABLE batch_operation_results MODIFY COLUMN asset_id BIGINT UNSIGNED NULL")

        if ("batch_operation_results", "output") in existing_columns:
            cursor.execute("""
                UPDATE batch_operation_results
                SET output_text = COALESCE(output_text, output)
                WHERE output IS NOT NULL
                  AND (output_text IS NULL OR output_text = '')
            """)

        if ("batch_operation_results", "created_at") in existing_columns:
            cursor.execute("""
                UPDATE batch_operation_results
                SET created_at = COALESCE(created_at, executed_at, NOW())
                WHERE created_at IS NULL
            """)
        conn.commit()
    finally:
        cursor.close()


def ensure_assets_agent_schema(conn):
    """Ensure the assets table has the runtime metadata columns used by the UI."""
    cursor = conn.cursor()
    try:
        cursor.execute(
            """
            SELECT column_name
            FROM information_schema.columns
            WHERE table_schema = %s
              AND table_name = 'assets'
            """,
            (DB_CONFIG["database"],),
        )
        existing_columns = {row[0] for row in cursor.fetchall()}

        asset_column_sql = {
            "agent_install_status": f"""
                ALTER TABLE assets
                ADD COLUMN agent_install_status VARCHAR(20) NOT NULL
                DEFAULT '{AGENT_INSTALL_STATUS_NOT_INSTALLED}'
                AFTER status
            """,
            "purchase_date": "ALTER TABLE assets ADD COLUMN purchase_date DATE NULL AFTER owner",
            "purchase_price": "ALTER TABLE assets ADD COLUMN purchase_price DECIMAL(12,2) NULL AFTER purchase_date",
            "supplier": "ALTER TABLE assets ADD COLUMN supplier VARCHAR(255) NULL AFTER purchase_price",
            "contract_no": "ALTER TABLE assets ADD COLUMN contract_no VARCHAR(100) NULL AFTER supplier",
            "warranty_start": "ALTER TABLE assets ADD COLUMN warranty_start DATE NULL AFTER contract_no",
            "warranty_end": "ALTER TABLE assets ADD COLUMN warranty_end DATE NULL AFTER warranty_start",
            "warranty_provider": "ALTER TABLE assets ADD COLUMN warranty_provider VARCHAR(255) NULL AFTER warranty_end",
            "deployment_date": "ALTER TABLE assets ADD COLUMN deployment_date DATE NULL AFTER warranty_provider",
            "asset_status": "ALTER TABLE assets ADD COLUMN asset_status VARCHAR(20) NOT NULL DEFAULT 'in_stock' AFTER deployment_date",
            "user_name": "ALTER TABLE assets ADD COLUMN user_name VARCHAR(100) NULL AFTER asset_status",
            "department": "ALTER TABLE assets ADD COLUMN department VARCHAR(100) NULL AFTER user_name",
            "retire_date": "ALTER TABLE assets ADD COLUMN retire_date DATE NULL AFTER department",
            "retire_reason": "ALTER TABLE assets ADD COLUMN retire_reason VARCHAR(255) NULL AFTER retire_date",
            "notes": "ALTER TABLE assets ADD COLUMN notes TEXT NULL AFTER retire_reason",
        }

        for column_name, sql in asset_column_sql.items():
            if column_name not in existing_columns:
                cursor.execute(sql)

        if table_exists(conn, "asset_software"):
            cursor.execute(
                """
                SELECT COUNT(*)
                FROM information_schema.columns
                WHERE table_schema = %s
                  AND table_name = 'asset_software'
                  AND column_name = 'category'
                """,
                (DB_CONFIG["database"],),
            )
            software_category_exists_row = cursor.fetchone()
            software_category_exists = bool(software_category_exists_row and software_category_exists_row[0])

            if not software_category_exists:
                cursor.execute("""
                    ALTER TABLE asset_software
                    ADD COLUMN category VARCHAR(100) NULL
                    AFTER vendor
                """)

        cursor.execute(f"""
            UPDATE assets
            SET agent_install_status = '{AGENT_INSTALL_STATUS_NOT_INSTALLED}'
            WHERE agent_install_status IS NULL OR agent_install_status = ''
        """)
        exists_clauses = []
        if table_exists(conn, "agent_heartbeat"):
            exists_clauses.append("""
                EXISTS (
                    SELECT 1 FROM agent_heartbeat h
                    WHERE h.asset_id = a.id
                )
            """)
        if table_exists(conn, "asset_software"):
            exists_clauses.append("""
                EXISTS (
                    SELECT 1 FROM asset_software s
                    WHERE s.asset_id = a.id
                )
            """)

        if exists_clauses:
            cursor.execute(f"""
                UPDATE assets a
                SET a.agent_install_status = '{AGENT_INSTALL_STATUS_INSTALLED}'
                WHERE a.agent_install_status <> '{AGENT_INSTALL_STATUS_INSTALLED}'
                  AND ({' OR '.join(exists_clauses)})
            """)
        conn.commit()
    finally:
        cursor.close()


def table_exists(conn, table_name: str) -> bool:
    """检查指定表是否存在，避免跨模块聚合时因缺表报错。"""
    cursor = conn.cursor()
    try:
        cursor.execute("""
            SELECT COUNT(*)
            FROM information_schema.tables
            WHERE table_schema = %s AND table_name = %s
        """, (DB_CONFIG["database"], table_name))
        row = cursor.fetchone()
        return bool(row and row[0])
    finally:
        cursor.close()


UNIFIED_LOG_TEXT_COLLATION = "utf8mb4_unicode_ci"


def unified_log_text_sql(expr: str, alias: str) -> str:
    """统一聚合日志文本字段的字符集与排序规则，避免 UNION 时出现 collation 冲突。"""
    return (
        f"CAST({expr} AS CHAR CHARACTER SET utf8mb4) "
        f"COLLATE {UNIFIED_LOG_TEXT_COLLATION} AS {alias}"
    )


def build_empty_unified_logs_select() -> str:
    return f"""
        SELECT
            NULL AS source_id,
            {unified_log_text_sql("NULL", "source_type")},
            {unified_log_text_sql("NULL", "module")},
            {unified_log_text_sql("NULL", "category")},
            {unified_log_text_sql("NULL", "action")},
            {unified_log_text_sql("NULL", "level")},
            {unified_log_text_sql("NULL", "result")},
            NULL AS asset_id,
            {unified_log_text_sql("NULL", "hostname")},
            {unified_log_text_sql("NULL", "ip_address")},
            {unified_log_text_sql("NULL", "operator_name")},
            {unified_log_text_sql("NULL", "session_id")},
            {unified_log_text_sql("NULL", "title")},
            {unified_log_text_sql("NULL", "message")},
            NULL AS event_time,
            {unified_log_text_sql("NULL", "details_json")},
            {unified_log_text_sql("NULL", "stdout_log")},
            {unified_log_text_sql("NULL", "stderr_log")}
        WHERE 1 = 0
    """


def build_unified_logs_union(conn) -> str:
    """拼装统一日志查询，按当前可用表动态聚合。"""
    selects = []

    if table_exists(conn, "system_activity_logs"):
        selects.append(f"""
            SELECT
                l.id AS source_id,
                {unified_log_text_sql("COALESCE(l.source_type, 'agent')", "source_type")},
                {unified_log_text_sql("l.module", "module")},
                {unified_log_text_sql("l.category", "category")},
                {unified_log_text_sql("l.action", "action")},
                {unified_log_text_sql("l.level", "level")},
                {unified_log_text_sql("l.result", "result")},
                l.asset_id AS asset_id,
                {unified_log_text_sql("l.hostname", "hostname")},
                {unified_log_text_sql("l.ip_address", "ip_address")},
                {unified_log_text_sql("l.operator_name", "operator_name")},
                {unified_log_text_sql("l.session_id", "session_id")},
                {unified_log_text_sql("l.title", "title")},
                {unified_log_text_sql("l.message", "message")},
                l.event_time AS event_time,
                {unified_log_text_sql("l.details_json", "details_json")},
                {unified_log_text_sql("l.stdout_log", "stdout_log")},
                {unified_log_text_sql("l.stderr_log", "stderr_log")}
            FROM system_activity_logs l
        """)

    if table_exists(conn, "alerts"):
        selects.append(f"""
            SELECT
                al.id AS source_id,
                {unified_log_text_sql("'alert'", "source_type")},
                {unified_log_text_sql("'alert_center'", "module")},
                {unified_log_text_sql("al.alert_type", "category")},
                {unified_log_text_sql("CASE WHEN al.status = 'resolved' THEN 'resolve' ELSE 'trigger' END", "action")},
                {unified_log_text_sql("al.severity", "level")},
                {unified_log_text_sql("al.status", "result")},
                al.asset_id AS asset_id,
                {unified_log_text_sql("a.hostname", "hostname")},
                {unified_log_text_sql("a.ip_address", "ip_address")},
                {unified_log_text_sql("al.resolved_by", "operator_name")},
                {unified_log_text_sql("NULL", "session_id")},
                {unified_log_text_sql("CONCAT(COALESCE(a.hostname, CONCAT('资产 ', al.asset_id)), ' 告警')", "title")},
                {unified_log_text_sql("al.message", "message")},
                COALESCE(al.last_seen_at, al.first_triggered_at, al.created_at) AS event_time,
                {unified_log_text_sql("al.details_json", "details_json")},
                {unified_log_text_sql("NULL", "stdout_log")},
                {unified_log_text_sql("NULL", "stderr_log")}
            FROM alerts al
            LEFT JOIN assets a ON a.id = al.asset_id
        """)

    if table_exists(conn, "software_task_results") and table_exists(conn, "software_tasks"):
        selects.append(f"""
            SELECT
                r.id AS source_id,
                {unified_log_text_sql("'software_task'", "source_type")},
                {unified_log_text_sql("'software_management'", "module")},
                {unified_log_text_sql("'task_result'", "category")},
                {unified_log_text_sql("COALESCE(t.task_type, 'task')", "action")},
                {unified_log_text_sql('''CASE
                    WHEN r.status = 'failed' THEN 'error'
                    WHEN r.status IN ('timeout', 'cancelled') THEN 'warning'
                    ELSE 'info'
                END''', "level")},
                {unified_log_text_sql("r.status", "result")},
                r.asset_id AS asset_id,
                {unified_log_text_sql("a.hostname", "hostname")},
                {unified_log_text_sql("a.ip_address", "ip_address")},
                {unified_log_text_sql("t.created_by", "operator_name")},
                {unified_log_text_sql("NULL", "session_id")},
                {unified_log_text_sql("COALESCE(t.task_name, p.display_name, t.software_name, CONCAT('任务 ', r.task_id))", "title")},
                {unified_log_text_sql('''COALESCE(
                    r.error_message,
                    CONCAT(
                        COALESCE(a.hostname, CONCAT('资产 ', r.asset_id)),
                        ' ',
                        COALESCE(t.task_type, 'task'),
                        ' ',
                        COALESCE(t.software_name, p.display_name, '软件包'),
                        ' 状态: ',
                        COALESCE(r.status, 'unknown')
                    )
                )''', "message")},
                COALESCE(r.updated_at, r.end_time, r.start_time, r.created_at) AS event_time,
                {unified_log_text_sql('''JSON_OBJECT(
                    'task_id', r.task_id,
                    'task_name', t.task_name,
                    'task_type', t.task_type,
                    'package_id', t.package_id,
                    'package_name', p.display_name,
                    'software_name', t.software_name,
                    'progress', r.progress,
                    'download_progress', r.download_progress,
                    'install_progress', r.install_progress,
                    'duration', r.duration,
                    'error_code', r.error_code
                )''', "details_json")},
                {unified_log_text_sql("r.stdout_log", "stdout_log")},
                {unified_log_text_sql("r.stderr_log", "stderr_log")}
            FROM software_task_results r
            LEFT JOIN software_tasks t ON t.id = r.task_id
            LEFT JOIN software_packages p ON p.id = t.package_id
            LEFT JOIN assets a ON a.id = r.asset_id
        """)

    if table_exists(conn, "software_policy_logs"):
        selects.append(f"""
            SELECT
                pl.id AS source_id,
                {unified_log_text_sql("'policy_log'", "source_type")},
                {unified_log_text_sql("'software_policy'", "module")},
                {unified_log_text_sql("'policy_execution'", "category")},
                {unified_log_text_sql("COALESCE(pl.action, 'policy_check')", "action")},
                {unified_log_text_sql('''CASE
                    WHEN pl.result = 'failed' THEN 'error'
                    WHEN pl.result = 'blocked' THEN 'warning'
                    ELSE 'info'
                END''', "level")},
                {unified_log_text_sql("pl.result", "result")},
                pl.asset_id AS asset_id,
                {unified_log_text_sql("a.hostname", "hostname")},
                {unified_log_text_sql("a.ip_address", "ip_address")},
                {unified_log_text_sql("NULL", "operator_name")},
                {unified_log_text_sql("NULL", "session_id")},
                {unified_log_text_sql("COALESCE(pl.software_name, CONCAT('策略 ', pl.policy_id))", "title")},
                {unified_log_text_sql("COALESCE(pl.message, CONCAT('策略执行: ', COALESCE(pl.action, 'policy_check')))", "message")},
                pl.created_at AS event_time,
                {unified_log_text_sql('''JSON_OBJECT(
                    'policy_id', pl.policy_id,
                    'software_name', pl.software_name
                )''', "details_json")},
                {unified_log_text_sql("NULL", "stdout_log")},
                {unified_log_text_sql("NULL", "stderr_log")}
            FROM software_policy_logs pl
            LEFT JOIN assets a ON a.id = pl.asset_id
        """)

    if table_exists(conn, "software_audit_logs"):
        selects.append(f"""
            SELECT
                sal.id AS source_id,
                {unified_log_text_sql("'software_audit'", "source_type")},
                {unified_log_text_sql("'software_management'", "module")},
                {unified_log_text_sql("COALESCE(sal.target_type, 'audit')", "category")},
                {unified_log_text_sql("sal.operation_type", "action")},
                {unified_log_text_sql('''CASE
                    WHEN sal.result = 'failed' THEN 'error'
                    ELSE 'info'
                END''', "level")},
                {unified_log_text_sql("sal.result", "result")},
                NULL AS asset_id,
                {unified_log_text_sql("NULL", "hostname")},
                {unified_log_text_sql("sal.operator_ip", "ip_address")},
                {unified_log_text_sql("sal.operator", "operator_name")},
                {unified_log_text_sql("NULL", "session_id")},
                {unified_log_text_sql("COALESCE(sal.target_name, sal.operation_type)", "title")},
                {unified_log_text_sql("COALESCE(sal.error_message, sal.operation_type)", "message")},
                sal.created_at AS event_time,
                {unified_log_text_sql("sal.operation_details", "details_json")},
                {unified_log_text_sql("NULL", "stdout_log")},
                {unified_log_text_sql("NULL", "stderr_log")}
            FROM software_audit_logs sal
        """)

    if not selects:
        return build_empty_unified_logs_select()

    return "\nUNION ALL\n".join(selects)


def build_unified_logs_where(
    source_type: Optional[str] = None,
    module: Optional[str] = None,
    category: Optional[str] = None,
    asset_id: Optional[int] = None,
    keyword: Optional[str] = None,
    level: Optional[str] = None,
    result: Optional[str] = None,
    start_time: Optional[str] = None,
    end_time: Optional[str] = None
) -> Tuple[str, List[Any]]:
    clauses = ["1 = 1"]
    params: List[Any] = []

    if source_type:
        clauses.append("logs.source_type = %s")
        params.append(source_type)

    if module:
        clauses.append("logs.module = %s")
        params.append(module)

    if category:
        clauses.append("logs.category = %s")
        params.append(category)

    if asset_id is not None:
        clauses.append("logs.asset_id = %s")
        params.append(asset_id)

    if level:
        clauses.append("logs.level = %s")
        params.append(level)

    if result:
        clauses.append("logs.result = %s")
        params.append(result)

    if keyword:
        keyword_like = f"%{keyword}%"
        clauses.append("""
            (
                logs.message LIKE %s
                OR logs.title LIKE %s
                OR logs.hostname LIKE %s
                OR logs.ip_address LIKE %s
                OR logs.operator_name LIKE %s
            )
        """)
        params.extend([keyword_like, keyword_like, keyword_like, keyword_like, keyword_like])

    if start_time:
        clauses.append("logs.event_time >= %s")
        params.append(start_time)

    if end_time:
        clauses.append("logs.event_time <= %s")
        params.append(end_time)

    return " AND ".join(clauses), params


def normalize_log_row(row: Dict[str, Any]):
    details = parse_json_field(row.get("details_json"))
    return {
        "id": f"{row.get('source_type')}-{row.get('source_id')}",
        "source_id": row.get("source_id"),
        "source_type": row.get("source_type"),
        "module": row.get("module"),
        "category": row.get("category"),
        "action": row.get("action"),
        "level": row.get("level"),
        "result": row.get("result"),
        "asset_id": row.get("asset_id"),
        "hostname": row.get("hostname"),
        "ip_address": row.get("ip_address"),
        "operator_name": row.get("operator_name"),
        "session_id": row.get("session_id"),
        "title": row.get("title"),
        "message": row.get("message"),
        "event_time": format_datetime(row.get("event_time")),
        "details": details,
        "stdout_log": row.get("stdout_log"),
        "stderr_log": row.get("stderr_log")
    }


def serialize_log_details(details: Any):
    """统一序列化日志详情，便于写入 JSON 列。"""
    if details in (None, "", b""):
        return None
    if isinstance(details, bytes):
        details = details.decode("utf-8", errors="ignore")
    if isinstance(details, str):
        details = details.strip()
        if not details:
            return None
        try:
            json.loads(details)
            return details
        except (TypeError, ValueError, json.JSONDecodeError):
            return json.dumps({"raw": details}, ensure_ascii=False)
    try:
        return json.dumps(details, ensure_ascii=False)
    except (TypeError, ValueError):
        return json.dumps({"raw": str(details)}, ensure_ascii=False)


def get_request_client_ip(request: Optional[Request]) -> Optional[str]:
    """提取请求来源 IP，优先取反向代理头。"""
    if not request:
        return None

    forwarded_for = (request.headers.get("x-forwarded-for") or "").strip()
    if forwarded_for:
        return forwarded_for.split(",")[0].strip() or None

    real_ip = (request.headers.get("x-real-ip") or "").strip()
    if real_ip:
        return real_ip or None

    client = getattr(request, "client", None)
    return getattr(client, "host", None)


def insert_system_activity_log(cursor, payload: SystemActivityLogCreate) -> Tuple[Optional[int], datetime]:
    """复用统一日志写入逻辑，避免各模块重复拼 SQL。"""
    event_time = payload.event_time or datetime.now()
    details_json = serialize_log_details(payload.details)
    cursor.execute("""
        INSERT INTO system_activity_logs (
            source_type, module, category, action, level, result,
            asset_id, hostname, ip_address, operator_name, session_id,
            title, message, details_json, stdout_log, stderr_log,
            event_time, created_at
        ) VALUES (
            %s, %s, %s, %s, %s, %s,
            %s, %s, %s, %s, %s,
            %s, %s, %s, %s, %s,
            %s, NOW()
        )
    """, (
        payload.source_type,
        payload.module,
        payload.category,
        payload.action,
        payload.level,
        payload.result,
        payload.asset_id,
        payload.hostname,
        payload.ip_address,
        payload.operator_name,
        payload.session_id,
        payload.title,
        payload.message,
        details_json,
        payload.stdout_log,
        payload.stderr_log,
        event_time,
    ))
    return cursor.lastrowid, event_time


def build_trusted_agent_operator_name(payload: SystemActivityLogCreate) -> str:
    if payload.asset_id:
        return normalize_actor_name(f"agent:{payload.asset_id}", fallback="agent")
    if payload.hostname:
        return normalize_actor_name(f"agent:{payload.hostname}", fallback="agent")
    if payload.ip_address:
        return normalize_actor_name(f"agent:{payload.ip_address}", fallback="agent")
    return "agent"


def record_system_activity_log(payload: SystemActivityLogCreate) -> Optional[int]:
    """内部场景写日志时使用，写入失败不影响主业务。"""
    conn = get_db_connection()
    if not conn:
        safe_console_print("⚠️ 登录日志写入失败: 数据库连接不可用")
        return None

    cursor = conn.cursor()
    try:
        ensure_system_activity_logs_table(conn)
        log_id, _ = insert_system_activity_log(cursor, payload)
        conn.commit()
        return log_id
    except Error as exc:
        conn.rollback()
        safe_console_print(f"⚠️ 登录日志写入失败: {exc}")
        return None
    finally:
        cursor.close()
        conn.close()


def truncate_text(value: Optional[str], limit: int = 160) -> str:
    text = (value or "").strip()
    if not text:
        return ""
    if len(text) <= limit:
        return text
    return f"{text[:limit - 3]}..."


def build_batch_parameters_text(operation_type: str, parameters: Dict[str, Any]) -> str:
    if operation_type == "command":
        return truncate_text(str(parameters.get("command") or ""), 300)
    if operation_type == "restart":
        delay = parameters.get("delay", 0)
        return f"delay={delay}s"
    if operation_type == "shutdown":
        delay = parameters.get("delay", 0)
        return f"delay={delay}s"
    if operation_type == "software":
        url = str(parameters.get("url") or "").strip()
        install_command = str(parameters.get("install_command") or "").strip()
        return truncate_text(f"url={url} | install={install_command}", 300)
    if operation_type == "script":
        return truncate_text(str(parameters.get("script") or ""), 300)
    return truncate_text(json.dumps(parameters, ensure_ascii=False), 300)


def build_batch_output(stdout_log: Optional[str], stderr_log: Optional[str], error_message: Optional[str]) -> str:
    parts = []
    if stdout_log:
        parts.append(str(stdout_log).strip())
    if stderr_log:
        parts.append(str(stderr_log).strip())
    if error_message:
        parts.append(str(error_message).strip())
    return "\n".join(part for part in parts if part)


def escape_powershell_single_quoted(value: str) -> str:
    return value.replace("'", "''")


def build_restart_command(parameters: Dict[str, Any]) -> str:
    try:
        delay = int(parameters.get("delay", 0) or 0)
    except (TypeError, ValueError):
        delay = 0
    delay = max(0, delay)
    return f"shutdown /r /t {delay} /f"


def build_shutdown_command(parameters: Dict[str, Any]) -> str:
    try:
        delay = int(parameters.get("delay", 0) or 0)
    except (TypeError, ValueError):
        delay = 0
    delay = max(0, delay)
    return f"shutdown /s /t {delay} /f"


def build_script_command(parameters: Dict[str, Any]) -> str:
    script = str(parameters.get("script") or "").strip()
    if not script:
        raise HTTPException(status_code=400, detail="Script content is required")
    encoded = base64.b64encode(script.encode("utf-16le")).decode("ascii")
    return f"powershell -NoProfile -ExecutionPolicy Bypass -EncodedCommand {encoded}"


def build_software_command(parameters: Dict[str, Any]) -> str:
    url = str(parameters.get("url") or "").strip()
    install_command = str(parameters.get("install_command") or "").strip()
    if not url:
        raise HTTPException(status_code=400, detail="Software URL is required")

    parsed = urlparse(url)
    file_name = os.path.basename(parsed.path) or "package.exe"
    safe_file_name = "".join(ch for ch in file_name if ch not in '<>:"/\\|?*').strip() or "package.exe"
    file_name_literal = escape_powershell_single_quoted(safe_file_name)
    url_literal = escape_powershell_single_quoted(url)

    default_install = """
if ($filePath.ToLower().EndsWith('.msi')) {
    $process = Start-Process -FilePath 'msiexec.exe' -ArgumentList @('/i', $filePath, '/qn', '/norestart') -Wait -PassThru
    exit $process.ExitCode
}
$process = Start-Process -FilePath $filePath -ArgumentList @('/quiet', '/norestart') -Wait -PassThru
exit $process.ExitCode
""".strip()

    custom_install = ""
    if install_command:
        custom_install = f"""
$installCommand = @'
{install_command}
'@
$installCommand = $installCommand.Replace('{{file}}', $filePath).Replace('{{filename}}', '{file_name_literal}').Replace('{{dir}}', $downloadDir)
cmd.exe /c $installCommand
exit $LASTEXITCODE
""".strip()

    ps_script = f"""
$ErrorActionPreference = 'Stop'
$downloadDir = Join-Path $env:TEMP 'CMDBBatch'
New-Item -ItemType Directory -Path $downloadDir -Force | Out-Null
$filePath = Join-Path $downloadDir '{file_name_literal}'
Invoke-WebRequest -Uri '{url_literal}' -OutFile $filePath -UseBasicParsing
Set-Location $downloadDir
{custom_install or default_install}
""".strip()

    encoded = base64.b64encode(ps_script.encode("utf-16le")).decode("ascii")
    return f"powershell -NoProfile -ExecutionPolicy Bypass -EncodedCommand {encoded}"


def build_batch_command(operation_type: str, parameters: Dict[str, Any]) -> str:
    if operation_type == "command":
        command = str(parameters.get("command") or "").strip()
        if not command:
            raise HTTPException(status_code=400, detail="Command is required")
        return command
    if operation_type == "restart":
        return build_restart_command(parameters)
    if operation_type == "shutdown":
        return build_shutdown_command(parameters)
    if operation_type == "software":
        return build_software_command(parameters)
    if operation_type == "script":
        return build_script_command(parameters)
    raise HTTPException(status_code=400, detail=f"Unsupported operation type: {operation_type}")


def get_batch_operation_timeout(operation_type: str) -> int:
    if operation_type == "restart":
        return 15
    if operation_type == "shutdown":
        return 15
    if operation_type == "software":
        return 180
    if operation_type == "script":
        return 120
    return 45


def execute_batch_command_on_agent(
    asset: Dict[str, Any],
    operation_type: str,
    parameters: Dict[str, Any],
    operator_name: str,
) -> Dict[str, Any]:
    asset_id = asset.get("id")
    hostname = asset.get("hostname")
    ip_address = asset.get("ip_address")
    status = asset.get("status")

    try:
        command_text = build_batch_command(operation_type, parameters)
    except HTTPException as exc:
        return {
            "asset_id": asset_id,
            "hostname": hostname,
            "ip_address": ip_address,
            "status": "failed",
            "command_text": None,
            "stdout_log": None,
            "stderr_log": None,
            "output_text": exc.detail,
            "returncode": None,
            "error_message": exc.detail,
        }

    if not ip_address:
        error_message = "Missing agent IP address"
        return {
            "asset_id": asset_id,
            "hostname": hostname,
            "ip_address": ip_address,
            "status": "failed",
            "command_text": command_text,
            "stdout_log": None,
            "stderr_log": None,
            "output_text": error_message,
            "returncode": None,
            "error_message": error_message,
        }

    if status != "online":
        error_message = "Target agent is offline"
        return {
            "asset_id": asset_id,
            "hostname": hostname,
            "ip_address": ip_address,
            "status": "failed",
            "command_text": command_text,
            "stdout_log": None,
            "stderr_log": None,
            "output_text": error_message,
            "returncode": None,
            "error_message": error_message,
        }

    timeout_seconds = get_batch_operation_timeout(operation_type)
    request_payload = {
        "command": command_text,
        "operator": operator_name,
        "requester": operator_name,
    }

    try:
        response = requests.post(
            f"http://{ip_address}:{AGENT_CONTROL_PORT}/api/v1/command",
            json=request_payload,
            headers=build_agent_auth_headers(),
            timeout=timeout_seconds,
        )
        response.raise_for_status()
        body = response.json()
    except requests.RequestException as exc:
        error_message = f"Agent request failed: {exc}"
        return {
            "asset_id": asset_id,
            "hostname": hostname,
            "ip_address": ip_address,
            "status": "failed",
            "command_text": command_text,
            "stdout_log": None,
            "stderr_log": None,
            "output_text": error_message,
            "returncode": None,
            "error_message": error_message,
        }
    except ValueError:
        error_message = "Agent returned invalid JSON"
        return {
            "asset_id": asset_id,
            "hostname": hostname,
            "ip_address": ip_address,
            "status": "failed",
            "command_text": command_text,
            "stdout_log": None,
            "stderr_log": None,
            "output_text": error_message,
            "returncode": None,
            "error_message": error_message,
        }

    success = bool(body.get("success"))
    stdout_log = body.get("stdout")
    stderr_log = body.get("stderr")
    returncode = body.get("returncode")
    error_message = body.get("error")

    if not success and not error_message:
        error_message = "Agent reported execution failure"

    output_text = build_batch_output(stdout_log, stderr_log, error_message)

    return {
        "asset_id": asset_id,
        "hostname": hostname,
        "ip_address": ip_address,
        "status": "success" if success else "failed",
        "command_text": command_text,
        "stdout_log": stdout_log,
        "stderr_log": stderr_log,
        "output_text": output_text or ("Completed" if success else "Execution failed"),
        "returncode": returncode,
        "error_message": error_message,
    }


def normalize_batch_result_row(row: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "id": row.get("id"),
        "asset_id": row.get("asset_id"),
        "hostname": row.get("hostname"),
        "ip_address": row.get("ip_address"),
        "status": row.get("status"),
        "command_text": row.get("command_text"),
        "stdout_log": row.get("stdout_log"),
        "stderr_log": row.get("stderr_log"),
        "output": row.get("output_text") or build_batch_output(
            row.get("stdout_log"),
            row.get("stderr_log"),
            row.get("error_message"),
        ),
        "returncode": row.get("returncode"),
        "error_message": row.get("error_message"),
        "executed_at": format_datetime(row.get("executed_at")),
    }


def fetch_asset_monitor_rows(conn):
    """获取资产与最新监控数据"""
    cursor = conn.cursor(dictionary=True)
    try:
        cursor.execute("""
            SELECT
                a.id AS asset_id,
                a.hostname,
                a.ip_address,
                a.last_seen,
                CASE
                    WHEN a.last_seen IS NULL THEN NULL
                    ELSE TIMESTAMPDIFF(SECOND, a.last_seen, NOW())
                END AS seconds_since_seen,
                CASE
                    WHEN a.last_seen IS NOT NULL
                         AND TIMESTAMPDIFF(SECOND, a.last_seen, NOW()) <= %s
                    THEN 'online'
                    ELSE 'offline'
                END AS real_status,
                h.cpu_usage,
                h.memory_usage,
                h.disk_usage,
                h.heartbeat_time
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
            WHERE a.deleted_at IS NULL
        """, (ALERT_ONLINE_SECONDS,))
        return cursor.fetchall()
    finally:
        cursor.close()


def build_current_alerts(rows: List[Dict[str, Any]]):
    """根据当前资产和心跳生成基础告警"""
    alerts = []

    for row in rows:
        asset_id = row['asset_id']
        hostname = row.get('hostname') or f'资产 {asset_id}'
        ip_address = row.get('ip_address') or '-'
        real_status = row.get('real_status') or 'offline'
        seconds_since_seen = row.get('seconds_since_seen')
        cpu_usage = safe_float(row.get('cpu_usage'))
        memory_usage = safe_float(row.get('memory_usage'))
        disk_usage = safe_float(row.get('disk_usage'))

        if seconds_since_seen is not None and seconds_since_seen > ALERT_OFFLINE_SECONDS:
            offline_minutes = max(1, int(round(seconds_since_seen / 60)))
            severity = 'critical' if seconds_since_seen >= 600 else 'warning'
            alerts.append({
                'asset_id': asset_id,
                'alert_type': 'offline',
                'severity': severity,
                'message': f'{hostname} 已离线 {offline_minutes} 分钟',
                'current_value': safe_float(seconds_since_seen),
                'threshold_value': float(ALERT_OFFLINE_SECONDS),
                'details_json': {
                    'hostname': hostname,
                    'ip_address': ip_address,
                    'last_seen': format_datetime(row.get('last_seen'))
                },
                'active_fingerprint': f'{asset_id}:offline'
            })

        if real_status != 'online':
            continue

        for alert_type, metric_value in (
            ('cpu', cpu_usage),
            ('memory', memory_usage),
            ('disk', disk_usage)
        ):
            if metric_value is None:
                continue

            thresholds = ALERT_THRESHOLDS[alert_type]
            severity = None
            threshold_value = None

            if metric_value >= thresholds['critical']:
                severity = 'critical'
                threshold_value = thresholds['critical']
            elif metric_value >= thresholds['warning']:
                severity = 'warning'
                threshold_value = thresholds['warning']

            if not severity:
                continue

            alerts.append({
                'asset_id': asset_id,
                'alert_type': alert_type,
                'severity': severity,
                'message': f'{hostname} {ALERT_TYPE_LABELS[alert_type]}达到 {metric_value:.1f}%',
                'current_value': metric_value,
                'threshold_value': float(threshold_value),
                'details_json': {
                    'hostname': hostname,
                    'ip_address': ip_address,
                    'heartbeat_time': format_datetime(row.get('heartbeat_time'))
                },
                'active_fingerprint': f'{asset_id}:{alert_type}'
            })

        health_score = compute_health_score(real_status, cpu_usage, memory_usage, disk_usage)
        if health_score is not None and health_score < ALERT_THRESHOLDS['health']['warning']:
            severity = 'critical' if health_score < ALERT_THRESHOLDS['health']['critical'] else 'warning'
            threshold_value = (
                ALERT_THRESHOLDS['health']['critical']
                if severity == 'critical'
                else ALERT_THRESHOLDS['health']['warning']
            )
            alerts.append({
                'asset_id': asset_id,
                'alert_type': 'health',
                'severity': severity,
                'message': f'{hostname} 健康度降至 {health_score} 分',
                'current_value': safe_float(health_score),
                'threshold_value': float(threshold_value),
                'details_json': {
                    'hostname': hostname,
                    'ip_address': ip_address,
                    'cpu_usage': cpu_usage,
                    'memory_usage': memory_usage,
                    'disk_usage': disk_usage
                },
                'active_fingerprint': f'{asset_id}:health'
            })

    return alerts


def sync_alerts(conn):
    """同步当前告警状态到数据库"""
    ensure_alerts_table(conn)

    current_alerts = build_current_alerts(fetch_asset_monitor_rows(conn))
    cursor = conn.cursor(dictionary=True)

    try:
        cursor.execute("""
            SELECT id, active_fingerprint
            FROM alerts
            WHERE status = 'active' AND active_fingerprint IS NOT NULL
        """)
        existing_alerts = {
            row['active_fingerprint']: row
            for row in cursor.fetchall()
            if row.get('active_fingerprint')
        }

        current_fingerprints = set()

        for alert in current_alerts:
            fingerprint = alert['active_fingerprint']
            current_fingerprints.add(fingerprint)
            details_json = json.dumps(alert['details_json'], ensure_ascii=False)
            cursor.execute("""
                INSERT INTO alerts (
                    asset_id, alert_type, severity, status, message,
                    current_value, threshold_value, details_json,
                    active_fingerprint, first_triggered_at, last_seen_at,
                    created_at, updated_at
                ) VALUES (
                    %s, %s, %s, 'active', %s,
                    %s, %s, %s,
                    %s, NOW(), NOW(),
                    NOW(), NOW()
                )
                ON DUPLICATE KEY UPDATE
                    asset_id = VALUES(asset_id),
                    alert_type = VALUES(alert_type),
                    severity = VALUES(severity),
                    status = 'active',
                    message = VALUES(message),
                    current_value = VALUES(current_value),
                    threshold_value = VALUES(threshold_value),
                    details_json = VALUES(details_json),
                    last_seen_at = NOW(),
                    resolved_at = NULL,
                    resolved_by = NULL,
                    updated_at = NOW()
            """, (
                alert['asset_id'],
                alert['alert_type'],
                alert['severity'],
                alert['message'],
                alert['current_value'],
                alert['threshold_value'],
                details_json,
                fingerprint
            ))

        stale_fingerprints = set(existing_alerts.keys()) - current_fingerprints
        for fingerprint in stale_fingerprints:
            cursor.execute("""
                UPDATE alerts
                SET status = 'resolved',
                    active_fingerprint = NULL,
                    resolved_at = NOW(),
                    resolved_by = 'system',
                    updated_at = NOW()
                WHERE active_fingerprint = %s
            """, (fingerprint,))

        conn.commit()
    finally:
        cursor.close()


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


def expand_discovery_targets(raw_items: List[str], max_targets: int = DISCOVERY_MAX_TARGETS) -> List[str]:
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
    conn = get_db_connection()
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

class AssetStats(BaseModel):
    total: int
    online: int
    offline: int
    unknown: int


class Asset(BaseModel):
    id: Optional[int] = None
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


# ============================================================
# API???

# API接口
# ============================================================

@app.get("/")
def root():
    """健康检查"""
    return {
        "service": "Z-View Assets API",
        "status": "running",
        "version": "1.0.0"
    }


@app.post("/api/v1/auth/login")
def login(payload: LoginRequest, request: Request):
    auth_user = authenticate_username_password(payload.username, payload.password)
    if not auth_user:
        record_system_activity_log(SystemActivityLogCreate(
            source_type="platform",
            module="auth",
            category="authentication",
            action="login",
            level="warning",
            result="failed",
            ip_address=get_request_client_ip(request),
            operator_name=payload.username,
            title="平台登录失败",
            message=f"用户 {payload.username} 登录失败：用户名或密码错误",
            details={
                "username": payload.username,
                "reason": "invalid_credentials",
                "user_agent": request.headers.get("user-agent"),
            },
        ))
        raise HTTPException(status_code=401, detail="用户名或密码错误")

    token_payload = issue_access_token(auth_user["username"])
    record_system_activity_log(SystemActivityLogCreate(
        source_type="platform",
        module="auth",
        category="authentication",
        action="login",
        level="info",
        result="success",
        ip_address=get_request_client_ip(request),
        operator_name=auth_user["username"],
        title="平台登录成功",
        message=f"用户 {auth_user['username']} 登录平台成功",
        details={
            "username": auth_user["username"],
            "credential_source": auth_user.get("credential_source") or "file",
            "must_change_password": bool(auth_user.get("must_change_password")),
            "issued_at": token_payload.get("issued_at"),
            "expires_at": token_payload.get("expires_at"),
            "user_agent": request.headers.get("user-agent"),
        },
    ))
    return token_payload


@app.get("/api/v1/auth/me")
def get_current_user(request: Request):
    auth_user = getattr(request.state, "auth_user", None)
    if not auth_user:
        raise HTTPException(status_code=401, detail="Unauthorized")
    profile = get_auth_profile(auth_user.get("username"))
    return {
        **auth_user,
        "password_updated_at": profile.get("password_updated_at"),
        "credential_source": profile.get("credential_source") or "file",
        "must_change_password": bool(profile.get("must_change_password")),
    }


@app.post("/api/v1/auth/change-password")
def change_current_user_password(payload: ChangePasswordRequest, request: Request):
    auth_user = getattr(request.state, "auth_user", None)
    if not auth_user:
        raise HTTPException(status_code=401, detail="Unauthorized")

    try:
        result = change_password(
            auth_user.get("username", ""),
            payload.current_password,
            payload.new_password,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return {
        "message": "密码修改成功，请重新登录",
        "username": result.get("username"),
        "password_updated_at": result.get("password_updated_at"),
    }


@app.get("/api/v1/assets/stats")
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


@app.get("/api/v1/assets")
def get_assets(
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


@app.get("/api/v1/assets/export")
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


@app.get("/api/v1/assets/{asset_id}")
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


@app.post("/api/v1/assets")
def create_asset(asset: Asset, request: Request):
    """Create asset."""
    conn = get_db_connection()
    if not conn:
        raise HTTPException(status_code=500, detail="Database connection failed")

    try:
        cursor = conn.cursor(dictionary=True)
        operator_name = get_request_username(request, fallback="console")

        cursor.execute("""
            INSERT INTO assets (
                asset_type, hostname, ip_address, mac_address,
                serial_number, manufacturer, model, os_type, os_version,
                cpu_cores, memory_mb, disk_gb, status, agent_install_status,
                location, owner,
                purchase_date, purchase_price, supplier, contract_no,
                warranty_start, warranty_end, warranty_provider,
                deployment_date, asset_status, user_name, department,
                retire_date, retire_reason, notes,
                created_at, updated_at
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, NOW(), NOW())
        """, (
            asset.asset_type, asset.hostname, asset.ip_address, asset.mac_address,
            asset.serial_number, asset.manufacturer, asset.model, asset.os_type, asset.os_version,
            asset.cpu_cores, asset.memory_mb, asset.disk_gb, asset.status or 'unknown',
            asset.agent_install_status or AGENT_INSTALL_STATUS_NOT_INSTALLED,
            asset.location, asset.owner,
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


@app.put("/api/v1/assets/{asset_id}")
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


@app.delete("/api/v1/assets/{asset_id}")
def delete_asset(asset_id: int):
    """删除资产（软删除）"""
    conn = get_db_connection()
    if not conn:
        raise HTTPException(status_code=500, detail="Database connection failed")

    try:
        cursor = conn.cursor()

        # 检查资产是否存在
        cursor.execute("SELECT id FROM assets WHERE id = %s AND deleted_at IS NULL", (asset_id,))
        if not cursor.fetchone():
            raise HTTPException(status_code=404, detail="Asset not found")

        # 软删除资产
        cursor.execute("UPDATE assets SET deleted_at = NOW() WHERE id = %s", (asset_id,))

        # 删除关联的软件清单
        cursor.execute("DELETE FROM asset_software WHERE asset_id = %s", (asset_id,))

        conn.commit()

        safe_console_print(f"[Asset] Deleting asset_id={asset_id} and related software records")

        return {"message": "Asset deleted successfully"}

    except Error as e:
        conn.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        cursor.close()
        conn.close()


@app.post("/api/v1/assets/batch-delete")
def batch_delete_assets(request: dict):
    """批量删除资产（软删除）"""
    ids = request.get('ids', [])

    if not ids:
        raise HTTPException(status_code=400, detail="No asset IDs provided")

    conn = get_db_connection()
    if not conn:
        raise HTTPException(status_code=500, detail="Database connection failed")

    try:
        cursor = conn.cursor()

        # 批量软删除
        placeholders = ','.join(['%s'] * len(ids))
        cursor.execute(
            f"UPDATE assets SET deleted_at = NOW() WHERE id IN ({placeholders}) AND deleted_at IS NULL",
            tuple(ids)
        )
        conn.commit()
        deleted_count = cursor.rowcount

        return {
            "message": f"Successfully deleted {deleted_count} assets",
            "deleted_count": deleted_count
        }

    except Error as e:
        conn.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        cursor.close()
        conn.close()


@app.post("/api/v1/batch/execute")
def execute_batch_operation(payload: BatchExecuteRequest, request: Request):
    """执行批量操作，并记录历史与逐台执行结果。"""
    terminal_ids = list(dict.fromkeys(payload.terminal_ids or []))
    if not terminal_ids:
        raise HTTPException(status_code=400, detail="No terminal IDs provided")

    operator_name = get_request_username(request, fallback="console")
    parameters = payload.parameters or {}
    parameters_text = build_batch_parameters_text(payload.operation_type, parameters)

    # 先做参数级校验，避免无效操作也进入历史。
    build_batch_command(payload.operation_type, parameters)

    conn = get_db_connection()
    if not conn:
        raise HTTPException(status_code=500, detail="Database connection failed")

    cursor = conn.cursor(dictionary=True)
    try:
        ensure_batch_tables(conn)

        placeholders = ",".join(["%s"] * len(terminal_ids))
        cursor.execute(f"""
            SELECT
                a.id,
                a.hostname,
                a.ip_address,
                CASE
                    WHEN a.last_seen IS NULL THEN 'offline'
                    WHEN TIMESTAMPDIFF(SECOND, a.last_seen, NOW()) <= 90 THEN 'online'
                    ELSE 'offline'
                END AS status
            FROM assets a
            WHERE a.deleted_at IS NULL
              AND a.id IN ({placeholders})
        """, terminal_ids)
        asset_rows = cursor.fetchall()
        asset_map = {row["id"]: row for row in asset_rows}

        created_at = datetime.now()
        cursor.execute("""
            INSERT INTO batch_operations (
                operation_type, operator_name, parameters_json, parameters_text,
                target_count, success_count, failed_count, created_at
            ) VALUES (
                %s, %s, %s, %s, %s, 0, 0, %s
            )
        """, (
            payload.operation_type,
            operator_name,
            json.dumps(parameters, ensure_ascii=False),
            parameters_text,
            len(terminal_ids),
            created_at,
        ))
        operation_id = cursor.lastrowid

        success_count = 0
        failed_count = 0
        ordered_results = []

        for terminal_id in terminal_ids:
            asset = asset_map.get(terminal_id)
            if not asset:
                result = {
                    "asset_id": terminal_id,
                    "hostname": f"资产 #{terminal_id}",
                    "ip_address": None,
                    "status": "failed",
                    "command_text": None,
                    "stdout_log": None,
                    "stderr_log": None,
                    "output_text": "Target asset not found",
                    "returncode": None,
                    "error_message": "Target asset not found",
                }
            else:
                result = execute_batch_command_on_agent(
                    asset=asset,
                    operation_type=payload.operation_type,
                    parameters=parameters,
                    operator_name=operator_name,
                )

            if result["status"] == "success":
                success_count += 1
            else:
                failed_count += 1

            ordered_results.append(result)

            cursor.execute("""
                INSERT INTO batch_operation_results (
                    operation_id, asset_id, hostname, ip_address, status,
                    command_text, stdout_log, stderr_log, output_text,
                    returncode, error_message, executed_at, created_at
                ) VALUES (
                    %s, %s, %s, %s, %s,
                    %s, %s, %s, %s,
                    %s, %s, NOW(), %s
                )
            """, (
                operation_id,
                result.get("asset_id"),
                result.get("hostname"),
                result.get("ip_address"),
                result.get("status"),
                result.get("command_text"),
                result.get("stdout_log"),
                result.get("stderr_log"),
                result.get("output_text"),
                result.get("returncode"),
                result.get("error_message"),
                created_at,
            ))

        cursor.execute("""
            UPDATE batch_operations
            SET success_count = %s,
                failed_count = %s,
                completed_at = NOW()
            WHERE id = %s
        """, (success_count, failed_count, operation_id))
        conn.commit()

        return {
            "message": "Batch operation executed",
            "id": operation_id,
            "operation_type": payload.operation_type,
            "target_count": len(terminal_ids),
            "success_count": success_count,
            "failed_count": failed_count,
            "parameters": parameters_text,
            "created_at": format_datetime(created_at),
        }
    except HTTPException:
        conn.rollback()
        raise
    except Error as e:
        conn.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        cursor.close()
        conn.close()


@app.get("/api/v1/batch/history")
def get_batch_history(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
):
    """获取批量操作执行历史。"""
    conn = get_db_connection()
    if not conn:
        raise HTTPException(status_code=500, detail="Database connection failed")

    cursor = conn.cursor(dictionary=True)
    try:
        ensure_batch_tables(conn)

        cursor.execute("SELECT COUNT(*) AS total FROM batch_operations")
        total = (cursor.fetchone() or {}).get("total", 0) or 0

        offset = (page - 1) * page_size
        cursor.execute("""
            SELECT
                id,
                operation_type,
                target_count,
                success_count,
                failed_count,
                parameters_text,
                operator_name,
                created_at,
                completed_at
            FROM batch_operations
            ORDER BY created_at DESC, id DESC
            LIMIT %s OFFSET %s
        """, (page_size, offset))
        rows = cursor.fetchall()

        return {
            "data": [
                {
                    "id": row["id"],
                    "operation_type": row["operation_type"],
                    "target_count": row["target_count"],
                    "success_count": row["success_count"],
                    "failed_count": row["failed_count"],
                    "parameters": row.get("parameters_text") or "",
                    "operator_name": row.get("operator_name"),
                    "created_at": format_datetime(row.get("created_at")),
                    "completed_at": format_datetime(row.get("completed_at")),
                }
                for row in rows
            ],
            "total": total,
            "page": page,
            "page_size": page_size,
        }
    except Error as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        cursor.close()
        conn.close()


@app.get("/api/v1/batch/{operation_id}/results")
def get_batch_operation_results(operation_id: int):
    """获取批量操作的逐台执行结果。"""
    conn = get_db_connection()
    if not conn:
        raise HTTPException(status_code=500, detail="Database connection failed")

    cursor = conn.cursor(dictionary=True)
    try:
        ensure_batch_tables(conn)

        cursor.execute("SELECT id FROM batch_operations WHERE id = %s", (operation_id,))
        if not cursor.fetchone():
            raise HTTPException(status_code=404, detail="Batch operation not found")

        cursor.execute("""
            SELECT
                id,
                asset_id,
                hostname,
                ip_address,
                status,
                command_text,
                stdout_log,
                stderr_log,
                output_text,
                returncode,
                error_message,
                executed_at
            FROM batch_operation_results
            WHERE operation_id = %s
            ORDER BY executed_at DESC, id DESC
        """, (operation_id,))
        rows = cursor.fetchall()

        return {
            "data": [normalize_batch_result_row(row) for row in rows]
        }
    except HTTPException:
        raise
    except Error as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        cursor.close()
        conn.close()


@app.get("/api/v1/assets/{asset_id}/detail")
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


@app.post("/api/v1/assets/{asset_id}/remote-control")
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
        return {
            "message": "远程桌面连接已就绪",
            "asset_id": asset_id,
            "action": action,
            "hostname": asset.get("hostname"),
            "ip_address": asset.get("ip_address"),
            "proxy_ws_path": f"/api/v1/assets/{asset_id}/remote-desktop/ws",
            "agent_ws_port": 9000,
        }
    finally:
        if cursor:
            cursor.close()
        conn.close()


@app.websocket("/api/v1/assets/{asset_id}/remote-desktop/ws")
async def proxy_remote_desktop_websocket(asset_id: int, websocket: WebSocket):
    """通过平台代理远程桌面 WebSocket，避免浏览器直连终端 9000 端口。"""
    auth_user = authenticate_websocket_request(websocket)
    if not auth_user:
        safe_console_print(f"[RemoteDesktopProxy] asset={asset_id} rejected: unauthorized websocket request")
        await close_browser_websocket(websocket, code=4401, reason="Unauthorized")
        return
    if not user_has_permission(auth_user, "remote_desktop:control"):
        safe_console_print(f"[RemoteDesktopProxy] asset={asset_id} rejected: missing remote_desktop:control permission")
        await close_browser_websocket(websocket, code=4403, reason="Forbidden")
        return

    conn = get_db_connection()
    if not conn:
        safe_console_print(f"[RemoteDesktopProxy] asset={asset_id} rejected: database connection failed")
        await send_browser_session_error(websocket, "平台数据库连接失败，请稍后重试", code=1011)
        return

    cursor = None
    try:
        cursor = conn.cursor(dictionary=True)
        asset = get_asset_agent_target(cursor, asset_id)
    except HTTPException as exc:
        close_code = 4404 if exc.status_code == 404 else 4409 if exc.status_code == 409 else 4400
        safe_console_print(f"[RemoteDesktopProxy] asset={asset_id} rejected: {exc.detail}")
        await send_browser_session_error(websocket, str(exc.detail), code=close_code)
        return
    finally:
        if cursor:
            cursor.close()
        conn.close()

    ip_address = str(asset.get("ip_address") or "").strip()
    if not ip_address:
        safe_console_print(f"[RemoteDesktopProxy] asset={asset_id} rejected: asset IP address is missing")
        await send_browser_session_error(websocket, "终端 IP 地址缺失，无法建立远程桌面连接", code=4400)
        return

    if asset.get("agent_install_status") != AGENT_INSTALL_STATUS_INSTALLED:
        safe_console_print(f"[RemoteDesktopProxy] asset={asset_id} rejected: agent is not installed")
        await send_browser_session_error(websocket, "目标终端未安装 Agent，无法建立远程桌面连接", code=4409)
        return

    if asset.get("resolved_status") != "online":
        safe_console_print(f"[RemoteDesktopProxy] asset={asset_id} rejected: target asset is offline")
        await send_browser_session_error(websocket, "目标终端当前离线，无法建立远程桌面连接", code=4409)
        return

    requester = get_remote_desktop_requester(websocket, auth_user)
    query_string = urlencode({"requester": requester})
    upstream_url = f"ws://{ip_address}:9000/remote-desktop?{query_string}"
    safe_console_print(
        f"[RemoteDesktopProxy] asset={asset_id} requester={requester} "
        f"ip={ip_address} upstream=ws://{ip_address}:9000/remote-desktop"
    )

    try:
        async with websockets.connect(
            upstream_url,
            additional_headers=build_agent_auth_headers({
                "X-Remote-Requester": requester,
            }),
            open_timeout=10,
            close_timeout=5,
            ping_interval=None,
            max_size=None,
        ) as upstream_socket:
            safe_console_print(f"[RemoteDesktopProxy] asset={asset_id} upstream connected")
            await websocket.accept()
            safe_console_print(f"[RemoteDesktopProxy] asset={asset_id} browser websocket accepted")

            browser_to_agent_task = asyncio.create_task(
                relay_browser_to_agent(websocket, upstream_socket, asset_id=asset_id)
            )
            agent_to_browser_task = asyncio.create_task(
                relay_agent_to_browser(websocket, upstream_socket, asset_id=asset_id)
            )

            done, pending = await asyncio.wait(
                {browser_to_agent_task, agent_to_browser_task},
                return_when=asyncio.FIRST_COMPLETED,
            )
            safe_console_print(
                f"[RemoteDesktopProxy] asset={asset_id} wait completed: "
                f"done={[get_remote_desktop_task_name(browser_to_agent_task, agent_to_browser_task, task) for task in done]} "
                f"pending={[get_remote_desktop_task_name(browser_to_agent_task, agent_to_browser_task, task) for task in pending]}"
            )

            for task in done:
                task_name = get_remote_desktop_task_name(
                    browser_to_agent_task,
                    agent_to_browser_task,
                    task,
                )
                log_remote_desktop_task_result(asset_id, task_name, task)
                if not task.cancelled():
                    with contextlib.suppress(Exception):
                        safe_console_print(
                            f"[RemoteDesktopProxy] asset={asset_id} task={task_name} result={task.result()}"
                        )

            if browser_to_agent_task in done:
                safe_console_print(f"[RemoteDesktopProxy] asset={asset_id} browser relay finished first")
                await close_upstream_websocket(
                    upstream_socket,
                    code=1000,
                    reason="browser_relay_finished",
                )

            if agent_to_browser_task in done:
                safe_console_print(f"[RemoteDesktopProxy] asset={asset_id} upstream relay finished first")
                await close_upstream_websocket(
                    upstream_socket,
                    code=1000,
                    reason="agent_to_browser_finished",
                )

            if pending:
                settled, still_pending = await asyncio.wait(pending, timeout=2.0)
                for task in settled:
                    task_name = get_remote_desktop_task_name(
                        browser_to_agent_task,
                        agent_to_browser_task,
                        task,
                    )
                    log_remote_desktop_task_result(asset_id, task_name, task)
                    if not task.cancelled():
                        with contextlib.suppress(Exception):
                            safe_console_print(
                                f"[RemoteDesktopProxy] asset={asset_id} task={task_name} result={task.result()}"
                            )

                for task in still_pending:
                    task_name = get_remote_desktop_task_name(
                        browser_to_agent_task,
                        agent_to_browser_task,
                        task,
                    )
                    safe_console_print(
                        f"[RemoteDesktopProxy] asset={asset_id} task={task_name} did not settle in time; cancelling"
                    )
                    task.cancel()

                await asyncio.gather(*still_pending, return_exceptions=True)

            await asyncio.gather(*done, return_exceptions=True)
            await asyncio.gather(*pending, return_exceptions=True)
    except Exception as exc:
        safe_console_print(
            f"[RemoteDesktopProxy] asset={asset_id} ip={asset.get('ip_address')} failed: {exc}"
        )
        exc_text = str(exc)
        if "HTTP 401" in exc_text or "HTTP 403" in exc_text:
            error_message = "终端 Agent 鉴权失败，请重新部署客户端或核对 token 配置"
            error_code = 1013
        else:
            error_message = "终端远程桌面服务不可用，请检查用户会话代理和 9000 端口"
            error_code = 1013

        if websocket.application_state == WebSocketState.CONNECTING:
            await send_browser_session_error(
                websocket,
                error_message,
                code=error_code,
            )
        elif websocket.application_state == WebSocketState.CONNECTED:
            await send_browser_session_error(
                websocket,
                error_message if error_code == 1013 else "终端远程桌面连接已断开",
                code=1011 if error_code != 1013 else error_code,
            )


@app.post("/api/v1/assets/{asset_id}/command")
def execute_asset_command(asset_id: int, payload: AssetCommandRequest, request: Request):
    """通过平台代理执行单终端命令"""
    conn = get_db_connection()
    if not conn:
        raise HTTPException(status_code=500, detail="Database connection failed")

    cursor = None
    try:
        cursor = conn.cursor(dictionary=True)
        asset = get_asset_agent_target(cursor, asset_id)
        requester = get_request_username(request, fallback=payload.requester or payload.operator or "console")
        request_payload = {
            "command": payload.command,
            "operator": requester,
            "requester": requester,
        }
        return proxy_agent_json_request(
            asset,
            "/api/v1/command",
            payload=request_payload,
            timeout_seconds=60,
        )
    finally:
        if cursor:
            cursor.close()
        conn.close()


@app.post("/api/v1/assets/{asset_id}/trigger-report")
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


# ============================================================
# 启动服务
# ============================================================

@app.get("/api/v1/software/all")
def get_all_software():
    """获取所有软件安装记录（详细清单）"""
    conn = get_db_connection()
    if not conn:
        raise HTTPException(status_code=500, detail="Database connection failed")

    try:
        cursor = conn.cursor(dictionary=True)

        cursor.execute("""
            SELECT
                s.id,
                s.software_name,
                s.version,
                s.vendor,
                s.install_date,
                s.size,
                a.id as asset_id,
                a.hostname,
                a.ip_address
            FROM asset_software s
            LEFT JOIN assets a ON s.asset_id = a.id
            WHERE a.deleted_at IS NULL
            ORDER BY s.software_name, a.hostname
        """)

        software_list = cursor.fetchall()

        return {"data": software_list}

    except Error as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        cursor.close()
        conn.close()


@app.get("/api/v1/discovery/tasks")
def get_discovery_tasks():
    """获取资产发现任务列表"""
    tasks = list_discovery_tasks()
    return {"data": tasks, "total": len(tasks)}


@app.get("/api/v1/discovery/tasks/{task_id}")
def get_discovery_task_detail(task_id: str):
    """获取资产发现任务详情"""
    task = get_discovery_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Discovery task not found")
    return serialize_discovery_task(task)


@app.post("/api/v1/discovery/tasks/{task_id}/cancel")
def cancel_discovery_task(task_id: str):
    """取消资产发现任务"""
    task = get_discovery_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Discovery task not found")
    if task["status"] in {"completed", "failed", "cancelled"}:
        return {"message": "Task already finished", "task_id": task_id, "status": task["status"]}

    update_discovery_task(task_id, cancel_requested=True, status="cancelled", completed_at=datetime.now())
    return {"message": "Task cancellation requested", "task_id": task_id, "status": "cancelled"}


@app.post("/api/v1/discovery/ping")
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


@app.post("/api/v1/discovery/snmp")
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


# ============================================================
# Agent心跳接口
# ============================================================

@app.post("/api/v1/agent/heartbeat")
def agent_heartbeat(data: dict, request: Request):
    """接收Agent上报的心跳数据"""
    require_agent_request(request)

    conn = get_db_connection()
    if not conn:
        raise HTTPException(status_code=500, detail="Database connection failed")

    try:
        cursor = conn.cursor(dictionary=True)

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
                    updated_at = NOW()
                WHERE id = %s
            """, (
                hostname,
                ip_address,
                mac_address,
                AGENT_INSTALL_STATUS_INSTALLED,
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
                        updated_at = NOW()
                    WHERE id = %s
                """, (
                    hostname,
                    ip_address,
                    mac_address,
                    AGENT_INSTALL_STATUS_INSTALLED,
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
                        status, agent_install_status, last_seen, created_at, updated_at
                    ) VALUES ('pc', %s, %s, %s, 'online', %s, NOW(), NOW(), NOW())
                """, (hostname, ip_address, mac_address, AGENT_INSTALL_STATUS_INSTALLED))
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
            # 插入心跳记录
            cursor.execute("""
                INSERT INTO agent_heartbeat (
                    asset_id, cpu_usage, memory_usage, disk_usage,
                    process_count, logged_users, disk_info, heartbeat_time, created_at
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, NOW(), NOW())
            """, (
                asset_id,
                data.get('cpu_usage', 0),
                data.get('memory_usage', 0),
                data.get('disk_usage', 0),
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
                    # 处理size字段：将字符串"1.5 MB"转换为数字
                    size_str = software.get('size', '')
                    size_mb = 0
                    if size_str:
                        try:
                            if 'KB' in size_str:
                                size_mb = float(size_str.replace('KB', '').strip()) / 1024
                            elif 'MB' in size_str:
                                size_mb = float(size_str.replace('MB', '').strip())
                            elif 'GB' in size_str:
                                size_mb = float(size_str.replace('GB', '').strip()) * 1024
                            else:
                                size_mb = 0
                        except:
                            size_mb = 0

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

        return {
            "status": "success",
            "asset_id": asset_id,
            "message": f"Heartbeat received: {report_type}"
        }

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

@app.get("/api/v1/groups")
def get_groups():
    """获取所有分组"""
    conn = get_db_connection()
    if not conn:
        raise HTTPException(status_code=500, detail="Database connection failed")

    try:
        cursor = conn.cursor(dictionary=True)

        cursor.execute("""
            SELECT g.id, g.name, g.description, g.created_at,
                   COUNT(a.id) as asset_count
            FROM asset_groups g
            LEFT JOIN assets a ON a.group_id = g.id AND a.deleted_at IS NULL
            GROUP BY g.id, g.name, g.description, g.created_at
            ORDER BY g.name
        """)

        groups = cursor.fetchall()

        # 格式化日期
        for group in groups:
            if group.get('created_at'):
                group['created_at'] = group['created_at'].strftime('%Y-%m-%d %H:%M:%S')

        return {"data": groups, "total": len(groups)}

    except Error as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        cursor.close()
        conn.close()


@app.post("/api/v1/groups")
def create_group(data: dict):
    """创建分组"""
    name = data.get('name')
    description = data.get('description', '')

    if not name:
        raise HTTPException(status_code=400, detail="Group name is required")

    conn = get_db_connection()
    if not conn:
        raise HTTPException(status_code=500, detail="Database connection failed")

    try:
        cursor = conn.cursor()

        # 检查名称是否重复
        cursor.execute("SELECT id FROM asset_groups WHERE name = %s", (name,))
        if cursor.fetchone():
            raise HTTPException(status_code=400, detail="Group name already exists")

        cursor.execute("""
            INSERT INTO asset_groups (name, description, created_at)
            VALUES (%s, %s, NOW())
        """, (name, description))

        conn.commit()
        group_id = cursor.lastrowid

        return {"message": "Group created successfully", "id": group_id}

    except HTTPException:
        raise
    except Error as e:
        conn.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        cursor.close()
        conn.close()


@app.put("/api/v1/groups/{group_id}")
def update_group(group_id: int, data: dict):
    """更新分组"""
    name = data.get('name')
    description = data.get('description', '')

    if not name:
        raise HTTPException(status_code=400, detail="Group name is required")

    conn = get_db_connection()
    if not conn:
        raise HTTPException(status_code=500, detail="Database connection failed")

    try:
        cursor = conn.cursor()

        # 检查分组是否存在
        cursor.execute("SELECT id FROM asset_groups WHERE id = %s", (group_id,))
        if not cursor.fetchone():
            raise HTTPException(status_code=404, detail="Group not found")

        # 检查名称是否与其他分组重复
        cursor.execute("SELECT id FROM asset_groups WHERE name = %s AND id != %s", (name, group_id))
        if cursor.fetchone():
            raise HTTPException(status_code=400, detail="Group name already exists")

        cursor.execute("""
            UPDATE asset_groups
            SET name = %s, description = %s
            WHERE id = %s
        """, (name, description, group_id))

        conn.commit()

        return {"message": "Group updated successfully"}

    except HTTPException:
        raise
    except Error as e:
        conn.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        cursor.close()
        conn.close()


@app.delete("/api/v1/groups/{group_id}")
def delete_group(group_id: int):
    """删除分组"""
    conn = get_db_connection()
    if not conn:
        raise HTTPException(status_code=500, detail="Database connection failed")

    try:
        cursor = conn.cursor()

        # 检查分组是否存在
        cursor.execute("SELECT id FROM asset_groups WHERE id = %s", (group_id,))
        if not cursor.fetchone():
            raise HTTPException(status_code=404, detail="Group not found")

        # 检查是否有资产使用该分组
        cursor.execute("SELECT COUNT(*) as count FROM assets WHERE group_id = %s AND deleted_at IS NULL", (group_id,))
        result = cursor.fetchone()
        if result and result[0] > 0:
            raise HTTPException(status_code=400, detail=f"Cannot delete group with {result[0]} assets")

        # 删除分组
        cursor.execute("DELETE FROM asset_groups WHERE id = %s", (group_id,))
        conn.commit()

        return {"message": "Group deleted successfully"}

    except HTTPException:
        raise
    except Error as e:
        conn.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        cursor.close()
        conn.close()


# ============================================================
# 软件管理接口
# ============================================================

@app.get("/api/v1/software/stats")
def get_software_stats(limit: int = Query(default=10, ge=1, le=100)):
    """获取软件安装统计（Top N）"""
    conn = get_db_connection()
    if not conn:
        raise HTTPException(status_code=500, detail="Database connection failed")

    try:
        cursor = conn.cursor(dictionary=True)

        cursor.execute("""
            SELECT
                s.software_name,
                s.version,
                s.vendor,
                COUNT(DISTINCT s.asset_id) as install_count,
                GROUP_CONCAT(DISTINCT a.hostname ORDER BY a.hostname SEPARATOR ', ') as hostnames,
                GROUP_CONCAT(DISTINCT a.hostname ORDER BY a.hostname SEPARATOR ', ') as installed_assets
            FROM asset_software s
            JOIN assets a ON s.asset_id = a.id AND a.deleted_at IS NULL
            GROUP BY s.software_name, s.version, s.vendor
            ORDER BY install_count DESC, s.software_name
            LIMIT %s
        """, (limit,))

        stats = cursor.fetchall()

        return {"data": stats, "total": len(stats)}

    except Error as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        cursor.close()
        conn.close()


# ============================================================
# 告警管理接口
# ============================================================

def normalize_alert_row(row: Dict[str, Any]):
    """统一前端告警输出结构"""
    details = row.get('details_json')
    if isinstance(details, str):
        try:
            details = json.loads(details)
        except json.JSONDecodeError:
            details = None

    return {
        "id": row["id"],
        "asset_id": row["asset_id"],
        "hostname": row.get("hostname") or (details or {}).get("hostname") or f"资产 {row['asset_id']}",
        "ip_address": row.get("ip_address") or (details or {}).get("ip_address") or "-",
        "alert_type": row["alert_type"],
        "severity": row["severity"],
        "status": row["status"],
        "message": row["message"],
        "current_value": safe_float(row.get("current_value")),
        "threshold_value": safe_float(row.get("threshold_value")),
        "created_at": format_datetime(row.get("first_triggered_at")),
        "last_seen_at": format_datetime(row.get("last_seen_at")),
        "resolved_at": format_datetime(row.get("resolved_at")),
        "resolved_by": row.get("resolved_by"),
        "details": details
    }


def build_alert_filters(
    status: Optional[str] = None,
    severity: Optional[str] = None,
    alert_type: Optional[str] = None,
    keyword: Optional[str] = None,
    start_time: Optional[str] = None,
    end_time: Optional[str] = None,
) -> Tuple[str, List[Any]]:
    where_clauses = ["1 = 1"]
    params: List[Any] = []

    if status:
        where_clauses.append("al.status = %s")
        params.append(status)

    if severity:
        where_clauses.append("al.severity = %s")
        params.append(severity)

    if alert_type:
        where_clauses.append("al.alert_type = %s")
        params.append(alert_type)

    if keyword:
        keyword_like = f"%{keyword}%"
        where_clauses.append("""
            (
                al.message LIKE %s
                OR a.hostname LIKE %s
                OR a.ip_address LIKE %s
            )
        """)
        params.extend([keyword_like, keyword_like, keyword_like])

    if start_time:
        where_clauses.append("COALESCE(al.first_triggered_at, al.created_at) >= %s")
        params.append(start_time)

    if end_time:
        where_clauses.append("COALESCE(al.first_triggered_at, al.created_at) <= %s")
        params.append(end_time)

    return " AND ".join(where_clauses), params


@app.get("/api/v1/alerts/stats")
def get_alert_stats():
    """获取告警统计"""
    conn = get_db_connection()
    if not conn:
        raise HTTPException(status_code=500, detail="Database connection failed")

    try:
        ensure_alerts_table(conn)
        try:
            sync_alerts(conn)
        except Exception as sync_error:
            conn.rollback()
            safe_console_print(f"[Alerts] Sync skipped during stats request: {sync_error}")

        cursor = conn.cursor(dictionary=True)

        cursor.execute("""
            SELECT
                COUNT(CASE WHEN first_triggered_at >= DATE_SUB(NOW(), INTERVAL 7 DAY) THEN 1 END) AS total_7days,
                COUNT(CASE WHEN status = 'active' THEN 1 END) AS active,
                COUNT(CASE WHEN status = 'resolved' THEN 1 END) AS resolved
            FROM alerts
        """)
        summary = cursor.fetchone() or {}

        cursor.execute("""
            SELECT severity, COUNT(*) AS count
            FROM alerts
            WHERE status = 'active'
            GROUP BY severity
        """)
        by_severity = {row["severity"]: row["count"] for row in cursor.fetchall()}

        cursor.execute("""
            SELECT alert_type, COUNT(*) AS count
            FROM alerts
            WHERE status = 'active'
            GROUP BY alert_type
        """)
        by_type = {row["alert_type"]: row["count"] for row in cursor.fetchall()}

        return {
            "total_7days": summary.get("total_7days", 0) or 0,
            "active": summary.get("active", 0) or 0,
            "resolved": summary.get("resolved", 0) or 0,
            "unresolved": summary.get("active", 0) or 0,
            "by_severity": by_severity,
            "by_type": by_type
        }
    except Error as e:
        raise HTTPException(status_code=500, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        if 'cursor' in locals():
            cursor.close()
        conn.close()


@app.get("/api/v1/alerts")
def get_alerts(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=100),
    status: Optional[str] = Query(default=None),
    severity: Optional[str] = Query(default=None),
    alert_type: Optional[str] = Query(default=None),
    keyword: Optional[str] = Query(default=None),
    start_time: Optional[str] = Query(default=None),
    end_time: Optional[str] = Query(default=None),
):
    """获取告警列表"""
    conn = get_db_connection()
    if not conn:
        raise HTTPException(status_code=500, detail="Database connection failed")

    cursor = conn.cursor(dictionary=True)
    try:
        sync_alerts(conn)
        where_sql, params = build_alert_filters(
            status=status,
            severity=severity,
            alert_type=alert_type,
            keyword=keyword,
            start_time=start_time,
            end_time=end_time,
        )

        cursor.execute(f"""
            SELECT COUNT(*) AS total
            FROM alerts al
            LEFT JOIN assets a ON a.id = al.asset_id
            WHERE {where_sql}
        """, params)
        total = cursor.fetchone()["total"]

        offset = (page - 1) * page_size
        cursor.execute(f"""
            SELECT
                al.*,
                a.hostname,
                a.ip_address
            FROM alerts al
            LEFT JOIN assets a ON a.id = al.asset_id
            WHERE {where_sql}
            ORDER BY
                CASE al.status WHEN 'active' THEN 0 ELSE 1 END,
                CASE al.severity WHEN 'critical' THEN 0 WHEN 'warning' THEN 1 ELSE 2 END,
                al.last_seen_at DESC,
                al.id DESC
            LIMIT %s OFFSET %s
        """, params + [page_size, offset])
        rows = cursor.fetchall()

        return {
            "data": [normalize_alert_row(row) for row in rows],
            "total": total,
            "page": page,
            "page_size": page_size
        }
    except Error as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        cursor.close()
        conn.close()


@app.get("/api/v1/alerts/{alert_id}/detail")
def get_alert_detail(alert_id: int):
    """获取告警详情"""
    conn = get_db_connection()
    if not conn:
        raise HTTPException(status_code=500, detail="Database connection failed")

    cursor = conn.cursor(dictionary=True)
    try:
        sync_alerts(conn)
        cursor.execute("""
            SELECT
                al.*,
                a.hostname,
                a.ip_address
            FROM alerts al
            LEFT JOIN assets a ON a.id = al.asset_id
            WHERE al.id = %s
            LIMIT 1
        """, (alert_id,))
        row = cursor.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Alert not found")
        return normalize_alert_row(row)
    except Error as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        cursor.close()
        conn.close()


@app.post("/api/v1/alerts/resolve-batch")
def resolve_alerts_batch(payload: AlertBatchResolveRequest, request: Request):
    """批量标记告警为已解决"""
    if not payload.ids:
        raise HTTPException(status_code=400, detail="Alert ids are required")

    conn = get_db_connection()
    if not conn:
        raise HTTPException(status_code=500, detail="Database connection failed")

    cursor = conn.cursor(dictionary=True)
    try:
        ensure_alerts_table(conn)
        placeholders = ", ".join(["%s"] * len(payload.ids))

        cursor.execute(
            f"""
            SELECT id, status
            FROM alerts
            WHERE id IN ({placeholders})
            """,
            payload.ids,
        )
        rows = cursor.fetchall()
        if not rows:
            raise HTTPException(status_code=404, detail="No alerts found")

        existing_ids = {row["id"] for row in rows}
        missing_ids = [alert_id for alert_id in payload.ids if alert_id not in existing_ids]
        active_ids = [row["id"] for row in rows if row["status"] == "active"]
        resolved_by = get_request_username(request, fallback="console")

        resolved_count = 0
        if active_ids:
            active_placeholders = ", ".join(["%s"] * len(active_ids))
            cursor.execute(
                f"""
                UPDATE alerts
                SET status = 'resolved',
                    active_fingerprint = NULL,
                    resolved_at = NOW(),
                    resolved_by = %s,
                    updated_at = NOW()
                WHERE id IN ({active_placeholders})
                """,
                [resolved_by] + active_ids,
            )
            resolved_count = cursor.rowcount or 0
            conn.commit()

        return {
            "message": "Batch resolve completed",
            "requested": len(payload.ids),
            "resolved": resolved_count,
            "resolved_count": resolved_count,
            "already_resolved": len(rows) - len(active_ids),
            "missing_ids": missing_ids,
        }
    except HTTPException:
        raise
    except Error as e:
        conn.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        cursor.close()
        conn.close()


@app.put("/api/v1/alerts/{alert_id}/resolve")
def resolve_alert(alert_id: int, request: Request):
    """标记告警为已解决"""
    conn = get_db_connection()
    if not conn:
        raise HTTPException(status_code=500, detail="Database connection failed")

    cursor = conn.cursor(dictionary=True)
    try:
        ensure_alerts_table(conn)

        cursor.execute("""
            SELECT id, status
            FROM alerts
            WHERE id = %s
        """, (alert_id,))
        row = cursor.fetchone()

        if not row:
            raise HTTPException(status_code=404, detail="Alert not found")

        if row["status"] != "active":
            return {"message": "Alert already resolved"}

        resolved_by = get_request_username(request, fallback="console")
        cursor.execute("""
            UPDATE alerts
            SET status = 'resolved',
                active_fingerprint = NULL,
                resolved_at = NOW(),
                resolved_by = %s,
                updated_at = NOW()
            WHERE id = %s
        """, (resolved_by, alert_id))
        conn.commit()

        return {"message": "Alert resolved successfully"}
    except Error as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        cursor.close()
        conn.close()


@app.get("/api/v1/alerts/export")
def export_alerts(
    status: Optional[str] = Query(default=None),
    severity: Optional[str] = Query(default=None),
    alert_type: Optional[str] = Query(default=None),
    keyword: Optional[str] = Query(default=None),
    start_time: Optional[str] = Query(default=None),
    end_time: Optional[str] = Query(default=None),
):
    """导出告警列表 CSV"""
    conn = get_db_connection()
    if not conn:
        raise HTTPException(status_code=500, detail="Database connection failed")

    severity_labels = {
        "critical": "严重",
        "error": "错误",
        "warning": "警告",
        "info": "信息",
    }
    type_labels = {
        "cpu": "CPU",
        "memory": "内存",
        "disk": "磁盘",
        "offline": "离线",
        "health": "健康度",
        "warranty": "保修",
    }
    status_labels = {
        "active": "活跃",
        "resolved": "已解决",
    }

    cursor = conn.cursor(dictionary=True)
    try:
        sync_alerts(conn)
        where_sql, params = build_alert_filters(
            status=status,
            severity=severity,
            alert_type=alert_type,
            keyword=keyword,
            start_time=start_time,
            end_time=end_time,
        )
        cursor.execute(
            f"""
            SELECT
                al.*,
                a.hostname,
                a.ip_address
            FROM alerts al
            LEFT JOIN assets a ON a.id = al.asset_id
            WHERE {where_sql}
            ORDER BY
                CASE al.status WHEN 'active' THEN 0 ELSE 1 END,
                CASE al.severity WHEN 'critical' THEN 0 WHEN 'warning' THEN 1 ELSE 2 END,
                al.last_seen_at DESC,
                al.id DESC
            """,
            params,
        )
        rows = cursor.fetchall()

        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow([
            "告警ID",
            "资产ID",
            "主机名",
            "IP地址",
            "告警类型",
            "严重程度",
            "状态",
            "告警信息",
            "当前值",
            "阈值",
            "首次触发时间",
            "最近出现时间",
            "解决时间",
            "解决人",
        ])

        for row in rows:
            normalized = normalize_alert_row(row)
            writer.writerow([
                normalized["id"],
                normalized["asset_id"],
                normalized["hostname"],
                normalized["ip_address"],
                type_labels.get(normalized["alert_type"], normalized["alert_type"]),
                severity_labels.get(normalized["severity"], normalized["severity"]),
                status_labels.get(normalized["status"], normalized["status"]),
                normalized["message"],
                normalized["current_value"] if normalized["current_value"] is not None else "",
                normalized["threshold_value"] if normalized["threshold_value"] is not None else "",
                normalized["created_at"] or "",
                normalized["last_seen_at"] or "",
                normalized["resolved_at"] or "",
                normalized["resolved_by"] or "",
            ])

        csv_content = "\ufeff" + output.getvalue()
        return Response(
            content=csv_content,
            media_type="text/csv; charset=utf-8",
            headers={
                "Content-Disposition": f"attachment; filename=alerts-{datetime.now().strftime('%Y%m%d-%H%M%S')}.csv"
            },
        )
    except Error as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        cursor.close()
        conn.close()


@app.post("/api/v1/logs")
def create_system_activity_log(payload: SystemActivityLogCreate, request: Request):
    """写入统一运行时日志。"""
    token = extract_bearer_token(request)
    auth_user = verify_access_token(token)
    agent_auth = None
    if auth_user:
        # 该接口对 Agent 上报做了中间件豁免，平台用户仍必须按 RBAC 校验写入权限。
        require_request_permission(auth_user, request.url.path, request.method)
        request.state.auth_user = auth_user
        payload.operator_name = normalize_actor_name(auth_user.get("username"), fallback="console")
    else:
        agent_auth = verify_agent_token(token)
        if agent_auth:
            request.state.agent_auth = agent_auth
            payload.operator_name = build_trusted_agent_operator_name(payload)

    if not auth_user and not agent_auth:
        raise HTTPException(status_code=401, detail="Unauthorized")

    conn = get_db_connection()
    if not conn:
        raise HTTPException(status_code=500, detail="Database connection failed")

    cursor = conn.cursor()
    try:
        ensure_system_activity_logs_table(conn)
        log_id, event_time = insert_system_activity_log(cursor, payload)
        conn.commit()

        return {
            "message": "Log created successfully",
            "id": log_id,
            "event_time": format_datetime(event_time),
        }
    except Error as e:
        conn.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        cursor.close()
        conn.close()


@app.get("/api/v1/logs")
def get_unified_logs(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=200),
    source_type: Optional[str] = Query(default=None),
    module: Optional[str] = Query(default=None),
    category: Optional[str] = Query(default=None),
    asset_id: Optional[int] = Query(default=None),
    level: Optional[str] = Query(default=None),
    result: Optional[str] = Query(default=None),
    keyword: Optional[str] = Query(default=None),
    start_time: Optional[str] = Query(default=None),
    end_time: Optional[str] = Query(default=None),
):
    """聚合查询统一日志。"""
    conn = get_db_connection()
    if not conn:
        raise HTTPException(status_code=500, detail="Database connection failed")

    cursor = conn.cursor(dictionary=True)
    try:
        ensure_system_activity_logs_table(conn)
        union_sql = build_unified_logs_union(conn)
        where_sql, params = build_unified_logs_where(
            source_type=source_type,
            module=module,
            category=category,
            asset_id=asset_id,
            keyword=keyword,
            level=level,
            result=result,
            start_time=start_time,
            end_time=end_time,
        )

        cursor.execute(f"""
            SELECT COUNT(*) AS total
            FROM ({union_sql}) logs
            WHERE {where_sql}
        """, params)
        total = (cursor.fetchone() or {}).get("total", 0) or 0

        offset = (page - 1) * page_size
        cursor.execute(f"""
            SELECT *
            FROM ({union_sql}) logs
            WHERE {where_sql}
            ORDER BY logs.event_time DESC, logs.source_id DESC
            LIMIT %s OFFSET %s
        """, params + [page_size, offset])
        rows = cursor.fetchall()

        return {
            "data": [normalize_log_row(row) for row in rows],
            "total": total,
            "page": page,
            "page_size": page_size,
        }
    except Error as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        cursor.close()
        conn.close()


@app.get("/api/v1/logs/stats")
def get_unified_log_stats(
    start_time: Optional[str] = Query(default=None),
    end_time: Optional[str] = Query(default=None),
):
    """获取统一日志统计。"""
    conn = get_db_connection()
    if not conn:
        raise HTTPException(status_code=500, detail="Database connection failed")

    cursor = conn.cursor(dictionary=True)
    try:
        ensure_system_activity_logs_table(conn)
        union_sql = build_unified_logs_union(conn)
        where_sql, params = build_unified_logs_where(
            start_time=start_time,
            end_time=end_time,
        )

        cursor.execute(f"""
            SELECT
                COUNT(*) AS total,
                COUNT(CASE WHEN logs.event_time >= DATE_SUB(NOW(), INTERVAL 24 HOUR) THEN 1 END) AS total_24h,
                COUNT(CASE WHEN logs.event_time >= DATE_SUB(NOW(), INTERVAL 7 DAY) THEN 1 END) AS total_7days,
                COUNT(CASE WHEN logs.level = 'error' THEN 1 END) AS error_count,
                COUNT(CASE WHEN logs.level = 'warning' THEN 1 END) AS warning_count
            FROM ({union_sql}) logs
            WHERE {where_sql}
        """, params)
        summary = cursor.fetchone() or {}

        cursor.execute(f"""
            SELECT logs.level, COUNT(*) AS count
            FROM ({union_sql}) logs
            WHERE {where_sql}
            GROUP BY logs.level
            ORDER BY count DESC
        """, params)
        by_level = {row["level"] or "unknown": row["count"] for row in cursor.fetchall()}

        cursor.execute(f"""
            SELECT logs.module, COUNT(*) AS count
            FROM ({union_sql}) logs
            WHERE {where_sql}
            GROUP BY logs.module
            ORDER BY count DESC
        """, params)
        by_module = {row["module"] or "unknown": row["count"] for row in cursor.fetchall()}

        cursor.execute(f"""
            SELECT logs.source_type, COUNT(*) AS count
            FROM ({union_sql}) logs
            WHERE {where_sql}
            GROUP BY logs.source_type
            ORDER BY count DESC
        """, params)
        by_source_type = {row["source_type"] or "unknown": row["count"] for row in cursor.fetchall()}

        return {
            "total": summary.get("total", 0) or 0,
            "total_24h": summary.get("total_24h", 0) or 0,
            "total_7days": summary.get("total_7days", 0) or 0,
            "error_count": summary.get("error_count", 0) or 0,
            "warning_count": summary.get("warning_count", 0) or 0,
            "by_level": by_level,
            "by_module": by_module,
            "by_source_type": by_source_type,
        }
    except Error as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        cursor.close()
        conn.close()


@app.on_event("startup")
def startup_background_workers():
    """启动后台守护线程。"""
    conn = get_db_connection()
    if conn:
        try:
            ensure_assets_agent_schema(conn)
            ensure_asset_changes_table(conn)
        finally:
            conn.close()
    ensure_status_reconcile_worker_started()


if __name__ == "__main__":
    import uvicorn

    safe_console_print("=" * 60)
    safe_console_print("Z-View Assets API Starting...")
    safe_console_print("=" * 60)
    safe_console_print("Service: http://localhost:8080")
    safe_console_print("API Docs: http://localhost:8080/docs")
    safe_console_print("Health Check: http://localhost:8080/")
    safe_console_print("=" * 60)

    uvicorn.run(
        app,
        host="0.0.0.0",
        port=8080,
        log_level="info"
    )
