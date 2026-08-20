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
            ctx.options = {
                "preset": "ultrafast",
                "tune": "zerolatency",
                "crf": str(self._crf),
                "threads": "4",
            }
        else:
            # 硬编按码率控制（CRF 语义不同）：质量档位映射码率
            ctx.bit_rate = {18: 8_000_000, 22: 6_000_000, 26: 4_000_000, 32: 2_000_000}.get(
                self._crf, 4_000_000
            )
            ctx.options = {"preset": "p1", "async_depth": "1"}
        self._ctx = ctx
        self._codec_name = codec_name
        self._pts = 0

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
                if pil_image.mode != "RGB":
                    converted = pil_image.convert("RGB")
                    pil_image = converted
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
                packets: list[dict[str, Any]] = []
                for packet in self._ctx.encode(frame):
                    data = bytes(packet)
                    if not data:
                        continue
                    keyframe_flag = bool(packet.is_keyframe)
                    packets.append({"data": data, "keyframe": keyframe_flag})
                return packets
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
    def is_hardware(self) -> bool:
        return self._codec_name in _HW_PROBE_BACKENDS
