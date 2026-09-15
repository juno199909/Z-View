# -*- coding: utf-8 -*-
"""Z-View Agent 包（V1.8.0 模块化）。

模块边界（渐进迁移自 cmdb_agent_unified_v2.py / cmdb_agent_core.py 两个单体）：
- layout.py   版本目录与 current junction 管理（V1.6.0 迁入）
- hygiene.py  临时文件/进程残留清理（V1.8.0 迁入）

后续增量：auth（设备凭据）、upgrade（升级状态机）、collectors（采集器）、
policy（策略执行）、jobs（任务执行端）。单体文件保留为薄委托层以兼容
存量导入与 frozen 打包入口。
"""

# Agent 版本号（与 cmdb_agent_core.AGENT_VERSION 同源，发布时 bump）
__version__ = "1.9.30"
