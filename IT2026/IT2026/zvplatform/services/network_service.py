# -*- coding: utf-8 -*-
"""终端网络监控服务（第一阶段）。

职责（需求 §17）：接收 → 存储（1 分钟粒度聚合）→ 查询 → 告警评估。
Agent 只负责采集/计算/上报，本模块不做任何采集。
写入策略：实时状态单行 upsert（network_state），历史按分钟桶 upsert（last-write-wins），
绝不做 5-10 秒级逐条写库。
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Dict, List, Optional

from console_utils import safe_console_print
from zvplatform.constants import (
    ALERT_THRESHOLDS,
    NETWORK_HISTORY_RETENTION_DAYS,
    NETWORK_STALE_SECONDS,
)

_TABLES_READY = False

INTERFACE_TYPES = ("ethernet", "wifi", "vpn", "virtual", "other")


def ensure_network_tables(conn) -> None:
    """幂等建表（进程内仅首次执行 DDL）。"""
    global _TABLES_READY
    if _TABLES_READY:
        return
    cursor = conn.cursor()
    try:
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS network_interfaces (
                id BIGINT UNSIGNED PRIMARY KEY AUTO_INCREMENT,
                asset_id BIGINT UNSIGNED NOT NULL,
                interface_name VARCHAR(100) NOT NULL,
                description VARCHAR(255) NULL,
                interface_type VARCHAR(20) NOT NULL DEFAULT 'other',
                mac_address VARCHAR(50) NULL,
                ipv4 VARCHAR(64) NULL,
                ipv6 VARCHAR(255) NULL,
                gateway VARCHAR(64) NULL,
                dns VARCHAR(255) NULL,
                link_speed BIGINT NULL COMMENT 'Mbps, NULL=unavailable',
                status VARCHAR(10) NOT NULL DEFAULT 'down',
                is_primary TINYINT(1) NOT NULL DEFAULT 0,
                last_seen DATETIME NULL,
                created_at DATETIME NOT NULL,
                updated_at DATETIME NOT NULL,
                UNIQUE KEY uk_asset_iface (asset_id, interface_name),
                INDEX idx_asset (asset_id)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS network_traffic_stats (
                id BIGINT UNSIGNED PRIMARY KEY AUTO_INCREMENT,
                asset_id BIGINT UNSIGNED NOT NULL,
                interface_id BIGINT UNSIGNED NOT NULL,
                ts DATETIME NOT NULL COMMENT '分钟桶',
                rx_bytes BIGINT NULL,
                tx_bytes BIGINT NULL,
                rx_packets BIGINT NULL,
                tx_packets BIGINT NULL,
                rx_rate BIGINT NULL COMMENT 'bytes/s',
                tx_rate BIGINT NULL COMMENT 'bytes/s',
                rx_errors INT NULL,
                tx_errors INT NULL,
                rx_drops INT NULL,
                tx_drops INT NULL,
                created_at DATETIME NOT NULL,
                UNIQUE KEY uk_iface_ts (interface_id, ts),
                INDEX idx_asset_ts (asset_id, ts)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS network_quality_stats (
                id BIGINT UNSIGNED PRIMARY KEY AUTO_INCREMENT,
                asset_id BIGINT UNSIGNED NOT NULL,
                ts DATETIME NOT NULL COMMENT '分钟桶',
                gateway_rtt_ms INT NULL,
                dns_rtt_ms INT NULL,
                internet_rtt_ms INT NULL,
                packet_loss_pct FLOAT NULL,
                created_at DATETIME NOT NULL,
                UNIQUE KEY uk_asset_ts (asset_id, ts)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS network_state (
                asset_id BIGINT UNSIGNED PRIMARY KEY,
                primary_interface VARCHAR(100) NULL,
                rx_rate BIGINT NULL COMMENT 'bytes/s 全局(非虚拟)',
                tx_rate BIGINT NULL,
                interfaces_json JSON NULL,
                quality_json JSON NULL,
                reported_at DATETIME NULL,
                updated_at DATETIME NOT NULL
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """)
        conn.commit()
        _TABLES_READY = True
    except Exception:
        conn.rollback()
        raise
    finally:
        cursor.close()


def _parse_json_field(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, (list, dict)):
        return value
    if isinstance(value, str):
        try:
            return json.loads(value)
        except (ValueError, TypeError):
            return None
    return None


def _minute_bucket(ts_value: Any) -> datetime:
    if isinstance(ts_value, datetime):
        dt = ts_value
    elif isinstance(ts_value, (int, float)):
        dt = datetime.fromtimestamp(int(ts_value))
    else:
        dt = datetime.now()
    return dt.replace(second=0, microsecond=0)


def ingest_network_report(cursor, conn, asset_id: int, network: Dict[str, Any]) -> None:
    """接收 Agent network 上报：实时状态 + 网卡表 + 分钟粒度历史。任何异常仅记录不中断心跳。"""
    if not isinstance(network, dict) or not network:
        return

    interfaces = _parse_json_field(network.get("interfaces")) or []
    quality = _parse_json_field(network.get("quality")) or {}
    primary_name = network.get("primary")

    ensure_network_tables(conn)

    reported_names: List[str] = []
    iface_id_by_name: Dict[str, int] = {}

    for itf in interfaces:
        if not isinstance(itf, dict):
            continue
        name = str(itf.get("interface_name") or "").strip()[:100]
        if not name:
            continue
        reported_names.append(name)
        nic_type = str(itf.get("interface_type") or "other")
        if nic_type not in INTERFACE_TYPES:
            nic_type = "other"
        cursor.execute("""
            INSERT INTO network_interfaces (
                asset_id, interface_name, description, interface_type, mac_address,
                ipv4, ipv6, gateway, dns, link_speed, status, is_primary,
                last_seen, created_at, updated_at
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, NOW(), NOW(), NOW())
            ON DUPLICATE KEY UPDATE
                description = VALUES(description),
                interface_type = VALUES(interface_type),
                mac_address = VALUES(mac_address),
                ipv4 = VALUES(ipv4),
                ipv6 = VALUES(ipv6),
                gateway = VALUES(gateway),
                dns = VALUES(dns),
                link_speed = VALUES(link_speed),
                status = VALUES(status),
                is_primary = VALUES(is_primary),
                last_seen = NOW(),
                updated_at = NOW()
        """, (
            asset_id, name,
            (str(itf.get("description"))[:255] if itf.get("description") else None),
            nic_type,
            itf.get("mac_address"),
            itf.get("ipv4"),
            (str(itf.get("ipv6"))[:255] if itf.get("ipv6") else None),
            itf.get("gateway"),
            (str(itf.get("dns"))[:255] if itf.get("dns") else None),
            itf.get("link_speed"),
            "up" if itf.get("status") == "up" else "down",
            1 if primary_name == name else 0,
        ))
        if cursor.lastrowid:
            iface_id_by_name[name] = int(cursor.lastrowid)

    # 本轮未上报的网卡视为已消失
    if reported_names:
        placeholders = ",".join(["%s"] * len(reported_names))
        cursor.execute(f"""
            UPDATE network_interfaces
            SET status = 'down', is_primary = 0, updated_at = NOW()
            WHERE asset_id = %s AND interface_name NOT IN ({placeholders})
        """, (asset_id, *reported_names))

    if not iface_id_by_name:
        cursor.execute("SELECT id, interface_name FROM network_interfaces WHERE asset_id = %s", (asset_id,))
        for row in cursor.fetchall():
            iface_id_by_name[row["interface_name"]] = int(row["id"])

    # 实时状态（单行 upsert）
    cursor.execute("""
        INSERT INTO network_state (
            asset_id, primary_interface, rx_rate, tx_rate,
            interfaces_json, quality_json, reported_at, updated_at
        ) VALUES (%s, %s, %s, %s, %s, %s, NOW(), NOW())
        ON DUPLICATE KEY UPDATE
            primary_interface = VALUES(primary_interface),
            rx_rate = VALUES(rx_rate),
            tx_rate = VALUES(tx_rate),
            interfaces_json = VALUES(interfaces_json),
            quality_json = VALUES(quality_json),
            reported_at = NOW(),
            updated_at = NOW()
    """, (
        asset_id,
        primary_name,
        _safe_int(network.get("rx_rate")),
        _safe_int(network.get("tx_rate")),
        json.dumps(interfaces, ensure_ascii=False),
        json.dumps(quality, ensure_ascii=False),
    ))

    # 分钟粒度历史（last-write-wins，每网卡每分钟一行）
    bucket = _minute_bucket(network.get("ts"))
    for itf in interfaces:
        name = str(itf.get("interface_name") or "").strip()[:100]
        iface_id = iface_id_by_name.get(name)
        if not iface_id or itf.get("rx_bytes") is None:
            continue
        cursor.execute("""
            INSERT INTO network_traffic_stats (
                asset_id, interface_id, ts, rx_bytes, tx_bytes,
                rx_packets, tx_packets, rx_rate, tx_rate,
                rx_errors, tx_errors, rx_drops, tx_drops, created_at
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, NOW())
            ON DUPLICATE KEY UPDATE
                rx_bytes = VALUES(rx_bytes), tx_bytes = VALUES(tx_bytes),
                rx_packets = VALUES(rx_packets), tx_packets = VALUES(tx_packets),
                rx_rate = VALUES(rx_rate), tx_rate = VALUES(tx_rate),
                rx_errors = VALUES(rx_errors), tx_errors = VALUES(tx_errors),
                rx_drops = VALUES(rx_drops), tx_drops = VALUES(tx_drops)
        """, (
            asset_id, iface_id, bucket,
            _safe_int(itf.get("rx_bytes")), _safe_int(itf.get("tx_bytes")),
            _safe_int(itf.get("rx_packets")), _safe_int(itf.get("tx_packets")),
            _safe_int(itf.get("rx_rate")), _safe_int(itf.get("tx_rate")),
            _safe_int(itf.get("rx_errors")), _safe_int(itf.get("tx_errors")),
            _safe_int(itf.get("rx_drops")), _safe_int(itf.get("tx_drops")),
        ))

    # 网络质量分钟桶
    if quality:
        cursor.execute("""
            INSERT INTO network_quality_stats (
                asset_id, ts, gateway_rtt_ms, dns_rtt_ms, internet_rtt_ms, packet_loss_pct, created_at
            ) VALUES (%s, %s, %s, %s, %s, %s, NOW())
            ON DUPLICATE KEY UPDATE
                gateway_rtt_ms = VALUES(gateway_rtt_ms),
                dns_rtt_ms = VALUES(dns_rtt_ms),
                internet_rtt_ms = VALUES(internet_rtt_ms),
                packet_loss_pct = VALUES(packet_loss_pct)
        """, (
            asset_id, bucket,
            _safe_int(quality.get("gateway_rtt_ms")),
            _safe_int(quality.get("dns_rtt_ms")),
            _safe_int(quality.get("internet_rtt_ms")),
            quality.get("packet_loss_pct"),
        ))

    _maybe_cleanup(cursor, conn)


def _safe_int(value: Any) -> Optional[int]:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _safe_float(value: Any) -> Optional[float]:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _maybe_cleanup(cursor, conn) -> None:
    """约每 40 次上报执行一次历史清理（7 天保留）。"""
    now = datetime.now()
    if now.minute % 7 != 0 or now.second >= 12:
        return
    try:
        cursor.execute(
            "DELETE FROM network_traffic_stats WHERE ts < NOW() - INTERVAL %d DAY"
            % NETWORK_HISTORY_RETENTION_DAYS
        )
        cursor.execute(
            "DELETE FROM network_quality_stats WHERE ts < NOW() - INTERVAL %d DAY"
            % NETWORK_HISTORY_RETENTION_DAYS
        )
        conn.commit()
    except Exception as exc:
        safe_console_print(f"[Network] history cleanup failed: {exc}")


def get_network_live_state(conn, asset_id: int) -> Optional[Dict[str, Any]]:
    """实时网络状态（network_state 单行 + 网卡表兜底）。"""
    ensure_network_tables(conn)
    cursor = conn.cursor(dictionary=True)
    try:
        cursor.execute("SELECT * FROM network_state WHERE asset_id = %s", (asset_id,))
        state = cursor.fetchone()
        cursor.execute("""
            SELECT id, interface_name, description, interface_type, mac_address,
                   ipv4, ipv6, gateway, dns, link_speed, status, is_primary, last_seen
            FROM network_interfaces WHERE asset_id = %s
            ORDER BY is_primary DESC, status DESC, interface_name
        """, (asset_id,))
        interfaces = cursor.fetchall()
        if not state and not interfaces:
            return None
        stale = False
        if state and state.get("reported_at"):
            stale = (datetime.now() - state["reported_at"]).total_seconds() > NETWORK_STALE_SECONDS
        return {
            "asset_id": asset_id,
            "primary_interface": (state or {}).get("primary_interface"),
            "rx_rate": (state or {}).get("rx_rate"),
            "tx_rate": (state or {}).get("tx_rate"),
            "quality": _parse_json_field((state or {}).get("quality_json")) or {},
            "interfaces": interfaces,
            "interfaces_live": _parse_json_field((state or {}).get("interfaces_json")) or [],
            "reported_at": (state or {}).get("reported_at").strftime("%Y-%m-%d %H:%M:%S") if (state or {}).get("reported_at") else None,
            "stale": stale,
        }
    finally:
        cursor.close()


def get_network_history(conn, asset_id: int, range_seconds: int = 900) -> Dict[str, Any]:
    """历史趋势（分钟粒度）：聚合非虚拟网卡速率 + 网络质量序列。"""
    ensure_network_tables(conn)
    range_seconds = max(60, min(int(range_seconds), 86400))
    cursor = conn.cursor(dictionary=True)
    try:
        cursor.execute(f"""
            SELECT s.ts, SUM(COALESCE(s.rx_rate, 0)) AS rx_rate, SUM(COALESCE(s.tx_rate, 0)) AS tx_rate,
                   SUM(COALESCE(s.rx_bytes, 0)) AS rx_bytes, SUM(COALESCE(s.tx_bytes, 0)) AS tx_bytes
            FROM network_traffic_stats s
            JOIN network_interfaces i ON i.id = s.interface_id
            WHERE s.asset_id = %s AND s.ts >= NOW() - INTERVAL {range_seconds} SECOND
              AND i.interface_type IN ('ethernet', 'wifi')
            GROUP BY s.ts ORDER BY s.ts
        """, (asset_id,))
        series = [
            {
                "ts": row["ts"].strftime("%Y-%m-%d %H:%M") if row.get("ts") else None,
                "rx_rate": int(row["rx_rate"] or 0),
                "tx_rate": int(row["tx_rate"] or 0),
                "rx_bytes": int(row["rx_bytes"] or 0),
                "tx_bytes": int(row["tx_bytes"] or 0),
            }
            for row in cursor.fetchall()
        ]
        cursor.execute(f"""
            SELECT ts, gateway_rtt_ms, dns_rtt_ms, internet_rtt_ms, packet_loss_pct
            FROM network_quality_stats
            WHERE asset_id = %s AND ts >= NOW() - INTERVAL {range_seconds} SECOND
            ORDER BY ts
        """, (asset_id,))
        quality = [
            {
                "ts": row["ts"].strftime("%Y-%m-%d %H:%M") if row.get("ts") else None,
                "gateway_rtt_ms": row.get("gateway_rtt_ms"),
                "dns_rtt_ms": row.get("dns_rtt_ms"),
                "internet_rtt_ms": row.get("internet_rtt_ms"),
                "packet_loss_pct": None if row.get("packet_loss_pct") is None else float(row["packet_loss_pct"]),
            }
            for row in cursor.fetchall()
        ]
        return {"range_seconds": range_seconds, "series": series, "quality": quality}
    finally:
        cursor.close()


def build_network_alerts(conn) -> List[Dict[str, Any]]:
    """基于 network_state/quality 的第一阶段异常检测（断网/丢包/高流量），恢复由 fingerprint 自动 resolve。"""
    ensure_network_tables(conn)
    alerts: List[Dict[str, Any]] = []
    cursor = conn.cursor(dictionary=True)
    try:
        cursor.execute("""
            SELECT ns.asset_id, a.hostname, a.ip_address, ns.primary_interface,
                   ns.rx_rate, ns.tx_rate, ns.interfaces_json, ns.quality_json, ns.reported_at
            FROM network_state ns
            JOIN assets a ON a.id = ns.asset_id AND a.deleted_at IS NULL
        """)
        rows = cursor.fetchall()
        now = datetime.now()
        for row in rows:
            asset_id = row["asset_id"]
            hostname = row.get("hostname") or f"资产 {asset_id}"
            if row.get("reported_at") and (now - row["reported_at"]).total_seconds() > NETWORK_STALE_SECONDS:
                continue  # 数据过期不评估（离线告警已覆盖）

            interfaces = _parse_json_field(row.get("interfaces_json")) or []
            for itf in interfaces:
                if not isinstance(itf, dict):
                    continue
                if itf.get("interface_name") == row.get("primary_interface") and itf.get("status") != "up":
                    alerts.append({
                        "asset_id": asset_id,
                        "alert_type": "network_down",
                        "severity": "critical",
                        "message": f"{hostname} 主网卡 {itf.get('interface_name')} 已断开",
                        "current_value": 0.0,
                        "threshold_value": 0.0,
                        "details_json": {
                            "hostname": hostname,
                            "ip_address": row.get("ip_address"),
                            "interface": itf.get("interface_name"),
                            "ipv4": itf.get("ipv4"),
                        },
                        "active_fingerprint": f"{asset_id}:network_down:{itf.get('interface_name')}",
                    })

            quality = _parse_json_field(row.get("quality_json")) or {}
            loss = _safe_float(quality.get("packet_loss_pct"))
            threshold_loss = _safe_float(ALERT_THRESHOLDS.get("network_loss", {}).get("warning", 10.0)) or 10.0
            if loss is not None and loss >= threshold_loss:
                loss_minutes = int(ALERT_THRESHOLDS.get("network_loss", {}).get("minutes", 3))
                cursor.execute(f"""
                    SELECT ts, packet_loss_pct FROM network_quality_stats
                    WHERE asset_id = %s ORDER BY ts DESC LIMIT {loss_minutes}
                """, (asset_id,))
                recent = cursor.fetchall()
                if (
                    len(recent) >= loss_minutes
                    and all(_safe_float(r.get("packet_loss_pct")) is not None
                            and _safe_float(r["packet_loss_pct"]) >= threshold_loss for r in recent)
                ):
                    alerts.append({
                        "asset_id": asset_id,
                        "alert_type": "network_quality",
                        "severity": "warning",
                        "message": f"{hostname} 网络丢包率持续达到 {loss:.1f}%",
                        "current_value": loss,
                        "threshold_value": threshold_loss,
                        "details_json": {
                            "hostname": hostname,
                            "ip_address": row.get("ip_address"),
                            "internet_rtt_ms": quality.get("internet_rtt_ms"),
                            "gateway_rtt_ms": quality.get("gateway_rtt_ms"),
                        },
                        "active_fingerprint": f"{asset_id}:network_quality",
                    })

            rx_rate = _safe_int(row.get("rx_rate")) or 0
            threshold_bytes = _safe_int(
                ALERT_THRESHOLDS.get("network_traffic", {}).get("warning_bytes_per_sec", 100 * 1024 * 1024)
            ) or (100 * 1024 * 1024)
            if rx_rate >= threshold_bytes:
                traffic_minutes = int(ALERT_THRESHOLDS.get("network_traffic", {}).get("minutes", 5))
                cursor.execute(f"""
                    SELECT ts, SUM(COALESCE(s.rx_rate, 0)) AS rx_rate
                    FROM network_traffic_stats s
                    JOIN network_interfaces i ON i.id = s.interface_id
                    WHERE s.asset_id = %s AND s.ts >= NOW() - INTERVAL {traffic_minutes} MINUTE
                      AND i.interface_type IN ('ethernet', 'wifi')
                    GROUP BY s.ts ORDER BY ts DESC
                """, (asset_id,))
                buckets = cursor.fetchall()
                if (
                    len(buckets) >= max(3, traffic_minutes - 1)
                    and all(_safe_int(b["rx_rate"]) is not None and _safe_int(b["rx_rate"]) >= threshold_bytes
                            for b in buckets)
                ):
                    mbps = rx_rate / (1024.0 * 1024.0)
                    alerts.append({
                        "asset_id": asset_id,
                        "alert_type": "network_traffic",
                        "severity": "warning",
                        "message": f"{hostname} 下载流量持续异常（{mbps:.1f} MB/s，已持续 {traffic_minutes} 分钟）",
                        "current_value": round(mbps, 2),
                        "threshold_value": round(threshold_bytes / (1024.0 * 1024.0), 2),
                        "details_json": {
                            "hostname": hostname,
                            "ip_address": row.get("ip_address"),
                            "rx_rate_bytes": rx_rate,
                            "sustained_minutes": traffic_minutes,
                        },
                        "active_fingerprint": f"{asset_id}:network_traffic",
                    })
        return alerts
    finally:
        cursor.close()
