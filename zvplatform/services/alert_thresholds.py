# -*- coding: utf-8 -*-
"""告警阈值可配置化（P1 告警中心，V1.9.0）。

阈值存 alert_threshold_config 单例行（id=1），列为 NULL 时回落
zvplatform.constants.ALERT_THRESHOLDS 默认值。控制台 GET/PUT 读写，
告警评估（build_current_alerts）每轮同步读取生效值。
"""
from __future__ import annotations

from zvplatform.constants import ALERT_OFFLINE_SECONDS, ALERT_THRESHOLDS

_TABLE = "alert_threshold_config"

# 可配置键（列名与默认值），NULL = 使用默认
_COLUMNS = {
    "cpu_warning": float(ALERT_THRESHOLDS["cpu"]["warning"]),
    "cpu_critical": float(ALERT_THRESHOLDS["cpu"]["critical"]),
    "memory_warning": float(ALERT_THRESHOLDS["memory"]["warning"]),
    "memory_critical": float(ALERT_THRESHOLDS["memory"]["critical"]),
    "disk_warning": float(ALERT_THRESHOLDS["disk"]["warning"]),
    "disk_critical": float(ALERT_THRESHOLDS["disk"]["critical"]),
    "health_warning": float(ALERT_THRESHOLDS["health"]["warning"]),
    "health_critical": float(ALERT_THRESHOLDS["health"]["critical"]),
    "offline_seconds": float(ALERT_OFFLINE_SECONDS),
}


def ensure_threshold_config_table(conn) -> None:
    cursor = conn.cursor()
    try:
        columns = ",\n".join(f"{name} FLOAT NULL" for name in _COLUMNS)
        cursor.execute(
            f"CREATE TABLE IF NOT EXISTS {_TABLE} ("
            "id INT PRIMARY KEY, "
            f"{columns}, "
            "updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP"
            ")"
        )
        cursor.execute(f"INSERT IGNORE INTO {_TABLE} (id) VALUES (1)")
        conn.commit()
    finally:
        cursor.close()


def get_threshold_config(conn) -> dict:
    """原始配置行（NULL = 未覆盖）。"""
    ensure_threshold_config_table(conn)
    cursor = conn.cursor(dictionary=True)
    try:
        cursor.execute(f"SELECT * FROM {_TABLE} WHERE id = 1")
        return cursor.fetchone() or {}
    finally:
        cursor.close()


def get_effective_thresholds(conn) -> dict:
    """生效阈值：DB 覆盖值优先，NULL 回落默认。形状与 ALERT_THRESHOLDS 一致。"""
    row = get_threshold_config(conn)

    def pick(name: str, default: float) -> float:
        value = row.get(name)
        try:
            value = float(value)
        except (TypeError, ValueError):
            return default
        return value

    offline_seconds = pick("offline_seconds", float(ALERT_OFFLINE_SECONDS))
    return {
        "cpu": {
            "warning": pick("cpu_warning", float(ALERT_THRESHOLDS["cpu"]["warning"])),
            "critical": pick("cpu_critical", float(ALERT_THRESHOLDS["cpu"]["critical"])),
        },
        "memory": {
            "warning": pick("memory_warning", float(ALERT_THRESHOLDS["memory"]["warning"])),
            "critical": pick("memory_critical", float(ALERT_THRESHOLDS["memory"]["critical"])),
        },
        "disk": {
            "warning": pick("disk_warning", float(ALERT_THRESHOLDS["disk"]["warning"])),
            "critical": pick("disk_critical", float(ALERT_THRESHOLDS["disk"]["critical"])),
        },
        "health": {
            "warning": pick("health_warning", float(ALERT_THRESHOLDS["health"]["warning"])),
            "critical": pick("health_critical", float(ALERT_THRESHOLDS["health"]["critical"])),
        },
        "offline_seconds": offline_seconds,
        # 网络类阈值暂保持默认（需要分钟窗口语义，后续开放）
        "network_loss": ALERT_THRESHOLDS["network_loss"],
        "network_traffic": ALERT_THRESHOLDS["network_traffic"],
    }


def update_threshold_config(conn, patch: dict) -> dict:
    """合并式更新（校验范围 + 同类 warning < critical 约束，基于生效值合并）。"""
    ensure_threshold_config_table(conn)
    allowed = set(_COLUMNS)
    values = {}
    for key, value in (patch or {}).items():
        if key not in allowed or value is None or value == "":
            continue
        try:
            values[key] = float(value)
        except (TypeError, ValueError):
            raise ValueError(f"{key} 必须为数字")
    # 范围校验
    for key, value in values.items():
        if key == "offline_seconds":
            if not (60 <= value <= 86400):
                raise ValueError("offline_seconds 取值范围 60~86400")
        elif not (1 <= value <= 100):
            raise ValueError(f"{key} 取值范围 1~100")
    # 同类约束：合并生效值与本次补丁后校验。
    # cpu/memory/disk 正向指标（越高越严重）：warning < critical；
    # health 反向指标（分数越低越严重）：warning > critical。
    effective = get_effective_thresholds(conn)
    for prefix in ("cpu", "memory", "disk"):
        warning = values.get(f"{prefix}_warning")
        critical = values.get(f"{prefix}_critical")
        warning = effective[prefix]["warning"] if warning is None else float(warning)
        critical = effective[prefix]["critical"] if critical is None else float(critical)
        if warning >= critical:
            raise ValueError(f"{prefix}: warning ({warning}) 必须小于 critical ({critical})")
    warning = values.get("health_warning")
    critical = values.get("health_critical")
    warning = effective["health"]["warning"] if warning is None else float(warning)
    critical = effective["health"]["critical"] if critical is None else float(critical)
    if warning <= critical:
        raise ValueError(f"health: warning ({warning}) 必须大于 critical ({critical})")

    if values:
        cursor = conn.cursor()
        try:
            columns = ", ".join(f"{key} = %s" for key in values)
            cursor.execute(
                f"UPDATE {_TABLE} SET {columns} WHERE id = 1",
                tuple(values.values()),
            )
            conn.commit()
        finally:
            cursor.close()
    return get_threshold_config(conn)

