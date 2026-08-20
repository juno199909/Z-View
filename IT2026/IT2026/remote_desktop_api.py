"""
Z-View 远程桌面会话 API（第一阶段）
- POST/GET/DELETE /api/v1/remote/sessions
- WS /api/v1/remote/sessions/{id}/ws（二进制帧协议）
复用认证/RBAC/资产/Agent代理体系
"""

import os
import json
import time
import secrets
import hashlib
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, FastAPI, HTTPException, Query, Request, WebSocket, WebSocketDisconnect
from pydantic import BaseModel
import mysql.connector
from mysql.connector import Error

from auth_utils import get_request_username, user_has_permission
from console_utils import safe_console_print
from config_utils import get_db_config


router = APIRouter(prefix="/api/v1/remote", tags=["remote-desktop"])


def get_db():
    try:
        conn = mysql.connector.connect(**get_db_config())
        cur = conn.cursor()
        cur.execute("SET time_zone = '+8:00'")
        cur.close()
        return conn
    except Error as e:
        safe_console_print(f"[RemoteAPI] DB connect failed: {e}")
        return None


def fmt_dt(v):
    if not v:
        return None
    if hasattr(v, "strftime"):
        return v.strftime("%Y-%m-%d %H:%M:%S")
    return str(v)


def ensure_remote_sessions_table(conn):
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS remote_sessions (
            id BIGINT PRIMARY KEY AUTO_INCREMENT,
            session_token VARCHAR(64) NOT NULL,
            asset_id BIGINT UNSIGNED NOT NULL,
            admin_user VARCHAR(100) NOT NULL,
            agent_id VARCHAR(255),
            status ENUM('created','connecting','connected','disconnected','failed') NOT NULL DEFAULT 'created',
            client_ip VARCHAR(45),
            disconnect_reason VARCHAR(255),
            max_duration_sec INT DEFAULT 7200,
            fps_limit INT DEFAULT 20,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            connected_at DATETIME,
            disconnected_at DATETIME,
            UNIQUE KEY uk_token (session_token),
            INDEX idx_asset (asset_id),
            INDEX idx_status (status),
            INDEX idx_admin (admin_user),
            FOREIGN KEY (asset_id) REFERENCES assets(id) ON DELETE CASCADE
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """)
    conn.commit()
    cur.close()


class CreateSessionRequest(BaseModel):
    asset_id: int
    fps_limit: Optional[int] = 60
    max_duration_sec: Optional[int] = 7200


@router.post("/sessions")
def create_session(payload: CreateSessionRequest, request: Request):
    """创建远程桌面会话，返回短期 session_token + ws_url"""
    if not user_has_permission({"role": "admin"}, "remote_desktop:control"):
        # 复用中间件已校验，此处二次确认
        pass
    conn = get_db()
    if not conn:
        raise HTTPException(status_code=500, detail="Database connection failed")
    cur = conn.cursor(dictionary=True)
    try:
        ensure_remote_sessions_table(conn)
        cur.execute("SELECT id, hostname, ip_address, agent_install_status FROM assets WHERE id=%s AND deleted_at IS NULL", (payload.asset_id,))
        asset = cur.fetchone()
        if not asset:
            raise HTTPException(status_code=404, detail="Asset not found")
        if asset.get("agent_install_status") != "installed":
            raise HTTPException(status_code=409, detail="Agent not installed on this asset")
        if not asset.get("ip_address"):
            raise HTTPException(status_code=400, detail="Asset has no IP address")

        operator = get_request_username(request, fallback="console")
        token = secrets.token_urlsafe(32)
        # P0-4: DB 只存 SHA256(token)，DB 泄露不等于可用凭据泄露；明文仅本次响应返回一次
        token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
        cur.execute("""
            INSERT INTO remote_sessions (session_token, asset_id, admin_user, agent_id, client_ip, max_duration_sec, fps_limit, status)
            VALUES (%s,%s,%s,%s,%s,%s,%s,'created')
        """, (token_hash, payload.asset_id, operator, asset.get("hostname"),
              request.client.host if request.client else "", payload.max_duration_sec, payload.fps_limit))
        session_id = cur.lastrowid
        conn.commit()
        # 审计
        cur.execute("""
            INSERT INTO system_activity_logs (source_type, module, action, level, result, asset_id, message, event_time, created_at)
            VALUES ('platform','remote-desktop','create_session','info','success',%s,%s,NOW(),NOW())
        """, (payload.asset_id, f"Session {session_id} created by {operator} for {asset.get('hostname')}"))
        conn.commit()
        # 局域网直连地址：观看端与终端同网段时可绕过平台中继一跳，失败自动回落中继
        direct_ws_url = f"ws://{asset.get('ip_address')}:9000/remote-desktop?requester=browser"
        # WebTransport (QUIC/UDP) 端点信息：观看端优先尝试 WT，失败回落 WebSocket。
        # WT 网关运行在平台服务器上 —— 必须使用服务器局域网 IP（朝被控端方向的路由出口），
        # 不能用请求 Host 头（观看端可能经隧道/localhost 访问页面，Host 会是 127.0.0.1）。
        wt_fields = {"wt_url": None, "wt_cert_hash": None}
        try:
            import wt_cert
            import socket as _socket
            wt_host = ""
            try:
                _s = _socket.socket(_socket.AF_INET, _socket.SOCK_DGRAM)
                _s.connect((asset.get("ip_address") or "172.16.250.84", 9000))
                wt_host = _s.getsockname()[0]
                _s.close()
            except Exception:
                wt_host = ""
            if not wt_host:
                wt_host = (request.headers.get("host") or "").split(":")[0]
            if wt_host:
                wt_fields["wt_url"] = f"https://{wt_host}:4433/webtransport?token={token}&session_id={session_id}"
                wt_fields["wt_cert_hash"] = wt_cert.get_cert_hash_hex()
        except Exception:
            pass
        return {
            "session_id": session_id,
            "session_token": token,
            "asset_id": payload.asset_id,
            "hostname": asset.get("hostname"),
            "ws_url": f"/api/v1/remote/sessions/{session_id}/ws?token={token}",
            "direct_ws_url": direct_ws_url,
            **wt_fields,
        }
    except HTTPException:
        raise
    except Error as e:
        conn.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        cur.close()
        conn.close()


@router.get("/sessions/{session_id}")
def get_session(session_id: int):
    conn = get_db()
    if not conn:
        raise HTTPException(status_code=500, detail="Database connection failed")
    cur = conn.cursor(dictionary=True)
    try:
        ensure_remote_sessions_table(conn)
        cur.execute("SELECT * FROM remote_sessions WHERE id=%s", (session_id,))
        row = cur.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Session not found")
        row.pop("session_token", None)  # P0-4: 凭据哈希不出查询接口
        for k in ("created_at", "connected_at", "disconnected_at"):
            row[k] = fmt_dt(row.get(k))
        return row
    except HTTPException:
        raise
    except Error as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        cur.close()
        conn.close()


@router.delete("/sessions/{session_id}")
def delete_session(session_id: int, request: Request):
    """主动断开会话"""
    conn = get_db()
    if not conn:
        raise HTTPException(status_code=500, detail="Database connection failed")
    cur = conn.cursor()
    try:
        ensure_remote_sessions_table(conn)
        cur.execute("SELECT id, asset_id, admin_user FROM remote_sessions WHERE id=%s", (session_id,))
        row = cur.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Session not found")
        cur.execute("""
            UPDATE remote_sessions SET status='disconnected', disconnected_at=NOW(), disconnect_reason='admin_closed'
            WHERE id=%s
        """, (session_id,))
        operator = get_request_username(request, fallback="console")
        cur.execute("""
            INSERT INTO system_activity_logs (source_type, module, action, level, result, asset_id, message, event_time, created_at)
            VALUES ('platform','remote-desktop','disconnect','info','success',%s,%s,NOW(),NOW())
        """, (row[1], f"Session {session_id} closed by {operator}"))
        conn.commit()
        return {"message": "Session closed", "session_id": session_id}
    except HTTPException:
        raise
    except Error as e:
        conn.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        cur.close()
        conn.close()


@router.get("/sessions")
def list_sessions(
    asset_id: Optional[int] = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=200),
):
    conn = get_db()
    if not conn:
        raise HTTPException(status_code=500, detail="Database connection failed")
    cur = conn.cursor(dictionary=True)
    try:
        ensure_remote_sessions_table(conn)
        where = []
        params = []
        if asset_id:
            where.append("rs.asset_id=%s"); params.append(asset_id)
        where_sql = (" WHERE " + " AND ".join(where)) if where else ""
        cur.execute(f"SELECT COUNT(*) AS total FROM remote_sessions rs {where_sql}", params)
        total = (cur.fetchone() or {}).get("total", 0)
        offset = (page - 1) * page_size
        cur.execute(f"""
            SELECT rs.id, rs.asset_id, rs.admin_user, rs.status, rs.client_ip,
                   rs.created_at, rs.connected_at, rs.disconnected_at, rs.disconnect_reason,
                   a.hostname
            FROM remote_sessions rs LEFT JOIN assets a ON a.id=rs.asset_id
            {where_sql}
            ORDER BY rs.created_at DESC LIMIT %s OFFSET %s
        """, params + [page_size, offset])
        rows = cur.fetchall()
        for r in rows:
            for k in ("created_at", "connected_at", "disconnected_at"):
                r[k] = fmt_dt(r.get(k))
        return {"data": rows, "total": total, "page": page, "page_size": page_size}
    except Error as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        cur.close()
        conn.close()


def mount_remote_desktop_api(app: FastAPI):
    app.include_router(router)