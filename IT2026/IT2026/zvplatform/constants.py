# -*- coding: utf-8 -*-
"""平台常量：告警阈值（P1-01 从 assets_api 迁出，原处 import 回来保持兼容）。"""

ALERT_ONLINE_SECONDS = 90
ALERT_OFFLINE_SECONDS = 180
ALERT_THRESHOLDS = {
    "cpu": {"warning": 80.0, "critical": 90.0},
    "memory": {"warning": 90.0, "critical": 95.0},
    "disk": {"warning": 90.0, "critical": 95.0},
    "health": {"warning": 60.0, "critical": 40.0},
    "network_loss": {"warning": 10.0, "minutes": 3},
    "network_traffic": {"warning_bytes_per_sec": 100 * 1024 * 1024, "minutes": 5},
}
ALERT_TYPE_LABELS = {
    "cpu": "CPU使用率",
    "memory": "内存使用率",
    "disk": "磁盘使用率",
    "offline": "终端离线",
    "health": "健康度",
    "network_down": "网络断开",
    "network_quality": "网络质量下降",
    "network_traffic": "网络流量异常",
}

DISCOVERY_MAX_TASKS = 100
DISCOVERY_MAX_TARGETS = 4096
DISCOVERY_TASK_RETENTION_SECONDS = 24 * 60 * 60
AGENT_INSTALL_STATUS_INSTALLED = "installed"
AGENT_INSTALL_STATUS_NOT_INSTALLED = "not_installed"

# 网络监控（第一阶段）
NETWORK_HISTORY_RETENTION_DAYS = 7
NETWORK_STALE_SECONDS = 300
