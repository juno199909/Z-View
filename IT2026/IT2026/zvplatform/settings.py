# -*- coding: utf-8 -*-
"""P4-06：统一配置中心（zvplatform/settings.py）。

覆盖优先级（低→高）：代码默认值 < .env 环境变量。
集中定义 Z-View 全部可调配置项，替代散落在各文件的硬编码。
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field

# 复用 config_utils 的 env 加载
import sys as _sys
_sys_path = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _sys_path not in _sys.path:
    _sys.path.insert(0, _sys_path)

from config_utils import ensure_env_loaded, get_env  # noqa: E402

ensure_env_loaded()


def _env_int(name: str, default: int) -> int:
    try:
        return int(get_env(name, str(default)) or default)
    except (TypeError, ValueError):
        return default


def _env_bool(name: str, default: bool) -> bool:
    return str(get_env(name, str(default)) or default).strip().lower() in ("1", "true", "yes", "on")


def _env_str(name: str, default: str) -> str:
    return str(get_env(name, default) or default)


@dataclass
class PlatformSettings:
    """平台服务配置"""
    host: str = "0.0.0.0"
    port: int = 8080
    tls_port: int = 8443
    agent_tls_enabled: bool = True
    tls_certfile: str = ""
    tls_keyfile: str = ""

    @classmethod
    def load(cls) -> "PlatformSettings":
        app_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        return cls(
            host=_env_str("ZVIEW_PLATFORM_HOST", "0.0.0.0"),
            port=_env_int("ZVIEW_PLATFORM_PORT", 8080),
            tls_port=_env_int("ZVIEW_AGENT_TLS_PORT", 8443),
            agent_tls_enabled=_env_bool("ZVIEW_AGENT_TLS_ENABLED", True),
            tls_certfile=_env_str("ZVIEW_TLS_CERTFILE",
                                  os.path.join(app_dir, "frontend", "certs", "zview-cert.pem")),
            tls_keyfile=_env_str("ZVIEW_TLS_KEYFILE",
                                 os.path.join(app_dir, "frontend", "certs", "zview-key.pem")),
        )


@dataclass
class AgentSettings:
    """Agent 通道配置"""
    control_port: int = 9001
    remote_port: int = 9000
    heartbeat_interval: int = 30
    heartbeat_max_failures: int = 10  # P0-06 自愈阈值

    @classmethod
    def load(cls) -> "AgentSettings":
        return cls(
            control_port=_env_int("ZVIEW_AGENT_CONTROL_PORT", 9001),
            remote_port=_env_int("ZVIEW_AGENT_REMOTE_PORT", 9000),
            heartbeat_interval=_env_int("ZVIEW_AGENT_HEARTBEAT_INTERVAL", 30),
            heartbeat_max_failures=_env_int("ZVIEW_AGENT_HEARTBEAT_MAX_FAILURES", 10),
        )


@dataclass
class DiscoverySettings:
    """终端发现配置"""
    max_tasks: int = 100
    max_targets: int = 4096
    task_retention_seconds: int = 24 * 60 * 60
    ping_timeout: int = 3
    ping_concurrency: int = 64

    @classmethod
    def load(cls) -> "DiscoverySettings":
        return cls(
            max_tasks=_env_int("ZVIEW_DISCOVERY_MAX_TASKS", 100),
            max_targets=_env_int("ZVIEW_DISCOVERY_MAX_TARGETS", 4096),
            task_retention_seconds=_env_int("ZVIEW_DISCOVERY_TASK_RETENTION_SECONDS", 86400),
            ping_timeout=_env_int("ZVIEW_DISCOVERY_PING_TIMEOUT", 3),
            ping_concurrency=_env_int("ZVIEW_DISCOVERY_PING_CONCURRENCY", 64),
        )


@dataclass
class AlertSettings:
    """告警阈值配置"""
    online_seconds: int = 90
    offline_seconds: int = 180
    cpu_warning: float = 80.0
    cpu_critical: float = 90.0
    memory_warning: float = 90.0
    memory_critical: float = 95.0
    disk_warning: float = 90.0
    disk_critical: float = 95.0

    @classmethod
    def load(cls) -> "AlertSettings":
        return cls(
            online_seconds=_env_int("ZVIEW_ALERT_ONLINE_SECONDS", 90),
            offline_seconds=_env_int("ZVIEW_ALERT_OFFLINE_SECONDS", 180),
            cpu_warning=float(get_env("ZVIEW_ALERT_CPU_WARNING", "80") or 80),
            cpu_critical=float(get_env("ZVIEW_ALERT_CPU_CRITICAL", "90") or 90),
            memory_warning=float(get_env("ZVIEW_ALERT_MEMORY_WARNING", "90") or 90),
            memory_critical=float(get_env("ZVIEW_ALERT_MEMORY_CRITICAL", "95") or 95),
            disk_warning=float(get_env("ZVIEW_ALERT_DISK_WARNING", "90") or 90),
            disk_critical=float(get_env("ZVIEW_ALERT_DISK_CRITICAL", "95") or 95),
        )


@dataclass
class AppSettings:
    """聚合配置（单例入口）"""
    platform: PlatformSettings = field(default_factory=PlatformSettings.load)
    agent: AgentSettings = field(default_factory=AgentSettings.load)
    discovery: DiscoverySettings = field(default_factory=DiscoverySettings.load)
    alerts: AlertSettings = field(default_factory=AlertSettings.load)

    @classmethod
    def load(cls) -> "AppSettings":
        return cls(
            platform=PlatformSettings.load(),
            agent=AgentSettings.load(),
            discovery=DiscoverySettings.load(),
            alerts=AlertSettings.load(),
        )


# 模块级单例（懒加载，首次访问时读 env）
_settings: AppSettings | None = None


def get_settings() -> AppSettings:
    """获取全局配置单例（reload 场景调用 get_settings 重读）"""
    global _settings
    if _settings is None:
        _settings = AppSettings.load()
    return _settings


def reload_settings() -> AppSettings:
    """强制重读配置（测试/运维用）"""
    global _settings
    _settings = AppSettings.load()
    return _settings
