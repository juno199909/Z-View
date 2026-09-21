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
import re
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
from fastapi import APIRouter, FastAPI, HTTPException, Query, Request, Response, WebSocket, WebSocketDisconnect
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
    get_user_scoped_group_ids,
    set_user_scoped_groups,
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
    {"path": "/api/v1/agent/exit/verify", "methods": ["POST"]},
    {"path": "/api/v1/agent/security-policies", "methods": ["GET"]},
    {"path": "/api/v1/agent/security-policy-result", "methods": ["POST"]},
    {"path": "/api/v1/agent/upgrade/download", "methods": ["GET"]},
    {"path": "/api/v1/logs", "methods": ["POST"]},
    # Agent 软件通道（反向代理到 8081；8081 侧同为 agent 专属豁免路径）
    {"prefix": "/api/v1/software/agent/"},
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
from zvplatform.routers.agent_exit_policy import router as agent_exit_policy_router  # 1.9.55：退出密码策略
from zvplatform.routers.agent_deploy import router as agent_deploy_router  # 1.9.55：终端部署三件套（#16 迁入）
from zvplatform.routers.alert_settings import router as alert_settings_router  # 1.9.55：告警通知/阈值配置（#16 迁入）
from zvplatform.routers.agent_credentials import router as agent_credentials_router  # 1.9.55：设备凭据管理（#16 迁入）
from zvplatform.routers.patch_management import router as patch_management_router  # 1.9.55：补丁管理（#16 迁入）
from zvplatform.routers.agent_security_policies import router as agent_security_policies_router  # 1.9.55：终端安全策略 Agent 上报（#16 迁入）
from zvplatform.routers.assets import router as assets_core_router, bind_helpers as bind_assets_helpers  # 1.9.55：资产 CRUD（#16 迁入）
from zvplatform.routers.auth import router as auth_router, bind_helpers as bind_auth_helpers  # 1.9.55：认证与用户管理（#16 迁入）
from zvplatform.routers.agent_jobs import router as agent_jobs_router  # V1.8.3：通用任务通道
from zvplatform.routers.log_retention import router as log_retention_router  # V1.9.23：监控中心·日志配置
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






ASSET_SCOPE_PATH_RE = re.compile(r"^/api/v1/assets/(\d+)(?:/|$)")


def _asset_in_user_scope(auth_user: Optional[Dict[str, Any]], asset_id: int) -> bool:
    """Scoped RBAC：校验资产是否在用户可见分组范围内（admin/未配置范围不限制）。"""
    from auth_utils import get_user_scoped_group_ids

    scoped = get_user_scoped_group_ids(auth_user)
    if scoped is None:
        return True
    conn = get_db_connection()
    if not conn:
        # 数据库不可用时按无权限处理，避免范围校验被绕过
        return False
    cursor = None
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT group_id FROM assets WHERE id=%s AND deleted_at IS NULL", (asset_id,))
        row = cursor.fetchone()
        group_id = row[0] if row else None
        return group_id is not None and group_id in scoped
    except Exception:
        return False
    finally:
        if cursor:
            cursor.close()
        conn.close()


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

    # Scoped RBAC（P1）：路径携带资产 id 的请求统一做分组范围校验
    scope_match = ASSET_SCOPE_PATH_RE.match(request.url.path)
    if scope_match and not _asset_in_user_scope(auth_user, int(scope_match.group(1))):
        return JSONResponse(
            status_code=403,
            content={"detail": "无权访问该终端（资产分组范围限制）"},
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


def _insert_remote_shell_audit(asset_id: int, action: str, message: str) -> None:
    """远程 Shell 审计落库（best-effort：审计失败不阻断远控链路）。

    system_activity_logs.event_time 为 NOT NULL 无默认值，必须显式 NOW()。
    """
    conn = get_db_connection()
    if not conn:
        return
    cursor = None
    try:
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO system_activity_logs (source_type, module, action, level, result, asset_id, message, event_time, created_at) "
            "VALUES ('platform','remote-shell',%s,'info','success',%s,%s,NOW(),NOW())",
            (action, asset_id, message[:900]),
        )
        conn.commit()
    except Exception:
        pass
    finally:
        if cursor:
            try:
                cursor.close()
            except Exception:
                pass
        try:
            conn.close()
        except Exception:
            pass


def build_remote_shell_audit_hooks(
    asset_id: int,
    operator: str,
    session_id: int | None = None,
):
    """生成中继双向审计钩子：浏览器→agent 记命令提交，agent→浏览器记执行结果。"""
    prefix = f"Session {session_id}: " if session_id else ""

    def audit_hook(action: str, **fields) -> None:
        try:
            if action == "shell_exec":
                command = str(fields.get("command") or "").strip()
                message = f"{prefix}{operator} 执行远程命令: {command}"
            elif action == "shell_exit":
                message = (
                    f"{prefix}{operator} 远程命令[id={fields.get('id')}]执行完成: "
                    f"exit_code={fields.get('exit_code')} duration_ms={fields.get('duration_ms')} "
                    f"timed_out={fields.get('timed_out')} truncated={fields.get('truncated')}"
                )
            elif action == "shell_stop":
                message = f"{prefix}{operator} 终止远程命令[id={fields.get('id')}]"
            else:
                return
            asyncio.create_task(asyncio.to_thread(_insert_remote_shell_audit, asset_id, action, message))
        except Exception:
            pass

    return audit_hook


def _sniff_shell_message(text_data: str) -> Optional[Dict[str, Any]]:
    """轻量嗅探 shell 控制消息（避免对普通 JSON 控制消息做完整解析）。"""
    try:
        if "shell_" not in text_data or '"type"' not in text_data:
            return None
        parsed = json.loads(text_data)
    except Exception:
        return None
    if isinstance(parsed, dict) and str(parsed.get("type") or "").startswith("shell_"):
        return parsed
    return None


async def relay_browser_to_agent(
    websocket: WebSocket,
    upstream_socket,
    *,
    asset_id: int,
    shell_audit_hook=None,
) -> None:
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
                if shell_audit_hook is not None:
                    shell_msg = _sniff_shell_message(text_data)
                    if shell_msg:
                        shell_audit_hook(
                            str(shell_msg.get("type")),
                            command=shell_msg.get("command"),
                            id=shell_msg.get("id"),
                        )
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


async def relay_agent_to_browser(
    websocket: WebSocket,
    upstream_socket,
    *,
    asset_id: int,
    shell_audit_hook=None,
) -> None:
    close_code = 1000
    close_reason = ""
    outcome = "upstream_stream_ended"
    try:
        async for payload in upstream_socket:
            if isinstance(payload, bytes):
                await websocket.send_bytes(payload)
            else:
                if shell_audit_hook is not None:
                    shell_msg = _sniff_shell_message(payload)
                    if shell_msg and shell_msg.get("type") in ("shell_exit", "shell_error"):
                        shell_audit_hook(
                            str(shell_msg.get("type")),
                            id=shell_msg.get("id"),
                            exit_code=shell_msg.get("exit_code"),
                            duration_ms=shell_msg.get("duration_ms"),
                            timed_out=shell_msg.get("timed_out"),
                            truncated=shell_msg.get("truncated"),
                        )
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

# V1.9.23：日志留存策略迁移至 zvplatform/routers/log_retention.py（监控中心·日志配置）——
# 受管表注册表 / 默认天数 / system_config 持久化 / 立即清理均由该模块提供；
# 本文件的 worker 与清理入口仅做委托，天数以 system_config 配置为准。
RETENTION_CHECK_INTERVAL_SECONDS = 6 * 3600  # 每 6 小时检查一次
DATA_RETENTION_THREAD = None
DATA_RETENTION_STARTED = False
DATA_RETENTION_LOCK = threading.Lock()


def run_data_retention_cleanup() -> dict:
    """按留存配置清理过期数据（委托 log_retention 模块，返回各表删除行数）。"""
    try:
        from zvplatform.routers.log_retention import run_retention_cleanup
    except Exception as exc:
        safe_console_print(f"[DataRetention] log_retention module unavailable: {exc}")
        return {"error": str(exc)}
    try:
        deleted = run_retention_cleanup()
        if any(isinstance(v, int) and v > 0 for v in deleted.values()):
            safe_console_print(f"[DataRetention] cleaned: {deleted}")
        return deleted
    except Exception as exc:
        safe_console_print(f"[DataRetention] error: {exc}")
        return {"error": str(exc)}


def data_retention_loop():
    """数据保留清理循环（启动先执行一次，之后每 6 小时）+ 磁盘缓存清理。"""
    worker_health.register("data-retention", "数据保留清理 + 磁盘缓存清理（6h）")
    safe_console_print(
        f"[DataRetention] Worker started; interval={RETENTION_CHECK_INTERVAL_SECONDS}s; "
        f"policy=system_config:{'monitoring.log_retention_days'}"
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

            shell_audit_hook = build_remote_shell_audit_hooks(
                asset_id,
                requester,
            )
            browser_to_agent_task = asyncio.create_task(
                relay_browser_to_agent(websocket, upstream_socket, asset_id=asset_id, shell_audit_hook=shell_audit_hook)
            )
            agent_to_browser_task = asyncio.create_task(
                relay_agent_to_browser(websocket, upstream_socket, asset_id=asset_id, shell_audit_hook=shell_audit_hook)
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
        cursor.execute("SELECT asset_id, admin_user, status, fps_limit, created_at, max_duration_sec FROM remote_sessions WHERE id=%s AND session_token=%s", (session_id, token_hash))
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

            browser_to_agent_task = asyncio.create_task(relay_browser_to_agent(
                websocket, upstream_socket, asset_id=asset_id,
                shell_audit_hook=build_remote_shell_audit_hooks(
                    asset_id,
                    str(row.get("admin_user") or "console"),
                    session_id=session_id,
                ),
            ))
            agent_to_browser_task = asyncio.create_task(relay_agent_to_browser(
                websocket, upstream_socket, asset_id=asset_id,
                shell_audit_hook=build_remote_shell_audit_hooks(
                    asset_id,
                    str(row.get("admin_user") or "console"),
                    session_id=session_id,
                ),
            ))
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
                s.size_mb,
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
                from zvplatform.routers.security import ensure_security_tables
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


# ============================================================
# 终端安全管理 - Agent 上报端点（agent_token 认证）
# security-policies/security-policy-result 已迁至
# zvplatform/routers/agent_security_policies.py（#16 模块化）
# ============================================================

from zvplatform.routers.security import router as security_router  # 1.9.55：终端安全管理（#16 迁入）

# 挂载 security router（/api/v1/security/* 走用户认证中间件）
app.include_router(security_router)

# P1-01：告警中心路由（platform.routers.alerts）
app.include_router(alerts_platform_router)
app.include_router(logs_platform_router)  # P1-01：统一日志路由
app.include_router(batch_platform_router)  # P1-01：批量操作路由
app.include_router(discovery_platform_router)  # P1-01：终端发现路由
app.include_router(groups_platform_router)  # P1-01：终端分组路由
app.include_router(agent_policy_router)  # P1-01：Agent 策略路由
app.include_router(agent_heartbeat_router)  # P1-01：心跳路由
app.include_router(agent_exit_policy_router)  # 1.9.55：退出密码策略
app.include_router(agent_deploy_router)  # 1.9.55：终端部署三件套（#16 迁入）
app.include_router(alert_settings_router)  # 1.9.55：告警通知/阈值配置（#16 迁入）
app.include_router(agent_credentials_router)  # 1.9.55：设备凭据管理（#16 迁入）
app.include_router(patch_management_router)  # 1.9.55：补丁管理（#16 迁入）
app.include_router(agent_security_policies_router)  # 1.9.55：终端安全策略 Agent 上报（#16 迁入）
app.include_router(assets_core_router)  # 1.9.55：资产 CRUD（#16 迁入）
app.include_router(auth_router)  # 1.9.55：认证与用户管理（#16 迁入）
app.include_router(agent_jobs_router)  # V1.8.3：通用任务通道
app.include_router(log_retention_router)  # V1.9.23：监控中心·日志配置
app.include_router(incidents_router)  # V1.9.0：事件列表/确认/关闭


# ============================================================
# Agent 软件通道反向代理（P1-软件仓库）：Agent 统一指向平台 8443/8080，
# 软件管理的 Agent 端点实现在 8081 软件服务，这里做透明转发。
# 覆盖：tasks/poll、policies、packages/{id}/download、task-results/{id} 等。
# ============================================================

SOFTWARE_SERVICE_BASE = get_env("ZVIEW_SOFTWARE_SERVICE_URL", "http://127.0.0.1:8081")
_agent_software_proxy_router = APIRouter(prefix="/api/v1/software/agent")


@_agent_software_proxy_router.api_route("/{path:path}", methods=["GET", "POST", "PUT"])
async def proxy_agent_software_endpoints(path: str, request: Request):
    target_url = f"{SOFTWARE_SERVICE_BASE}/api/v1/software/agent/{path}"
    try:
        import httpx as _httpx

        body = await request.body()
        # 透传 Agent 鉴权头（8081 端点级 require_agent_request 依赖 Bearer 令牌）
        forward_headers = {
            "Content-Type": request.headers.get("content-type", "application/json"),
            "Authorization": request.headers.get("authorization", ""),
        }
        async with _httpx.AsyncClient(timeout=60) as client:
            resp = await client.request(
                request.method,
                target_url,
                params=request.query_params,
                content=body,
                headers=forward_headers,
            )
        return Response(
            content=resp.content,
            status_code=resp.status_code,
            media_type=resp.headers.get("content-type", "application/json"),
        )
    except Exception as proxy_exc:
        safe_console_print(f"[AgentSoftwareProxy] forward failed: {proxy_exc}")
        raise HTTPException(status_code=502, detail="软件服务不可达")


app.include_router(_agent_software_proxy_router)

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
# 终端部署三件套（P1-04）：已迁至 zvplatform/routers/agent_deploy.py（#16 模块化）
# ============================================================


# ============================================================
# 告警通知/阈值配置：已迁至 zvplatform/routers/alert_settings.py（#16 模块化）
# ============================================================


# 补丁管理端点：已迁至 zvplatform/routers/patch_management.py（#16 模块化）


# 设备凭据管理端点：已迁至 zvplatform/routers/agent_credentials.py（#16 模块化）


# #16：向资产路由注入 assets_api 共享助手（调用期解析，避免循环导入）
bind_assets_helpers(
    get_asset_agent_target=get_asset_agent_target,
    proxy_agent_json_request=proxy_agent_json_request,
    record_system_activity_log=record_system_activity_log,
    ensure_asset_changes_table=ensure_asset_changes_table,
    AGENT_CONTROL_PORT=AGENT_CONTROL_PORT,
)
bind_auth_helpers(record_system_activity_log=record_system_activity_log)


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
