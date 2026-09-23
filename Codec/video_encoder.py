from __future__ import annotations

from dataclasses import dataclass


@dataclass(slots=True)
class VideoEncoderProfile:
    codec: str = "jpeg-websocket-compat"
    target_codec: str = "h264"
    hardware_acceleration: tuple[str, ...] = ("intel_qsv", "nvidia_nvenc", "amd_amf")
    implementation: str = "legacy_jpeg_encoder"
    target_pipeline: str = "h264_hw_acceleration"
    adaptive_bitrate: bool = True

    def to_dict(self) -> dict:
        return {
            "codec": self.codec,
            "target_codec": self.target_codec,
            "hardware_acceleration": list(self.hardware_acceleration),
            "implementation": self.implementation,
            "target_pipeline": self.target_pipeline,
            "adaptive_bitrate": self.adaptive_bitrate,
        }
