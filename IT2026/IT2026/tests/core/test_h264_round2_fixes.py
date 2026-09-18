# -*- coding: utf-8 -*-
"""H264 媒体面第二轮加固（遗留风险 1/2/3）单元测试。

覆盖：
- 硬编 SPS/PPS 拆包兜底：_prepend_stream_header 纯函数 + _attach_hw_stream_header 流程
- 发送槽关键帧组保护 2.5s 时效上限（timestamp 检查）
- 编码队列关键帧插队：appendleft 入队 + 消费侧关键帧优先
"""
import asyncio
import collections
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, r"D:\IT2026\IT2026\IT2026\IT2026")

from Codec import h264_encoder as he  # noqa: E402
from remote_desktop_engine_v2 import RemoteDesktopSession  # noqa: E402


# ============ 风险 1：_prepend_stream_header 纯函数 ============

def test_prepend_to_first_keyframe_packet():
    pkts = [
        {"data": b"\x00\x00\x00\x01\x41", "keyframe": False},
        {"data": b"\x00\x00\x00\x01\x65", "keyframe": True},
        {"data": b"\x00\x00\x00\x01\x41", "keyframe": False},
    ]
    out = he._prepend_stream_header(pkts, b"\x00\x00\x00\x01\x67\x00\x00\x00\x01\x68")
    assert out[0]["data"] == b"\x00\x00\x00\x01\x41"  # delta 不动
    assert out[1]["data"].startswith(b"\x00\x00\x00\x01\x67")  # 关键帧前置 extradata
    assert out[1]["data"].endswith(b"\x00\x00\x00\x01\x65")
    assert out[1]["keyframe"] is True
    assert out[2]["data"] == b"\x00\x00\x00\x01\x41"


def test_prepend_without_keyframe_targets_first_packet():
    """无 keyframe 标记的包列表：兜底前置到首个包（保持原 keyframe 标记）。"""
    pkts = [{"data": b"\xaa", "keyframe": False}]
    out = he._prepend_stream_header(pkts, b"\x67")
    assert out[0]["data"] == b"\x67\xaa"
    assert out[0]["keyframe"] is False


def test_prepend_noop_on_empty_extradata():
    pkts = [{"data": b"\xaa", "keyframe": True}]
    assert he._prepend_stream_header(pkts, None) is pkts
    assert he._prepend_stream_header(pkts, b"") is pkts


def test_prepend_noop_on_empty_packets():
    assert he._prepend_stream_header([], b"\x67") == []


def test_prepend_does_not_mutate_input():
    pkts = [{"data": b"\xaa", "keyframe": True}]
    he._prepend_stream_header(pkts, b"\x67")
    assert pkts[0]["data"] == b"\xaa"


# ============ 风险 1：_attach_hw_stream_header 流程 ============

def make_hw_encoder(codec_name="h264_nvenc", extradata=None, ctx_extradata=None):
    enc = he.H264StreamEncoder.__new__(he.H264StreamEncoder)
    enc._codec_name = codec_name
    enc._ctx = None if ctx_extradata is None and extradata is None and codec_name == "libx264" else object()
    enc._hw_stream_header = extradata
    enc._hw_header_emitted = False
    if ctx_extradata is not None:
        ctx = type("FakeCtx", (), {})()
        ctx.extradata = ctx_extradata
        enc._ctx = ctx
    return enc


def test_attach_hw_header_prepends_once():
    enc = make_hw_encoder(extradata=b"\x00\x00\x00\x01\x67\x00\x00\x00\x01\x68")
    pkts = [{"data": b"\x00\x00\x00\x01\x65", "keyframe": True}]
    out = enc._attach_hw_stream_header(pkts)
    assert out[0]["data"].startswith(b"\x00\x00\x00\x01\x67")
    assert enc._hw_header_emitted is True
    # 第二帧不再前置（已输出过流头）
    pkts2 = [{"data": b"\x00\x00\x00\x01\x41", "keyframe": False}]
    out2 = enc._attach_hw_stream_header(pkts2)
    assert out2[0]["data"] == b"\x00\x00\x00\x01\x41"


def test_attach_hw_header_skipped_for_libx264():
    enc = make_hw_encoder(codec_name="libx264")
    enc._ctx = None
    pkts = [{"data": b"\x00\x00\x00\x01\x67\x65", "keyframe": True}]
    out = enc._attach_hw_stream_header(pkts)
    assert out[0]["data"] == b"\x00\x00\x00\x01\x67\x65"
    assert enc._hw_header_emitted is False


def test_attach_hw_header_fresh_read_from_ctx():
    """_open 时 extradata 未就绪（惰性 open）：首帧输出后从 ctx 现取。"""
    enc = make_hw_encoder(ctx_extradata=b"\x00\x00\x00\x01\x67")
    assert enc._hw_stream_header is None
    pkts = [{"data": b"\x00\x00\x00\x01\x65", "keyframe": True}]
    out = enc._attach_hw_stream_header(pkts)
    assert out[0]["data"].startswith(b"\x00\x00\x00\x01\x67")
    assert enc._hw_header_emitted is True


def test_attach_hw_header_no_extradata_keeps_flag_false():
    """始终拿不到 extradata：不置标志（每帧重试，一旦就绪即前置）。"""
    enc = make_hw_encoder()
    enc._ctx = type("FakeCtx", (), {})()  # 无 extradata 属性
    pkts = [{"data": b"\x00\x00\x00\x01\x65", "keyframe": True}]
    out = enc._attach_hw_stream_header(pkts)
    assert out[0]["data"] == b"\x00\x00\x00\x01\x65"
    assert enc._hw_header_emitted is False


# ============ 风险 2：关键帧组保护 2.5s 时效上限 ============

def make_enqueue_session():
    s = RemoteDesktopSession.__new__(RemoteDesktopSession)
    s._h264_keyframe_requested = False
    s._latest_frame_payload = None
    s._frame_sender_task = None
    s.running = False
    s._h264_stats = {"frames": 0, "bytes": 0, "drops_backpressure": 0}
    return s


def _kf_group(**extra):
    base = {"type": "h264_group", "packets": [], "keyframe": True, "width": 64, "height": 64, "frame_size": 1}
    base.update(extra)
    return base


def _delta_group(**extra):
    base = {"type": "h264_group", "packets": [], "keyframe": False, "width": 64, "height": 64, "frame_size": 1}
    base.update(extra)
    return base


def test_fresh_keyframe_group_still_protected():
    s = make_enqueue_session()

    async def run():
        s._enqueue_frame(_kf_group())
        assert s._enqueue_frame(_delta_group()) is True
        assert s._latest_frame_payload.get("keyframe") is True
        assert s._h264_stats["drops_backpressure"] == 1

    asyncio.run(run())


def test_expired_keyframe_group_allows_delta_overwrite():
    """保护超过 2.5s 过期：delta 覆盖滞留关键帧组，inflight 不被长期推高。"""
    s = make_enqueue_session()

    async def run():
        kf = _kf_group()
        s._enqueue_frame(kf)
        # 人为把入队时间拨回 3s 前
        kf["_enqueued_at"] = time.monotonic() - 3.0
        delta = _delta_group()
        assert s._enqueue_frame(delta) is True
        assert s._latest_frame_payload is delta
        assert s._h264_stats["drops_backpressure"] == 0

    asyncio.run(run())


def test_missing_enqueued_at_defaults_to_protected():
    """无 _enqueued_at 的旧格式 payload：保守沿用保护（旧行为）。"""
    s = make_enqueue_session()

    async def run():
        kf = _kf_group()
        s._latest_frame_payload = kf  # 直塞，不带 _enqueued_at
        assert s._enqueue_frame(_delta_group()) is True
        assert s._latest_frame_payload is kf
        assert s._h264_stats["drops_backpressure"] == 1

    asyncio.run(run())


def test_new_keyframe_refreshes_enqueued_at():
    s = make_enqueue_session()

    async def run():
        first = _kf_group()
        s._enqueue_frame(first)
        first["_enqueued_at"] = time.monotonic() - 3.0  # 已过期
        second = _kf_group()
        s._enqueue_frame(second)  # keyframe 覆盖 keyframe 不受限
        assert s._latest_frame_payload is second
        assert second["_enqueued_at"] > time.monotonic() - 1.0  # 时间戳已刷新
        # 新关键帧重新受保护
        assert s._enqueue_frame(_delta_group()) is True
        assert s._latest_frame_payload is second

    asyncio.run(run())


def test_enqueued_at_not_sent_on_wire():
    """_enqueued_at 仅调度内部使用，_send_binary_frame 序列化不含它。"""
    s = make_enqueue_session()
    s.send_lock = asyncio.Lock()
    s.websocket = type("WS", (), {"frames": []})()

    async def send_bytes(data):
        s.websocket.frames.append(bytes(data))

    s.websocket.send_bytes = send_bytes
    s._h264_sent_seq = 0
    s._h264_acked_seq = 0
    s._h264_send_times = {}

    async def run():
        import base64
        kf = _kf_group(
            packets=[{"data": base64.b64encode(b"\x00\x00\x00\x01\x67").decode("ascii"), "width": 64, "height": 64}]
        )
        s._enqueue_frame(kf)
        await s._send_binary_frame(s._latest_frame_payload)
        wire = s.websocket.frames[0]
        assert b"_enqueued_at" not in wire

    asyncio.run(run())


# ============ 风险 3：编码队列关键帧插队 ============

def test_drain_prefers_keyframe_and_clears_stale_deltas():
    """[d1, kf, d2]：取 kf，清空其前的 d1（IDR 前 delta 已过时），d2 留待下轮。"""
    s = RemoteDesktopSession.__new__(RemoteDesktopSession)
    s._h264_keyframe_requested = False
    s._h264_encode_queue = collections.deque(maxlen=3)
    s._h264_encode_queue.append({"keyframe": False, "n": 1})
    s._h264_encode_queue.append({"keyframe": True, "n": 2})
    s._h264_encode_queue.append({"keyframe": False, "n": 3})
    job, dropped = s._drain_h264_encode_queue()
    assert job["n"] == 2 and dropped is False
    assert [j["n"] for j in s._h264_encode_queue] == [3]


def test_drain_no_keyframe_keeps_latest_wins():
    s = RemoteDesktopSession.__new__(RemoteDesktopSession)
    s._h264_keyframe_requested = False
    s._h264_encode_queue = collections.deque(maxlen=2)
    s._h264_encode_queue.append({"keyframe": False, "n": 1})
    s._h264_encode_queue.append({"keyframe": False, "n": 2})
    job, dropped = s._drain_h264_encode_queue()
    assert job["n"] == 2 and dropped is False
    assert not s._h264_encode_queue


def test_keyframe_survives_delta_enqueue_manual_eviction():
    """等效 maxlen=2 手动淘汰：队首关键帧永不淘汰，delta latest-wins。"""
    s = RemoteDesktopSession.__new__(RemoteDesktopSession)
    s._h264_keyframe_requested = False
    s._h264_encode_queue = collections.deque(maxlen=8)

    class FakeShot:
        def __init__(self):
            self.closed = False

        def close(self):
            self.closed = True

    kf_shot, d1_shot, d2_shot = FakeShot(), FakeShot(), FakeShot()
    s._enqueue_h264_encode_job({"keyframe": True, "n": 0, "screenshot": kf_shot})
    s._enqueue_h264_encode_job({"keyframe": False, "n": 1, "screenshot": d1_shot})
    s._enqueue_h264_encode_job({"keyframe": False, "n": 2, "screenshot": d2_shot})
    assert [j["n"] for j in s._h264_encode_queue] == [0, 2]
    assert d1_shot.closed is True  # 被淘汰的 delta 截图已释放
    assert kf_shot.closed is False and d2_shot.closed is False
    job, _ = s._drain_h264_encode_queue()
    assert job["n"] == 0


def test_new_keyframe_enqueue_drops_stale_jobs():
    """新关键帧入队：滞留 job（旧 delta/旧关键帧）全部过时，清空后插队。"""
    s = RemoteDesktopSession.__new__(RemoteDesktopSession)
    s._h264_keyframe_requested = False
    s._h264_encode_queue = collections.deque(maxlen=8)

    class FakeShot:
        def __init__(self):
            self.closed = False

        def close(self):
            self.closed = True

    stale_shots = [FakeShot() for _ in range(3)]
    s._h264_encode_queue.append({"keyframe": False, "n": 1, "screenshot": stale_shots[0]})
    s._h264_encode_queue.append({"keyframe": True, "n": 2, "screenshot": stale_shots[1]})
    s._h264_encode_queue.append({"keyframe": False, "n": 3, "screenshot": stale_shots[2]})
    s._enqueue_h264_encode_job({"keyframe": True, "n": 9, "screenshot": FakeShot()})
    assert [j["n"] for j in s._h264_encode_queue] == [9]
    assert all(shot.closed for shot in stale_shots)


def test_delta_enqueue_without_keyframe_evicts_oldest():
    """无关键帧时保持 latest-wins：队满淘汰最旧 delta。"""
    s = RemoteDesktopSession.__new__(RemoteDesktopSession)
    s._h264_keyframe_requested = False
    s._h264_encode_queue = collections.deque(maxlen=8)

    class FakeShot:
        def close(self):
            pass

    s._enqueue_h264_encode_job({"keyframe": False, "n": 1, "screenshot": FakeShot()})
    s._enqueue_h264_encode_job({"keyframe": False, "n": 2, "screenshot": FakeShot()})
    s._enqueue_h264_encode_job({"keyframe": False, "n": 3, "screenshot": FakeShot()})
    assert [j["n"] for j in s._h264_encode_queue] == [2, 3]


def test_consume_order_keyframe_first_then_remaining_delta():
    """连续两轮消费：先关键帧，再 latest-wins 其余 delta。"""
    s = RemoteDesktopSession.__new__(RemoteDesktopSession)
    s._h264_keyframe_requested = False
    s._h264_encode_queue = collections.deque(maxlen=3)
    s._h264_encode_queue.append({"keyframe": False, "n": 1})
    s._h264_encode_queue.append({"keyframe": True, "n": 2})
    s._h264_encode_queue.append({"keyframe": False, "n": 3})
    job1, _ = s._drain_h264_encode_queue()
    assert job1["n"] == 2
    job2, _ = s._drain_h264_encode_queue()
    assert job2["n"] == 3
