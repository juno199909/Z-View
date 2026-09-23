"""H.264 流式编码器（PyAV/FFmpeg）。

RustDesk 阶段二改造：以 H.264 取代逐帧 JPEG。
- 后端探测：h264_nvenc → h264_qsv → libx264（软编兜底，探测结果做真实编码验证并缓存）
- 输出 Annex-B 裸流（WebCodecs VideoDecoder 不带 description 时即为 Annex B）
- 分辨率变化自动重建；force_keyframe 通过重建编码器实现（首个包必为 IDR）
- 线程约束：encode() 仅允许单线程调用（capture 线程）
"""

from __future__ import annotations

import io
import logging
import threading
import time
from fractions import Fraction
from typing import Any

logger = logging.getLogger(__name__)

try:
    import numpy as np
except ImportError:  # pragma: no cover
    np = None

try:
    import av
except ImportError:  # pragma: no cover
    av = None

_HW_PROBE_CACHE: dict[str, Any] = {}
_HW_PROBE_LOCK = threading.Lock()
_HW_PROBE_BACKENDS = ("h264_nvenc", "h264_qsv", "h264_amf")


def _probe_hardware_encoders() -> dict[str, bool]:
    """真实编码一帧来探测硬编可用性（结果进程级缓存）。"""
    with _HW_PROBE_LOCK:
        if _HW_PROBE_CACHE:
            return dict(_HW_PROBE_CACHE)
        result: dict[str, bool] = {}
        if av is None or np is None:
            _HW_PROBE_CACHE.update({name: False for name in _HW_PROBE_BACKENDS})
            return dict(_HW_PROBE_CACHE)
        black = np.zeros((64, 64, 3), dtype=np.uint8)
        for name in _HW_PROBE_BACKENDS:
            ok = False
            ctx = None
            try:
                ctx = av.CodecContext.create(name, "w")
                ctx.width = 64
                ctx.height = 64
                ctx.pix_fmt = "yuv420p"
                ctx.time_base = Fraction(1, 30)
                ctx.framerate = Fraction(30, 1)
                frame = av.VideoFrame.from_ndarray(black, format="rgb24")
                for _packet in ctx.encode(frame):
                    ok = True
                for _packet in ctx.encode(None):
                    ok = True
            except Exception:
                ok = False
            finally:
                try:
                    if ctx is not None:
                        ctx.close()
                except Exception:
                    pass
            result[name] = ok
        _HW_PROBE_CACHE.update(result)
        return dict(_HW_PROBE_CACHE)


def get_h264_backend_name() -> str:
    """返回可用的 H.264 后端名（无 PyAV 时返回空串）。"""
    if av is None or np is None:
        return ""
    probed = _probe_hardware_encoders()
    for name in _HW_PROBE_BACKENDS:
        if probed.get(name):
            return name
    return "libx264"


def h264_available() -> bool:
    return bool(get_h264_backend_name())


def _hardware_bitrate_for_crf(crf: int) -> int:
    """Map the shared quality scale to hardware-encoder bitrates.

    Remote-desktop text has many hard edges and does not tolerate the video
    bitrates that look acceptable for camera footage.  The high preset is for
    a trusted LAN and therefore reserves enough bitrate for a 1080p60 desktop.
    """
    crf = max(16, min(36, int(crf)))
    if crf <= 17:
        return 28_000_000
    if crf <= 20:
        return 16_000_000
    if crf <= 23:
        return 10_000_000
    if crf <= 27:
        return 4_000_000
    if crf <= 31:
        return 2_500_000
    return 1_500_000


def _prepend_stream_header(pkts: list[dict[str, Any]], extradata: Any) -> list[dict[str, Any]]:
    """将 extradata（SPS/PPS）前置到首个关键帧包 data 前（纯函数，可测）。

    防御场景：硬编后端（nvenc/qsv/amf）重建后首包可能不含 in-band SPS/PPS
    （仅 extradata 携带），观看端 reset 后首 chunk 将无法解码。前置后保证
    首个关键帧包自含参数集。libx264 首包已含 in-band 参数集，extradata
    前置不适用（调用方不传入即可）。
    """
    if not extradata or not pkts:
        return pkts
    header = bytes(extradata)
    if not header:
        return pkts
    out = list(pkts)
    idx = next((i for i, p in enumerate(out) if p.get("keyframe")), 0)
    first = out[idx]
    out[idx] = {
        **first,
        "data": header + bytes(first.get("data") or b""),
    }
    return out


class H264StreamEncoder:
    """单会话 H.264 流式编码器。

    encode(pil_image) 返回 [{"data": bytes, "keyframe": bool}, ...]（Annex-B）。
    """

    def __init__(self, width: int, height: int, fps: int = 30, *, crf: int = 26):
        self.width = int(width)
        self.height = int(height)
        self.fps = max(1, int(fps))
        self._crf = int(crf)
        self._codec_name = ""
        self._ctx: Any = None
        self._pts = 0
        self._lock = threading.Lock()
        self._last_metrics: dict[str, float | str | bool] = {}
        self._target_bitrate_bps = 0
        self._open()

    # ---------- 内部 ----------

    def _open(self) -> None:
        if av is None or np is None:
            raise RuntimeError("pyav/numpy unavailable")
        codec_name = get_h264_backend_name()
        if not codec_name:
            raise RuntimeError("no h264 backend")
        ctx = av.CodecContext.create(codec_name, "w")
        ctx.width = int(self.width)
        ctx.height = int(self.height)
        ctx.pix_fmt = "yuv420p"
        ctx.time_base = Fraction(1, self.fps)
        ctx.framerate = Fraction(self.fps, 1)
        ctx.gop_size = max(2 * self.fps, 60)  # 约 2 秒一个 IDR
        ctx.max_b_frames = 0  # 低延迟：无 B 帧
        if codec_name == "libx264":
            # 低 CRF（高清档）用 superfast 提升压缩效率/清晰度；高 CRF 保持 ultrafast 保帧率
            x264_preset = "superfast" if self._crf <= 20 else "ultrafast"
            ctx.options = {
                "preset": x264_preset,
                "tune": "zerolatency",
                "crf": str(self._crf),
                "threads": "4",
            }
        else:
            # 硬编按码率控制（CRF 语义不同）：质量档位映射码率
            self._target_bitrate_bps = _hardware_bitrate_for_crf(self._crf)
            ctx.bit_rate = self._target_bitrate_bps
            # p1 optimizes throughput above all else.  p4 retains low-latency
            # operation (B-frames stay disabled below) while spending enough
            # RDO on desktop text, icons, and thin UI borders.
            ctx.options = {"preset": "p4", "tune": "ll", "async_depth": "1"}
        self._ctx = ctx
        self._codec_name = codec_name
        self._pts = 0
        # P0-H7 防御：硬编后端首包可能不含 in-band SPS/PPS，捕获 extradata
        # 并在首个关键帧包前置（libx264 首包已含 in-band 参数集，不受影响）
        self._hw_stream_header: Any = None
        self._hw_header_emitted = False
        if codec_name in _HW_PROBE_BACKENDS:
            try:
                extradata = getattr(ctx, "extradata", None)
                if extradata:
                    self._hw_stream_header = bytes(extradata)
            except Exception:
                self._hw_stream_header = None

    def _close(self) -> None:
        try:
            if self._ctx is not None:
                self._ctx.close()
        except Exception:
            pass
        self._ctx = None

    def _ensure_size(self, width: int, height: int) -> None:
        if int(width) != self.width or int(height) != self.height:
            self.width = int(width)
            self.height = int(height)
            self._close()
            self._open()

    # ---------- 公开 ----------

    def force_keyframe(self) -> None:
        """下一帧强制 IDR（通过重建编码器实现，重建耗时毫秒级）。"""
        with self._lock:
            self._close()
            self._open()

    def set_crf(self, crf: int) -> None:
        """QoS 动态码率：调整质量档位（重建编码器，下一帧自动为 IDR）。

        libx264：CRF 直接生效；硬编后端：映射为码率档位。
        """
        crf = max(16, min(36, int(crf)))
        with self._lock:
            if crf == self._crf:
                return
            self._crf = crf
            self._close()
            self._open()

    @property
    def crf(self) -> int:
        return self._crf

    def _attach_hw_stream_header(self, packets: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """硬编防御：未输出过流头时将 extradata 前置到首个关键帧包。

        extradata 在 _open 时可能尚未就绪（PyAV 惰性 open），这里从 ctx
        现取兜底；一旦成功前置即置 _hw_header_emitted（重建编码器后重置，
        新 SPS/PPS 随新 IDR 重新下发）。
        """
        if not self.is_hardware or self._hw_header_emitted:
            return packets
        header = self._hw_stream_header
        if not header and self._ctx is not None:
            try:
                header = getattr(self._ctx, "extradata", None)
            except Exception:
                header = None
        packets = _prepend_stream_header(packets, header)
        if header:
            self._hw_header_emitted = True
        return packets

    def encode(self, pil_image: Any, *, keyframe: bool = False) -> list[dict[str, Any]]:
        """编码一帧 PIL RGB 图像，返回 Annex-B 包列表。"""
        if self._ctx is None:
            raise RuntimeError("encoder closed")
        width, height = pil_image.width, pil_image.height
        with self._lock:
            if keyframe and (self._pts > 0 or self._codec_name == "libx264"):
                # 请求关键帧：重建编码器（下一帧必为 IDR）
                self._close()
                self._open()
            self._ensure_size(width, height)

            converted = None
            try:
                started_at = time.perf_counter()
                if pil_image.mode != "RGB":
                    converted = pil_image.convert("RGB")
                    pil_image = converted
                input_ready_at = time.perf_counter()
                arr = np.asarray(pil_image, dtype=np.uint8)
                frame = av.VideoFrame.from_ndarray(arr, format="rgb24")
                # RGB→YUV 用 BT.709 矩阵并写入 VUI 元数据：浏览器解码高清流默认按
                # BT.709 渲染，PyAV 默认 601 矩阵会造成色相偏移（远程桌面"颜色不对"根因）
                frame = frame.reformat(format="yuv420p", src_colorspace="itu709")
                frame.color_primaries = 1  # BT.709
                frame.color_trc = 1  # BT.709
                frame.colorspace = 1  # BT.709
                frame.color_range = 1  # limited (mpeg)
                frame.pts = self._pts
                frame.time_base = Fraction(1, self.fps)
                self._pts += 1
                converted_at = time.perf_counter()
                packets: list[dict[str, Any]] = []
                for packet in self._ctx.encode(frame):
                    data = bytes(packet)
                    if not data:
                        continue
                    keyframe_flag = bool(packet.is_keyframe)
                    packets.append({"data": data, "keyframe": keyframe_flag})
                encoded_at = time.perf_counter()
                self._last_metrics = {
                    "backend": self._codec_name,
                    "hardware": self.is_hardware,
                    "input_ms": round((input_ready_at - started_at) * 1000, 3),
                    "convert_ms": round((converted_at - input_ready_at) * 1000, 3),
                    "codec_ms": round((encoded_at - converted_at) * 1000, 3),
                    "total_ms": round((encoded_at - started_at) * 1000, 3),
                    "bitrate_bps": self._target_bitrate_bps,
                }
                return self._attach_hw_stream_header(packets)
            finally:
                if converted is not None:
                    converted.close()

    def flush(self) -> list[dict[str, Any]]:
        """冲刷编码器（会话结束时调用）。"""
        if self._ctx is None:
            return []
        with self._lock:
            try:
                return [{"data": bytes(p), "keyframe": bool(p.is_keyframe)} for p in self._ctx.encode(None)]
            except Exception:
                return []

    def close(self) -> None:
        with self._lock:
            self._close()

    @property
    def codec_name(self) -> str:
        return self._codec_name

    @property
    def last_metrics(self) -> dict[str, float | str | bool]:
        return dict(self._last_metrics)

    @property
    def is_hardware(self) -> bool:
        return self._codec_name in _HW_PROBE_BACKENDS

    @property
    def target_bitrate_bps(self) -> int:
        return int(self._target_bitrate_bps)
