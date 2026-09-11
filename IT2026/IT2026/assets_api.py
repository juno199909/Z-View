"""
Assets API - 资产管理接口
使用FastAPI实现资产的增删改查
"""

import asyncio
import csv
import hashlib
import hmac
import http.client
import io
import ipaddress
import secrets
import datetime
import contextlib
import json
import socket
import subprocess
import threading
import time
import uuid
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from datetime import datetime
from urllib.parse import urlencode

import websockets
from fastapi import FastAPI, HTTPException, Query, Request, Response, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field
from starlette.websockets import WebSocketState
from typing import List, Optional, Dict, Any, Tuple
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
    set_agent_device_credential_verifier,
    TOKEN_SECRET,
    user_has_permission,
    issue_access_token,
    verify_access_token,
    list_users,
    create_user,
    update_user_role,
    set_user_enabled,
    admin_reset_password,
    delete_user,
)
from console_utils import enable_utf8_stdio, safe_console_print
from config_utils import get_cors_middleware_options, get_db_config, get_env

SNMP_API_MODE = None



enable_utf8_stdio()

app = FastAPI(title="Z-View Assets API", version="1.0.0")

# 配置CORS
# P4-03：Request ID 中间件（生成/透传 X-Request-ID，响应头返回）
@app.middleware("http")
async def request_id_middleware(request: Request, call_next):
    rid = request.headers.get("x-request-id") or new_request_id()
    request_id_var.set(rid)
    start_time = time.perf_counter()
    response = await call_next(request)
    response.headers["X-Request-ID"] = rid
    # P4-04：HTTP 指标（方法/状态/耗时）
    try:
        route = request.scope.get("route")
        endpoint = getattr(route, "path", request.url.path)
        labels = {"method": request.method, "endpoint": endpoint, "status": str(response.status_code)}
        inc_counter("zview_http_requests_total", labels)
        observe_histogram("zview_http_request_duration_seconds", time.perf_counter() - start_time, labels)
    except Exception:
        pass
    return response


app.add_middleware(
    CORSMiddleware,
    **get_cors_middleware_options(),
)

AUTH_EXEMPTIONS = (
    {"path": "/api/v1/auth/login", "methods": ["POST"]},
    {"path": "/api/health", "methods": ["GET"]},  # P4-01/P1-08：运维健康探针（内网）
    {"path": "/metrics", "methods": ["GET"]},  # P4-04：Prometheus 抓取端点（内网）
    {"path": "/api/v1/agent/heartbeat", "methods": ["POST"]},
    {"path": "/api/v1/agent/policies", "methods": ["GET"]},
    {"path": "/api/v1/agent/security-policies", "methods": ["GET"]},
    {"path": "/api/v1/agent/security-policy-result", "methods": ["POST"]},
    {"path": "/api/v1/agent/upgrade/download", "methods": ["GET"]},
    {"path": "/api/v1/logs", "methods": ["POST"]},
)

# 数据库配置
DB_CONFIG = get_db_config()

# P1-01/P1-06：平台分层架构（常量/工具/告警服务/路由迁至 platform 包，原名保持兼容）
from zvplatform.settings import get_settings as _get_app_settings  # noqa: E402
_alert_settings = _get_app_settings().alerts
ALERT_OFFLINE_SECONDS = _alert_settings.offline_seconds
ALERT_ONLINE_SECONDS = _alert_settings.online_seconds
ALERT_THRESHOLDS = {
    "cpu": {"warning": _alert_settings.cpu_warning, "critical": _alert_settings.cpu_critical},
    "memory": {"warning": _alert_settings.memory_warning, "critical": _alert_settings.memory_critical},
    "disk": {"warning": _alert_settings.disk_warning, "critical": _alert_settings.disk_critical},
    "health": {"warning": 60.0, "critical": 40.0},
}
from zvplatform.common import (  # noqa: E402
    compute_health_score,
    get_request_client_ip,
    parse_json_field,
    safe_float,
)
from zvplatform.db import format_datetime  # noqa: E402
from zvplatform.repositories.alert_repository import build_alert_filters  # noqa: E402
from zvplatform.db import create_connection  # noqa: E402
STATUS_RECONCILE_INTERVAL_SECONDS = 30  # P1-01：原常量区迁出后保留（对账线程间隔）
from zvplatform.models import SystemActivityLogCreate  # noqa: E402
from zvplatform.db import table_exists  # noqa: E402
from zvplatform.repositories.log_repository import (  # noqa: E402
    ensure_system_activity_logs_table,  # noqa: E402
    insert_system_activity_log,  # noqa: E402
    normalize_log_row,  # noqa: F401  (logs 路由内部使用)
    )
from zvplatform.agent_client import AGENT_CONTROL_PORT, build_agent_auth_headers  # noqa: E402
from zvplatform.common import truncate_text  # noqa: E402
from zvplatform.services.batch_service import (  # noqa: E402
    build_batch_command,  # noqa: F401
    build_batch_output,  # noqa: F401
    build_batch_parameters_text,  # noqa: F401
    build_batch_zview_cmd,  # noqa: F401
    build_restart_command,  # noqa: F401
    build_script_command,  # noqa: F401
    build_shutdown_command,  # noqa: F401
    build_software_command,  # noqa: F401
    escape_powershell_single_quoted,  # noqa: F401
    execute_batch_command_on_agent,  # noqa: F401
    get_batch_operation_timeout,  # noqa: F401
    normalize_batch_result_row,  # noqa: F401
)
from zvplatform.routers.batch import router as batch_platform_router
from zvplatform.obs import format_log_line, get_request_id, new_request_id, request_id_var  # noqa: E402
from zvplatform.metrics import inc_counter, observe_histogram, render_prometheus, set_gauge  # noqa: E402
from zvplatform.routers.discovery import router as discovery_platform_router
from zvplatform.routers.agent_heartbeat import router as agent_heartbeat_router  # P1-01：心跳本体
from zvplatform.routers.agent_jobs import router as agent_jobs_router  # V1.8.3：通用任务通道
from zvplatform.routers.incidents import router as incidents_router  # V1.9.0：事件聚合
from zvplatform.routers.groups import router as groups_platform_router
from zvplatform.routers.agent_policy import router as agent_policy_router
from zvplatform.routers.logs import (  # noqa: E402
    build_trusted_agent_operator_name,  # noqa: F401  (logs 路由内部使用)
    router as logs_platform_router,
)
from zvplatform.routers.alerts import router as alerts_platform_router  # noqa: E402
from zvplatform.services.alert_service import sync_alerts  # noqa: E402
from zvplatform import worker_health


AGENT_CONTROL_PORT = int(get_env("ZVIEW_AGENT_CONTROL_PORT", "9001") or "9001")
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


class LoginRequest(BaseModel):
    username: str
    password: str


class ChangePasswordRequest(BaseModel):
    current_password: str = Field(..., min_length=1)
    new_password: str = Field(..., min_length=6)


    operator_name: Optional[str] = "console"


class DiscoveryPingRequest(BaseModel):
    ip_ranges: List[str] = Field(default_factory=list)
    concurrency: int = Field(default=100, ge=1, le=1000)
    timeout: int = Field(default=3000, ge=500, le=10000)


class DiscoveryImportRequest(BaseModel):
    ip_address: str
    hostname: Optional[str] = None
    mac_address: Optional[str] = None
    vendor: Optional[str] = None
    device_type: Optional[str] = None


class DiscoverySNMPTarget(BaseModel):
    ip: str
    community: str = "public"


class DiscoverySNMPRequest(BaseModel):
    targets: List[DiscoverySNMPTarget] = Field(default_factory=list)
    version: int = Field(default=2)
    timeout: int = Field(default=5, ge=1, le=30)




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


def fmt_dt(value):
    """格式化时间字段为字符串（P1-01 迁移补齐，供设备凭据等接口使用）。"""
    return format_datetime(value)


def get_db_connection():
    """数据库连接（P1-06：实现迁至 platform.db，保留原函数名兼容）。"""
    return create_connection()


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

        cursor.execute(
            """
            SELECT column_name, column_type
            FROM information_schema.columns
            WHERE table_schema = %s
              AND table_name = 'asset_changes'
            """,
            (DB_CONFIG["database"],),
        )
        existing_columns = {
            row[0]: (row[1] or "").lower()
            for row in cursor.fetchall()
        }

        # Older deployments used changed_by/changed_at and enum values that do
        # not support the current agent and platform history records.
        compatible_columns = {
            "operator_name": "ALTER TABLE asset_changes ADD COLUMN operator_name VARCHAR(120) NULL AFTER source_type",
            "details_json": "ALTER TABLE asset_changes ADD COLUMN details_json LONGTEXT NULL AFTER operator_name",
            "created_at": "ALTER TABLE asset_changes ADD COLUMN created_at DATETIME NULL AFTER details_json",
        }
        for column_name, statement in compatible_columns.items():
            if column_name not in existing_columns:
                cursor.execute(statement)

        if existing_columns.get("change_type", "").startswith("enum("):
            cursor.execute("ALTER TABLE asset_changes MODIFY COLUMN change_type VARCHAR(50) NOT NULL")

        if existing_columns.get("source_type", "").startswith("enum("):
            cursor.execute(
                """
                UPDATE asset_changes
                SET source_type = 'agent'
                WHERE source_type IS NULL OR source_type = ''
                """
            )
            cursor.execute(
                """
                ALTER TABLE asset_changes
                MODIFY COLUMN source_type VARCHAR(50) NOT NULL DEFAULT 'platform'
                """
            )

        if "changed_by" in existing_columns:
            cursor.execute(
                """
                UPDATE asset_changes
                SET operator_name = changed_by
                WHERE (operator_name IS NULL OR operator_name = '')
                  AND changed_by IS NOT NULL
                  AND changed_by <> ''
                """
            )

        if "changed_at" in existing_columns:
            cursor.execute(
                """
                UPDATE asset_changes
                SET created_at = changed_at
                WHERE created_at IS NULL
                  AND changed_at IS NOT NULL
                """
            )

        cursor.execute(
            """
            UPDATE asset_changes
            SET created_at = NOW()
            WHERE created_at IS NULL
            """
        )
        cursor.execute(
            """
            ALTER TABLE asset_changes
            MODIFY COLUMN created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
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


def reconcile_expired_remote_sessions() -> int:
    """P0-07：把超过 TTL 的远控会话标记为 expired 断开。"""
    conn = get_db_connection()
    if not conn:
        return 0
    cursor = None
    try:
        from remote_desktop_api import ensure_remote_sessions_table
        cursor = conn.cursor()
        ensure_remote_sessions_table(conn)
        cursor.execute("""
            UPDATE remote_sessions
            SET status='disconnected', disconnected_at=NOW(), disconnect_reason='expired'
            WHERE status IN ('created','connecting','connected')
              AND created_at < DATE_SUB(NOW(), INTERVAL COALESCE(max_duration_sec,7200) SECOND)
        """)
        expired = cursor.rowcount
        conn.commit()
        return expired
    except Exception as exc:
        safe_console_print(f"[StatusReconcile] expire remote sessions error: {exc}")
        try:
            conn.rollback()
        except Exception:
            pass
        return 0
    finally:
        if cursor:
            cursor.close()
        conn.close()


def status_reconcile_loop():
    """后台状态对账线程。"""
    worker_health.register("status-reconcile", "资产状态对账 + 远控会话 TTL（30s）")
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
            expired = reconcile_expired_remote_sessions()
            if expired:
                safe_console_print(f"[StatusReconcile] Remote sessions expired: {expired}")
            worker_health.mark_run("status-reconcile", ok=True)
        except Exception as exc:
            worker_health.mark_run("status-reconcile", ok=False, error=exc)
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


# ============================================================
# 告警后台评估线程（P1-02）：不再依赖告警中心页面访问触发
# ============================================================

ALERT_SYNC_INTERVAL_SECONDS = 60
ALERT_SYNC_LOCK = threading.Lock()
ALERT_SYNC_THREAD = None
ALERT_SYNC_STARTED = False


def alert_sync_loop():
    """后台告警评估线程：周期执行 sync_alerts（指纹比对 + 自动恢复）。"""
    worker_health.register("alert-sync", "告警规则评估同步（60s）")
    safe_console_print(f"[AlertSync] Worker started; interval={ALERT_SYNC_INTERVAL_SECONDS}s")

    while True:
        try:
            conn = create_connection()
            if conn:
                try:
                    new_alerts = sync_alerts(conn)
                    # V1.9.0 事件聚合：按当前活跃告警归并事件（每资产一个开放事件）
                    try:
                        from zvplatform.services.incident_service import sync_incidents
                        incident_result = sync_incidents(conn)
                        if incident_result.get("opened") or incident_result.get("resolved"):
                            safe_console_print(f"[AlertSync] incidents: "
                                               f"{ {k: v for k, v in incident_result.items() if k != 'resolved_incidents'} }")
                        # V1.9.0 恢复通知：事件自动恢复后推送到已配置通道
                        resolved_incidents = incident_result.get("resolved_incidents") or []
                        if resolved_incidents:
                            from zvplatform.services.alert_notify import dispatch_recovery_notifications
                            recovery_result = dispatch_recovery_notifications(conn, resolved_incidents)
                            if recovery_result.get("sent"):
                                safe_console_print(f"[AlertSync] recovery notification sent: "
                                                   f"{recovery_result.get('channel')} x{len(resolved_incidents)}")
                    except Exception as inc_exc:
                        safe_console_print(f"[AlertSync] incident sync failed: {inc_exc}")
                    # V1.7.1 通知层：新触发告警分发（webhook/邮件，配置见 alert_notify_config）
                    try:
                        from zvplatform.services.alert_notify import dispatch_alert_notifications
                        notify_result = dispatch_alert_notifications(conn, new_alerts or [])
                        if notify_result.get("notified"):
                            safe_console_print(f"[AlertSync] notify dispatched: {notify_result}")
                    except Exception as notify_exc:
                        safe_console_print(f"[AlertSync] notify dispatch failed: {notify_exc}")
                    worker_health.mark_run("alert-sync", ok=True)
                finally:
                    conn.close()
            else:
                worker_health.mark_run("alert-sync", ok=False, error="db_unavailable")
        except Exception as exc:
            worker_health.mark_run("alert-sync", ok=False, error=exc)
        time.sleep(ALERT_SYNC_INTERVAL_SECONDS)


def ensure_alert_sync_worker_started():
    """确保告警同步线程只启动一次。"""
    global ALERT_SYNC_THREAD, ALERT_SYNC_STARTED
    with ALERT_SYNC_LOCK:
        if ALERT_SYNC_STARTED and ALERT_SYNC_THREAD and ALERT_SYNC_THREAD.is_alive():
            return
        ALERT_SYNC_THREAD = threading.Thread(
            target=alert_sync_loop,
            daemon=True,
            name="alert-sync",
        )
        ALERT_SYNC_THREAD.start()
        ALERT_SYNC_STARTED = True


# ============================================================
# 磁盘水位守护线程（2026-09-09 C 盘 0 字节事故）：60s 检查，
# 低于阈值立即触发紧急缓存清理，不等 6h data-retention 周期
# ============================================================

DISK_GUARD_INTERVAL_SECONDS = 60
DISK_GUARD_MIN_FREE_BYTES = 5 * 1024 * 1024 * 1024
DISK_GUARD_LOCK = threading.Lock()
DISK_GUARD_THREAD = None
DISK_GUARD_STARTED = False


def disk_guard_loop():
    worker_health.register("disk-guard", "磁盘水位守护（60s，<5GB 触发紧急清理）")
    import shutil as _shutil

    while True:
        try:
            free = _shutil.disk_usage("C:\\").free
            if free < DISK_GUARD_MIN_FREE_BYTES:
                from zvplatform.disk_cleanup import run_disk_cache_cleanup
                results = run_disk_cache_cleanup(emergency=True)
                freed = results.pop("freed_bytes", 0) or 0
                safe_console_print(
                    f"[DiskGuard] low disk C: free={free / (1024 ** 3):.2f}GB, "
                    f"emergency cleanup freed={freed / (1024 ** 2):.1f}MB detail={results}"
                )
            worker_health.mark_run("disk-guard", ok=True)
        except Exception as exc:
            worker_health.mark_run("disk-guard", ok=False, error=exc)
        time.sleep(DISK_GUARD_INTERVAL_SECONDS)


def ensure_disk_guard_worker_started():
    """确保磁盘守护线程只启动一次。"""
    global DISK_GUARD_THREAD, DISK_GUARD_STARTED
    with DISK_GUARD_LOCK:
        if DISK_GUARD_STARTED and DISK_GUARD_THREAD and DISK_GUARD_THREAD.is_alive():
            return
        DISK_GUARD_THREAD = threading.Thread(
            target=disk_guard_loop,
            daemon=True,
            name="disk-guard",
        )
        DISK_GUARD_THREAD.start()
        DISK_GUARD_STARTED = True


# ============================================================
# 数据保留策略（审计 R11）：每日清理过期日志/事件/会话/心跳
# ============================================================

DATA_RETENTION_DAYS = {
    "system_activity_logs": 180,
    "security_policy_exec_results": 180,
    "remote_sessions": 90,
    "usb_events": 180,
}
RETENTION_CHECK_INTERVAL_SECONDS = 6 * 3600  # 每 6 小时检查一次
DATA_RETENTION_THREAD = None
DATA_RETENTION_STARTED = False
DATA_RETENTION_LOCK = threading.Lock()


def run_data_retention_cleanup() -> dict:
    """按保留天数清理过期数据，返回各表删除行数。"""
    conn = get_db_connection()
    if not conn:
        return {"error": "db_unavailable"}
    cursor = None
    deleted = {}
    try:
        cursor = conn.cursor()
        for table, days in DATA_RETENTION_DAYS.items():
            if table in ("usb_events",):
                time_col = "occurred_at"
            elif table == "security_policy_exec_results":
                time_col = "executed_at"
            elif table == "remote_sessions":
                time_col = "disconnected_at"
            else:
                time_col = "created_at"
            try:
                cursor.execute(
                    f"DELETE FROM {table} WHERE {time_col} < DATE_SUB(NOW(), INTERVAL %s DAY)",
                    (days,),
                )
                deleted[table] = cursor.rowcount
            except Exception as exc:
                deleted[table] = f"error: {exc}"
        conn.commit()
        if any(isinstance(v, int) and v > 0 for v in deleted.values()):
            safe_console_print(f"[DataRetention] cleaned: {deleted}")
        return deleted
    except Exception as exc:
        safe_console_print(f"[DataRetention] error: {exc}")
        try:
            conn.rollback()
        except Exception:
            pass
        return {"error": str(exc)}
    finally:
        if cursor:
            cursor.close()
        conn.close()


def data_retention_loop():
    """数据保留清理循环（启动先执行一次，之后每 6 小时）+ 磁盘缓存清理。"""
    worker_health.register("data-retention", "数据保留清理 + 磁盘缓存清理（6h）")
    safe_console_print(
        f"[DataRetention] Worker started; interval={RETENTION_CHECK_INTERVAL_SECONDS}s; "
        f"policy={DATA_RETENTION_DAYS}"
    )

    def _run_all():
        run_data_retention_cleanup()
        try:
            from zvplatform.disk_cleanup import log_disk_cache_cleanup
            log_disk_cache_cleanup()
        except Exception as exc:
            safe_console_print(f"[DataRetention] disk cleanup skipped: {exc}")

    try:
        _run_all()
        worker_health.mark_run("data-retention", ok=True)
    except Exception as exc:
        worker_health.mark_run("data-retention", ok=False, error=exc)
    while True:
        time.sleep(RETENTION_CHECK_INTERVAL_SECONDS)
        try:
            _run_all()
            worker_health.mark_run("data-retention", ok=True)
        except Exception as exc:
            worker_health.mark_run("data-retention", ok=False, error=exc)


def ensure_data_retention_worker_started():
    """确保数据保留线程只启动一次。"""
    global DATA_RETENTION_THREAD, DATA_RETENTION_STARTED
    with DATA_RETENTION_LOCK:
        if DATA_RETENTION_STARTED and DATA_RETENTION_THREAD and DATA_RETENTION_THREAD.is_alive():
            return
        DATA_RETENTION_THREAD = threading.Thread(
            target=data_retention_loop,
            daemon=True,
            name="data-retention",
        )
        DATA_RETENTION_THREAD.start()
        DATA_RETENTION_STARTED = True


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
            "agent_version": "ALTER TABLE assets ADD COLUMN agent_version VARCHAR(32) NULL AFTER agent_install_status",
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


def discovery_duration_text(started_at: Optional[datetime], completed_at: Optional[datetime] = None) -> str:
    if not started_at:
        return "-"
    end_time = completed_at or datetime.now()
    elapsed = max(0, int((end_time - started_at).total_seconds()))
    minutes, seconds = divmod(elapsed, 60)
    hours, minutes = divmod(minutes, 60)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}"



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


def cleanup_discovery_tasks_locked():
    # 恢复自 HEAD：P1-01 迁移服务版操作 discovery_service 自己的任务存储，
    # 本地任务字典仍由 assets_api 维护，需保留本地清理实现。
    from zvplatform.constants import DISCOVERY_TASK_RETENTION_SECONDS
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


# ============================================================
# API???

# API接口
# ============================================================


# P4-01/P1-08：健康探针（免认证，供运维探活与监控）
# P4-04：Prometheus 指标端点（免认证，内网抓取）
@app.get("/metrics")
def prometheus_metrics():
    # DB 健康作为指标
    conn = create_connection()
    db_ok = False
    if conn:
        try:
            cur = conn.cursor()
            cur.execute("SELECT 1")
            cur.fetchall()
            cur.close()
            db_ok = True
        except Exception:
            pass
        finally:
            conn.close()
    set_gauge("zview_db_up", 1 if db_ok else 0)

    conn2 = create_connection()
    if conn2:
        try:
            cur2 = conn2.cursor(dictionary=True)
            cur2.execute("SELECT COUNT(*) AS c FROM assets WHERE deleted_at IS NULL")
            total_assets = int((cur2.fetchone() or {}).get("c") or 0)
            cur2.execute("SELECT COUNT(*) AS c FROM assets WHERE deleted_at IS NULL AND last_seen >= DATE_SUB(NOW(), INTERVAL 90 SECOND)")
            online = int((cur2.fetchone() or {}).get("c") or 0)
            set_gauge("zview_assets_total", total_assets)
            set_gauge("zview_assets_online", online)
            # P2-03：远控会话指标
            cur2.execute("SELECT COUNT(*) AS c FROM remote_sessions WHERE status='connected'")
            set_gauge("zview_remote_sessions_active", int((cur2.fetchone() or {}).get("c") or 0))
            cur2.execute("SELECT COUNT(*) AS c FROM remote_sessions")
            set_gauge("zview_remote_sessions_total", int((cur2.fetchone() or {}).get("c") or 0))
            cur2.execute("SELECT COALESCE(transport_type,'unknown') AS t, COUNT(*) AS c FROM remote_sessions GROUP BY transport_type")
            for trow in cur2.fetchall():
                set_gauge("zview_remote_sessions_transport", int(trow.get("c") or 0), {"type": trow.get("t") or "unknown"})
        except Exception:
            pass
        finally:
            conn2.close()

    from fastapi import Response
    return Response(content=render_prometheus(), media_type="text/plain; version=0.0.4; charset=utf-8")


@app.get("/api/health")
def platform_health():
    from zvplatform.db import create_connection as _create_connection

    db_ok = False
    db_error = None
    conn = _create_connection()
    if conn:
        try:
            cur = conn.cursor()
            cur.execute("SELECT 1")
            cur.fetchall()
            cur.close()
            db_ok = True
        except Exception as _db_exc:
            import traceback as _tb
            db_error = _tb.format_exc()
            safe_console_print("[Health] db check failed:\n" + _tb.format_exc())
        finally:
            conn.close()
    else:
        db_error = "create_connection returned None"

    health = worker_health.snapshot()
    return {
        "status": "ok" if (db_ok and health["status"] == "ok") else "degraded",
        "checks": {
            "database": "ok" if db_ok else "failed",
            "database_error": db_error,
            "workers": health,
        },
        "request_id": get_request_id(),
        "version": "3.1.0",
        "timestamp": format_datetime(datetime.now()),
    }


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
    safe_console_print(format_log_line("info", f"user {auth_user['username']} logged in"))
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


# ============================================================
# 用户管理（仅 admin：/api/v1/auth/users → auth:manage 权限）
# ============================================================

class CreateUserRequest(BaseModel):
    username: str
    password: str
    role: str = "viewer"


class UpdateUserRequest(BaseModel):
    role: Optional[str] = None
    enabled: Optional[bool] = None


class ResetUserPasswordRequest(BaseModel):
    new_password: str


def _log_user_management(action: str, result: str, level: str, message: str,
                          target_user: str, operator: str, request: Request, details=None):
    record_system_activity_log(SystemActivityLogCreate(
        source_type="platform",
        module="auth",
        category="user_management",
        action=action,
        level=level,
        result=result,
        ip_address=get_request_client_ip(request),
        operator_name=operator,
        title=f"用户管理-{action}",
        message=message,
        details={"target_user": target_user, "operator": operator, **(details or {})},
    ))


@app.get("/api/v1/auth/users")
def list_platform_users(request: Request):
    auth_user = getattr(request.state, "auth_user", None)
    if not auth_user:
        raise HTTPException(status_code=401, detail="Unauthorized")
    return list_users()


@app.post("/api/v1/auth/users")
def create_platform_user(payload: CreateUserRequest, request: Request):
    auth_user = getattr(request.state, "auth_user", None)
    if not auth_user:
        raise HTTPException(status_code=401, detail="Unauthorized")
    operator = get_request_username(request)

    try:
        profile = create_user(payload.username, payload.password, payload.role, operator)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    _log_user_management(
        "create_user", "success", "info",
        f"管理员 {operator} 创建用户 {profile['username']}（角色 {profile['role']}）",
        profile["username"], operator, request, {"role": profile["role"]},
    )
    return {"message": "用户创建成功", "user": profile}


@app.put("/api/v1/auth/users/{username}")
def update_platform_user(username: str, payload: UpdateUserRequest, request: Request):
    auth_user = getattr(request.state, "auth_user", None)
    if not auth_user:
        raise HTTPException(status_code=401, detail="Unauthorized")
    operator = get_request_username(request)

    try:
        if payload.role is not None:
            profile = update_user_role(username, payload.role, operator)
            _log_user_management(
                "update_role", "success", "info",
                f"管理员 {operator} 将用户 {username} 角色变更为 {profile['role']}",
                username, operator, request, {"role": profile["role"]},
            )
        if payload.enabled is not None:
            profile = set_user_enabled(username, payload.enabled, operator)
            action = "enable_user" if payload.enabled else "disable_user"
            _log_user_management(
                action, "success", "info",
                f"管理员 {operator} {'启用' if payload.enabled else '停用'}用户 {username}",
                username, operator, request, {"enabled": payload.enabled},
            )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    return {"message": "用户更新成功", "user": profile}


@app.put("/api/v1/auth/users/{username}/reset-password")
def reset_platform_user_password(username: str, payload: ResetUserPasswordRequest, request: Request):
    auth_user = getattr(request.state, "auth_user", None)
    if not auth_user:
        raise HTTPException(status_code=401, detail="Unauthorized")
    operator = get_request_username(request)

    try:
        profile = admin_reset_password(username, payload.new_password, operator)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    _log_user_management(
        "reset_password", "success", "warning",
        f"管理员 {operator} 重置了用户 {username} 的登录密码",
        username, operator, request,
    )
    return {"message": "密码已重置，该用户原登录令牌已全部失效", "user": profile}


@app.delete("/api/v1/auth/users/{username}")
def delete_platform_user(username: str, request: Request):
    auth_user = getattr(request.state, "auth_user", None)
    if not auth_user:
        raise HTTPException(status_code=401, detail="Unauthorized")
    operator = get_request_username(request)

    try:
        result = delete_user(username, operator)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    _log_user_management(
        "delete_user", "success", "warning",
        f"管理员 {operator} 删除了用户 {username}",
        username, operator, request,
    )
    return {"message": "用户已删除", **result}


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


@app.get("/api/v1/assets/options")
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


ASSET_TYPE_CHOICES = ("switch", "router", "server", "pc", "unknown")
ASSET_STATUS_CHOICES = ("online", "offline", "unknown")


@app.post("/api/v1/assets")
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

        # 删除关联的软件清单（与单个删除保持一致，避免孤儿记录）
        cursor.execute(
            f"DELETE FROM asset_software WHERE asset_id IN ({placeholders})",
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


@app.get("/api/v1/assets/{asset_id}/status")
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


@app.get("/api/v1/assets/{asset_id}/status/history")
def get_asset_status_history(asset_id: int, limit: int = 20):
    """Get recent heartbeat history for an asset."""
    limit = max(1, min(int(limit or 20), 200))
    conn = get_db_connection()
    if not conn:
        raise HTTPException(status_code=500, detail="Database connection failed")
    try:
        cursor = conn.cursor(dictionary=True)
        cursor.execute("""
            SELECT cpu_usage, memory_usage, disk_usage, process_count,
                   logged_users, heartbeat_time
            FROM agent_heartbeat
            WHERE asset_id = %s
            ORDER BY heartbeat_time DESC
            LIMIT %s
        """, (asset_id, limit))
        rows = cursor.fetchall()
        for h in rows:
            if h.get('heartbeat_time'):
                h['heartbeat_time'] = h['heartbeat_time'].strftime('%Y-%m-%d %H:%M:%S')
        return {"data": rows, "total": len(rows)}
    except Error as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        cursor.close()
        conn.close()


@app.get("/api/v1/assets/{asset_id}/changes")
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
            ORDER BY created_at DESC
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


@app.get("/api/v1/assets/{asset_id}/uptime")
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


@app.websocket("/api/v1/remote/sessions/{session_id}/ws")
async def proxy_remote_session_ws(session_id: int, websocket: WebSocket):
    """基于 session_token 的远程桌面 WS（二进制帧协议）。复用旧代理鉴权+桥接。"""
    from remote_desktop_api import ensure_remote_sessions_table
    token = str(websocket.query_params.get("token") or "").strip()
    if not token:
        await close_browser_websocket(websocket, code=4401, reason="Missing session token")
        return
    conn = get_db_connection()
    if not conn:
        await send_browser_session_error(websocket, "平台数据库连接失败", code=1011)
        return
    cursor = None
    try:
        cursor = conn.cursor(dictionary=True)
        ensure_remote_sessions_table(conn)
        import hashlib as _hashlib
        token_hash = _hashlib.sha256(token.encode("utf-8")).hexdigest()  # P0-4: hash 对比
        # P0-07: TTL 强制执行（created_at + max_duration_sec）
        cursor.execute("SELECT asset_id, status, fps_limit, created_at, max_duration_sec FROM remote_sessions WHERE id=%s AND session_token=%s", (session_id, token_hash))
        row = cursor.fetchone()
        if not row:
            await close_browser_websocket(websocket, code=4401, reason="Invalid session token")
            return
        if row.get("status") in ("disconnected", "failed"):
            await send_browser_session_error(websocket, "会话已结束，请重新发起", code=1008)
            return
        created_at = row.get("created_at")
        max_sec = int(row.get("max_duration_sec") or 7200)
        if created_at is not None:
            import datetime as _dt
            if _dt.datetime.now() > created_at + _dt.timedelta(seconds=max_sec):
                try:
                    cursor.execute("UPDATE remote_sessions SET status='disconnected', disconnected_at=NOW(), disconnect_reason='expired' WHERE id=%s", (session_id,))
                    conn.commit()
                except Exception:
                    pass
                await close_browser_websocket(websocket, code=4400, reason="Session expired (TTL)")
                return
        asset_id = row["asset_id"]
        fps_limit = row.get("fps_limit") or 20
    except Exception as exc:
        safe_console_print(f"[RemoteSessionWS] session={session_id} error: {exc}")
        await send_browser_session_error(websocket, "会话校验失败", code=1011)
        return
    finally:
        if cursor:
            cursor.close()
        conn.close()

    asset = None
    conn = get_db_connection()
    try:
        cursor = conn.cursor(dictionary=True)
        asset = get_asset_agent_target(cursor, asset_id)
    except HTTPException as exc:
        await send_browser_session_error(websocket, str(exc.detail), code=4400)
        return
    finally:
        if cursor:
            cursor.close()
        conn.close()

    if not asset or not asset.get("ip_address"):
        await send_browser_session_error(websocket, "终端信息缺失", code=4400)
        return

    ip_address = str(asset.get("ip_address") or "").strip()
    conn = get_db_connection()
    try:
        cur = conn.cursor(); cur.execute("UPDATE remote_sessions SET status='connecting' WHERE id=%s", (session_id,)); conn.commit()
    except Exception:
        pass
    finally:
        cur.close(); conn.close()

    requester = f"session-{session_id}"
    upstream_url = f"ws://{ip_address}:9000/remote-desktop?requester={requester}"
    safe_console_print(f"[RemoteSessionWS] session={session_id} asset={asset_id} ip={ip_address}")

    try:
        async with websockets.connect(
            upstream_url,
            additional_headers=build_agent_auth_headers({"X-Remote-Requester": requester}),
            open_timeout=10, close_timeout=5, ping_interval=None, max_size=None,
        ) as upstream_socket:
            await websocket.accept()
            await websocket.send_text(json.dumps({"type": "session_start", "fps": fps_limit}))
            conn = get_db_connection()
            try:
                c2 = conn.cursor(); c2.execute("UPDATE remote_sessions SET status='connected', connected_at=NOW(), transport_type='ws-tcp' WHERE id=%s", (session_id,)); conn.commit()
            except Exception:
                pass
            finally:
                c2.close(); conn.close()

            browser_to_agent_task = asyncio.create_task(relay_browser_to_agent(websocket, upstream_socket, asset_id=asset_id))
            agent_to_browser_task = asyncio.create_task(relay_agent_to_browser(websocket, upstream_socket, asset_id=asset_id))
            done, pending = await asyncio.wait({browser_to_agent_task, agent_to_browser_task}, return_when=asyncio.FIRST_COMPLETED)
            for task in pending:
                task.cancel()
                with contextlib.suppress(Exception):
                    await task
            await asyncio.gather(*done, return_exceptions=True)
    except Exception as exc:
        safe_console_print(f"[RemoteSessionWS] session={session_id} failed: {exc}")
        if websocket.application_state == WebSocketState.CONNECTING:
            await send_browser_session_error(websocket, "远程桌面服务不可用", code=1013)
    finally:
        conn = get_db_connection()
        try:
            cur = conn.cursor()
            cur.execute("UPDATE remote_sessions SET status='disconnected', disconnected_at=NOW(), disconnect_reason='relay_ended' WHERE id=%s AND status!='disconnected'", (session_id,))
            conn.commit()
        except Exception:
            pass
        finally:
            cur.close(); conn.close()


@app.post("/api/v1/assets/{asset_id}/command")
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
def get_all_software(asset_id: Optional[int] = Query(default=None)):
    """获取所有软件安装记录（详细清单）"""
    conn = get_db_connection()
    if not conn:
        raise HTTPException(status_code=500, detail="Database connection failed")

    try:
        cursor = conn.cursor(dictionary=True)

        base_sql = """
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
        """
        if asset_id is not None:
            cursor.execute(base_sql + " AND s.asset_id = %s ORDER BY s.software_name", (asset_id,))
        else:
            cursor.execute(base_sql + " ORDER BY s.software_name, a.hostname")

        software_list = cursor.fetchall()

        return {"data": software_list}

    except Error as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        cursor.close()
        conn.close()




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



# ============================================================
# Agent心跳接口
# ============================================================

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
# 后台守护线程启动入口（修复：历史版本丢失 def 行导致 worker 从未启动）
# ============================================================

@app.on_event("startup")
def start_background_workers():
    """启动后台守护线程。"""
    conn = get_db_connection()
    if conn:
        try:
            ensure_assets_agent_schema(conn)
            ensure_asset_changes_table(conn)
            try:
                from security_api import ensure_security_tables
                ensure_security_tables(conn)
            except Exception as exc:
                safe_console_print(f"[Startup] security tables ensure warn: {exc}")
        finally:
            conn.close()
    ensure_status_reconcile_worker_started()
    ensure_data_retention_worker_started()
    ensure_alert_sync_worker_started()
    ensure_disk_guard_worker_started()


# ============================================================
# Agent 设备凭据（一机一密，P0-01）
# 协议：Authorization: Bearer zv1:{asset_id}:{device_secret}
# 发放：Agent 用全局 token 心跳时，响应下发 agent_credential（仅一次）
# 存储：DB 只存 SHA256(secret + pepper)，不存明文
# ============================================================

def ensure_agent_credentials_table(conn) -> None:
    cursor = conn.cursor()
    try:
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS agent_credentials (
                asset_id BIGINT UNSIGNED PRIMARY KEY,
                secret_hash CHAR(64) NOT NULL,
                status ENUM('active','revoked') NOT NULL DEFAULT 'active',
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                last_used_at DATETIME NULL,
                CONSTRAINT fk_agent_credentials_asset
                    FOREIGN KEY (asset_id) REFERENCES assets(id) ON DELETE CASCADE
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """)
        conn.commit()
    finally:
        cursor.close()


def _hash_device_secret(asset_id: int, secret: str) -> str:
    return hashlib.sha256(f"zv1:{asset_id}:{secret}:{TOKEN_SECRET}".encode("utf-8")).hexdigest()


def verify_agent_device_credential(agent_id: int, secret: str) -> bool:
    """auth_utils 设备凭据校验回调（注册于模块加载完成处）"""
    if not isinstance(agent_id, int) or agent_id <= 0 or not secret:
        return False
    conn = get_db_connection()
    if not conn:
        return False
    cursor = conn.cursor(dictionary=True)
    try:
        cursor.execute(
            "SELECT secret_hash, status, last_used_at FROM agent_credentials WHERE asset_id=%s",
            (agent_id,),
        )
        row = cursor.fetchone()
        if not row or row.get("status") != "active":
            return False
        expected = str(row.get("secret_hash") or "")
        if not hmac.compare_digest(_hash_device_secret(agent_id, str(secret)), expected):
            return False
        cursor.execute(
            "UPDATE agent_credentials SET last_used_at=NOW() WHERE asset_id=%s",
            (agent_id,),
        )
        conn.commit()
        return True
    except Error:
        return False
    finally:
        cursor.close()
        conn.close()


def _agent_version_tuple(value: Any) -> tuple:
    try:
        parts = str(value or "").strip().split(".")
        return tuple(int(p) for p in parts[:3]) if parts and parts[0] else (0, 0, 0)
    except (TypeError, ValueError):
        return (0, 0, 0)


def _issue_agent_device_credential(asset_id: int, cursor, allow_rotate: bool = False) -> Optional[Dict[str, Any]]:
    """为资产签发设备凭据。
    - 无凭据行 / 已吊销 → 签发新凭据
    - 已有 active 凭据 → 默认不重发（明文不可恢复）；
      allow_rotate=True（Agent 已是 1.6.0+，具备保存能力）时轮换重发，
      覆盖"旧版本期间凭据已签发但被忽略"的灰度断点。"""
    cursor.execute(
        "SELECT status FROM agent_credentials WHERE asset_id=%s FOR UPDATE",
        (asset_id,),
    )
    row = cursor.fetchone()
    if row and row.get("status") == "active" and not allow_rotate:
        return None
    device_secret = secrets.token_urlsafe(32)
    cursor.execute("""
        INSERT INTO agent_credentials (asset_id, secret_hash, status)
        VALUES (%s, %s, 'active')
        ON DUPLICATE KEY UPDATE secret_hash=VALUES(secret_hash), status='active', last_used_at=NULL
    """, (asset_id, _hash_device_secret(asset_id, device_secret)))
    return {"agent_id": asset_id, "device_secret": device_secret}


# 注册设备凭据校验器（必须在函数定义之后）
set_agent_device_credential_verifier(verify_agent_device_credential)


def _enforce_device_asset_binding(request: Request, asset_id: int) -> None:
    """设备凭据只能访问自身资产的数据（全局 token 在迁移窗口内不受限）"""
    from auth_utils import get_request_agent_auth
    auth_info = get_request_agent_auth(request)
    if auth_info and auth_info.get("agent_auth_type") == "device":
        if auth_info.get("agent_id") != asset_id:
            raise HTTPException(status_code=403, detail="Agent credential does not match this asset")


# ============================================================
# 终端安全管理 - Agent 上报端点（agent_token 认证）
# ============================================================

from security_api import (
    mount_security_api,
)

# 挂载 security router（/api/v1/security/* 走用户认证中间件）
mount_security_api(app)

# P1-01：告警中心路由（platform.routers.alerts）
app.include_router(alerts_platform_router)
app.include_router(logs_platform_router)  # P1-01：统一日志路由
app.include_router(batch_platform_router)  # P1-01：批量操作路由
app.include_router(discovery_platform_router)  # P1-01：终端发现路由
app.include_router(groups_platform_router)  # P1-01：终端分组路由
app.include_router(agent_policy_router)  # P1-01：Agent 策略路由
app.include_router(agent_heartbeat_router)  # P1-01：心跳路由
app.include_router(agent_jobs_router)  # V1.8.3：通用任务通道
app.include_router(incidents_router)  # V1.9.0：事件列表/确认/关闭

# 网络监控路由（第一阶段：实时状态 + 历史趋势）
from zvplatform.routers.network import router as network_router
app.include_router(network_router)


# ============================================================
# 远程桌面会话 API（/api/v1/remote/*）
# ============================================================
from remote_desktop_api import mount_remote_desktop_api
mount_remote_desktop_api(app)

# ============================================================
# Agent 自动升级 API（/api/v1/agent/upgrade/*）
# ============================================================
from agent_upgrade_api import mount_agent_upgrade_api
mount_agent_upgrade_api(app)


# ============================================================
# 设备凭据管理台（admin/policies:write）
# ============================================================
# ============================================================
# 终端部署三件套（P1-04，参照火绒企业版）：
# 1) 网页自助下载安装包  2) 一键部署脚本（域开机脚本/三方桌管静默推送）
# Agent 侧配套：Z-View.exe --install --quiet --server-url <center>
# ============================================================


def _resolve_latest_agent_package() -> tuple[Optional[str], Optional[str]]:
    import os

    from agent_upgrade_api import UPGRADE_DIR, get_latest_upgrade

    latest = get_latest_upgrade()
    version = str(latest.get("version") or "")
    if not version:
        return None, None
    exe_path = os.path.join(UPGRADE_DIR, version, "Z-View.exe")
    if not os.path.exists(exe_path):
        return None, None
    return version, exe_path


@app.get("/api/v1/console/agent-deploy/package")
def download_agent_deploy_package(request: Request):
    """网页自助部署：下载最新版 Agent 安装包（admin）。

    终端用户拿到包后运行 `Z-View.exe --install --quiet --server-url <中心地址>`，
    或配合部署脚本自动完成。升级通道已有 SHA256 校验，无需重复签名。
    """
    require_request_permission(getattr(request.state, "auth_user", None), request.url.path, request.method)
    version, exe_path = _resolve_latest_agent_package()
    if not exe_path:
        raise HTTPException(
            status_code=404,
            detail="No agent package available; upload one via /api/v1/agent/upgrade/upload first",
        )
    return FileResponse(
        exe_path,
        media_type="application/octet-stream",
        filename=f"Z-View-Setup-{version}.exe",
    )


def _build_agent_deploy_ps_script(center_url: str, deploy_token: str) -> str:
    return f"""# Z-View Agent 一键部署脚本（需管理员权限运行）
# 生成时间: {datetime.now():%Y-%m-%d %H:%M:%S}   中心: {center_url}
# 用途: 网页自助部署 / 域开机脚本 / 三方桌管静默推送（火绒企业版同款三件套）
# 注意: 脚本内嵌 Agent Token，仅限内部分发，勿公开传播
$ErrorActionPreference = 'Stop'
$Center = '{center_url}'
$Token  = '{deploy_token}'
$WorkDir = Join-Path $env:TEMP 'zview-agent-deploy'
New-Item -ItemType Directory -Force -Path $WorkDir | Out-Null

Write-Host '[1/2] downloading agent package...'
Invoke-WebRequest -UseBasicParsing `
    -Uri "$Center/api/v1/agent/upgrade/download?agent_token=$Token" `
    -OutFile (Join-Path $WorkDir 'Z-View.exe')

Write-Host '[2/2] installing service...'
& (Join-Path $WorkDir 'Z-View.exe') --install --quiet --server-url $Center
if ($LASTEXITCODE -eq 0) {{
    Write-Host 'Z-View Agent deployed successfully.'
}} else {{
    Write-Host "deploy failed: exit=$LASTEXITCODE" -ForegroundColor Red
    exit 1
}}
"""


@app.get("/api/v1/console/agent-deploy/script")
def get_agent_deploy_script(
    request: Request,
    center: Optional[str] = Query(default=None, description="覆盖中心地址（终端访问用的 IP/域名），默认取当前访问地址"),
):
    """生成终端一键部署脚本（内嵌中心地址与下载 Token，admin 专用，勿外发）。"""
    require_request_permission(getattr(request.state, "auth_user", None), request.url.path, request.method)
    if center:
        base_url = center.rstrip("/")
    else:
        host = request.headers.get("host") or request.url.netloc
        scheme = request.headers.get("x-forwarded-proto") or request.url.scheme
        base_url = f"{scheme}://{host}"
    from auth_utils import get_expected_agent_token

    token = get_expected_agent_token()
    if not token:
        raise HTTPException(status_code=500, detail="agent token not configured on server")
    content = _build_agent_deploy_ps_script(base_url, token)
    return Response(
        content=content,
        media_type="text/plain; charset=utf-8",
        headers={"Content-Disposition": "attachment; filename=deploy-zview-agent.ps1"},
    )


# ============================================================
# 告警通知配置（P1 告警中心通知层，V1.7.1）
# ============================================================


@app.get("/api/v1/console/alert-notify-config")
def get_alert_notify_config_api(request: Request):
    """读取告警通知配置（密码脱敏）。"""
    require_request_permission(getattr(request.state, "auth_user", None), request.url.path, request.method)
    from zvplatform.services.alert_notify import get_notify_config
    conn = get_db_connection()
    if not conn:
        raise HTTPException(status_code=500, detail="Database connection failed")
    try:
        cfg = get_notify_config(conn)
        cfg["has_smtp_password"] = bool(cfg.get("smtp_password"))
        cfg.pop("smtp_password", None)
        return cfg
    finally:
        conn.close()


@app.put("/api/v1/console/alert-notify-config")
def update_alert_notify_config_api(request: Request, patch: dict):
    """更新告警通知配置（合并式；smtp_password 传空串表示保持不变）。"""
    require_request_permission(getattr(request.state, "auth_user", None), request.url.path, request.method)
    if not isinstance(patch, dict):
        raise HTTPException(status_code=422, detail="Body must be a JSON object")
    from zvplatform.services.alert_notify import update_notify_config
    conn = get_db_connection()
    if not conn:
        raise HTTPException(status_code=500, detail="Database connection failed")
    try:
        cfg = update_notify_config(conn, patch)
        cfg["has_smtp_password"] = bool(cfg.get("smtp_password"))
        cfg.pop("smtp_password", None)
        return cfg
    finally:
        conn.close()


@app.post("/api/v1/console/alert-notify-test")
def test_alert_notify_config(request: Request):
    """发送测试通知（admin）：按已保存配置逐通道试发，返回每通道结果。"""
    require_request_permission(getattr(request.state, "auth_user", None), request.url.path, request.method)
    from zvplatform.services.alert_notify import send_test_notification
    conn = get_db_connection()
    if not conn:
        raise HTTPException(status_code=500, detail="Database connection failed")
    try:
        return send_test_notification(conn)
    finally:
        conn.close()


@app.get("/api/v1/console/alert-thresholds")
def get_alert_thresholds_api(request: Request):
    """读取告警阈值配置（NULL = 使用默认）。"""
    require_request_permission(getattr(request.state, "auth_user", None), request.url.path, request.method)
    from zvplatform.services.alert_thresholds import get_threshold_config, get_effective_thresholds
    conn = get_db_connection()
    if not conn:
        raise HTTPException(status_code=500, detail="Database connection failed")
    try:
        return {
            "config": get_threshold_config(conn),
            "effective": get_effective_thresholds(conn),
        }
    finally:
        conn.close()


@app.put("/api/v1/console/alert-thresholds")
def update_alert_thresholds_api(request: Request, patch: dict):
    """更新告警阈值（合并式；NULL/空串 = 回落默认；校验范围与 warning<critical）。"""
    require_request_permission(getattr(request.state, "auth_user", None), request.url.path, request.method)
    if not isinstance(patch, dict):
        raise HTTPException(status_code=422, detail="Body must be a JSON object")
    from zvplatform.services.alert_thresholds import get_threshold_config, get_effective_thresholds, update_threshold_config
    conn = get_db_connection()
    if not conn:
        raise HTTPException(status_code=500, detail="Database connection failed")
    try:
        try:
            update_threshold_config(conn, patch)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc))
        return {
            "config": get_threshold_config(conn),
            "effective": get_effective_thresholds(conn),
        }
    finally:
        conn.close()


@app.get("/api/v1/console/patch-status")
def list_patch_status_api(request: Request, asset_id: Optional[int] = None):
    """补丁状态列表（admin）：各终端 WU 待安装补丁（Patch Management Phase 1）。"""
    require_request_permission(getattr(request.state, "auth_user", None), request.url.path, request.method)
    conn = get_db_connection()
    if not conn:
        raise HTTPException(status_code=500, detail="Database connection failed")
    cursor = conn.cursor(dictionary=True)
    try:
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
        conn.commit()
        if asset_id:
            cursor.execute(
                "SELECT p.*, a.hostname, a.ip_address FROM agent_patches p "
                "JOIN assets a ON a.id = p.asset_id WHERE p.asset_id = %s",
                (asset_id,),
            )
        else:
            cursor.execute(
                "SELECT p.*, a.hostname, a.ip_address FROM agent_patches p "
                "JOIN assets a ON a.id = p.asset_id ORDER BY p.pending_count DESC"
            )
        rows = cursor.fetchall() or []
        total_pending = sum(int(r.get("pending_count") or 0) for r in rows)
        reboot_count = sum(1 for r in rows if r.get("reboot_required"))
        return {"terminals": rows, "total": len(rows),
                "total_pending": total_pending, "reboot_required_count": reboot_count}
    finally:
        cursor.close()
        conn.close()


@app.get("/api/v1/console/agent-credentials")
def list_agent_credentials(request: Request):
    """设备凭据注册状态（含未注册资产）"""
    require_request_permission(getattr(request.state, "auth_user", None), request.url.path, request.method)
    conn = get_db_connection()
    if not conn:
        raise HTTPException(status_code=500, detail="Database connection failed")
    cursor = conn.cursor(dictionary=True)
    try:
        ensure_agent_credentials_table(conn)
        cursor.execute("""
            SELECT a.id AS asset_id, a.hostname, a.ip_address, a.status, a.last_seen,
                   ac.status AS credential_status, ac.created_at AS enrolled_at, ac.last_used_at
            FROM assets a
            LEFT JOIN agent_credentials ac ON ac.asset_id = a.id
            WHERE a.deleted_at IS NULL
            ORDER BY (ac.asset_id IS NULL) ASC, a.id ASC
        """)
        rows = cursor.fetchall()
        for r in rows:
            r["last_seen"] = fmt_dt(r.get("last_seen"))
            r["enrolled_at"] = fmt_dt(r.get("enrolled_at"))
            r["last_used_at"] = fmt_dt(r.get("last_used_at"))
            r["enrolled"] = r.get("credential_status") == "active"
        enrolled = sum(1 for r in rows if r["enrolled"])
        return {"data": rows, "total": len(rows), "enrolled": enrolled, "pending": len(rows) - enrolled}
    except Error as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        cursor.close()
        conn.close()


@app.delete("/api/v1/console/agent-credentials/{asset_id}")
def revoke_agent_credential(asset_id: int, request: Request):
    """吊销设备凭据：Agent 下次全局 token 心跳时自动重新签发"""
    require_request_permission(getattr(request.state, "auth_user", None), request.url.path, request.method)
    conn = get_db_connection()
    if not conn:
        raise HTTPException(status_code=500, detail="Database connection failed")
    cursor = conn.cursor()
    try:
        ensure_agent_credentials_table(conn)
        cursor.execute(
            "UPDATE agent_credentials SET status='revoked' WHERE asset_id=%s AND status='active'",
            (asset_id,),
        )
        revoked = cursor.rowcount
        conn.commit()
        if not revoked:
            raise HTTPException(status_code=404, detail="No active credential for this asset")
        conn.commit()
        # 写审计
        try:
            insert_system_activity_log(cursor, SystemActivityLogCreate(
                source_type="platform",
                module="agent_credentials",
                category="security",
                action="agent_credential_revoke",
                level="warning",
                result="success",
                asset_id=asset_id,
                operator_name=get_request_username(request, fallback="console"),
                title="吊销 Agent 设备凭据",
                message=f"Agent 设备凭据已吊销，等待全局 token 心跳重新签发 (asset_id={asset_id})",
                details={"asset_id": asset_id},
            ))
            conn.commit()
        except Error:
            conn.rollback()
        return {"status": "success", "asset_id": asset_id, "revoked": True}
    except HTTPException:
        raise
    except Error as e:
        conn.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        cursor.close()
        conn.close()


@app.get("/api/v1/agent/security-policies")
def agent_get_security_policies(
    request: Request,
    asset_id: int = Query(...),
):
    """Agent 拉取安全策略（按三级优先级解析：global > group > asset）"""
    require_agent_request(request)
    _enforce_device_asset_binding(request, asset_id)
    conn = get_db_connection()
    if not conn:
        raise HTTPException(status_code=500, detail="Database connection failed")
    cursor = conn.cursor(dictionary=True)
    try:
        from security_api import ensure_security_tables
        ensure_security_tables(conn)
        # 查询 asset 的 group_id
        cursor.execute("SELECT group_id FROM assets WHERE id=%s", (asset_id,))
        row = cursor.fetchone()
        group_id = row.get("group_id") if row else None

        # 优先级：asset > group > global（数值越大优先级越高，取最高优先级的生效配置合并）
        cursor.execute("""
            SELECT sp.id, sp.policy_name, sp.policy_type, sp.priority, sp.version,
                   sp.config_json, spb.scope_type, spb.scope_id
            FROM security_policy_bindings spb
            JOIN security_policies sp ON sp.id=spb.policy_id
            WHERE spb.enabled=TRUE AND sp.enabled=TRUE AND (
                spb.scope_type='global'
                OR (spb.scope_type='asset' AND spb.scope_id=%s)
                OR (spb.scope_type='group' AND spb.scope_id=%s)
            )
        """, (asset_id, group_id))  # P1-05：排序与去重收敛到 policy_engine
        policies = []
        for r in cursor.fetchall():
            try:
                config = json.loads(r.get("config_json") or "{}")
            except Exception:
                config = {}
            policies.append({
                "id": r["id"],
                "policy_name": r["policy_name"],
                "policy_type": r["policy_type"],
                "priority": int(r["priority"] or 0),
                "version": int(r["version"] or 1),
                "scope_type": r["scope_type"],
                "config": config,
            })
        # P1-05：统一策略引擎解析生效策略
        from zvplatform.policy_engine import resolve_effective_policies
        return {"status": "success", "asset_id": asset_id,
                "policies": resolve_effective_policies(policies)}
    except Error as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        cursor.close()
        conn.close()


@app.post("/api/v1/agent/security-policy-result")
def agent_security_policy_result(data: dict, request: Request):
    """Agent 回传策略执行结果"""
    require_agent_request(request)
    _enforce_device_asset_binding(request, int(data.get("asset_id") or 0))
    conn = get_db_connection()
    if not conn:
        raise HTTPException(status_code=500, detail="Database connection failed")
    cursor = conn.cursor()
    try:
        from security_api import ensure_security_tables
        ensure_security_tables(conn)
        policy_id = data.get("policy_id")
        asset_id = data.get("asset_id")
        status = data.get("status", "success")
        if not policy_id or not asset_id:
            raise HTTPException(status_code=422, detail="policy_id and asset_id required")
        cursor.execute("SELECT id FROM security_policies WHERE id=%s", (policy_id,))
        if not cursor.fetchone():
            raise HTTPException(status_code=404, detail="Policy not found")
        cursor.execute("""
            INSERT INTO security_policy_exec_results
                (policy_id, asset_id, scope_type, status, applied_rules, failed_rules, error_detail, executed_at, reported_at)
            VALUES (%s,%s,%s,%s,%s,%s,%s,NOW(),NOW())
        """, (
            policy_id, asset_id, data.get("scope_type", "asset"),
            status, int(data.get("applied_rules") or 0), int(data.get("failed_rules") or 0),
            data.get("error_detail"),
        ))
        conn.commit()
        return {"status": "success"}
    except HTTPException:
        raise
    except Error as e:
        conn.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        cursor.close()
        conn.close()


if __name__ == "__main__":
    import uvicorn

    # P0-02：Agent 控制面 TLS 监听（8443），与 8080 同一应用双监听。
    # 证书复用 frontend/certs 自签根（SAN 含 172.16.250.120）；Agent 用同一证书作 CA bundle 校验。
    def _start_tls_listener():
        import os as _os
        if str(get_env("ZVIEW_AGENT_TLS_ENABLED", "1") or "1").strip().lower() in ("0", "false", "no", "off"):
            safe_console_print("[TLS] 8443 listener disabled by ZVIEW_AGENT_TLS_ENABLED")
            return
        from zvplatform.settings import get_settings as _get_settings
        _app_settings = _get_settings()
        cert_file = _app_settings.platform.tls_certfile or _os.path.join(
            _os.path.dirname(_os.path.abspath(__file__)), "frontend", "certs", "zview-cert.pem")
        key_file = _app_settings.platform.tls_keyfile or _os.path.join(
            _os.path.dirname(_os.path.abspath(__file__)), "frontend", "certs", "zview-key.pem")
        if not (_os.path.exists(cert_file) and _os.path.exists(key_file)):
            safe_console_print(f"[TLS] cert/key not found ({cert_file}); 8443 listener skipped")
            return
        try:
            uvicorn.run(app, host="0.0.0.0", port=8443,
                        ssl_certfile=cert_file, ssl_keyfile=key_file, log_level="warning")
        except Exception as exc:
            safe_console_print(f"[TLS] 8443 listener failed: {exc}")

    threading.Thread(target=_start_tls_listener, daemon=True, name="assets-api-tls-8443").start()

    safe_console_print("=" * 60)
    safe_console_print("Z-View Assets API Starting...")
    safe_console_print("=" * 60)
    safe_console_print("Service: http://localhost:8080")
    safe_console_print("Agent TLS: https://localhost:8443")
    safe_console_print("API Docs: http://localhost:8080/docs")
    safe_console_print("Health Check: http://localhost:8080/")
    safe_console_print("=" * 60)

    uvicorn.run(
        app,
        host="0.0.0.0",
        port=8080,
        log_level="info"
    )
