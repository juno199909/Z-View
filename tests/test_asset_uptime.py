# -*- coding: utf-8 -*-
from datetime import datetime, timedelta

import zvplatform.routers.assets as assets_router


def test_current_online_segment_accumulates_across_heartbeats():
    now = datetime(2026, 9, 24, 12, 0, 0)
    times = [
        now - timedelta(minutes=5),
        now - timedelta(minutes=4),
        now - timedelta(minutes=3),
        now - timedelta(minutes=2),
        now - timedelta(seconds=30),
    ]

    start = assets_router._find_current_online_start(times, now=now)

    assert start == times[0]
    assert int((now - start).total_seconds()) == 300


def test_current_online_segment_restarts_after_offline_gap():
    now = datetime(2026, 9, 24, 12, 0, 0)
    times = [now - timedelta(minutes=20), now - timedelta(minutes=18), now - timedelta(seconds=30)]

    start = assets_router._find_current_online_start(times, now=now)

    assert start == times[-1]


def test_online_seconds_caps_heartbeat_gaps_and_clips_window():
    now = datetime(2026, 9, 24, 12, 0, 0)
    window_start = now - timedelta(minutes=10)
    times = [window_start - timedelta(minutes=2), now - timedelta(minutes=8), now - timedelta(minutes=1)]

    online = assets_router._estimate_online_seconds(
        times,
        window_start=window_start,
        window_end=now,
    )

    # The pre-window heartbeat contributes nothing; each in-window gap is capped
    # at the 90-second online threshold.
    assert online == 90 + 60


def test_format_uptime_duration_supports_days():
    assert assets_router._format_uptime_duration(90061) == "1天 1时 1分 1秒"
