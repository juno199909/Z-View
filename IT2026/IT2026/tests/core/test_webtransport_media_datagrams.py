import struct
import sys

import pytest

sys.path.insert(0, r"D:\IT2026\IT2026\IT2026\IT2026")

from webtransport_gateway import (  # noqa: E402
    AgentBridge,
    FrameAccumulator,
    MAX_MEDIA_DATAGRAM_FRAGMENTS,
    MEDIA_DATAGRAM_HEADER,
    MEDIA_DATAGRAM_MAGIC,
    MEDIA_DATAGRAM_PAYLOAD_BYTES,
    is_h264_keyframe,
    is_media_frame,
    split_media_datagrams,
)


def test_media_datagrams_preserve_payload_and_frame_metadata():
    payload = bytes(range(256)) * 10
    datagrams = split_media_datagrams(payload, 42)

    assert len(datagrams) == 3
    recovered = bytearray()
    for index, datagram in enumerate(datagrams):
        magic, frame_id, fragment_index, fragment_count = MEDIA_DATAGRAM_HEADER.unpack(
            datagram[:MEDIA_DATAGRAM_HEADER.size]
        )
        assert magic == MEDIA_DATAGRAM_MAGIC
        assert frame_id == 42
        assert fragment_index == index
        assert fragment_count == len(datagrams)
        assert len(datagram) <= MEDIA_DATAGRAM_HEADER.size + MEDIA_DATAGRAM_PAYLOAD_BYTES
        recovered.extend(datagram[MEDIA_DATAGRAM_HEADER.size:])

    assert bytes(recovered) == payload


def test_media_datagram_frame_identifier_wraps_to_uint32():
    datagram = split_media_datagrams(b"frame", 0x1_0000_0001)[0]
    _, frame_id, _, _ = struct.unpack("!4sIHH", datagram[:MEDIA_DATAGRAM_HEADER.size])
    assert frame_id == 1


def test_media_datagrams_reject_unbounded_frame_sizes():
    payload = b"x" * (MEDIA_DATAGRAM_PAYLOAD_BYTES * (MAX_MEDIA_DATAGRAM_FRAGMENTS + 1))
    with pytest.raises(ValueError, match="too many datagrams"):
        split_media_datagrams(payload, 1)


def test_only_jpeg_and_h264_binary_frames_use_datagrams():
    assert is_media_frame(b"\x02jpeg")
    assert is_media_frame(b"\x03h264")
    assert not is_media_frame(b"\x01other")
    assert not is_media_frame(b"")


def test_h264_keyframes_are_identified_from_the_wire_header():
    keyframe = struct.pack(">BIIIIB", 0x03, 1, 1920, 1080, 4, 1) + b"data"
    delta = struct.pack(">BIIIIB", 0x03, 2, 1920, 1080, 4, 0) + b"data"

    assert is_h264_keyframe(keyframe)
    assert not is_h264_keyframe(delta)


def test_frame_accumulator_accepts_raw_webtransport_application_data():
    # aioquic exposes application bytes in WebTransportStreamDataReceived.data.
    # The WebTransport stream header and session identifier are consumed already.
    accumulator = FrameAccumulator()
    frame = len(b'{"type":"ping"}').to_bytes(4, "big") + b"\x00" + b'{"type":"ping"}'

    assert accumulator.feed(frame) == [(0, b'{"type":"ping"}')]


def test_bridge_sends_raw_media_to_datagrams_and_control_to_reliable_stream():
    class FakeProtocol:
        def __init__(self):
            self.datagrams = []
            self.reliable = []
            self.keyframes = []

        def send_media_datagrams(self, stream_id, datagrams):
            self.datagrams.append((stream_id, datagrams))

        def send_wt_data(self, stream_id, payload):
            self.reliable.append((stream_id, payload))

        def send_keyframe_stream(self, stream_id, payload):
            self.keyframes.append((stream_id, payload))

    bridge = AgentBridge.__new__(AgentBridge)
    bridge.proto = FakeProtocol()
    bridge.asset_ip = "127.0.0.1"
    bridge.connect_stream_id = 0
    bridge._media_frame_id = 0
    bridge._media_frames_sent = 0
    bridge._media_datagrams_sent = 0
    bridge._media_frames_dropped = 0
    bridge._media_keyframes_reliable = 0
    bridge._last_stats_sent_at = float("inf")

    bridge.send_agent_frame(4, 1, b"\x03media")
    assert len(bridge.proto.datagrams) == 1
    assert bridge.proto.datagrams[0][0] == 0
    assert bridge.proto.reliable == []

    bridge.send_agent_frame(4, 0, b'{"type":"pong"}')
    assert bridge.proto.reliable[0][1][4] == 0

    keyframe = struct.pack(">BIIIIB", 0x03, 3, 1920, 1080, 4, 1) + b"data"
    bridge.send_agent_frame(4, 1, keyframe)
    assert bridge.proto.keyframes == [(0, keyframe)]
