# -*- coding: utf-8 -*-
"""H264 配置一致性（黑屏专项）单元测试。

覆盖：
- 首帧 IDR：编码队列 latest-wins 排水不丢关键帧请求（P0-H5）
- 发送槽关键帧保护：待发 keyframe 组不被 delta 帧覆盖（P0-H6）
- 时间戳/序号单调性：_h264_sent_seq 跨编码器重建/回退单调递增
- SPS/PPS 与切片同帧：libx264 首包含 SPS+PPS+IDR（Annex-B in-band）
"""
import asyncio
import base64
import collections
import struct
import sys
from pathlib import Path

import pytest

sys.path.insert(0, r"D:\IT2026\IT2026\IT2026\IT2026")

from remote_desktop_engine_v2 import RemoteDesktopSession  # noqa: E402


def make_session():
    s = RemoteDesktopSession.__new__(RemoteDesktopSession)
    s._h264_keyframe_requested = False
    s._h264_stats = {"frames": 0, "bytes": 0, "drops_backpressure": 0}
    return s


def test_hardware_bitrate_quality_mapping():
    from Codec import h264_encoder as encoder

    assert encoder._hardware_bitrate_for_crf(17) == 28_000_000
    assert encoder._hardware_bitrate_for_crf(19) == 16_000_000
    assert encoder._hardware_bitrate_for_crf(23) == 10_000_000
    assert encoder._hardware_bitrate_for_crf(28) == 2_500_000
    assert encoder._hardware_bitrate_for_crf(36) == 1_500_000


def test_h264_results_report_encoded_dimensions():
    session = make_session()
    results = session._h264_results_from(
        [{"data": b"packet", "keyframe": True}],
        1280,
        720,
    )
    assert results == [{
        "type": "h264",
        "data": base64.b64encode(b"packet").decode("ascii"),
        "keyframe": True,
        "width": 1280,
        "height": 720,
    }]


# ============ P0-H5：编码队列 latest-wins 不丢关键帧 ============

def test_drain_keeps_newest_frame():
    """latest-wins：排水后应保留最新帧（修复前 oldest-wins 会丢最新帧）。"""
    s = make_session()
    s._h264_encode_queue = collections.deque(maxlen=2)
    s._h264_encode_queue.append({"keyframe": False, "n": 1})
    s._h264_encode_queue.append({"keyframe": False, "n": 2})
    job, dropped = s._drain_h264_encode_queue()
    assert job is not None and job["n"] == 2
    assert dropped is False


def test_drain_consumes_keyframe_with_priority():
    """第二轮修复：关键帧 job（appendleft 插队）优先消费，不再被 delta 挤掉。"""
    s = make_session()
    s._h264_encode_queue = collections.deque(maxlen=2)
    s._h264_encode_queue.append({"keyframe": True, "n": 1})
    s._h264_encode_queue.append({"keyframe": False, "n": 2})
    job, dropped = s._drain_h264_encode_queue()
    assert job["n"] == 1
    assert dropped is False
    # 关键帧之后的 delta 留待下轮 latest-wins
    assert len(s._h264_encode_queue) == 1


def test_drain_newest_keyframe_not_reported_dropped():
    """最新帧本身就是关键帧：正常编码，不算被挤出。"""
    s = make_session()
    s._h264_encode_queue = collections.deque(maxlen=2)
    s._h264_encode_queue.append({"keyframe": False, "n": 1})
    s._h264_encode_queue.append({"keyframe": True, "n": 2})
    job, dropped = s._drain_h264_encode_queue()
    assert job["n"] == 2
    assert dropped is False


def test_drain_empty_queue():
    s = make_session()
    s._h264_encode_queue = collections.deque(maxlen=2)
    job, dropped = s._drain_h264_encode_queue()
    assert job is None
    assert dropped is False


def test_keyframe_survives_delta_enqueue():
    """关键帧插队后 delta 入队不挤掉关键帧（手动淘汰等效 maxlen=2）。"""
    s = make_session()
    s._h264_encode_queue = collections.deque(maxlen=8)

    class FakeShot:
        def close(self):
            pass

    s._enqueue_h264_encode_job({"keyframe": True, "n": 1, "screenshot": FakeShot()})
    s._enqueue_h264_encode_job({"keyframe": False, "n": 2, "screenshot": FakeShot()})
    s._enqueue_h264_encode_job({"keyframe": False, "n": 3, "screenshot": FakeShot()})
    assert [j["n"] for j in s._h264_encode_queue] == [1, 3]
    job, dropped = s._drain_h264_encode_queue()
    assert job["n"] == 1
    assert dropped is False


# ============ P0-H6：发送槽关键帧保护 ============

def _run_enqueue(coro):
    return asyncio.run(coro)


def test_pending_keyframe_group_not_overwritten_by_delta():
    s = make_session()
    s._latest_frame_payload = None
    s._frame_sender_task = None
    s.running = False
    s._h264_stats = {"frames": 0, "bytes": 0, "drops_backpressure": 0}

    async def run():
        keyframe_group = {"type": "h264_group", "packets": [], "keyframe": True, "width": 64, "height": 64, "frame_size": 1}
        delta_group = {"type": "h264_group", "packets": [], "keyframe": False, "width": 64, "height": 64, "frame_size": 1}
        assert s._enqueue_frame(keyframe_group) is True
        assert s._enqueue_frame(delta_group) is True
        assert s._latest_frame_payload is keyframe_group
        assert s._enqueue_frame(delta_group) is True
        assert s._latest_frame_payload is keyframe_group
        assert s._h264_stats["drops_backpressure"] == 2

    _run_enqueue(run())


def test_new_keyframe_overwrites_pending_keyframe():
    """更新更好的关键帧允许覆盖待发关键帧。"""
    s = make_session()
    s._latest_frame_payload = None
    s._frame_sender_task = None
    s.running = False
    s._h264_stats = {"frames": 0, "bytes": 0, "drops_backpressure": 0}

    async def run():
        first = {"type": "h264_group", "packets": [], "keyframe": True}
        second = {"type": "h264_group", "packets": [], "keyframe": True}
        s._enqueue_frame(first)
        s._enqueue_frame(second)
        assert s._latest_frame_payload is second

    _run_enqueue(run())


def test_delta_group_still_overwritten_by_newer_delta():
    """无关键帧在途时保持丢帧语义（Frame Queue = 1）。"""
    s = make_session()
    s._latest_frame_payload = None
    s._frame_sender_task = None
    s.running = False
    s._h264_stats = {"frames": 0, "bytes": 0, "drops_backpressure": 0}

    async def run():
        d1 = {"type": "h264_group", "packets": [], "keyframe": False}
        d2 = {"type": "h264_group", "packets": [], "keyframe": False}
        s._enqueue_frame(d1)
        s._enqueue_frame(d2)
        assert s._latest_frame_payload is d2
        assert s._h264_stats["drops_backpressure"] == 0

    _run_enqueue(run())


def test_jpeg_frame_overwrites_normally():
    """JPEG 帧不受 H.264 关键帧保护影响。"""
    s = make_session()
    s._latest_frame_payload = None
    s._frame_sender_task = None
    s.running = False
    s._h264_stats = {"frames": 0, "bytes": 0, "drops_backpressure": 0}

    async def run():
        kf = {"type": "h264_group", "packets": [], "keyframe": True}
        jpeg = {"type": "frame", "data": "", "width": 64, "height": 64}
        s._enqueue_frame(kf)
        s._enqueue_frame(jpeg)
        assert s._latest_frame_payload is jpeg

    _run_enqueue(run())


# ============ 序号单调性（时间戳单调性的引擎侧保证） ============

class FakeWebSocket:
    def __init__(self):
        self.frames = []

    async def send_bytes(self, data):
        self.frames.append(bytes(data))


def make_send_session():
    s = make_session()
    s.send_lock = asyncio.Lock()
    s.websocket = FakeWebSocket()
    s._h264_sent_seq = 0
    s._h264_acked_seq = 0
    s._h264_send_times = {}
    return s


def test_h264_group_seq_monotonic_across_encoder_rebuild():
    """编码器重建/回退 JPEG 再恢复不重置 seq：发送侧序号严格单调。"""

    async def run():
        s = make_send_session()
        group = {
            "type": "h264_group",
            "keyframe": True,
            "width": 64,
            "height": 64,
            "frame_size": 5,
            "packets": [
                {"data": base64.b64encode(b"\x00\x00\x00\x01\x67").decode("ascii"), "width": 64, "height": 64}
            ],
        }
        await s._send_binary_frame(group)
        await s._send_binary_frame(group)  # 模拟编码器重建后继续发送
        seqs = []
        for frame in s.websocket.frames:
            assert frame[0] == 0x03
            seqs.append(struct.unpack(">I", frame[1:5])[0])
        assert seqs == [1, 2]
        # 时间戳单调性直接依赖 seq：观看端 timestamp = seq * 1000
        assert seqs == sorted(seqs) and len(set(seqs)) == len(seqs)

    asyncio.run(run())


def test_single_h264_and_group_share_seq_counter():
    """单包 h264 与整帧 group 共用同一 seq 计数器，不回退不重复。"""

    async def run():
        s = make_send_session()
        group = {
            "type": "h264_group",
            "keyframe": True,
            "width": 64,
            "height": 64,
            "frame_size": 5,
            "packets": [
                {"data": base64.b64encode(b"\x00\x00\x00\x01\x67").decode("ascii"), "width": 64, "height": 64}
            ],
        }
        single = {
            "type": "h264",
            "keyframe": False,
            "width": 64,
            "height": 64,
            "data": base64.b64encode(b"\x00\x00\x00\x01\x41").decode("ascii"),
        }
        await s._send_binary_frame(group)
        await s._send_binary_frame(single)
        seqs = [struct.unpack(">I", frame[1:5])[0] for frame in s.websocket.frames]
        assert seqs == [1, 2]

    asyncio.run(run())


# ============ SPS/PPS 与切片同帧（libx264 Annex-B in-band） ============

def _nal_types(data: bytes) -> list:
    types = []
    i = 0
    while i < len(data) - 3:
        if data[i:i + 3] == b"\x00\x00\x01":
            types.append(data[i + 3] & 0x1F)
            i += 3
        else:
            i += 1
    return types


@pytest.fixture
def libx264_encoder(monkeypatch):
    from Codec import h264_encoder as he
    from PIL import Image
    monkeypatch.setattr(he, "get_h264_backend_name", lambda: "libx264")
    enc = he.H264StreamEncoder(64, 48, fps=30, crf=28)
    img = Image.new("RGB", (64, 48), (30, 60, 90))
    return enc, img


def test_first_packet_contains_sps_pps_idr(libx264_encoder):
    """首个 packet 必须同时携带 SPS(7)/PPS(8)/IDR(5)——观看端 configure 后第一 chunk 可解码。"""
    enc, img = libx264_encoder
    packets = enc.encode(img)
    enc.close()
    assert packets, "no packets produced"
    first = packets[0]
    assert first["keyframe"] is True
    types = set(_nal_types(first["data"]))
    assert {7, 8, 5} <= types, f"SPS/PPS/IDR missing in first packet: {types}"


def test_force_keyframe_produces_idr(libx264_encoder):
    """force_keyframe（重建编码器）后下一帧必为 IDR 且带 SPS/PPS。"""
    enc, img = libx264_encoder
    enc.encode(img)
    enc.encode(img)
    enc.force_keyframe()
    packets = enc.encode(img)
    enc.close()
    assert any(p["keyframe"] for p in packets)
    keyframe_pkt = next(p for p in packets if p["keyframe"])
    assert {7, 8, 5} <= set(_nal_types(keyframe_pkt["data"]))


def test_encoder_reports_conversion_and_codec_timing(libx264_encoder):
    enc, img = libx264_encoder
    enc.encode(img)
    metrics = enc.last_metrics
    enc.close()

    assert metrics["backend"] == "libx264"
    assert metrics["hardware"] is False
    assert all(float(metrics[key]) >= 0 for key in ("input_ms", "convert_ms", "codec_ms", "total_ms"))


def test_resolution_change_rebuilds_with_idr(libx264_encoder):
    """分辨率热重建（新 SPS/PPS）后首帧为 IDR。"""
    enc, img = libx264_encoder
    enc.encode(img)
    from PIL import Image as PILImage
    img2 = PILImage.new("RGB", (32, 32), (10, 20, 30))
    packets = enc.encode(img2)
    enc.close()
    assert enc.width == 32 and enc.height == 32
    assert packets and packets[0]["keyframe"] is True
    assert {7, 8, 5} <= set(_nal_types(packets[0]["data"]))
