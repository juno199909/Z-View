# -*- coding: utf-8 -*-
"""画质预设 → H264 缩放上限 单元测试。"""
import sys
from pathlib import Path

sys.path.insert(0, r"D:\IT2026\IT2026\IT2026\IT2026")
from PIL import Image  # noqa: E402

from remote_desktop_engine_v2 import RemoteDesktopSession  # noqa: E402


def make_session(scale, override):
    s = RemoteDesktopSession.__new__(RemoteDesktopSession)
    s._h264_scale = scale
    s._h264_scale_override = override
    return s


def test_override_ceil_high_preset_full_scale():
    s = make_session(1.0, 1.0)
    img = Image.new("RGB", (200, 100))
    assert s._apply_h264_scale(img).size == (200, 100)


def test_override_smooth_caps_at_70():
    s = make_session(1.0, 0.7)
    img = Image.new("RGB", (200, 100))
    assert s._apply_h264_scale(img).size == (140, 70)


def test_qos_can_still_drop_below_override():
    """弱机压力下 QoS 降到 0.6，仍低于预设上限 0.7——继续生效。"""
    s = make_session(0.6, 0.7)
    img = Image.new("RGB", (200, 100))
    assert s._apply_h264_scale(img).size == (120, 60)


def test_no_override_legacy_behavior():
    s = make_session(0.8, None)
    img = Image.new("RGB", (200, 100))
    assert s._apply_h264_scale(img).size == (160, 80)
