# -*- coding: utf-8 -*-
"""NetworkCollector 单元测试（无需 Windows/数据库，纯计算逻辑）。"""
import os
import sys

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from network_monitor import (  # noqa: E402
    NetworkCollector,
    TrafficRateCalculator,
    classify_interface_type,
    compute_rate,
    select_primary_interface,
    snapshot_to_payload,
)


def _counter(rx, tx, **extra):
    base = {
        "rx_bytes": rx, "tx_bytes": tx,
        "rx_packets": rx // 1500, "tx_packets": tx // 1500,
        "rx_errors": 0, "tx_errors": 0, "rx_drops": 0, "tx_drops": 0,
    }
    base.update(extra)
    return base


class TestComputeRate:
    def test_normal_delta(self):
        assert compute_rate(1100, 100, 10.0) == 100.0

    def test_counter_reset_returns_none(self):
        # current < previous：计数器重置，必须返回 None 重建基线
        assert compute_rate(50, 100, 10.0) is None

    def test_zero_delta(self):
        assert compute_rate(100, 100, 10.0) == 0.0

    def test_invalid_elapsed(self):
        assert compute_rate(200, 100, 0) is None
        assert compute_rate(200, 100, -1) is None

    def test_missing_values(self):
        assert compute_rate(None, 100, 10.0) is None
        assert compute_rate(200, None, 10.0) is None


class TestTrafficRateCalculator:
    def test_first_round_builds_baseline_without_rate(self):
        calc = TrafficRateCalculator()
        rates = calc.update({"Ethernet": _counter(1000, 500)}, now=100.0)
        assert rates["Ethernet"]["rx_rate"] is None
        assert rates["Ethernet"]["tx_rate"] is None

    def test_second_round_rates(self):
        calc = TrafficRateCalculator()
        calc.update({"Ethernet": _counter(1000, 500)}, now=100.0)
        rates = calc.update({"Ethernet": _counter(11000, 5500)}, now=110.0)
        assert rates["Ethernet"]["rx_rate"] == 1000.0
        assert rates["Ethernet"]["tx_rate"] == 500.0

    def test_counter_reset_rebaselines(self):
        calc = TrafficRateCalculator()
        calc.update({"Ethernet": _counter(100_000, 50_000)}, now=100.0)
        # 计数器回退：本轮速率为 None，且以新值为基线
        rates = calc.update({"Ethernet": _counter(2_000, 1_000)}, now=110.0)
        assert rates["Ethernet"]["rx_rate"] is None
        rates = calc.update({"Ethernet": _counter(12_000, 6_000)}, now=120.0)
        assert rates["Ethernet"]["rx_rate"] == 1000.0

    def test_nic_removed_and_readded(self):
        calc = TrafficRateCalculator()
        calc.update({"Ethernet": _counter(1000, 500)}, now=100.0)
        calc.update({"Ethernet": _counter(2000, 1000)}, now=110.0)
        # 网卡消失
        rates = calc.update({}, now=120.0)
        assert rates == {}
        # 网卡重现（例如 Wi-Fi 切换回有线）：按新基线处理，不产生巨量速率
        rates = calc.update({"Ethernet": _counter(999_999_999, 1)}, now=130.0)
        assert rates["Ethernet"]["rx_rate"] is None

    def test_multiple_nics_independent(self):
        calc = TrafficRateCalculator()
        calc.update({"Ethernet": _counter(1000, 500), "Wi-Fi": _counter(10, 10)}, now=100.0)
        rates = calc.update({"Ethernet": _counter(2000, 1000), "Wi-Fi": _counter(30, 40)}, now=110.0)
        assert rates["Ethernet"]["rx_rate"] == 100.0
        assert rates["Wi-Fi"]["rx_rate"] == 2.0


class TestClassifyInterfaceType:
    def test_ethernet(self):
        assert classify_interface_type("以太网", "Realtek PCIe GbE Family Controller") == "ethernet"
        assert classify_interface_type("Ethernet", "Intel(R) I211") == "ethernet"

    def test_wifi(self):
        assert classify_interface_type("WLAN", "Intel(R) Wi-Fi 6 AX201") == "wifi"
        assert classify_interface_type("Wi-Fi", "") == "wifi"

    def test_vpn(self):
        assert classify_interface_type("TAP-Windows Adapter", "") == "vpn"

    def test_virtual(self):
        assert classify_interface_type("VMware Network Adapter VMnet1", "") == "virtual"
        assert classify_interface_type("vEthernet (Default Switch)", "") == "virtual"
        assert classify_interface_type("Loopback Pseudo-Interface 1", "") == "virtual"

    def test_other_fallback(self):
        assert classify_interface_type("", "") == "other"


class TestSelectPrimaryInterface:
    def test_prefers_fastest_physical_up(self):
        ifaces = [
            {"interface_name": "VMware", "status": "up", "ipv4": "192.168.88.1", "interface_type": "virtual", "link_speed": 1000},
            {"interface_name": "Ethernet", "status": "up", "ipv4": "192.168.10.125", "interface_type": "ethernet", "link_speed": 1000},
            {"interface_name": "WLAN", "status": "up", "ipv4": "192.168.1.8", "interface_type": "wifi", "link_speed": 866},
        ]
        assert select_primary_interface(ifaces)["interface_name"] == "Ethernet"

    def test_skips_down_and_virtual(self):
        ifaces = [
            {"interface_name": "Loopback", "status": "up", "ipv4": "127.0.0.1", "interface_type": "virtual", "link_speed": None},
            {"interface_name": "WLAN", "status": "down", "ipv4": None, "interface_type": "wifi", "link_speed": None},
        ]
        assert select_primary_interface(ifaces) is None

    def test_falls_back_to_any_up_with_ipv4(self):
        ifaces = [
            {"interface_name": "VPN", "status": "up", "ipv4": "10.8.0.2", "interface_type": "vpn", "link_speed": None},
        ]
        assert select_primary_interface(ifaces)["interface_name"] == "VPN"


class TestSnapshotToPayload:
    def test_payload_shape_and_json_fields(self):
        snap = {
            "ts": 1700000000, "interval": 10, "primary": "Ethernet",
            "rx_rate": 100, "tx_rate": 50,
            "interfaces": [{
                "interface_name": "Ethernet", "description": "Realtek", "interface_type": "ethernet",
                "mac_address": "AA-BB-CC-DD-EE-FF", "ipv4": "192.168.10.125", "ipv6": None,
                "gateway": "192.168.10.1", "dns": "192.168.10.1", "status": "up", "link_speed": 1000,
                "rx_bytes": 1_000_000, "tx_bytes": 2_000, "rx_packets": 800, "tx_packets": 20,
                "rx_errors": 0, "tx_errors": 0, "rx_drops": 0, "tx_drops": 0,
                "rx_rate": 100, "tx_rate": 50,
            }],
            "quality": {"ts": 1700000000, "gateway_rtt_ms": 2, "internet_rtt_ms": 38, "packet_loss_pct": 0.0},
        }
        payload = snapshot_to_payload(snap)
        assert payload["primary"] == "Ethernet"
        assert payload["rx_rate"] == 100
        ifaces = __import__("json").loads(payload["interfaces"])
        assert ifaces[0]["ipv4"] == "192.168.10.125"
        assert __import__("json").loads(payload["quality"])["internet_rtt_ms"] == 38

    def test_collector_snapshot_failsafe(self):
        # NetworkCollector 在非异常环境下返回快照结构完整
        collector = NetworkCollector(interval=10, quality_interval=3600)
        snap = collector.snapshot()
        assert snap is not None
        assert "interfaces" in snap and "quality" in snap and "primary" in snap
        for itf in snap["interfaces"]:
            assert itf["status"] in ("up", "down")
            assert itf["interface_type"] in ("ethernet", "wifi", "vpn", "virtual", "other")

    def test_collector_rates_second_round(self):
        import time
        collector = NetworkCollector(interval=10, quality_interval=3600)
        first = collector.snapshot()
        time.sleep(1.1)
        second = collector.snapshot()
        assert first is not None and second is not None
        # 第二轮已建立基线，主网卡速率应为数值或 None（计数器未变化为 0）
        primary = second.get("primary")
        if primary:
            itf = next(i for i in second["interfaces"] if i["interface_name"] == primary)
            assert itf["rx_rate"] is None or isinstance(itf["rx_rate"], int)
