# 软件管理中心 - 开发进度

**最后更新**: 2026-07-29 16:32:49 +08:00  
**当前结论**: 功能开发已补齐到 100%，生产级真实安装验收仍需在有数据库、后端服务和测试终端的环境中执行。

---

## 总体状态

### 1. 后端 API

状态：已完成。

- 软件包仓库：列表、详情、上传、更新、删除、下载、分类、统计已实现。
- 任务管理：创建、列表、详情、取消、重试、统计、结果回写、日志上传已实现。
- Agent 通信：策略同步、任务轮询、软件包下载、结果回写已实现。
- 策略与规则：黑名单、白名单、安装策略、合规规则、合规结果、合规扫描已实现。
- 健康检查：`/api/v1/software/health` 已实现。

### 2. Agent 软件管理

状态：已完成。

- 已接入统一 Agent 入口，由 `cmdb_agent_unified_v2.py` 动态加载核心模块。
- 已支持策略同步和任务轮询。
- 已支持安装、升级、卸载任务执行。
- 已支持按后端 `task_id/result_id/package_info` 格式解析任务。
- 已支持下载 URL 携带 `asset_id`，满足后端下载鉴权要求。
- 已支持按服务端文件名保存软件包，避免固定 `.exe` 后缀导致 MSI/BAT/ZIP 等包类型异常。
- 已支持断点续传，并在服务端不支持 Range 时自动重写文件，避免追加损坏包。
- 已支持 SHA256 校验，哈希不匹配会失败并回传明确错误。
- 已支持 `{file_path}`、`{package_path}`、`%FILE_PATH%`、`%PACKAGE_PATH%` 安装命令占位符。
- 已按后端接受的状态回传：`downloading`、`installing`、`success`、`failed`。
- 已回传进度、错误原因、标准输出和标准错误日志。

### 3. 前端软件中心

状态：已完成代码接入。

- 软件中心主页面已按标签页接入软件仓库、任务管理、已装软件、合规管理、策略管理。
- 前端 API 已拆分为软件管理 API 和策略 API。
- Vite 代理已配置软件管理服务、策略服务和资产服务。

### 4. 打包发布

状态：已完成。

- `build_agent.ps1` 已使用独立构建目录，默认落到 `D:\IT2026-temp\zview-build`，减少 C 盘压力。
- 已同步 `Z-View.exe` 和 `config.json` 到 GPO 部署包。
- `verify_release_package.ps1` 已检查源码、GPO 包、配置、PE 头、SHA256、PowerShell 语法和虚拟显示驱动载荷。

---

## 本次补齐内容

- 修复 Agent 使用旧字段导致无法执行后端下发任务的问题。
- 修复 Agent 把 `task_id` 当成 `result_id` 回传的问题。
- 修复 Agent 下载接口缺少 `asset_id` 查询参数的问题。
- 修复 Agent 成功状态上报为 `completed` 导致后端无法汇总为完成的问题。
- 修复卸载任务后端未下发 `uninstall_command` 的问题。
- 修复后端轮询可能下发已失败终态任务的问题。
- 修复批量任务中单台失败导致其余 pending 资产提前停止的问题。

---

## 已完成验证

```powershell
$env:PYTHONDONTWRITEBYTECODE='1'
python -c "from pathlib import Path; files=[Path(r'IT2026\IT2026\cmdb_agent_unified_v2.py'),Path(r'IT2026\IT2026\cmdb_agent_unified_v2.py.backup'),Path(r'IT2026\IT2026\software_management_api_complete_v2.py')]; [compile(p.read_text(encoding='utf-8-sig'), str(p), 'exec') for p in files]; print('syntax ok')"
python IT2026\IT2026\cmdb_agent_unified_v2.py --help
python -c "import sys; sys.path.insert(0, r'IT2026\IT2026'); import software_management_api_complete_v2; print('backend import ok')"
```

结果：

- `syntax ok`
- Agent 入口帮助正常输出。
- `backend import ok`

---

## 生产验收建议

以下属于真实环境验收，不再是代码开发缺口：

1. 启动资产服务、软件管理服务、策略服务和前端服务。
2. 上传一个无害测试包，例如只写日志的 BAT/PS1 包。
3. 创建安装任务并指定一台测试终端。
4. 确认 Agent 可轮询到任务。
5. 确认下载进度、安装进度、最终 `success/failed` 状态可在前端看到。
6. 校验 `software_task_results` 中的 `stdout_log`、`stderr_log`、`error_message` 是否符合预期。

---

## 风险

- 未在本次操作中启动真实服务和数据库执行端到端安装，因为这会影响当前机器服务状态。
- 已存在大量 `node_modules`、`dist`、`__pycache__` 等历史或生成文件变更，本次没有回退这些无关改动。
- 如果软件包安装命令本身不支持静默安装，Agent 会执行失败并回传退出码，需要在软件包元数据中配置正确静默参数。
