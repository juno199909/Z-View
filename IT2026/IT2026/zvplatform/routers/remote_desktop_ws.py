# -*- coding: utf-8 -*-
"""远程桌面 / 远程会话 WebSocket 路由（1.9.55 自 assets_api 迁入 #16 尾批，逻辑逐字保留）。

WS 会话基建（会话管理器、桥接与审计助手）仍在 assets_api 模块作用域，
经 bind_helpers 注入共享引用（可变状态共享不变）；数据面行为与迁移前一致。
"""
import asyncio
import contextlib
import json
from urllib.parse import urlencode

from fastapi import APIRouter, HTTPException, WebSocket
from starlette.websockets import WebSocketState

import websockets

from console_utils import safe_console_print
from zvplatform.constants import AGENT_INSTALL_STATUS_INSTALLED
from zvplatform.db import create_connection as get_db_connection

router = APIRouter()

_injected = {}


def bind_remote_ws_helpers(**kwargs):
    """assets_api 尾部注入 WS 会话基建与共享助手（可变对象按引用共享）。"""
    _injected.update(kwargs)
    globals().update(kwargs)


@router.websocket("/api/v1/assets/{asset_id}/remote-desktop/ws")
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


@router.websocket("/api/v1/remote/sessions/{session_id}/ws")
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
    cur = None
    try:
        if conn:
            cur = conn.cursor(); cur.execute("UPDATE remote_sessions SET status='connecting' WHERE id=%s", (session_id,)); conn.commit()
    except Exception:
        pass
    finally:
        if cur:
            cur.close()
        if conn:
            conn.close()

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
            c2 = None
            try:
                if conn:
                    c2 = conn.cursor(); c2.execute("UPDATE remote_sessions SET status='connected', connected_at=NOW(), transport_type='ws-tcp' WHERE id=%s", (session_id,)); conn.commit()
            except Exception:
                pass
            finally:
                if c2:
                    c2.close()
                if conn:
                    conn.close()

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
        cur = None
        try:
            if conn:
                cur = conn.cursor()
                cur.execute("UPDATE remote_sessions SET status='disconnected', disconnected_at=NOW(), disconnect_reason='relay_ended' WHERE id=%s AND status!='disconnected'", (session_id,))
                conn.commit()
        except Exception:
            pass
        finally:
            if cur:
                cur.close()
            if conn:
                conn.close()



