# -*- coding: utf-8 -*-
"""P4-04：Prometheus /metrics 端点（zvplatform/metrics.py）。

- 简易指标注册表（计数器/直方图摘要），线程安全
- Prometheus 文本格式输出（GET /metrics，免认证）
- assets_api 挂载：app.include_router + HTTP 中间件记录请求指标
"""
from __future__ import annotations

import threading
import time
from typing import Dict, List, Tuple

# 线程安全注册表
_LOCK = threading.Lock()
_COUNTERS: Dict[Tuple[str, Tuple[Tuple[str, str], ...]], float] = {}
_HISTOGRAMS: Dict[Tuple[str, Tuple[Tuple[str, str], ...]], List[float]] = {}
_START_TIME = time.time()


def _normalize_labels(labels: Dict[str, str] | None) -> Tuple[Tuple[str, str], ...]:
    if not labels:
        return ()
    return tuple(sorted((str(k), str(v)) for k, v in labels.items()))


def inc_counter(name: str, labels: Dict[str, str] | None = None, value: float = 1.0) -> None:
    """计数器递增（HTTP 请求总数、错误总数等）。"""
    key = (name, _normalize_labels(labels))
    with _LOCK:
        _COUNTERS[key] = _COUNTERS.get(key, 0.0) + value


def observe_histogram(name: str, value: float, labels: Dict[str, str] | None = None) -> None:
    """观测值记录（HTTP 请求延迟秒等）。"""
    key = (name, _normalize_labels(labels))
    with _LOCK:
        hist = _HISTOGRAMS.setdefault(key, [])
        hist.append(value)
        # 防内存增长：每指标最多保留 5000 个样本
        if len(hist) > 5000:
            del hist[: len(hist) - 5000]


def set_gauge(name: str, value: float, labels: Dict[str, str] | None = None) -> None:
    key = (name, _normalize_labels(labels))
    with _LOCK:
        _COUNTERS[key] = value  # gauge 复用计数器存储（覆盖语义）


def render_prometheus() -> str:
    """渲染 Prometheus 文本格式（0.0.4 text format）。"""
    lines: List[str] = []
    now_ms = int(time.time() * 1000)

    # 计数器与 gauge（gauge 带 _gauge 后缀约定区分）
    seen_names = set()
    with _LOCK:
        counters_snapshot = dict(_COUNTERS)
        hists_snapshot = {k: list(v) for k, v in _HISTOGRAMS.items()}

    for (name, labels), value in sorted(counters_snapshot.items()):
        if name not in seen_names:
            lines.append(f"# TYPE {name} counter")
            seen_names.add(name)
        label_str = ",".join(f'{k}="{v}"' for k, v in labels) if labels else ""
        lines.append(f"{name}{{{label_str}}} {value} {now_ms}")

    seen_hist = set()
    for (name, labels), samples in sorted(hists_snapshot.items()):
        if not samples:
            continue
        if name not in seen_hist:
            lines.append(f"# TYPE {name} summary")
            seen_hist.add(name)
        label_str = "".join(f'{k}="{v}"' for k, v in labels) if labels else ""
        s = sorted(samples)
        for quantile, frac in ((0.5, 0.5), (0.95, 0.95), (0.99, 0.99)):
            qv = s[min(len(s) - 1, int(len(s) * frac))]
            qlabel = (label_str + "," if label_str else "") + f'quantile="{quantile}"'
            lines.append(f"{name}{{{qlabel}}} {qv} {now_ms}")
        lines.append(f"{name}_sum{{{label_str}}} {sum(samples)} {now_ms}")
        lines.append(f"{name}_count{{{label_str}}} {len(samples)} {now_ms}")

    # 运行时长
    lines.append("# TYPE zview_uptime_seconds gauge")
    lines.append(f"zview_uptime_seconds {time.time() - _START_TIME:.0f} {now_ms}")

    return "\n".join(lines) + "\n"
