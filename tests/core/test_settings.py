# -*- coding: utf-8 -*-
"""zvplatform/settings.py 单元测试"""
import os
import sys

sys.path.insert(0, r"D:\IT2026\IT2026\IT2026\IT2026")
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from zvplatform.settings import get_settings, reload_settings

s = reload_settings()
# 默认值验证
assert s.platform.port == 8080
assert s.platform.tls_port == 8443
assert s.platform.agent_tls_enabled is True
assert s.agent.control_port == 9001
assert s.agent.remote_port == 9000
assert s.agent.heartbeat_max_failures == 10
assert s.discovery.max_tasks == 100
assert s.discovery.task_retention_seconds == 86400
assert s.alerts.online_seconds == 90
assert s.alerts.cpu_warning == 80.0

# env 覆盖验证
os.environ["ZVIEW_AGENT_HEARTBEAT_INTERVAL"] = "15"
os.environ["ZVIEW_DISCOVERY_PING_TIMEOUT"] = "5"
s2 = reload_settings()
assert s2.agent.heartbeat_interval == 15
assert s2.discovery.ping_timeout == 5

# 清理
del os.environ["ZVIEW_AGENT_HEARTBEAT_INTERVAL"]
del os.environ["ZVIEW_DISCOVERY_PING_TIMEOUT"]
s3 = reload_settings()
assert s3.agent.heartbeat_interval == 30

print("settings tests OK")
