# -*- coding: utf-8 -*-
"""WebTransport (QUIC over UDP) 网关。

浏览器观看端（HTTPS 页面）通过 WebTransport 连接本网关（UDP 4433），
网关校验 session_token 后桥接到目标终端 Agent 的 9000 WS——与平台 WS 代理
完全等价，但承载在 QUIC/UDP 上（无 TCP 队头阻塞），观看端自适应切换。

流帧协议（WT 流是无消息边界的字节流，与 WS 不同）：
  [4B len][1B type(0=text JSON, 1=binary frame)][payload]

启动：python webtransport_gateway.py [--port 4433]
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import logging
import os
import sys
from urllib.parse import parse_qs, urlparse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from aioquic.asyncio import QuicConnectionProtocol, serve
from aioquic.h3.connection import H3_ALPN, H3Connection, FrameType, encode_uint_var
from aioquic.h3.events import (
    DataReceived,
    HeadersReceived,
    H3Event,
    WebTransportStreamDataReceived,
)
from aioquic.quic.configuration import QuicConfiguration
from aioquic.quic.events import ConnectionTerminated, QuicEvent

import wt_cert

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("wt-gateway")

UPSTREAM_TIMEOUT = 10.0


# ============================================================
# Agent 上游桥接
# ============================================================

class AgentBridge:
    """WebTransport 会话 ↔ 目标终端 Agent 9000 WS 的双向桥。"""

    def __init__(self, proto: "WebTransportGatewayProtocol", session_id: int,
                 connect_stream_id: int, asset_ip: str):
        self.proto = proto
        self.session_id = session_id
        self.connect_stream_id = connect_stream_id
        self.data_stream_id: int | None = None  # 观看端发起的 WT 数据流（回写用）
        self.asset_ip = asset_ip
        self.upstream_ws = None
        self._pending_frames: list[tuple[int, bytes]] = []  # 上游未就绪时缓存早期消息
        self._upstream_task: asyncio.Task | None = None
        self._closed = False

    def set_data_stream(self, stream_id: int) -> None:
        if self.data_stream_id is None:
            self.data_stream_id = stream_id

    # ---- WT → Agent ----

    async def browser_to_agent(self, frame_type: int, payload: bytes):
        """观看端发来的单帧（已由长度前缀解析）→ 转发给 Agent WS（text/binary）。"""
        if self._closed:
            return
        if self.upstream_ws is None:
            # 上游未就绪：缓存早期消息（如 capabilities），连接建立后按序补发
            self._pending_frames.append((frame_type, payload))
            logger.info(f"wt→agent buffered (upstream pending): type={frame_type} len={len(payload)}")
            return
        await self._send_to_upstream(frame_type, payload)

    async def _send_to_upstream(self, frame_type: int, payload: bytes):
        try:
            if frame_type == 0:  # text JSON 控制消息
                await self.upstream_ws.send(payload.decode("utf-8"))
            else:  # binary 屏幕帧
                await self.upstream_ws.send_bytes(payload)
        except Exception as exc:
            logger.warning(f"[{self.asset_ip}] wt→agent send failed: {exc}")

    # ---- Agent → WT ----

    async def _upstream_loop(self):
        import websockets

        upstream_url = f"ws://{self.asset_ip}:9000/remote-desktop?requester=platform"
        try:
            async with websockets.connect(
                upstream_url, open_timeout=UPSTREAM_TIMEOUT, max_size=None,
                ping_interval=None,
            ) as upstream:
                self.upstream_ws = upstream
                logger.info(f"[{self.asset_ip}] upstream connected")
                # 补发上游未就绪期间缓存的早期消息（capabilities 等）
                for ft, pl in self._pending_frames:
                    await self._send_to_upstream(ft, pl)
                self._pending_frames.clear()
                async for message in upstream:
                    if self._closed:
                        break
                    payload, _ftype = _encode_frame(message)
                    stream_id = self.data_stream_id
                    if stream_id is None:
                        continue
                    self.proto.send_wt_data(stream_id, payload)
        except Exception as exc:
            logger.info(f"[{self.asset_ip}] upstream ended: {exc}")
        finally:
            self.close()

    # ---- 生命周期 ----

    def start(self):
        self._upstream_task = asyncio.get_event_loop().create_task(self._upstream_loop())

    def close(self):
        if self._closed:
            return
        self._closed = True
        try:
            if self.upstream_ws is not None:
                asyncio.get_event_loop().create_task(self.upstream_ws.close())
        except Exception:
            pass
        # 通知观看端会话结束（text 帧）
        try:
            payload, _ = _encode_frame(json.dumps({"type": "session_error", "message": "远程桌面会话已结束"}))
            stream_id = self.data_stream_id
            if stream_id is not None:
                self.proto.send_wt_data(stream_id, payload)
        except Exception:
            pass


# ============================================================
# 长度前缀帧编解码
# ============================================================

def _encode_frame(message) -> tuple[bytes, int]:
    if isinstance(message, (bytes, bytearray)):
        payload = bytes(message)
        ftype = 1
    else:
        payload = message.encode("utf-8") if isinstance(message, str) else str(message).encode("utf-8")
        ftype = 0
    return (len(payload)).to_bytes(4, "big") + bytes([ftype]) + payload, ftype


class FrameAccumulator:
    """把 WT 流的任意分块重组为完整的长度前缀帧。"""

    def __init__(self):
        self._buffer = b""

    def feed(self, data: bytes) -> list[tuple[int, bytes]]:
        self._buffer += data
        frames = []
        while len(self._buffer) >= 5:
            length = int.from_bytes(self._buffer[:4], "big")
            if length > 4 * 1024 * 1024:
                self._buffer = b""
                break
            if len(self._buffer) < 5 + length:
                break
            ftype = self._buffer[4]
            payload = self._buffer[5:5 + length]
            self._buffer = self._buffer[5 + length:]
            frames.append((ftype, payload))
        return frames


# ============================================================
# 鉴权
# ============================================================

def validate_session(token: str, session_id: int) -> dict | None:
    """校验 session_token 并返回目标资产信息（与平台 WS 代理同逻辑）。"""
    import mysql.connector
    from config_utils import get_db_config

    cfg = get_db_config()
    conn = mysql.connector.connect(
        host=cfg["host"], port=cfg["port"], user=cfg["user"],
        password=cfg["password"], database=cfg["database"],
    )
    try:
        cur = conn.cursor(dictionary=True)
        token_hash = hashlib.sha256(token.encode()).hexdigest()
        cur.execute(
            "SELECT asset_id FROM remote_sessions WHERE id=%s AND session_token=%s AND status!='disconnected'",
            (session_id, token_hash),
        )
        row = cur.fetchone()
        if not row:
            return None
        cur.execute(
            "SELECT ip_address, hostname, agent_install_status, status FROM assets WHERE id=%s AND deleted_at IS NULL",
            (row["asset_id"],),
        )
        asset = cur.fetchone()
        if not asset or asset["agent_install_status"] != "installed":
            return None
        return {"asset_id": row["asset_id"], "ip_address": asset["ip_address"],
                "hostname": asset["hostname"], "status": asset["status"]}
    finally:
        conn.close()


# ============================================================
# QUIC 协议处理
# ============================================================

class WebTransportGatewayProtocol(QuicConnectionProtocol):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # enable_webtransport=True：否则 WT 流数据不会产生 WebTransportStreamDataReceived
        self._http: H3Connection | None = H3Connection(self._quic, enable_webtransport=True)
        self._bridge: AgentBridge | None = None
        self._accumulators: dict[int, FrameAccumulator] = {}
        self._session_prefix_stripped: dict[int, bool] = {}

    def quic_event_received(self, event: QuicEvent):
        if isinstance(event, ConnectionTerminated):
            if self._bridge:
                self._bridge.close()
                self._bridge = None
            return
        if self._http is not None:
            for h3_event in self._http.handle_event(event):
                self._handle_h3_event(h3_event)

    def _handle_h3_event(self, event: H3Event):
        if isinstance(event, HeadersReceived):
            self._handle_headers(event)
        elif isinstance(event, WebTransportStreamDataReceived):
            bridge = self._bridge
            if bridge is None:
                return
            # 记录观看端的数据流 id（服务端回写用）
            bridge.set_data_stream(event.stream_id)
            acc = self._accumulators.setdefault(event.stream_id, FrameAccumulator())
            data = event.data
            if not self._session_prefix_stripped.get(event.stream_id):
                # 观看端 WT 流的首个 0x41 帧载荷 = session_id varint（即 CONNECT 流 id），
                # 不属于应用数据，剥离之（否则长度前缀协议错位）
                n = len(encode_uint_var(bridge.connect_stream_id))
                data = data[n:]
                self._session_prefix_stripped[event.stream_id] = True
            for ftype, payload in acc.feed(data):
                asyncio.ensure_future(bridge.browser_to_agent(ftype, payload))
            if event.stream_ended:
                bridge.close()
        elif isinstance(event, DataReceived):
            bridge = self._bridge
            if bridge and event.stream_id in self._accumulators:
                for ftype, payload in self._accumulators[event.stream_id].feed(event.data):
                    asyncio.ensure_future(bridge.browser_to_agent(payload))

    def _handle_headers(self, event: HeadersReceived):
        headers = {k.decode().lower(): v.decode() for k, v in event.headers}
        if headers.get(":method") != "CONNECT" or headers.get(":protocol") != "webtransport":
            return
        path = headers.get(":path", "")
        qs = parse_qs(urlparse(path).query)
        token = (qs.get("token") or [""])[0]
        try:
            session_id = int((qs.get("session_id") or ["0"])[0])
        except ValueError:
            session_id = 0

        try:
            info = validate_session(token, session_id)
        except Exception as exc:
            logger.error(f"session validation failed: {exc}")
            info = None

        if not info:
            self._http.send_headers(
                stream_id=event.stream_id,
                headers=[(b":status", b"403")],
                end_stream=True,
            )
            self.transmit()
            return

        # 接受 WebTransport 会话：200 + sec-webtransport-http3-draft（官方示例要求）
        self._http.send_headers(
            stream_id=event.stream_id,
            headers=[
                (b":status", b"200"),
                (b"sec-webtransport-http3-draft", b"draft02"),
            ],
            end_stream=False,
        )
        self.transmit()

        bridge = AgentBridge(self, session_id, connect_stream_id=event.stream_id,
                             asset_ip=info["ip_address"])
        self._bridge = bridge
        bridge.start()
        logger.info(f"webtransport session bridged: asset={info['asset_id']} "
                    f"({info['hostname']}) session={session_id}")

    def send_wt_data(self, stream_id: int, payload: bytes):
        """Agent → 观看端：在观看端发起的 WT 双向流上续写 WEBTRANSPORT_STREAM 数据。"""
        try:
            self._quic.send_stream_data(stream_id=stream_id, data=payload)
            self.transmit()
        except Exception as exc:
            logger.warning(f"send_wt_data failed: {exc}")


# ============================================================
# 服务器入口
# ============================================================

class GatewayServerProtocol(WebTransportGatewayProtocol):
    pass


async def run_server(host: str, port: int):
    cert_file, key_file = wt_cert.ensure_wt_cert()
    configuration = QuicConfiguration(
        is_client=False,
        alpn_protocols=H3_ALPN,
        server_name=host,
        max_datagram_frame_size=65536,  # H3_DATAGRAM 设置必需（WebTransport 依赖）
    )
    configuration.load_cert_chain(cert_file, key_file)

    logger.info(f"WebTransport gateway listening on udp://{host}:{port}")
    await serve(
        host,
        port,
        configuration=configuration,
        create_protocol=GatewayServerProtocol,
    )
    await asyncio.Future()  # run forever


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=4433)
    args = parser.parse_args()
    asyncio.run(run_server(args.host, args.port))


if __name__ == "__main__":
    main()
