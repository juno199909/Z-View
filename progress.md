# 项目接手说明书

## 概览

当前版本可分为 4 层：

- 前端管理台：`frontend/`
- 主后端：`assets_api.py`
- 软件子系统：`software_management_api_complete_v2.py`、`software_policy_api.py`
- Agent 与远控底座：`cmdb_agent_unified_v2.py`、`RemoteAgent/`、`RemoteService/`、`Capture/`、`Input/`、`IPC/`、`Codec/`

## 模块职责

### 前端

- 仪表板、资产、终端、软件中心、告警、日志、批量、发现、登录
- 终端详情集成 Web 远控与远程 Shell
- 软件中心拆为仓库、任务、已安装、合规、策略 5 个子页

### 主后端

- 认证、资产、分组、告警、日志、批量操作、发现、远控 WebSocket、Agent 心跳
- 负责统一向前端提供主平台 API

### 软件子系统

- 软件包仓库、上传、分发任务、任务结果回写
- 白名单、强制安装策略、合规检查与结果统计

### Agent / 远控

- Agent 服务宿主、用户会话 Agent、高权限 helper、同意 UI
- 远程桌面、剪贴板、文件传输、输入注入、命名管道 IPC

## 完成度评估

| 模块 | 完成度 | 说明 |
|---|---:|---|
| 认证与权限 | 85% | 登录、会话、改密、首登强制改密已闭环 |
| 资产管理 | 80% | 列表、详情、创建、编辑、删除、导出已实现 |
| 终端监控 | 85% | 总览、详情、健康度、实时状态较完整 |
| 远程桌面/命令 | 70% | 功能齐，但链路复杂、风险高 |
| 软件包仓库 | 85% | 上传、列表、详情、分发任务已实现 |
| 软件任务 | 85% | 创建、查询、结果回写、日志回写已实现 |
| 软件合规 | 80% | 规则、结果、统计、导出已实现 |
| 软件策略 | 80% | 黑白名单、强制安装、执行、日志已实现 |
| 告警中心 | 85% | 统计、列表、详情、解决、导出已实现 |
| 日志中心 | 85% | 聚合查询、统计、详情已实现 |
| 资产发现 | 65% | 后端有，前端未完全产品化 |
| Agent 宿主 | 70% | 能跑，但历史遗留较多 |
| 数据库/部署 | 70% | 表结构齐，但交付规范需继续收口 |

## 关键文件

- `assets_api.py`
- `software_management_api_complete_v2.py`
- `software_policy_api.py`
- `cmdb_agent_unified_v2.py`
- `cmdb_agent_core.py`
- `cmdb_agent_consent_ui.py`
- `RemoteService/session_manager.py`
- `frontend/src/`
- `database/`

## 主要风险

- 敏感配置曾以明文形式存在于仓库中，需持续保证不再提交运行态密钥
- 远控链路依赖 Windows 会话、桌面切换、helper IPC，回归成本高
- Agent 历史上依赖 `.backup` / `.pyc`，后续变更需优先验证入口稳定性
- 仓库存在旧目录、副本目录和生成物，接手时要确认当前版本目录

## 接手顺序

1. 先看 `assets_api.py`
2. 再看 `software_management_api_complete_v2.py`、`software_policy_api.py`
3. 然后看 `cmdb_agent_unified_v2.py`、`cmdb_agent_core.py`
4. 最后看 `frontend/src/` 和 `RemoteService/`

## 验证建议

- 后端：`python -m py_compile ...`
- 前端：`npm run build`
- Agent：Windows 下验证服务宿主、用户会话、同意 UI、远控登录后的完整链路
