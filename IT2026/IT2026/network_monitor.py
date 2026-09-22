# -*- coding: utf-8 -*-
"""Z-View Agent 网络监控采集器（第一阶段：网卡统计 + 流量速率 + 基础网络质量）。

设计原则（需求 §14/§15/§23）：
- 仅读取 Windows 系统累计计数器（psutil / IP Helper），差值计算速率，不抓包、不 DPI
- 默认 10s 采集（允许 5/10/30/60s），网络质量默认 45s 一次轻量探测
- 采集 / 计算 / 上报分离：本模块只产出 NetworkSnapshot 字典，不做任何 IO 上报
- 全程 fail-safe：单接口异常、计数器回退、Agent 重启、网卡增删均不产生脏数据
"""

from __future__ import annotations

import json
import os
import re
import socket
import subprocess
import time
from typing import Any, Dict, List, Optional

import psutil
from zvagent.collectors.system import get_platform_route_interface_name

try:  # 仅 Windows Agent 存在；非 Windows（单测环境）自动降级
    import winreg  # noqa: F401
except ImportError:  # pragma: no cover
    winreg = None

AF_INET_ = socket.AF_INET
AF_INET6_ = socket.AF_INET6
AF_LINK_ = int(getattr(psutil, "AF_LINK", -1))

CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

DEFAULT_INTERNET_HOST = "223.5.5.5"
DEFAULT_QUALITY_INTERVAL = 45
META_CACHE_TTL = 600.0

NIC_TYPE_KEYWORDS = {
    "wifi": ("wi-fi", "wifi", "wireless", "802.11", "wlan", "无线"),
    "vpn": ("vpn", "tap", "tun", "pptp", "l2tp", "wireguard", "openvpn"),
    "virtual": (
        "vmware", "virtualbox", "vbox", "hyper-v", "virtual", "docker",
        "vethernet", "loopback", "pseudo", "wan miniport", "microsoft wi-fi direct",
        "bluetooth", "teredo", "isatap", "qos", "microsoft kernel",
    ),
}


def classify_interface_type(name: str, description: str) -> str:
    """网卡类型分类：ethernet / wifi / vpn / virtual / other。"""
    text = f"{name} {description}".lower().strip()
    if not text:
        return "other"
    for nic_type, keywords in NIC_TYPE_KEYWORDS.items():
        if any(kw in text for kw in keywords):
            return nic_type
    return "ethernet"


def is_virtual_interface(name: str, description: str = "") -> bool:
    return classify_interface_type(name, description) in ("vpn", "virtual")


def _norm_mac(mac: Optional[str]) -> str:
    """MAC 归一化（WMI 用冒号、psutil 用横杠），去除分隔符后比对。"""
    return re.sub(r"[:\-\.]", "", str(mac or "")).lower()


def compute_rate(current: Optional[int], previous: Optional[int], elapsed_seconds: float) -> Optional[float]:
    """差值速率（bytes/s）。

    - current < previous：计数器重置（网卡重启/系统计数器归零），返回 None 重建基线，
      不得产生负数或异常巨量速率
    - elapsed <= 0 或数据缺失：返回 None
    """
    if current is None or previous is None:
        return None
    if elapsed_seconds is None or elapsed_seconds <= 0:
        return None
    if current < previous:
        return None
    return (current - previous) / elapsed_seconds


class TrafficRateCalculator:
    """基于累计计数器基线的速率计算器。

    - Agent 重启后基线为空：首轮返回 None（不伪造速率），次轮起正常
    - 网卡消失：基线随快照丢弃；网卡重现/切换：按新网卡重新建立基线
    - 计数器回退：该轮速率为 None，并以当前值为新基线
    """

    def __init__(self) -> None:
        self._baseline: Dict[str, Dict[str, Any]] = {}

    def update(self, counters: Dict[str, Dict[str, Any]], now: float) -> Dict[str, Dict[str, Optional[float]]]:
        rates: Dict[str, Dict[str, Optional[float]]] = {}
        new_baseline: Dict[str, Dict[str, Any]] = {}
        for nic, c in counters.items():
            prev = self._baseline.get(nic)
            rx_rate = compute_rate(c.get("rx_bytes"), (prev or {}).get("rx_bytes"), now - (prev or {}).get("ts", 0))
            tx_rate = compute_rate(c.get("tx_bytes"), (prev or {}).get("tx_bytes"), now - (prev or {}).get("ts", 0))
            rates[nic] = {"rx_rate": rx_rate, "tx_rate": tx_rate}
            new_baseline[nic] = {"ts": now, "rx_bytes": c.get("rx_bytes"), "tx_bytes": c.get("tx_bytes")}
        self._baseline = new_baseline
        return rates

    def reset(self) -> None:
        self._baseline = {}


def collect_traffic_counters() -> Dict[str, Dict[str, Any]]:
    """读取系统累计流量计数器（per-NIC，nowrap 保证计数器不回绕）。"""
    io = psutil.net_io_counters(pernic=True, nowrap=True)
    counters: Dict[str, Dict[str, Any]] = {}
    for nic, c in io.items():
        counters[nic] = {
            "rx_bytes": int(c.bytes_recv or 0),
            "tx_bytes": int(c.bytes_sent or 0),
            "rx_packets": int(c.packets_recv or 0),
            "tx_packets": int(c.packets_sent or 0),
            "rx_errors": int(c.errin or 0),
            "tx_errors": int(c.errout or 0),
            "rx_drops": int(c.dropin or 0),
            "tx_drops": int(c.dropout or 0),
        }
    return counters


def collect_interfaces(meta: Optional[Dict[str, Dict[str, Any]]] = None) -> List[Dict[str, Any]]:
    """采集网卡信息（多网卡，含虚拟网卡；status/speed 来自系统）。"""
    meta = meta or {}
    addrs_by_nic = psutil.net_if_addrs()
    stats_by_nic = psutil.net_if_stats()
    interfaces: List[Dict[str, Any]] = []

    for name, addrs in addrs_by_nic.items():
        st = stats_by_nic.get(name)
        ipv4: Optional[str] = None
        ipv6: Optional[str] = None
        mac: Optional[str] = None
        for addr in addrs:
            if addr.family == AF_INET_:
                if not ipv4 and addr.address != "127.0.0.1":
                    ipv4 = addr.address
            elif addr.family == AF_INET6_:
                if not ipv6 and addr.address != "::1" and not str(addr.address).startswith("fe80"):
                    ipv6 = addr.address.split("%")[0]
            elif addr.family == AF_LINK_:
                mac = addr.address

        m = meta.get(_norm_mac(mac)) or {}
        description = str(m.get("description") or "")[:255]
        interfaces.append({
            "interface_name": name[:100],
            "description": description,
            "interface_type": classify_interface_type(name, description or name),
            "mac_address": mac,
            "ipv4": ipv4,
            "ipv6": ipv6,
            "gateway": m.get("gateway"),
            "dns": m.get("dns"),
            "status": "up" if (st.isup if st else False) else "down",
            # link_speed Mbps；Win 对部分虚拟网卡返回 0/-1，允许 unavailable
            "link_speed": int(st.speed) if st and (st.speed or 0) > 0 else None,
        })
    return interfaces


class NicMetaCache:
    """网卡描述/网关/DNS 缓存（WMI，TTL 默认 10 分钟，降低采集开销）。"""

    def __init__(self, ttl: float = META_CACHE_TTL) -> None:
        self._ttl = ttl
        self._cache: Dict[str, Dict[str, Any]] = {}
        self._expires = 0.0

    def get(self) -> Dict[str, Dict[str, Any]]:
        now = time.time()
        if self._cache and now < self._expires:
            return self._cache
        try:
            self._cache = {(_norm_mac(k) or k): v for k, v in self._query_wmi().items()}
            self._expires = now + self._ttl
        except Exception:
            # fail-safe：查询失败沿用旧缓存（若有），避免影响采集主流程
            self._expires = now + 60.0
        return self._cache

    @staticmethod
    def _query_wmi() -> Dict[str, Dict[str, Any]]:
        if winreg is None:  # 非 Windows
            return {}
        import pythoncom
        import win32com.client
        pythoncom.CoInitialize()
        try:
            wmi = win32com.client.Dispatch("WbemScripting.SWbemLocator")
            svc = wmi.ConnectServer(".", r"root\cimv2")
            result: Dict[str, Dict[str, Any]] = {}
            for item in svc.ExecQuery(
                "SELECT Description, MACAddress, DefaultIPGateway, DNSServerSearchOrder "
                "FROM Win32_NetworkAdapterConfiguration WHERE IPEnabled = True"
            ):
                mac = str(item.MACAddress or "").strip()
                if not mac:
                    continue
                gateways = []
                dns_servers = []
                try:
                    gateways = [str(g) for g in (item.DefaultIPGateway or [])]
                except Exception:
                    pass
                try:
                    dns_servers = [str(d) for d in (item.DNSServerSearchOrder or [])]
                except Exception:
                    pass
                result[mac] = {
                    "description": str(item.Description or ""),
                    "gateway": gateways[0] if gateways else None,
                    "dns": ",".join(dns_servers[:4]) if dns_servers else None,
                }
            return result
        finally:
            pythoncom.CoUninitialize()


def _run_ping(target: str, count: int, timeout_ms: int) -> Dict[str, Any]:
    """轻量 ICMP 探测（单进程、有限包数），解析 RTT 与丢包率（跨语言环境兼容）。"""
    try:
        r = subprocess.run(
            ["ping", "-n", str(count), "-w", str(timeout_ms), target],
            capture_output=True, text=True,
            encoding="mbcs" if os.name == "nt" else "utf-8",
            errors="replace",
            timeout=max(3, count * timeout_ms // 1000 + 3),
            creationflags=CREATE_NO_WINDOW, check=False,
        )
        output = r.stdout or ""
        rtts = [int(v) for v in re.findall(r"(?:time|时间)[=<]\s*(\d+)\s*ms", output, flags=re.IGNORECASE)]
        if not rtts:
            # 双保险：TTL= 行在任何语言环境下都含 ASCII 片段 "<n>ms TTL="
            rtts = [int(v) for v in re.findall(r"=\s*(\d+)\s*ms\s+TTL=", output, flags=re.IGNORECASE)]
        loss_pct = round(max(0.0, (count - len(rtts)) * 100.0 / count), 1)
        avg_rtt = int(round(sum(rtts) / len(rtts))) if rtts else None
        return {"success": len(rtts) > 0, "rtt_ms": avg_rtt, "loss_pct": loss_pct}
    except Exception as exc:
        return {"success": False, "rtt_ms": None, "loss_pct": 100.0, "error": str(exc)[:200]}


def _tcp_connect_ms(host: str, port: int, timeout: float = 1.5) -> Optional[int]:
    """TCP 连接耗时（ms），用于 DNS 可达性轻量探测。"""
    try:
        start = time.perf_counter()
        sock = socket.create_connection((host, port), timeout=timeout)
        elapsed_ms = int(round((time.perf_counter() - start) * 1000))
        sock.close()
        return elapsed_ms
    except Exception:
        return None


class NetworkQualityCollector:
    """基础网络质量探测（Gateway/DNS/Internet RTT + 丢包），默认 45s 一次。"""

    def __init__(self, interval: int = DEFAULT_QUALITY_INTERVAL, internet_host: str = DEFAULT_INTERNET_HOST) -> None:
        self.interval = max(30, min(int(interval), 3600))
        self.internet_host = internet_host
        self._last_run = 0.0
        self._last_result: Optional[Dict[str, Any]] = None

    def maybe_collect(self, gateway: Optional[str], dns: Optional[str], now: float) -> Optional[Dict[str, Any]]:
        if now - self._last_run < self.interval:
            return self._last_result
        self._last_run = now
        result: Dict[str, Any] = {"ts": int(now)}

        gateway_rtt = None
        if gateway:
            gateway_rtt = _run_ping(gateway, count=2, timeout_ms=1000)
        internet = _run_ping(self.internet_host, count=4, timeout_ms=1000)

        result["gateway_rtt_ms"] = gateway_rtt.get("rtt_ms") if gateway_rtt else None
        result["internet_rtt_ms"] = internet.get("rtt_ms")
        result["packet_loss_pct"] = internet.get("loss_pct")
        primary_dns = str(dns or "").split(",")[0].strip()
        result["dns_rtt_ms"] = _tcp_connect_ms(primary_dns, 53) if primary_dns else None
        result["success"] = internet.get("success", False)
        self._last_result = result
        return result


def select_primary_interface(
    interfaces: List[Dict[str, Any]],
    routed_interface_name: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """选择当前活跃主网卡，优先使用实际到平台的路由出口。

    路由出口可用时即使它是 VPN / 隧道也应如实上报；无法解析路由时，保留原有
    的非虚拟网卡、链路速率优先的降级策略。
    """
    if routed_interface_name:
        route_name = routed_interface_name.casefold()
        for itf in interfaces:
            if (
                str(itf.get("interface_name") or "").casefold() == route_name
                and itf.get("status") == "up"
                and itf.get("ipv4")
            ):
                return itf

    candidates = [
        itf for itf in interfaces
        if itf.get("status") == "up" and itf.get("ipv4")
        and itf.get("interface_type") not in ("vpn", "virtual")
    ]
    if not candidates:
        candidates = [
            itf for itf in interfaces
            if itf.get("status") == "up" and itf.get("ipv4")
            and str(itf.get("ipv4")).split(".")[0] not in ("127", "169")
        ]
    if not candidates:
        return None
    candidates.sort(key=lambda itf: itf.get("link_speed") or 0, reverse=True)
    return candidates[0]


class NetworkCollector:
    """网络采集门面：采集 → 计算 → 产出 NetworkSnapshot（不做上报）。"""

    def __init__(
        self,
        interval: int = 10,
        quality_interval: int = DEFAULT_QUALITY_INTERVAL,
        internet_host: str = DEFAULT_INTERNET_HOST,
    ) -> None:
        self.interval = max(5, min(int(interval), 60))
        self._calc = TrafficRateCalculator()
        self._quality = NetworkQualityCollector(interval=quality_interval, internet_host=internet_host)
        self._meta = NicMetaCache()

    def snapshot(self) -> Optional[Dict[str, Any]]:
        """采集一轮网络快照；异常时返回 None（fail-safe，不抛出）。"""
        try:
            now = time.time()
            counters = collect_traffic_counters()
            rates = self._calc.update(counters, now)
            interfaces = collect_interfaces(self._meta.get())

            rx_rate = tx_rate = 0
            for itf in interfaces:
                nic_rates = rates.get(itf["interface_name"], {})
                rx = nic_rates.get("rx_rate")
                tx = nic_rates.get("tx_rate")
                itf["rx_rate"] = int(round(rx)) if rx is not None else None
                itf["tx_rate"] = int(round(tx)) if tx is not None else None
                if itf["status"] == "up" and itf.get("rx_rate") is not None:
                    if itf["interface_type"] not in ("vpn", "virtual"):
                        rx_rate += max(0, itf["rx_rate"])
                        tx_rate += max(0, itf["tx_rate"] or 0)
                counter = counters.get(itf["interface_name"], {})
                itf.update({k: counter.get(k) for k in (
                    "rx_bytes", "tx_bytes", "rx_packets", "tx_packets",
                    "rx_errors", "tx_errors", "rx_drops", "tx_drops",
                )})

            primary = select_primary_interface(
                interfaces,
                routed_interface_name=get_platform_route_interface_name(),
            )
            gateway = (primary or {}).get("gateway")
            quality = self._quality.maybe_collect(gateway, (primary or {}).get("dns"), now)

            return {
                "ts": int(now),
                "interval": self.interval,
                "interfaces": interfaces,
                "primary": (primary or {}).get("interface_name"),
                "rx_rate": rx_rate,
                "tx_rate": tx_rate,
                "quality": quality,
            }
        except Exception:
            return None


def snapshot_to_payload(snapshot: Dict[str, Any]) -> Dict[str, Any]:
    """裁剪快照为上报 payload（剔除超长描述等，控制网络开销）。"""
    interfaces = []
    for itf in snapshot.get("interfaces", []):
        interfaces.append({
            "interface_name": itf.get("interface_name"),
            "description": itf.get("description"),
            "interface_type": itf.get("interface_type"),
            "mac_address": itf.get("mac_address"),
            "ipv4": itf.get("ipv4"),
            "ipv6": itf.get("ipv6"),
            "gateway": itf.get("gateway"),
            "dns": itf.get("dns"),
            "status": itf.get("status"),
            "link_speed": itf.get("link_speed"),
            "rx_bytes": itf.get("rx_bytes"), "tx_bytes": itf.get("tx_bytes"),
            "rx_packets": itf.get("rx_packets"), "tx_packets": itf.get("tx_packets"),
            "rx_errors": itf.get("rx_errors"), "tx_errors": itf.get("tx_errors"),
            "rx_drops": itf.get("rx_drops"), "tx_drops": itf.get("tx_drops"),
            "rx_rate": itf.get("rx_rate"), "tx_rate": itf.get("tx_rate"),
        })
    return {
        "ts": snapshot.get("ts"),
        "interfaces": json.dumps(interfaces, ensure_ascii=False),
        "primary": snapshot.get("primary"),
        "rx_rate": snapshot.get("rx_rate"),
        "tx_rate": snapshot.get("tx_rate"),
        "quality": json.dumps(snapshot.get("quality") or {}, ensure_ascii=False),
    }
