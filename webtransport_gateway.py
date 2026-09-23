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
import struct
import sys
import time
from urllib.parse import parse_qs, urlparse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from aioquic.asyncio import QuicConnectionProtocol, serve
from aioquic.h3.connection import H3_ALPN, H3Connection
from aioquic.h3.events import (
    DataReceived,
    DatagramReceived,
    HeadersReceived,
    H3Event,
    WebTransportStreamDataReceived,
)
from aioquic.quic.configuration import QuicConfiguration
from aioquic.quic.events import ConnectionTerminated, QuicEvent

import wt_cert
from zvplatform.agent_client import build_agent_auth_headers

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("wt-gateway")

UPSTREAM_TIMEOUT = 10.0
MEDIA_DATAGRAM_MAGIC = b"ZVMD"
MEDIA_DATAGRAM_HEADER = struct.Struct("!4sIHH")
MEDIA_DATAGRAM_PAYLOAD_BYTES = 1100
MAX_MEDIA_DATAGRAM_FRAGMENTS = 512


def split_media_datagrams(payload: bytes, frame_id: int) -> list[bytes]:
    """Split one media frame into bounded, independently discardable datagrams."""
    if not payload:
        return []
    fragment_count = (len(payload) + MEDIA_DATAGRAM_PAYLOAD_BYTES - 1) // MEDIA_DATAGRAM_PAYLOAD_BYTES
    if fragment_count > MAX_MEDIA_DATAGRAM_FRAGMENTS:
        raise ValueError(f"media frame requires too many datagrams: {fragment_count}")
    return [
        MEDIA_DATAGRAM_HEADER.pack(
            MEDIA_DATAGRAM_MAGIC,
            frame_id & 0xFFFFFFFF,
            index,
            fragment_count,
        ) + payload[offset:offset + MEDIA_DATAGRAM_PAYLOAD_BYTES]
        for index, offset in enumerate(range(0, len(payload), MEDIA_DATAGRAM_PAYLOAD_BYTES))
    ]


def is_media_frame(payload: bytes) -> bool:
    """Only remote desktop JPEG and H.264 frames may use the lossy media path."""
    return bool(payload) and payload[0] in (0x02, 0x03)


def is_h264_keyframe(payload: bytes) -> bool:
    """H.264 packets carry their keyframe bit after the 17-byte wire header."""
    return len(payload) >= 18 and payload[0] == 0x03 and payload[17] == 1


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
        self._pre_stream_frames: list[tuple[int, bytes]] = []  # 数据流打开前缓冲的 Agent 帧（控制帧全留/视频帧留最近2帧）
        self._pending_frames: list[tuple[int, bytes]] = []  # 上游未就绪时缓存早期消息
        self._upstream_task: asyncio.Task | None = None
        self._closed = False
        self._media_frame_id = 0
        self._media_frames_sent = 0
        self._media_datagrams_sent = 0
        self._media_frames_dropped = 0
        self._media_keyframes_reliable = 0
        self._last_stats_sent_at = 0.0
        self._session_close_recorded = False

    def _buffer_agent_frame(self, frame_type: int, payload: bytes) -> None:
        """数据流未打开时缓冲 Agent 帧；防膨胀：视频帧只保留最近 2 帧。"""
        self._pre_stream_frames.append((frame_type, payload))
        if frame_type == 1:
            bin_positions = [i for i, (t, _) in enumerate(self._pre_stream_frames) if t == 1]
            while len(bin_positions) > 2:
                del self._pre_stream_frames[bin_positions[0]]
                bin_positions = bin_positions[1:]

    def set_data_stream(self, stream_id: int) -> None:
        if self.data_stream_id is None:
            self.data_stream_id = stream_id
            self._wt_stream_header_sent = False
            # 冲刷数据流打开前缓冲的 Agent 帧（capabilities/最近视频帧）
            try:
                for ftype, payload in self._pre_stream_frames:
                    self.send_agent_frame(stream_id, ftype, payload)
            except Exception as exc:
                logger.warning(f"pre-stream frame flush failed: {exc}")
            self._pre_stream_frames.clear()

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
                # 新版 websockets（asyncio ClientConnection）统一用 send()，
                # 旧 API 的 send_bytes 已不存在（会导致 WT 数据路径全断）
                await self.upstream_ws.send(payload)
        except Exception as exc:
            logger.warning(f"[{self.asset_ip}] wt→agent send failed: {exc}")

    def send_agent_frame(self, stream_id: int, frame_type: int, payload: bytes) -> None:
        """Route media over lossy datagrams and preserve reliable control ordering."""
        if frame_type == 1 and is_media_frame(payload):
            if is_h264_keyframe(payload):
                try:
                    self.proto.send_keyframe_stream(self.connect_stream_id, payload)
                    self._media_keyframes_reliable += 1
                    self._maybe_send_transport_stats(stream_id)
                    return
                except Exception as exc:
                    logger.warning(f"[{self.asset_ip}] reliable keyframe stream failed: {exc}")
            self._media_frame_id = (self._media_frame_id + 1) & 0xFFFFFFFF
            try:
                datagrams = split_media_datagrams(payload, self._media_frame_id)
                # HTTP/3 datagrams are scoped to the WebTransport CONNECT stream,
                # not the separate bidirectional stream used for reliable control.
                self.proto.send_media_datagrams(self.connect_stream_id, datagrams)
                self._media_frames_sent += 1
                self._media_datagrams_sent += len(datagrams)
                self._maybe_send_transport_stats(stream_id)
                return
            except Exception as exc:
                self._media_frames_dropped += 1
                logger.warning(f"[{self.asset_ip}] media datagram drop: {exc}")
        self.proto.send_wt_data(stream_id, _encode_wire_frame(frame_type, payload))

    def _maybe_send_transport_stats(self, stream_id: int) -> None:
        now = time.monotonic()
        if now - self._last_stats_sent_at < 1.0:
            return
        self._last_stats_sent_at = now
        self.proto.send_wt_data(
            stream_id,
            _encode_frame(json.dumps({
                "type": "transport_stats",
                "transport": "wt-quic-datagram",
                "media_frames_sent": self._media_frames_sent,
                "media_datagrams_sent": self._media_datagrams_sent,
                "media_frames_dropped": self._media_frames_dropped,
                "media_keyframes_reliable": self._media_keyframes_reliable,
            }))[0],
        )

    # ---- Agent → WT ----

    async def _upstream_loop(self):
        import websockets

        upstream_url = f"ws://{self.asset_ip}:9000/remote-desktop?requester=platform"
        forwarded = 0
        try:
            async with websockets.connect(
                upstream_url, open_timeout=UPSTREAM_TIMEOUT, max_size=None,
                ping_interval=None,
                additional_headers=build_agent_auth_headers({
                    "X-Remote-Requester": f"session-{self.session_id}",
                }),
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
                    if isinstance(message, (bytes, bytearray)):
                        payload, _ftype = bytes(message), 1
                    else:
                        payload, _ftype = str(message).encode("utf-8"), 0
                    stream_id = self.data_stream_id
                    if stream_id is None:
                        # 数据流未打开：缓冲（控制帧全留，视频帧留最近2帧），打开时按序冲刷
                        self._buffer_agent_frame(_ftype, payload)
                        continue
                    forwarded += 1
                    if forwarded <= 5:
                        logger.info(f"[{self.asset_ip}] agent→wt frame #{forwarded}: len={len(payload)} "
                                    f"type={_ftype} stream_id={stream_id}")
                    self.send_agent_frame(stream_id, _ftype, payload)
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
        if not self._session_close_recorded:
            self._session_close_recorded = True
            try:
                asyncio.get_running_loop().create_task(self._record_session_closed())
            except RuntimeError:
                pass
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

    async def _record_session_closed(self) -> None:
        """Keep the platform session record aligned with the QUIC connection lifecycle."""
        def update_session() -> None:
            import mysql.connector
            from config_utils import get_db_config

            conn = mysql.connector.connect(**get_db_config(), connection_timeout=5)
            try:
                cur = conn.cursor()
                cur.execute(
                    "UPDATE remote_sessions SET status='disconnected', disconnected_at=NOW(), "
                    "disconnect_reason='wt_gateway_closed' "
                    "WHERE id=%s AND status!='disconnected'",
                    (self.session_id,),
                )
                conn.commit()
                cur.close()
            finally:
                conn.close()

        try:
            await asyncio.to_thread(update_session)
        except Exception as exc:
            logger.warning("failed to record WebTransport session close: %s", exc)


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
    return _encode_wire_frame(ftype, payload), ftype


def _encode_wire_frame(frame_type: int, payload: bytes) -> bytes:
    return len(payload).to_bytes(4, "big") + bytes([frame_type]) + payload


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
            # P0-07: TTL 强制执行（created_at + max_duration_sec）
            "SELECT asset_id FROM remote_sessions WHERE id=%s AND session_token=%s "
            "AND status IN ('created', 'connecting', 'connected') "
            "AND created_at > DATE_SUB(NOW(), INTERVAL COALESCE(max_duration_sec,7200) SECOND)",
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
            # 记录观看端的数据流 id（服务端回写用）——只接受客户端发起的
            # 双向流（stream_id % 4 == 0）；单向流服务端不可写（写入即 H3 0x105）
            if bridge.data_stream_id is None and event.stream_id % 4 == 0:
                bridge.set_data_stream(event.stream_id)
                logger.info(f"wt data stream selected: {event.stream_id} (bidi)")
            acc = self._accumulators.setdefault(event.stream_id, FrameAccumulator())
            # aioquic has already consumed the WebTransport stream type and session
            # identifier before emitting this event, so event.data is application data.
            for ftype, payload in acc.feed(event.data):
                asyncio.ensure_future(bridge.browser_to_agent(ftype, payload))
            if event.stream_ended:
                bridge.close()
        elif isinstance(event, DataReceived):
            bridge = self._bridge
            if bridge and event.stream_id in self._accumulators:
                for ftype, payload in self._accumulators[event.stream_id].feed(event.data):
                    asyncio.ensure_future(bridge.browser_to_agent(0, payload))
        elif isinstance(event, DatagramReceived):
            # Client datagrams are intentionally unsupported. Keyboard, mouse, and
            # session control remain on the reliable bidirectional stream.
            logger.debug("ignored client datagram stream_id=%s bytes=%s", event.stream_id, len(event.data))

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

        # P2-04：记录 WT(QUIC/UDP) 通道到会话表（可观测性）
        try:
            import mysql.connector
            from config_utils import get_db_config as _db_config
            conn = mysql.connector.connect(**_db_config(), connection_timeout=5)
            cur = conn.cursor()
            cur.execute(
                "UPDATE remote_sessions SET transport_type='wt-udp', status='connected',"
                " connected_at=COALESCE(connected_at, NOW()) WHERE id=%s",
                (session_id,),
            )
            conn.commit()
            cur.close()
            conn.close()
        except Exception as exc:
            print(f"[WT] session transport record failed: {exc}")

        bridge = AgentBridge(self, session_id, connect_stream_id=event.stream_id,
                             asset_ip=info["ip_address"])
        self._bridge = bridge
        bridge.start()
        logger.info(f"webtransport session bridged: asset={info['asset_id']} "
                    f"({info['hostname']}) session={session_id}")

    def send_wt_data(self, stream_id: int, payload: bytes):
        """Agent → 观看端：在观看端发起的 WT 双向流上续写应用数据。

        注意：WT 流的会话头由建流方（浏览器）发送，服务端回写为裸应用帧
        （[4B len][1B type][payload]，与观看端适配器的解析一致）；
        注入 [0x41][session_id] 头会破坏观看端的帧解析导致 0 FPS。
        """
        try:
            self._quic.send_stream_data(stream_id=stream_id, data=payload)
            self.transmit()
        except Exception as exc:
            logger.warning(f"send_wt_data failed: {exc}")

    def send_media_datagrams(self, stream_id: int, datagrams: list[bytes]):
        """Emit every media fragment as an HTTP/3 datagram, then flush once."""
        for datagram in datagrams:
            self._http.send_datagram(stream_id, datagram)
        self.transmit()

    def send_keyframe_stream(self, connect_stream_id: int, payload: bytes):
        """Send IDR data on its own reliable WT stream, isolated from controls."""
        stream_id = self._http.create_webtransport_stream(
            connect_stream_id,
            is_unidirectional=True,
        )
        self._quic.send_stream_data(
            stream_id,
            _encode_wire_frame(1, payload),
            end_stream=True,
        )
        self.transmit()


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
