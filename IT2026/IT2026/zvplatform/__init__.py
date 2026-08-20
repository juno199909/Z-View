# -*- coding: utf-8 -*-
"""Z-View 平台分层架构包（P1-01/P1-06）。

分层：
  db.py            数据库连接与格式化工具
  constants.py     告警阈值等平台常量
  common.py        纯函数工具（safe_float / compute_health_score 等）
  repositories/    数据访问层（纯 SQL，无路由逻辑）
  services/        业务服务层（告警评估等）
  routers/         FastAPI 路由（薄层，依赖 services/repositories）
  worker_health.py 后台线程健康注册表（P1-08）
"""
