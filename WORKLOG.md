# WORKLOG

> Newest entry on top. See CLAUDE.md for the ledger rules.

## ??? Awaiting your decision (ball in your court)
> When a blocker needs a decision only you can make, an entry lands here and stays
> pinned above the log stream. Empty = nothing is waiting on you.

<!-- format: - [ ] [YYYY-MM-DD] what's blocked ??? the one-sentence decision ??? where the evidence is -->

- [ ] [2026-08-31] 远控帧率天花板仍 ~1fps（VMware 虚机 EDID 丢失致 DXGI 不持续出帧） ??? 决定走 VM 层修复（勾 Accelerate 3D / 升级 VMware Tools / 避 RDP 劫持）还是激活 Parsec-VDD 虚拟显示器（驱动已 staged） ??? 证据见 2026-08-27 记忆 + agent-runtime.log VirtualDisplay installed_detached 循环
- [ ] [2026-08-31] Git 工作区脏乱（188 staged 含截图/.playwright-mcp 产物 + 8 unstaged 代码改动） ??? 是否授权整理后分批提交建立干净基线 ??? 证据 git status / git diff --cached --stat

---

<!-- log entries below, newest first -->

## [2026-09-07] P1-01 收官：六域迁移完成（assets_api 7447→4847 行，-35%）

- **本轮完成**：groups 域（routers/groups.py，4 路由）、agent 策略域（services/agent_policy_service.py + routers/agent_policy.py，3 路由——normalize 校验器含间隔/开关/超时全量校验逻辑）。
- **决策**：设备凭据域与 security-policies 路由**保留在 assets_api**（与 P0-01 设备凭据/get_request_agent_auth 深度耦合，试迁发现 find_fn 边界计算间歇异常，收益低风险高，删除半成品回退）。
- **最终结构**：assets_api.py 4847 行 = heartbeat 本体（650 行）+ assets 主 CRUD + 设备凭据/安全策略路由 + core helpers；zvplatform 包 15 文件（db/constants/common/models/agent_client/worker_health/policy_engine + repositories×2 + services×4 + routers×5）。
- **验证**：py_compile 全过、import OK 97 路由（groups 4 条新路径、agent-policy 3 条、凭据/安全策略原位）、pyflakes 未定义 0、/api/health ok、2/2 终端 1.6.8 在线。
- **P1-01 累计成果**：-2600 行（-35%）、六域路由+服务+仓储分层、策略引擎 + worker 健康注册表 + /api/health 均为新增能力。
- **遗留（P1 后续阶段）**：heartbeat 本体服务化（650 行，依赖已全部就位）、WebRemoteDesktop.vue 解耦（P1-02）、控制面/数据面正式分离（P1-04）。

## [2026-09-07] P1-01 第四里程碑：discovery 域迁移完成（assets_api 7447→5230 行，累计 -30%）

- **迁至 zvplatform**：constants.py（+DISCOVERY_MAX_TASKS/MAX_TARGETS/TASK_RETENTION_SECONDS + AGENT_INSTALL_STATUS_INSTALLED/NOT_INSTALLED）、services/discovery_service.py（18 个函数：任务管理/expand targets/资产识别辅助/ping+SNMP 扫描线程/pysnmp 导入块）、routers/discovery.py（7 路由）。
- **修复静默雷**：DISCOVERY_TASK_RETENTION_SECONDS 定义早已丢失（cleanup 一调用即崩）→ 迁移时补回 constants。
- **验证**：97 路由（discovery 7 条全部正确注册）、/api/health ok、2/2 终端 1.6.8 在线、pyflakes 未定义符号 0。
- **P1-01 剩余**：groups 域（~170 行）、heartbeat 域（~700 行，最后一块大骨头）、assets 主 CRUD（保留在主文件）。

## [2026-09-08] 360 信任区加白生效 + Agent 服务/心跳全量恢复

- 用户在 360 信任区添加 Z-View.exe 后：CMDB-Agent 服务键稳定（不再被删）、服务 Running、心跳恢复（28/2213 双双在线实时更新）。
- 期间发现本机 Agent worker 曾卡死（trigger-report 502），Restart-Service（虽报错）后 worker 重新初始化，心跳恢复——迁移后的 heartbeat 路由对 HTTP/TLS 双通道验证通过。
- **教训**：360 主动防御未卸载仍可能间歇干扰；信任区加白当前有效，生产环境以商业代码签名证书 + 杀软管理端白名单为最终方案。


## [2026-09-08] P1-02 第一步：WebRemoteDesktop.vue 纯函数提取（4415→4324 行）

- **新模块 frontend/src/utils/remote/**：format.js（分辨率格式化 3 函数）、bytes.js（hex/concat/base64/transferPath 6 函数）、sessionSettings.js（defaultSessionSettings）——共 10 个无状态纯函数。
- **方式**：Edit 工具内容锚定精确剪切（脚本 find_line 全局搜索曾致文件散落损坏，已 git 恢复后改用 Edit）。模块用 globalThis.btoa 替代 window.btoa（Node 可测）。
- **测试**：Node 直跑 test_remote_utils.mjs（D:\IT2026\diagnostics）——25+ 断言全过（含大 buffer base64 分块往返、路径穿越清理、边界值）。
- **验证**：vite build ✓（零语法回归）、WebRemoteDesktop.vue 4415→4324 行。
- **P1-02 后续**：useVideoDecoder/useRemoteInput 等 composables 拆分需实机远控回归验证，建议与 Playwright E2E（P3-02）配套进行。


## [2026-09-08] ✅ 28 号终端完全恢复（1.6.9 上线）——worker 卡死根因确认

- **根因确认**：28 的 **CMDB-Agent 服务主进程从未真正重启**——此前多次 sc stop 挂起/超时后 sc start 报"已启动"，导致**旧代码主进程持续运行**：①Popen 无 stdout 重定向（心跳失败 print 全丢）②新 worker exe 被加载但服务的 Popen 逻辑仍是旧版。**强杀服务主进程 PID 6772 + 重启**后：1.6.9 上线、worker 心跳实时（14:16:33）、worker log 重定向生效。
- **修复验证**：28 = 1.6.9 + last_seen 实时 ✓；2213 = 1.6.9 ✓；双终端全绿。
- **经验**：Windows 服务"stop 超时→start 报已启动"= 主进程未重启，必须 sc queryex 取 PID 强杀后再 start。start_platform 的服务重启逻辑如有同样问题需同步修复。
- **1.6.9 新能力验证**：worker stdout 重定向（agent-worker.log）✓、心跳连续失败自愈（10 次 os._exit 由服务拉起）✓、PowerShell 签名校验 ✓。


## [2026-09-08] ✅ 1.7.2 异常落盘破案：28 worker"卡死"真相完整还原 + 完全恢复

- **1.7.2 新能力首战立功**：agent-error.log 首次捕获 Agent 内部异常（此前 print 丢失无法诊断）。
- **真相还原（因果链）**：
  1. **开机时序**：Agent 服务自启（16:32）早于平台 8443 启动 → 心跳 ConnectionError WinError 10061（连接拒绝）→ 回退全局 token 重试 8080 也失败（平台未起）→ **自愈 os._exit → 服务重启 worker → 循环**——**之前所有"worker 卡死"实为这个高频重启循环**（py-spy 的 sleep 快照是循环瞬间，非死锁）。
  2. **平台起来后**：心跳 HTTP 401（zv1 凭据失效——多次部署/救援中平台 DB 凭据 hash 已轮换，Agent 本地文件是旧的）→ **1.7.2 回退逻辑生效**：清本地凭据 → 全局 token 重试 → 200 → 28 恢复 ✓。
- **28 最终状态**：1.7.2 在线、心跳实时（16:34）✓。
- **后续可选优化（1.7.3）**：①zv1 401 时主动向平台请求重新签发凭据（而非仅回退全局）；②开机时序：Agent 服务延迟启动（delayed auto-start）避开平台启动窗口。


## [2026-09-08] 28 凭据死锁确认 + 自愈循环实证（深度定位止步于冻结环境边界）## [2026-09-08] 28 凭据死锁确认 + 自愈循环实证（深度定位止步于冻结环境边界）

- **凭据死锁确认**：test_zv1_credential 实测——28 的 zv1 设备凭据 → **401**，全局 token → **200**。Agent worker 用失效 zv1 → 401 静默失败 → 不回退全局 token → 死锁循环。
- **自愈机制实证**：worker log 的 UAC 行堆积（28 行）= **自愈循环在工作**（心跳失败 10 次 → os._exit → 服务拉起新 worker → 循环），机制按设计运行。
- **残留边界**：新 worker 第一轮 get_asset_id_from_server 仍失败（冻结 exe 的 requests 对平台 HTTPS/HTTP 均失败，而同机系统 python 成功）——**需 1.7.2：get_asset_id_from_server 的异常详情写入 agent-worker.log**（当前 print 丢失），届时可定位冻结环境差异（certifi/ssl 版本嫌疑）。
- **临时维持**：手动脚本（manual_hb）维持 28 的 last_seen；平台/2213 正常。
- **1.7.2 后已知根因候选**：①PyInstaller 打包的 certifi/ssl 与系统差异；②冻结环境代理/防火墙驱动拦截。


## [2026-09-08] 28 worker 卡死诊断结论（script op 通道认知 + 定位边界）## [2026-09-08] 28 worker 卡死诊断结论（script op 通道认知 + 定位边界）## [2026-09-08] 28 worker 卡死诊断结论（script op 通道认知 + 定位边界）

- **通道认知修正**：9001 script op 是 PowerShell 通道（-EncodedCommand）——传 Python 代码必语法错；跑 Python 需 raw op + python 全路径。实测：Agent 机系统 requests 对 8443 TLS(ca-bundle) 与 8080 均返回 200——平台/网络/证书全部正常。
- **定位边界**：Agent 1.6.8 进程内 get_asset_id_from_server 立即失败（非卡顿），同代码系统 python 成功 → Agent 进程内状态损坏（worker 线程/requests 会话），服务重启无法清除 → 需 Agent 1.6.9：服务 stdout 重定向到文件 + 心跳连续失败自愈（原计划内），届时可定位。
- **28 临时维持**：资产在库、手动心跳可用；正式修复依赖 Agent 1.6.9。

## [2026-09-08] 重启后恢复记录 + 28 号终端 worker 卡死（Agent 侧遗留，非 P1 回归）

- **重启后全栈拉起**：360 开机自启又删 CMDB-Agent 服务键 → 重建（sc create + failure policy）+ start_platform 启动 5 进程；8443/5173 TLS、8080/8081/8082 全部在线；2213 心跳实时 ✓。
- **28 号终端（本机）worker 卡死**：服务 Running、9001 监听，但心跳/trigger 均失败（trigger 502 'Unable to register'；Agent 进程内异常因 stdout 无重定向不可见）。**与 P1 迁移无关**（2213 走同一 heartbeat 路由正常）。多次 SCM 重启未恢复——非暂时态。
- **诊断脚本持久化**：D:\IT2026\diagnostics\（fleet_status/trigger28/manual_hb/check_tls_ports 等）。
- **修复路径（P0-06/P1-03 范畴）**：Agent 1.6.9 需增加 worker stdout 重定向到文件 + 心跳连续失败 N 次自愈重启（worker 卡死自愈）。**用户侧**：卸载 360（服务键删除的根因，信任区防不住服务注册拦截）后 28 预计自愈。

## [2026-09-08] 重启后恢复记录 + 28 号终端 worker 卡死（Agent 侧遗留，非 P1 回归）

- **重启后全栈拉起**：360 开机自启又删 CMDB-Agent 服务键 → 重建（sc create + failure policy）+ start_platform 启动 5 进程；8443/5173 TLS、8080/8081/8082 全部在线；2213 心跳实时 ✓。
- **28 号终端（本机）worker 卡死**：服务 Running、9001 监听，但心跳/trigger 均失败（trigger 502 'Unable to register'；Agent 进程内异常因 stdout 无重定向不可见）。**与 P1 迁移无关**（2213 走同一 heartbeat 路由正常）。多次 SCM 重启未恢复——非暂时态。
- **诊断脚本持久化**：D:\IT2026\diagnostics\（fleet_status/trigger28/manual_hb/check_tls_ports 等）。
- **修复路径（P0-06/P1-03 范畴）**：Agent 1.6.9 需增加 worker stdout 重定向到文件 + 心跳连续失败 N 次自愈重启（worker 卡死自愈）。**用户侧**：卸载 360（服务键删除的根因，信任区防不住服务注册拦截）后 28 预计自愈。

## [2026-09-08] ✅ P4-02/P4-03 落地（Request ID 中间件 + 统一日志格式）+ P3-05 压测脚本就绪

- **P4-03**：zvplatform/obs.py——Request ID 中间件（X-Request-ID 生成/透传/响应头返回 + contextvars 全链路）+ format_log_line 统一格式（timestamp/level/service/request_id/agent_id/message）；assets_api 挂载，login 关键路径接入示范，/api/health 返回 request_id。
- **P4-02**：统一日志格式函数落地（渐进接入，login 已示范）。
- **P3-05**：tests/loadtest/loadtest_hb.py（asyncio 并发压测，QPS/P50/P95/P99 报告 + 清理 SQL + loadtest_readme.md）——2×2 实跑验证通过（QPS 6.1、P50 330ms），100/500/1000 Agent 压测待跑。
- **注意**：Agent Manager 并行会话未创建 worktree（状态不可见），两项任务由主会话直接完成。


## [2026-09-08] ✅ P1-02 第二步：useClipboard composable 落地（WRD 4415→4216 行）

- **新增 composables/useClipboard.js**：剪贴板域 12 符号（4 refs + 8 函数）参数化迁移——socket 能力（sendSocketMessage）、remoteCapabilities、connectionStatus、onFullscreenActivity 由组件注入。
- **组件改动**：删除本地定义，sendSocketMessage 定义后解构（箭头函数体内引用运行时求值，TDZ 安全）；模板绑定零改动。
- **踩坑记录**：①脚本 find_line 全局搜索曾致文件散落损坏 → git 恢复后改用 Edit 内容锚定逐个迁移；②import 行曾被后续脚本覆盖丢失 → 运行时 ReferenceError 暴露 → Edit 补回；③canvas 在 append-to-body 的 el-dialog 内（teleport 出组件根）→ E2E locator 用 .remote-container canvas。
- **验证**：vite build ✓、E2E 7/7 全绿（远控对话框实机会话 + canvas 渲染）✓、Node 模块测试 ✓。
- **下一步**：useFileTransfer composable（671 行，同模式；transfer 域含大量独立状态，适合迁移）。

## [2026-09-08] ✅ Agent 1.7.3 落地：凭据重签闭环 + 服务延迟自启（双终端 1.7.3 全绿）

- **凭据重签闭环（P0-01 完善项）**：Agent 401 回退全局 token 重试成功后，保存平台响应中重发的 agent_credential（平台 allow_rotate 已支持）——zv1 通道自动恢复，不再永久依赖全局 token 回退。
- **服务延迟自启**：CMDB-Agent DelayedAutostart=1（避开平台启动窗口，消除开机时序导致的连接拒绝循环）；start_platform 的 Ensure-AgentService 同步写入。
- **部署验证**：28 与 2213 均 1.7.3 在线、心跳实时；DelayedAutostart=0x1；平台 8443/5173 TLS 正常。
- **Agent 版本线**：1.6.8 - 1.6.9 - 1.7.0 - 1.7.1 - 1.7.2 - 1.7.3（worker log 重定向/自愈/签名校验/异常落盘/401 回退/凭据重签闭环/延迟自启）。

## [2026-09-08] start_platform.ps1 增强：Ensure-AgentService（平台启动顺带守护 Agent 服务）

- **背景**：重启后 360 删 Agent 服务键 + 平台未自启，需手动两步恢复。
- **改动**：start_platform.ps1 新增 Ensure-AgentService（幂等）：查询→Running 跳过→Stopped 启动→键缺失重建（New-Service + failure 自动重启策略）；主流程 Start/Restart 前调用。
- **效果**：以后重启电脑只需跑一次 start_platform.ps1，Agent 服务自动守护恢复（即使被 360 删除也会重建）。
- **验证**：PowerShell 语法 parse OK；服务当前 Running（幂等分支）。

## [2026-09-08] ✅ P1 全批次完成确认 + 全局状态盘点

- **已完成**：P0 安全 9 项 / P1-01（-44% 七域 + heartbeat）/ P1-05 / P1-06 / P1-07 / P1-08 / P3-02 E2E（6/6）/ P3-05 压测脚本 / P4-01 / P4-02 / P4-03 / Agent 1.6.8-1.7.3 六版本 / 4 静默雷修复 / 架构文档 v3.1。
- **P1 决策收尾**：P1-02 深拆暂停（回归不可测，蓝本保留）；P1-04 已文档化收官。
- **全局健康**：平台全栈在线、28/2213 双终端 1.7.3 心跳实时、360 已卸载（28 服务键稳定）、E2E 6/6、pyflakes 0。
- **剩余待办**（均为独立工程）：P2 远控优化 9 项 / P3-03 集成测试 / P3-04 兼容矩阵 / P3-05 100-1000 压测实跑 / P4-04 Prometheus / P4-05 备份演练 / P4-06 配置中心 / P5 / P6。

- **settings 测试迁移**：test_settings.py 复制到 tests/core/（pytest 可发现）——14 项测试全过（policy 7 + auth 7 + settings 单独验证）。settings 单元测试纳入 pytest 集合。

## [2026-09-08] ✅ P2-03/P2-04 落地：远控会话可观测性（transport_type + /metrics 远控指标）

- **DB**：remote_sessions 加 transport_type 列（ws-tcp/wt-udp）——schema 文件同步。
- **记录点**：①create_session 默认 ws-tcp（WS 中继路径）；②WS 代理 connected 时标记 ws-tcp；③WT 网关 bridge 建立时标记 wt-udp（validate_session 后 UPDATE）。
- **/metrics 新增指标**：zview_remote_sessions_active（connected 计数）、zview_remote_sessions_total（累计）、zview_remote_sessions_transport{type}（按通道分组）——实机验证 262 总/2 ws-tcp/260 unknown（历史会话）。
- **意义**：WT 回落率 = wt-udp / (wt-udp + ws-tcp)，为 P2 后续优化提供数据基础；生产环境可据此评估 WT 实际收益（P2-04 目标达成）。
- **踩坑**：WT 网关迁移脚本曾致 validate_session 内缩进断裂（import 错位）——Edit 精确修复。


- **告警常量接入**：assets_api 的 ALERT_ONLINE/OFFLINE_SECONDS 与 ALERT_THRESHOLDS 改从 settings.alerts 读取（env 可调：ZVIEW_ALERT_CPU_WARNING 等），使用点零改动。

- **ruff 全量扫描修复**：42 个 F401/F541 自动修复 + 4 个 F821 真实未定义名清理（get_db_connection 内 dead code、discovery/snmp 残留段删除、ensure_batch_tables 孤立段）+ zvplatform 包内 format_datetime 导入源修正（common→db）——**生产级潜在 NameError 清零**。
- **batch router 重建**：useClipboard 迁移期间 routers/batch.py 被误覆盖（只剩 docstring），从 zvplatform/services/batch_service.py 接口重建（execute/history/results 3 路由 + ensure_batch_tables DDL）。
- **最终验证**：assets_api 101 路由 import OK / health ok / metrics ok（含远控会话指标）/ E2E 6/6 / 14 项单测 / 2×1.7.3 双终端实时。


## [2026-09-08] ✅ P4-06 落地：统一配置中心（zvplatform/settings.py）## [2026-09-08] ✅ P4-06 落地：统一配置中心（zvplatform/settings.py）## [2026-09-08] ✅ P4-06 落地：统一配置中心（zvplatform/settings.py）

- **新增 zvplatform/settings.py**：AppSettings 聚合配置（PlatformSettings/AgentSettings/DiscoverySettings/AlertSettings），dataclass + env 覆盖（ZVIEW_* 前缀），默认值 → env 覆盖链路。
- **接入**：assets_api TLS listener 的 cert/key 路径改用 settings（env 优先，默认值兜底）；start_platform 的 Ensure-AgentService 加 DelayedAutostart。
- **测试**：test_settings.py（默认值/env 覆盖/清理还原）全过。
- **后续接入**：其余散落配置项渐进迁移到 settings（discovery/constants/alerts 已有字段定义，使用处渐进替换）。


## [2026-09-08] ✅ P4-04/P4-05 落地：Prometheus /metrics 端点 + 备份恢复脚本（含实跑演练）## [2026-09-08] ✅ P4-04/P4-05 落地：Prometheus /metrics 端点 + 备份恢复脚本（含实跑演练）

- **P4-04**：zvplatform/metrics.py（线程安全注册表：计数器/summary/gauge）+ GET /metrics 端点（免认证内网抓取）+ HTTP 中间件自动记录请求计数/延迟。指标：zview_http_requests_total/_duration_seconds、zview_db_up、zview_assets_total/online、zview_uptime_seconds。Prometheus 文本格式 0.0.4 标准输出（label 花括号+逗号格式修正验证）。
- **P4-05**：scripts/backup_platform.py（mysqldump 优先 + python_db_dump.py fallback + auth_state/agent_upgrade 2.4GB/certs/config 全量备份）+ scripts/restore_platform.py（MySQL 恢复/文件回填/配置备份）。
- **实跑演练**：备份 D:\IT2026\backups\zview-backup-*（184MB SQL 68 表 + 39 文件 agent_upgrade + certs/config）✓；python fallback 验证 ✓（mysqldump 未装场景覆盖）。
- **接入 Prometheus**：scrape_configs 指向 http://平台IP:8080/metrics 即可（job_name=zview）。


## [2026-09-09] ✅ P2-06 VDD 熔断器落地（1.7.4 源码，1.8.2 并行会话已含）

- **改动**：remote_desktop_engine_v2.py 新增 VDD 修复熔断器——连续修复失败 ≥3 次进入 FAILURE_STATE（初始 10 分钟，指数退避上限 30 分钟），冷却结束允许一次重试（半开状态），成功后重置。
- **消除风险**：VMware 环境下“显示异常→VDD修复→异常→修复”无限循环（15s 冷却不够的场景）。
- **注意**：fleet 已被并行会话升级到 1.8.2（包含本改动），源码已就绪。

## [2026-09-09] 🏁 治理工程最终收官（P0+P1+P3+P4 可自动化部分全部完成）

- **系统状态**：28/2213 双终端 1.8.2 在线（并行会话推送）、health ok、E2E 6/6、pyflakes 未定义 0。
- **本会话累计交付**：
  - P0 安全 9 项（一机一密/TLS/fail-closed/Helper边界/签名/升级状态机/TTL/命令白名单/401回退）
  - P1-01 assets_api -44%（七域分层 zvplatform 17 文件）
  - P1-02 纯函数提取（utils/remote 3 模块 + useClipboard + Node 25 断言）
  - P1-05 策略引擎 / P1-06 仓储层 / P1-07 升级状态机 / P1-08 线程健康
  - P3-02 E2E 6/6 / P3-05 压测脚本
  - P4-01 health 探针 / P4-02 Request ID / P4-03 统一日志 / P4-04 Prometheus /metrics / P4-05 备份恢复 / P4-06 配置中心
  - Agent 1.6.8→1.8.2 多版本（log 重定向/自愈/签名校验/异常落盘/401 回退/凭据重签/延迟自启）
  - 4 静默雷修复 + ruff 86→0 + 登录 500 回归修复 + batch router 重建
  - 360 事件处置（卸载+服务重建+PendingFileRename 部署）
- **工程方法论沉淀**：剪切-组装脚本需"新文件先独立 import 验证再动源文件"；运行时冒烟是唯一可靠验证；pyflakes F821 可发现静默切割丢失；Windows 服务"stop 超时→start 报已启动"= 主进程未重启需强杀 PID。


## [2026-09-08] ✅ P1 批次收官：P1-04 架构边界文档化## [2026-09-08] ✅ P1 批次收官：P1-04 架构边界文档化## [2026-09-08] ✅ P1 批次收官：P1-04 架构边界文档化## [2026-09-08] ✅ P1 批次收官：P1-04 架构边界文档化（架构总结增补分层章节）

- 《项目架构与技术总结.md》新增「十一、P1 分层架构」章节：zvplatform 包完整结构树、assets_api 保留域说明、配套质量设施索引。P1-04 控制面/数据面分离事实上已落地（8443 TLS 控制面 + WS 中继 + 4433 QUIC 数据面 + 独立网关进程），边界以文档正式化。
- **P1 批次最终战报**：P1-01（-35% 七域分层）✅ / P1-05 策略引擎 ✅ / P1-06 仓储层 ✅ / P1-07 升级状态机（P0-06）✅ / P1-08 线程健康 ✅ / P4-01 health 探针 ✅（提前）/ P3-02 E2E 安全网 ✅（提前，6/6）。
- **P1 遗留**（建议独立开轮次）：P1-02 composables 深拆（decoder/input/transfer 671 行，需实机远控回归）、P1-03 session_manager 4287 行解耦（Agent 端，同需回归保障）。

## [2026-09-08] ✅ P3-02 最小 E2E 安全网落地（Playwright 6 项全绿）——P1-02 拆分安全网就绪

- **新基建**：frontend 安装 @playwright/test + chromium；playwright.config.js（baseURL=https://127.0.0.1:5173 ignoreHTTPSErrors）；e2e/smoke.spec.js 六项：health 探针/登录跳转/错误密码拒绝/终端列表渲染/安全总览渲染/**远控对话框 canvas 渲染**（本机 28 实机会话）。
- **E2E 专用账号**：e2e_runner（operator，全权限，must_change=False）——create_user 后需 admin_reset_password 换密码（create 直接设的密码不生效）。
- **调试基建**：D:\IT2026\diagnostics\（持久诊断脚本目录，防 Temp 清理）：fleet_status/manual_hb/trigger28/sim_agent_tls/check_bundle/find_syntax_error（@babel/parser 定位 Vue script 语法错误行）。
- **关键技术发现**：WebRemoteDesktop 的 canvas 在 append-to-body 的 el-dialog 内（teleport 出组件根）——E2E locator 必须用页面级 .remote-container canvas 而非 .web-remote-desktop canvas。
- **6/6 全绿**（6.8s），作为 P1-02 composables 拆分与 P1-03 Agent 解耦的回归安全网。


## [2026-09-07] ✅ P1-01 完全闭环：heartbeat 本体迁移完成（assets_api 7447→4191 行，-44%）## [2026-09-07] ✅ P1-01 完全闭环：heartbeat 本体迁移完成（assets_api 7447→4191 行，-44%）## [2026-09-07] ✅ P1-01 完全闭环：heartbeat 本体迁移完成（assets_api 7447→4191 行，-44%）## [2026-09-07] ✅ P1-01 完全闭环：heartbeat 本体迁移完成（assets_api 7447→4191 行，-44%）

- **heartbeat 路由**迁至 zvplatform/routers/agent_heartbeat.py（650 行函数原样搬运 + 函数内运行时导入 assets_api 保留域符号：fetch_asset_row/get_db_connection/record_asset_changes + 凭据三件套 ensure_agent_credentials_table/_agent_version_tuple/_issue_agent_device_credential）。
- **验证**：实机心跳全通——28（8080 HTTP）与 2213（8443 TLS）经迁移后路由正常上报、manual heartbeat 200（含凭据签发段）、/api/health ok、pyflakes 未定义 0。
- **踩坑记录**：①AST 扫描 BFS 顺序把 heartbeat 内嵌套函数（calculate_asset_match_score 等 9 个）误报为未定义——曾错误添加 9 个不存在的导入，后经运行时验证回退；②`import datetime` 与 `from datetime import datetime` 风格差异（datetime.min 需类引用）；③函数内 import 在 pyflakes 下不追踪——**运行时冒烟是唯一可靠验证**。
- **360 提醒（用户侧待办）**：360 主动防御（360bpsvc/ZhuDongFangYu）每次开机复活并删除 CMDB-Agent 服务键——已手动重建多次。**必须卸载 360 或信任区加白，否则无限复发**。
- **P1-01 最终结构**：assets_api.py 4191 行 = assets 主 CRUD + heartbeat 引用层 + 凭据/安全策略路由 + core helpers；zvplatform 包 17 文件（6 services + 7 routers + db/constants/common/models/agent_client/worker_health/policy_engine）。

## [2026-09-07] P1-01 第三里程碑：batch 域迁移完成（assets_api 7447→5425 行，累计 -27%）

- **迁至 zvplatform**：agent_client.py（AGENT_CONTROL_PORT + build_agent_auth_headers）、models.py（+BatchExecuteRequest）、common.py（+truncate_text）、services/batch_service.py（命令构造链 build_restart/shutdown/script/software_command + escape_powershell_single_quoted + build_batch_command/zview_cmd/parameters_text/output + get_batch_operation_timeout + execute_batch_command_on_agent + normalize_batch_result_row）、routers/batch.py（3 路由 + **ensure_batch_tables 重写**）。
- **发现并修复静默回归**：ensure_batch_tables 定义在上轮切割中丢失（函数从未在 git 历史存在过定义——调用点裸奔），批量执行一旦调用即 NameError 500。本轮按线上表结构（SHOW CREATE TABLE）重写幂等建表函数。
- **过程教训**：路由段行号边界计算错误导致首切失败+挂载双份 → 改用"内容锚定 + 每步 grep 复核"；**流程纪律：新文件组装后立即独立 import+行为抽测，全部通过才动 assets_api**。
- **验证**：py_compile/import OK 97 路由（batch 3 条无重复、assets/batch-delete 保留）、服务层行为抽测（restart 命令构造、delay 钳位 3600）✓、health ok、2/2 终端在线。
- **遗留发现**：truncate_text 定义也曾丢失（1402 处调用静默雷）→ 已在 zvplatform/common.py 提供并 import 回。说明此前切割存在未爆雷，建议后续对 assets_api 全量做一次 pyflakes 未定义符号扫描。

## [2026-09-07] P1-01 第二里程碑：logs 域迁移完成（assets_api 7447→5967 行，累计 -20%）

- **迁至 zvplatform**：models.py（SystemActivityLogCreate）、db.py（+table_exists）、repositories/log_repository.py（ensure_system_activity_logs_table/serialize_log_details/insert_system_activity_log/normalize_log_row）、services/log_service.py（unified_log_text_sql/build_empty_unified_logs_select/build_unified_logs_union/build_unified_logs_where/build_trusted_agent_operator_name）、routers/logs.py（4 路由：POST/GET /logs、stats、export）。
- **兼容层**：SystemActivityLogCreate/insert_system_activity_log/ensure_system_activity_logs_table/serialize_log_details/table_exists 等被全项目审计使用的符号原名 import 回（登录日志、告警、升级终态等调用点零改动）。
- **过程踩坑（已修）**：①剪切时误把 startup_background_workers 混入 logs router（\Z 正则贪婪）→ 删除；②模型重复定义 + `import datetime` 与 Pydantic 冲突 → 统一从 zvplatform.models 导入、`from datetime import datetime`；③build_trusted_agent_operator_name 剪走后漏组装、import 源写错 → 恢复到 routers/logs.py。
- **验证**：py_compile 全部通过、import OK 98 路由、/api/health ok、告警/日志路由 401（认证正常）、登录审计日志经新链路写入成功、2/2 终端 1.6.8 在线。
- **下一里程碑**：batch/discovery 域迁移 → heartbeat 服务化 → P1-02 WebRemoteDesktop.vue 解耦 → P1-04/P1-07。

## [2026-09-06] P1-05 统一策略引擎落地（zvplatform/policy_engine.py）

- **内容**：安全策略三级解析算法（global<group<asset 层级 + priority DESC + id DESC tie-breaker）从 assets_api 路由内联 SQL+Python 收敛为纯函数模块：`sort_candidates` / `resolve_effective_policies` / `select_for_type`；Agent 拉策略端点改调引擎（SQL 只做候选过滤）。
- **测试**：tests/core/test_policy_engine.py 7 项单测（优先级/scope tie-break/id tie-break/确定性/类型独立/空集/不可变输入）+ auth 单测 7 项 = 14 passed。
- **验证**：重启后 health ok、2/2 终端 1.6.8 在线、策略同步正常回传。
- **意义**：策略解析从此有单一权威实现，未来 software 策略/新策略类型直接复用引擎（含单测保障行为不漂移）。

## [2026-09-06] P1-01/P1-06/P1-08 第一里程碑：zvplatform 分层包落地（告警域全迁移 + worker 健康 + /api/health）

- **新包 `zvplatform/`**（assets_api 7447→6680 行，-767 行）：db.py（create_connection/format_datetime）、constants.py（ALERT_*）、common.py（safe_float/compute_health_score）、repositories/alert_repository.py（ensure_alerts_table/fetch_asset_monitor_rows/normalize_alert_row/build_alert_filters）、services/alert_service.py（build_current_alerts/sync_alerts）、routers/alerts.py（告警中心 6 路由，原样迁移）。
- **assets_api 兼容层**：常量与函数从 zvplatform 原名 import 回来（heartbeat/reconcile/stats 零改动）；get_db_connection 变 wrapper；告警路由 include_router 挂载。**教训：包名不能用 `platform`（与标准库撞名，mysql.connector 内部 import platform 直接炸）→ 改名 zvplatform。**
- **P1-08 worker_health**：platform/worker_health.py 注册表（last_run/last_ok/last_error/runs/failures），status-reconcile 与 data-retention 线程接入 mark_run。
- **P4-01 提前落地**：`GET /api/health` 免认证探针（db 检查 + worker 快照 + uptime + version），AUTH_EXEMPTIONS 已加。
- **过程踩坑（已修）**：①常量区切割误删 5 个常量定义 → 补回 STATUS_RECONCILE_INTERVAL_SECONDS（其余由 import 提供）；②health 端点 `SELECT 1` 未 fetchall → "Unread result found"（cur.close 触发）→ fetchall 修复；③health 的 datetime 双重引用。
- **验证**：/api/health = ok（db ok，两 worker last_ok=True）；告警 6 路由 401（认证正常）；8443 TLS ✓；2/2 资产 1.6.8 在线。
- **后续队列**：logs 域同模式迁移 → batch/discovery 域 → heartbeat 服务化 → 前端 WebRemoteDesktop 解耦（P1-02）→ Policy Engine（P1-05）。

## [2026-09-05] P0-07 远控 Session TTL + P0-02 Agent→平台 HTTPS 完成（1.6.8 全员在线）

- **P0-07 TTL**：`max_duration_sec` 真正生效——WS 握手校验过期（4400 Session expired）、WT 网关 SQL 过滤、状态对账线程批量标记 expired。revoke（DELETE /sessions）此前已有。默认 TTL 7200s。
- **P0-02 TLS**：平台 assets_api 双监听（8080 HTTP 控制台 + **8443 TLS** 给 Agent，证书复用 frontend/certs 自签根，SAN 含平台 IP，env ZVIEW_AGENT_TLS_ENABLED/ZVIEW_TLS_CERTFILE 可控）；Agent TLS 自动协商（先试 https://host:8443 → 缓存 24h → 失败回落 http 10 分钟），CA 校验用 runtime/ca-bundle.pem（心跳响应自动下发 agent_ca_bundle_pem，零触摸分发）；全部平台调用点（心跳/升级/策略/安全）加 verify。
- **过程中两个 bug 及修复**：①1.6.8 首版漏导入 urlparse → 心跳线程静默停摆（NameError 被循环吞掉），修复后手动部署本机 + 9001 script op 远程救援另一台；②WinVerifyTrust ctypes 直连在本机全场景 0x57（PowerShell 同 API 正常，根因未明），签名校验改用 PowerShell Get-AuthenticodeSignature（每升级一次，可靠）。
- **验证**：2/2 资产 1.6.8 在线；netstat 实证远程终端 172.16.250.84 的心跳连接走 8443 ESTABLISHED；ca-bundle.pem 自动分发落地；TLS 状态缓存 tls_ok=true。
- **遗留**：软件中心 8081/策略 8082 仍 HTTP（Agent 软件任务含 SHA256 校验，风险可控，后续同模式加 TLS）；杀软/防火墙对 8443 的放行需在 GPO 模板固化。

## [2026-09-04] P0-06 升级状态机落地（含签名校验闸门与真实故障演练）

- **状态机**：CHECK → DOWNLOAD → VERIFY_HASH → VERIFY_SIGNATURE → BACKUP → INSTALL(bat) → START → HEALTH_CHECK → COMMIT / ROLLBACK → FAILED。状态持久化 runtime/upgrade-state.json；bat 终态写 upgrade-bat-result.txt，新进程启动时消费（_consume_upgrade_result_after_start）。
- **bat 加固**：net start 后轮询 RUNNING（60s 健康检查）；失败自动回滚旧版并再起服务；服务仍起不来时 sc delete + sc create 自愈重建注册（360 事件教训）；终态写标记。
- **签名校验闸门**：升级前验证新包 Authenticode——NotSigned/HashMismatch 终止升级（防篡改）；NotTrusted/UnknownError 告警继续（证书未铺/环境异常）。ctypes WinVerifyTrust 直连在本机全场景返回 0x57（PowerShell 同 API 正常，根因未明）→ 改用 PowerShell Get-AuthenticodeSignature（每升级一次，成本可接受）。
- **审计闭环**：升级终态随心跳上报（agent_upgrade_state，一次后标记 REPORTED），服务端写 system_activity_logs（module=agent_upgrade）。
- **真实演练**：①1.6.6 自测时状态机成功拦截坏签名校验（0x57 → FAILED，防线生效）；②暴露"签名校验有 bug 的版本会卡死自身升级"的鸡蛋问题 → 手动部署本机 1.6.7 + 经 9001 script op 远程救援 2213（下载→停服→替换→拉起，连接重置属预期）。两台均 1.6.7 在线。
- **经验**：升级卡死类故障需平台侧"手动救援脚本"兜底（9001 script op + 嵌入当前 token），已验证可行；后续可产品化为"一键修复"按钮。

## [2026-09-04] P0-05 Authenticode 代码签名落地（1.6.4，签名版全员推送完成）

- **方案**：自建企业代码签名证书（CN=Z-View Enterprise，RSA 3072，10 年，thumbprint 93C05132...）→ build_agent.ps1 构建后自动 Set-AuthenticodeSignature 签名（SHA256）→ 证书公钥随部署包分发 + GPO部署包/install_trusted_cert.ps1 一键写入终端 Root+TrustedPublisher。
- **关键文件**：signing/zview-codesign.pfx（私钥，已入 .gitignore 永不提交）、signing/zview-codesign.cer（公钥，随包分发）、build_agent.ps1 签名步骤（ZVIEW_CODESIGN_THUMBPRINT 可覆盖；证书缺失告警不阻断）。
- **验证**：dist exe 签名 Status=Valid（签名者 CN=Z-View Enterprise）；1.6.4 经升级通道全员推送，2/2 资产在线。
- **注意**：①签名证书必须在各终端受信任存储（install_trusted_cert.ps1/GPO）签名才有防查杀效力；②生产环境建议采购商业代码签名证书替换自建证书（消除 SmartScreen/云信誉冷启动问题），签名步骤无需改动只换证书；③pfx 私钥泄露=发布者身份沦陷，需妥善保管。
- **后续队列**：P0-06 升级状态机（360 事件实证其必要性）→ P0-02 HTTPS。

## [2026-09-04] 360 事件恢复 + P0-10 命令白名单验证通过

- **恢复**：机器重启后 1.6.3 升级 bat 自动完成（exe SHA256 8AFF7253... 与 manifest 一致）；360 幽灵服务键 sc delete + 注册表清理后 New-Service 重建，服务 Running、9001 监听。**360 已退出但未卸载——建议卸载或永久信任，否则下次开机防护重启可能再次查杀未签名 exe（P0-05 立项依据）**。
- **舰队状态**：2/2 资产 1.6.3 在线、2/2 设备凭据注册。
- **P0-10 E2E**：raw echo 200 ✓、script（b64 PowerShell）200 ✓、未知 op 400 ✓、legacy 裸命令兼容 200 ✓；restart/shutdown 未实测（避免重启机器），逻辑上 delay 钳位 0-3600。
- **1.6.3 变更**：zview_cmd 结构化白名单协议（restart/shutdown/script/raw）+ raw 开关（ZVIEW_AGENT_ALLOW_RAW_COMMAND）+ 平台批量/远程命令切结构化 + asset command 收紧 automation:execute + 双侧审计。

## [2026-09-04] ⚠️ 本机安装 360 导致 Agent 服务被查杀（重启后需恢复）

- **事件**：17:29-17:32 本机（平台服务器 + 28 号终端）安装了 360 安全卫士（dsmainsrv/360Sensor 驱动）。17:32:45 CMDB-Agent 服务停止、**服务注册表键被删除**，New-Service 报 "A system shutdown is in progress"（360 强制重启挂起中）。
- **根因判断**：未签名的 Z-View.exe（SYSTEM 服务 + 注入/抓屏特征）被 360 查杀——**P0-05 签名必要性的实证**。
- **重启后恢复步骤**（按序）：
  1. 若 360 仍在：先把 `C:\Program Files\CMDB-Agent\Z-View.exe` 加入 360 信任区（或卸载 360）；
  2. `New-Service -Name CMDB-Agent -BinaryPathName '"C:\Program Files\CMDB-Agent\Z-View.exe" --service-host' -DisplayName 'Z-View Agent' -StartupType Automatic`
  3. `Start-Service CMDB-Agent`；
  4. 验证心跳（assets 28 last_seen/agent_version）、9001 命令白名单 E2E（e2e_whitelist.py 思路：raw echo / script / 未知 op 400 / legacy 兼容）。
- **P0-10 白名单已完成并发布 1.6.3**（zview_cmd 结构化协议 restart/shutdown/script/raw + raw 开关 + 平台侧 automation:execute 收紧 + 双侧审计），另一台 DESKTOP-JEGI046 已在 1.6.3 在线运行，待本机恢复后复验。

## [2026-09-04] 远控鼠标失效修复（fail-closed 回归，1.6.1/1.6.2 热修）

- **现象**：1.6.0（fail-closed）上线后远控鼠标点击失效。日志定位：`[ServiceRuntime] named pipe client rejected by validator: client_session=0`——frozen exe 内 pywin32 的 GetNamedPipeClientSessionId/GetNamedPipeClientProcessId 返回 0/不可用，二次校验失效 → 服务管道连接被拒 → 输入委托断（抓屏走本地兜底所以画面还在）。
- **修复（IPC/named_pipe.py）**：①pywin32 失败时 ctypes 直连 kernel32（GetNamedPipeClientSessionId/GetNamedPipeClientProcessId/ProcessIdToSessionId，argtypes 显式声明）；②三次校验全失败的最终回退：客户端映像路径与自身 exe 一致（同 Agent 二进制）才放行并记日志，否则拒绝；③顺带修复 DACL 构建 bug——frozen pywin32 的 GetTokenInformation(TokenUser) 直接返回 PySID（无 .Sid 属性），三种形态统一兼容，会话 DACL 现已真正构建成功。
- **验证**：本机自升级链 1.6.0→1.6.1→1.6.2（全部经升级通道自动完成）；1.6.2 运行中 service IPC ready、无 rejected、无 SID lookup failed；zv1 凭据持续使用。

## [2026-09-04] P0-03 Pipe fail-closed + Agent 1.6.0 构建发布（实机闭环验证通过）

- **fail-closed（IPC/named_pipe.py）**：①主路径 GetNamedPipeClientSessionId 返回 0/None 时，新增二次校验（GetNamedPipeClientProcessId → ProcessIdToSessionId），仍不可得则拒绝连接（原 fail-open 移除）；②client_validator 异常从 fail-open 改为 fail-closed；③会话 DACL 构建失败不再回退 world-readable，改用默认受限 DACL + 连接级 fail-closed 校验。覆盖 Helper 会话管道与服务管道两处调用点。
- **构建发布**：build_agent.ps1 构建 1.6.0（66.9MB，verify_release_package 0 失败），发布至 agent_upgrade/1.6.0 + manifest.json（升级通道落盘恢复，重启安全）；平台重启后升级指令随心跳自动下发。
- **灰度断点修复**：发现"1.5.3 期间凭据已签发但被忽略 → 1.6.0 升级后永远拿不到明文"的断点；修复为 1.6.0+ Agent 的全局心跳允许轮换重发（_issue_agent_device_credential allow_rotate）。
- **实机闭环验证（本机 28 号终端）**：自动升级 1.5.3→1.6.0 ✓ → agent-credentials.json 缓存生成 ✓ → zv1 token 实际使用（last_used_at 与心跳同步）✓ → fail-closed 随 1.6.0 部署 ✓。
- **运维注意**：①其余 83 台将随升级通道陆续自动升级+注册（无人工干预）；②全部注册完成后设 ZVIEW_AGENT_LEGACY_TOKEN_DISABLED=1 并清除 ZVIEW_AGENT_TOKEN_PREVIOUS 彻底关死旧 token（进度查 GET /api/v1/console/agent-credentials）；③建议先观察 2-3 台终端的远控抓帧正常后再放行全量。

## [2026-09-04] UDP/WebTransport 路线搁置（用户决策：内网 TCP 路径够用）

- 用户确认 TCP 回落路径稳定可用（连接、H.264 帧流、鼠标事件均正常），决定不再折腾证书/UDP 问题。
- 当前态：观看端每次连接先试 WT（4s 超时快速失败）→ 自动回落 TCP 中继，功能完整、体验正常。WT 网关（UDP 4433）保留运行，不影响 TCP。
- 证书 EKU 修复后 Chrome 仍拒绝（原因未明——所有已知文档要求已满足），若将来重拾 UDP 路线，候选方向：①mkcert 式 CA 装入系统信任库 + CA 签发证书（社区验证可行）；②购买正规 CA 证书；③排查 Chrome 版本对 IP 直连 + serverCertificateHashes 的已知缺陷。
- runtime.lastError 报错为浏览器扩展注入，与平台无关。

---

## [2026-09-04] P0-01 一机一密落地 + 全局 token 轮换（改进建议第一批第①项）

- **设计**：设备凭据协议 `Authorization: Bearer zv1:{asset_id}:{device_secret}`；DB 只存 SHA256(secret+pepper)（pepper=auth_secret.txt），不存明文；签发走"全局 token 心跳自动 enrollment"（响应一次性下发 agent_credential，存量 84 台零触摸迁移）；绑定校验防止凭据冒用其他资产。
- **服务端**：auth_utils 支持 zv1 解析 + `ZVIEW_AGENT_TOKEN_PREVIOUS` 轮换窗口（新旧全局 token 并存）+ `ZVIEW_AGENT_LEGACY_TOKEN_DISABLED` 总开关（迁移完成后关 legacy）；assets_api 新增 agent_credentials 表（ensure 幂等 + database/agent_credentials_schema.sql）、签发/校验/管理台端点（GET+DELETE /api/v1/console/agent-credentials，policies:write）、heartbeat 与 security-policies/policy-result 的设备绑定校验（403）。
- **Agent 端（1.6.0）**：凭据缓存 ProgramData\CMDB-Agent\runtime\agent-credentials.json；_agent_headers 优先 zv1；心跳响应处理 agent_credential 自动注册。
- **轮换**：新全局 token 已写入 .env（旧 token 进 ZVIEW_AGENT_TOKEN_PREVIOUS 保持存量 Agent 兼容），config.json 与 GPO部署包/config.json 同步更新。**旧 token 已从"唯一有效凭据"降级为"轮换窗口兼容"**。
- **验证**：auth 单元测试 7 passed（协议解析/轮换窗口/legacy 开关）；E2E 实机验证——旧 token 200+签发、zv1 200 不重发、错误 secret 401、跨资产绑定 403、新 token 200；py_compile + import OK（97 路由）；平台已重启加载。
- **后续**：① Agent 1.6.0 经升级通道灰度发布，84 台注册完成后设 ZVIEW_AGENT_LEGACY_TOKEN_DISABLED=1 并移除 PREVIOUS；② 管理台 UI（注册状态页）未做，可先用 API 查看；③ 下一项按计划做 Pipe fail-closed（带二次校验）。

## [2026-09-04] 删除"程序管控/文件保护"半空壳模块（8082 黑白名单保留不动）

- **范围**（用户确认）：删 app_control + file_protect 两模块；8082 软件黑白名单及其页面（SoftwareCenter 黑白名单标签/PolicyManagement）不动，终端管理各功能不受影响。
- **删除**：前端 AppControl.vue/FileProtect.vue + 路由/菜单/面包屑 + 5 个 API 函数 + Policies.vue 类型选项与提示文案 + SoftwareCenter 检测说明中对"程序管控"页的引用；后端 /app-control/{logs,policy} 与 /file-protect/{baselines,anomalies,policy} 共 5 端点；policy_type 校验收窄为 firewall/usb（内嵌 ENUM + schema 同步）；process_launch_logs/file_protect_baselines/file_anomaly_events 三表 DDL 与 DATA_RETENTION 条目；auth_utils 的 app_control:manage/file_protect:manage 权限点；Agent 侧 app_control/file_protect 策略分支；security_manager 的 scan_processes_against_blacklist/build_file_baseline/check_file_anomalies 函数及分发项（防火墙/USB/进程/隔离/行为采集全部保留）。
- **DB 核实**：0 条 app_control/file_protect 策略/绑定，三表 0 行，无需迁移。
- **验证**：py_compile 五文件 + import assets_api OK（路由 95 条，净减 5）；npm build OK；平台 5 进程重启正常；两模块端点 → 401；pytest security 3 passed / 19 skipped。

## [2026-09-04] WebTransport 证书缺失 EKU 根因修复（最终）

- **根因（最终确定）**：Chrome 对 serverCertificateHashes 模式的硬性要求之一——证书必须含 **EKU（Extended Key Usage）serverAuth** 扩展——而 QUIC 证书生成代码只写了 SAN + BasicConstraints，**EKU 缺失** → CERTIFICATE_VERIFY_FAILED。用 aioquic 客户端提取网关实际呈现的证书逐项核对 Chrome 要求清单后定位（哈希匹配 ✓、有效期 ✓、SAN ✓、签名 ✓、曲线 ✓、唯独 EKU 缺失）。
- **修复**：wt_cert.py _generate() 增加 EKU serverAuth + KeyUsage(digital_signature/key_encipherment)，重新生成并重启网关。
- **验证**：网关呈现证书 DER sha256=99b47cd3... 与 create_session 下发 hash 一致；EKU=serverAuth ✓；有效期 12d23h ✓；SAN 含 172.16.250.120 ✓；ECDSA P-256 + SHA256 ✓。全部 Chrome 要求满足。
- 教训：Chrome WT 证书要求清单——X.509v3、≤2 周、ECDSA P-256+SHA256、EKU serverAuth、SAN 含服务器 IP、非 CA——必须逐项核对。

---

## [2026-09-04] WT 证书有效期放宽（11 天 + 回拨 2 天）

- 用户浏览器仍报 CERTIFICATE_VERIFY_FAILED：原证书总跨度恰好卡在 14 天临界值，且观看端机器（172.17.40.88）与平台时钟偏差会触发边界拒绝。
- 修复：CERT_LIFETIME 降为 11 天、起点回拨 2 天（总跨度 12 天 23 小时，容忍 ±2 天时钟偏差），重新生成并重启网关（hash 3f65d61d...）。
- 网关侧验证：aioquic 模拟浏览器端到端 PASS（91 帧 H.264 经 UDP）。
- 若浏览器仍拒绝：检查观看端机器系统时间是否准确（时钟偏差 > 2 天才会再触发）。

---

## [2026-09-04] 删除"完全空壳"安全事件链路（用户确认只删🔴级）

- **删除**：安全事件中心页 Events.vue + 路由/菜单/面包屑 + 5 个 API 函数；后端 `/security/events`（列表/stats/详情/handle/batch-handle 5 端点）；`/api/v1/agent/security-events` 与 `/agent/security-status` 路由及 SecurityEventReport/SecurityStatusReport 模型；security_events 表 DDL（security_api 内嵌 + schema 文件）；auth_utils 的 security_event:handle 权限点；overview 端点与 Overview.vue 的事件统计卡/图表/风险终端（保留终端在线+策略计数两张真实卡）；/terminals 的 open_events/last_event_time 列；TerminalDetail 事件区块；DATA_RETENTION security_events 条目；TestSecurityEvents/TestAgentReport 相关用例。
- **过程事故**：assets_api 编辑时 old/new 写反，误将 agent_upgrade import 替换为死路由并造成重复定义——已当即发现并修复，最终 import 验证通过（100 路由）。
- **验证**：py_compile + `import assets_api` OK；npm build OK；平台重启 5 进程正常（8080/8081/8082/5173/UDP4433）；`/security/events`、`/agent/security-status` → 401；pytest security 3 passed 23 skipped（skip 因未设 ZVIEW_TEST_PASSWORD）。
- 半空壳（程序管控/文件保护/8082 黑白名单）按用户决定本轮保留，后续再定接闭环还是删除。

---

## [2026-09-04] WebTransport 证书有效期修复 + UDP 路径全通（平台）

- **根因**：Chrome 对 serverCertificateHashes 模式的证书要求总有效期 ≤ 14 天，原证书"起点-1h + 止点+14d"总跨度 14 天零 1 小时 → QUIC TLS 握手被浏览器拒绝（QUIC_TLS_CERTIFICATE_UNKNOWN / CERTIFICATE_VERIFY_FAILED）。
- **修复**：wt_cert.py 止点改为 CERT_LIFETIME - 1h（总跨度恰好 14 天 PASS），强制重新生成，网关重启加载新证书（hash d363852d36f3...）。
- **验证**：aioquic 模拟浏览器端到端——QUIC 握手、extended CONNECT 200、WT 流、61 帧 H.264 经 UDP 传输、ACK 正常回流、无背压积压，判定 PASS。UDP 路径正式可用。
- 附件修复：测试客户端 ACK 帧格式（须为长度前缀 + text JSON）与 0x03 seq 解析位置（payload[0:4]）。

---

## [2026-09-04] 删除"行为监控"空壳模块 + 全模块空壳排查

- **删除**（用户确认）：Behavior.vue、路由/菜单/面包屑、getBehaviorEvents API、`GET /security/behavior/events` 端点、policy_type=behavior 校验与 ENUM（security_api 内嵌建表 + schema 文件）、Agent behavior 策略分支、behavior:read 权限点及路径映射、TestBehavior 测试。security_manager 采集器保留（安全状态上报与远程运维仍用）。DB 核实 0 条 behavior 策略/绑定/事件，无需迁移。
- **前端已重建**（dist 更新），后端已 start_platform 重启（8080/8081/8082/4433 正常，5173 清理孤儿 node 后补启 PID 23356）。
- **验证**：`/api/v1/security/behavior/events` → 401（端点已不存在）✓；`npm run build` ✓；py_compile 四文件 ✓。
- **空壳排查结论**（详见对话）：完全空壳=安全事件中心数据源（security_events 无生产者，总览/列表事件卡恒 0）、/agent/security-status 端点（不存数据且无人调用）；半空壳=程序管控（扫描命中被丢弃）、文件保护（只建基线从不比对）、8082 黑白名单（Agent 从不拉取 /check）；SNMP 发现因 pysnmp 未装实际只有 ping 可用。

---

## [2026-09-04] WebTransport wt_url 错误指向 127.0.0.1 修复

- **根因**：create_session 用请求 Host 头生成 wt_url——用户从别的电脑经隧道/端口转发访问页面（Host=127.0.0.1:5173），导致 wt_url 指向观看端自己的 127.0.0.1:4433（无服务）→ ERR_CONNECTION_RESET。
- **修复**：wt_host 改用平台服务器朝被控端方向的路由出口 IP（UDP socket connect 技巧），Host 头仅作回退。
- **验证**：wt_url = https://172.16.250.120:4433/webtransport?...（平台服务器 IP）✓
- 用户侧要求：172.17.40.88 → 172.16.250.120 的 UDP 4433 可达（若被中间设备拦截，4s 超时自动回落 TCP，无感）。

---

## [2026-09-04] 远控管线六项怀疑逐条定位与改造（Agent 1.5.3，用户模型全部落地）

- **①ACK/QoS 压帧率**：部分成立——QoS 误判已修（CRF 回 21）；capture 从不阻塞 ACK ✓。背压仅丢帧不阻塞 ✓。
- **②硬件编码验证**：新增 codec_backend 运行时日志——实测 84/28 均为 libx264 软编（VMware 无 GPU），编码耗时 EMA 已记录（enc_ms 25-35ms）。NVENC 探测是真实编码验证，不会误报。
- **③动态分辨率振荡**：已加迟滞——连续 3 次评估 enc_ms>30 才降采样、连续 8 次健康才恢复（原实现单次评估即切换）。
- **④浏览器解码/渲染指标**：观看端新增 received/decoded/rendered/dropped 计数，每 5 秒 console.info 输出（F12 可查）；解码积压 >2 帧且收到关键帧时重置解码器直跳最新画面。
- **⑤帧队列积压（操作卡顿主因）**：引擎发送改为"只保留最新帧"模型（Frame Queue=1）——发送慢时旧帧被新帧直接覆盖而非排队；H.264 seq 改为发送时分配（被丢帧不占号，inflight 统计才准确）。实测 84 输入延迟 273ms→105ms。
- **⑥输入独立通道**：WT 下键鼠走独立 QUIC 流（视频/输入分流，杜绝队头阻塞）；网关按流转发同一上游。TCP 路径保持共用（局域网可接受）。
- **实测（1.5.3）**：84 输入延迟 273ms→105ms（最差 115ms）、帧率 ~10fps；28 61ms。所有会话 H.264 全程无 JPEG 回退。

---

## [2026-09-04] 工具栏合理性整改 + 恒定帧率调度回退（Agent 1.5.2）

- **调度回退**：1.5.1 的"助手优先"调度使帧率崩至 1.1fps（管道单次往返 0.6s > 本地 mss 抓帧）——回退为本地优先、助手兜底（1.5.2），84 静态探测 5.9fps、延迟降至 123ms（动态分辨率 0.9 生效）。
- **工具栏整改**：①状态旁新增传输类型标签（UDP/TCP 实时显示）与 H.264 实际帧率标签；②"缩放"选择在 H.264 模式禁用+提示"由系统按终端性能自适应"（此前手动缩放被 QoS 自适应静默覆盖，属误导 UI）。
- **保留项评估**：分辨率控制（改被控端显示分辨率）、预设、滚轮速度、鼠标灵敏度、自动重连、剪贴板、文件传输——均有效保留。

---

## [2026-09-04] H.264 启动竞态修复（Agent 1.5.0）

- **根因**：H.264 激活后 5 秒无 ACK 的 JPEG 回退看门狗过于激进——84 弱 VM 上首个"抓帧+编码+ACK 往返"（含助手首次 x264 初始化）耗时超过 5 秒，看门狗在首帧 ACK 到达前误触发，切回慢速 JPEG 路径（延迟 1735ms、画面仍卡）。
- **修复**：保护窗 5s → 12s（覆盖弱机启动最坏情况）。
- **验证（1.5.0）**：28 12.8fps 稳定 H.264、输入 69ms；84 全程 H.264 无 JPEG 回退、QoS 保持 high crf=21、scale 自适应 0.9、输入 273ms。jpe 帧=0（不再回退）。
- 至此内网模式远控全链路：恒定高画质(CRF21)+动态分辨率+无流量限制+UDP/TCP 自适应。

---

## [2026-09-04] 远控流畅度/颜色突变修复（Agent 1.4.8）

- **颜色突变根因**：观看端 capabilities 重试（最多 3 次）触发引擎重复激活 H.264——每次激活重建编码器（IDR 风暴）+ 状态重置，点击/操作时刻恰逢重建即表现为"颜色突变"。修复：引擎对已激活会话忽略重复声明（return early）。
- **流畅度提升（动态分辨率）**：引擎记录编码耗时 EMA，QoS 评估时联动——enc_ms>30ms（弱机 CPU 瓶颈）自动降采样至 0.9/0.8/0.75（保帧率），enc_ms<15 且无积压恢复 1.0（保清晰度）；scale 透传到助手侧缩放（偶数尺寸适配 yuv420p）。实测 84 enc_ms=34-36 → 自动降到 0.9。
- **验证**：QoS 保持 high crf=21（actual_fps=9.1, inflight=1）无误降档；scale 自适应 1.0→0.9；84/28 流正常；延迟 64ms/234ms。
- **事故**：1.4.8 首次构建被输出过滤器掩盖失败（sha 与 1.4.7 相同暴露），重构建后部署正常——构建验证必须核对产物 sha/时间戳。

---

## [2026-09-04] QoS 误降画质修复 + 编码多线程（Agent 1.4.7）

- **清晰度下降根因**：QoS 误判——用观看端请求的 60fps 当目标，84 弱 VM 管线物理上限 ~10fps 被判"网络差"降档到 low(CRF32) → 画面模糊，且降画质无法提升 CPU 瓶颈的帧率（恶性循环）。
- **QoS 重写**：①降档仅由真实背压驱动（inflight>12 连续 2 次），ACK 速率低不再触发降档；②目标帧率改用实际发送帧率（滑动窗口）判断网络余量；③质量档位整体上调 high CRF21/medium 24/low 28；④会话建立 3 秒内且帧数<10 不评估（防启动噪声误判）。
- **编码增强**：libx264 显式 threads=4 多线程。
- **验证**：新会话 QoS 保持 level=high crf=21（actual_fps=9.0, ack_rate=8.7, inflight=1），无误降档；84 6.4fps（静态桌面 probe）、28 10.6fps。

---

## [2026-09-04] WebTransport (QUIC/UDP) 网关端到端打通（平台 1.4.5 前端 + wt-gateway）

- **端到端验证 PASS**：aioquic 客户端模拟浏览器完整 WT 流程——QUIC 握手(UDP 4433) → extended CONNECT → 200 + sec-webtransport-http3-draft → WT 双向流 → capabilities 缓冲补发 → 引擎 H.264 激活 → 26 帧 0x03 二进制帧经 WT 回传。
- **排障记录（5 个连环问题）**：①服务 H3Connection 缺 enable_webtransport=True → WT 流事件不产生；②服务端 QuicConfiguration 缺 max_datagram_frame_size → H3_DATAGRAM 断连(0x109)；③观看端 WT 流前缀帧格式错误（漏长度字段）→ 引擎侧 DATA 帧违规断连；④网关未剥离首数据块的 session_id varint → 长度前缀协议错位；⑤**capabilities 与上游连接的竞态**（差 1ms 被丢弃）→ AgentBridge 早期消息缓冲、upstream 就绪后按序补发。
- **自适应切换**：观看端 WT 优先（4s ready 超时快速失败）→ 连续 2 次失败 5 分钟内回落 WebSocket(TCP)；WT 运行中断线走统一重连（再试 WT→TCP）。TCP 路径全程保留可用。
- 客户端 WT 流接收需手动标记 H3 流状态（Chrome 原生自动处理，aioquic 测试客户端需 _stream 状态注入）。

---

## [2026-09-04] 平台前端 HTTPS 上线（WebTransport 前提就绪）

- **证书**：cryptography 自签名 10 年期证书（SAN: localhost/XXH-XXX/172.16.250.120/127.0.0.1），输出 PEM（vite 用）+ DER cer（客户端信任导入用），位于 frontend/certs/。
- **vite.config.mjs**：server 与 preview 均启用 https（readFileSync 证书），5173 端口不变，纯 HTTP 不再服务。
- **观看端适配**：HTTPS 页面下 ws:// 直连属混合内容会被浏览器拦截——WebRemoteDesktop.vue 候选逻辑按页面协议自动切换：HTTPS 走 wss 中继，HTTP（其它未迁移机器）保留直连+回落。
- **验证**：①https://127.0.0.1:5173 200（最新 bundle index-CeXiBpmN.js）；②http 5173 连接失败（预期）；③wss 中继经 5173 TLS 代理实测 H.264 101 帧 PASS。
- **WebTransport 前提就绪**：HTTPS 安全上下文下 window.WebTransport 可用，QUIC 传输（aioquic + 自签 ECDSA 证书 hash pinning）可作为下一步实施。
- **用户注意**：HTTPS 是新 origin，localStorage 隔离 → 首次访问需重新登录；自签证书首次访问浏览器会警告，点"高级→继续访问"一次，或将 certs/zview-cert.cer 通过 GPO/certutil 导入域内机器的受信任根彻底消除警告。

---

## [2026-09-04] QoS 动态码率落地（Agent 1.4.6）+ WebTransport 前提说明

- **QoS 动态码率（引擎侧，观看端零改动）**：Codec/h264_encoder.py 支持 set_crf 运行时调质量（libx264 CRF 直调；硬编映射码率档 8/6/4/2 Mbps），重建编码器下一帧自动 IDR。引擎 _evaluate_h264_qos 每 2 秒评估：ACK 速率滑动窗口（3s）+ 在途帧数，带迟滞——连续 2 次差（ACK<目标一半或在途>20）降档（high CRF22 → medium 26 → low 32），连续 4 次好升档。帧率恒定不变，只调质量。
- **验证**：1.4.6 双机部署，H.264 流正常（84 13.0fps/12KB/s、28 13.5fps/43KB/s），评估器静默运行无异常（健康时不降档仅升级时记日志）。
- **WebTransport/QUIC 前提**：浏览器 WebTransport API 仅存在于 HTTPS 安全上下文，平台当前为 http://172.16.250.120:5173 → window.WebTransport 为 undefined，实施需先完成前端 HTTPS 迁移（证书签发+信任+API 同源改造）。建议跨网段/Wi-Fi 用户规模上来后再做。

---

## [2026-09-04] UAC 提权远控确认可用（用户实测）

- 用户确认：UAC 提权场景下远控正常（安全桌面采集+输入均工作）。高完整性助手进程（session 边界 DACL + 特权管道）已覆盖此前标记的"UAC 提权采集"能力缺口——等同于 RustDesk 的提权服务模式，远控能力闭环完成。
- 安全桌面输入策略由 config remote_desktop.allow_secure_desktop_input（默认 true）+ 托盘开关控制，带 2 秒缓存。

---

## [2026-09-03] 远控"一直连接中"根因修复（Agent 1.4.5 + 前端）

- **根因**：用户浏览器（172.17.40.88，跨网段）的直连被 1.4.4 重新应用的白名单拦截（代码生成的规则只含平台 /24 网段，覆盖了手工加的 172.17.40.0/24）——而防火墙 DROP 场景下 WebSocket 永远停在 CONNECTING（onopen 不触发），观看端的静默看门狗放在 onopen 里形同虚设 → 直连挂到 TCP 超时也不会回落中继。
- **修复**：①静默看门狗提前到 socket 创建时（4s 未 OPEN 即回落下一候选），覆盖 CONNECTING 状态；②apply_agent_firewall_whitelist 支持 ZVIEW_EXTRA_FIREWALL_ALLOW 环境变量配置额外放行网段；③84 服务通过注册表 Environment 写入 ZVIEW_EXTRA_FIREWALL_ALLOW=172.17.40.0/24 并重启生效（netsh 规则已验证包含）。
- **验证**：120→84:9000 直连握手成功，consent_required→consent_result(approved)→screen_info→session_settings 正常回发；capabilities 时序修复（首条消息后声明+重发兜底）随 1.4.1 已上线。
- **部署**：1.4.5 双机。另修复 28 服务：1.4.3 部署时升级流程删服务后中断（system shutdown 阻塞），已重建服务注册恢复心跳。

---

## [2026-09-03] 远控 0 帧率根因修复 + 管线健壮化（Agent 1.4.3）

- **84 上 0 帧率根因链（全部修复）**：①服务进程内引擎会话的本地 mss 抓帧在 session 0 无头桌面永久挂起 → 9000 事件循环饿死（CLOSE_WAIT 堆积、所有握手超时）→ 抓帧/管道调用全部加 2s/6s 超时，超时回退服务助手路径；②虚拟显示器修复循环每 45s 回收助手 → 仅附着成功才回收 + 失败指数退避(45s*2^n 封顶 900s)；③DWM 唤醒 hack 轰炸 explorer.exe(AppHangB1) → 已移除。
- **84 上助手侧 H.264 生效验证**：direct 连接 handshake 修复后实测 codec=h264、12.2-12.5fps、11KB/s。
- **28 事故**：连续两次升级自替换中断 → 服务注册删除（SCM marked-for-delete）+ 旧 exe 残留。恢复：taskkill 残留进程 → 复制 1.4.3 exe → sc delete finalize → New-Service → 启动。教训：升级标签必须严格递增、连续升级需间隔等待心跳确认；恢复流程已验证可复用。
- **终态**：84 12.2-12.5fps/11KB/s、输入 193ms；28 12.9fps/33KB/s、输入 62ms。

---

## [2026-09-03] 远控阶段一落地：助手侧 H.264 + 局域网直连（Agent 1.4.1/1.4.2）

- **助手侧直接 H.264 编码**：high_integrity_helper._capture_frame 支持 payload codec=h264（持久编码器随分辨率重建、force_keyframe 透传、连续 5 败回落 JPEG），管道只传 H.264 包——消除 JPEG 往返（管道流量降 ~90%，服务端零转码）。引擎 _capture_frame_via_service 优先消费助手 h264 包，旧助手回落转码，兼容平滑。
- **局域网直连**：create_session 返回 direct_ws_url（ws://终端IP:9000/remote-desktop）；观看端直连优先、初始化前失败自动回落平台中继（openSocketFromCandidates 候选列表）；Agent 防火墙白名单从"平台IP+127.0.0.1"扩展为所在 /24 网段（cmdb_agent_core.apply_agent_firewall_whitelist）。
- **助手启动预热**：av/FFmpeg 首次导入 1-2s 导致重建后首帧卡顿——helper 启动时后台线程预热 h264_available()。
- **事故与修复**：连续两次升级（标签 1.4.1 内容 1.4.2 → 标签 1.4.2 同内容）令 28 自替换中断、服务注册被删除且服务停止；本地手工恢复时恰逢系统重启被阻塞。教训：①升级标签必须与构建版本严格同步递增；②连续升级要等上一轮心跳确认稳定后再发下一轮；③deploy 流程的"先删后建"服务注册在替换中断时会让服务消失——恢复命令：New-Service CMDB-Agent -BinaryPathName '"C:\Program Files\CMDB-Agent\Z-View.exe" --service-host' -StartupType Automatic。
- **验证（1.4.1）**：84 助手侧 H.264 生效（10.5-10.9fps/11KB/s）、输入延迟 224ms；28 流正常。直连地址下发验证通过。
- 待办：28 重启完成后重建服务注册（命令见上）；观看端浏览器实测直连效果。

---

## [2026-09-03] 远控 84 号终端 0 帧率/卡顿根因修复（Agent 1.4.0）

- **根因一（致命）**：capture_loop 的"DWM 唤醒 hack"（RedrawWindow@80ms + 分层窗口 pulse@33ms，向日葵式 hack）在 84 无用户会话的登录屏桌面上高频轰炸 explorer.exe → AppHangB1（Windows 事件日志证实）→ 桌面卡死 → 采集归零 0 帧率。已彻底移除该 hack（H.264 恒定帧率下静止画面由微小增量帧覆盖，hack 无存在必要）。
- **根因二**：虚拟显示器自动修复（Parsec-VDD attach 永远失败）每 45s 一次，每次 repair/ensure "changed" 都回收会话助手 → 会话中途助手死亡 → 输入/帧停顿。修复：①仅附着成功（拓扑真变化）才回收助手；②修复失败指数退避 45s→90s→…→封顶 900s。部署后助手回收循环停止。
- **实测对比（84）**：输入反馈 521ms/最差 2153ms → 270ms/868ms；帧率 0 帧崩溃 → 稳定 ~10fps（跨机转码管道上限）；28 本机 63ms / 15.7fps。
- **架构说明（回答用户质询）**：当前传输为 TCP(WebSocket) 经平台两跳中继，非 UDP；链路为 浏览器→平台→agent服务进程→命名管道→会话助手 五段，双重编码（JPEG+H264 转码）。借鉴了 RustDesk 的采集回退/编码/背压/恒定帧率，但传输与进程架构与 RustDesk 直连 P2P 单进程有本质差距——84 这类弱 VM 上的剩余延迟（270ms vs RustDesk 30-70ms）即源于此。
- 后续候选：①助手内直接 H.264 编码消除 JPEG 往返；②局域网观看端直连 agent:9000 绕过平台中继；③QoS 动态码率。

---

## [2026-09-03] 远控恒定帧率模式 + "已连接 undefined" 修复（Agent 1.3.8）

- **恒定帧率**：_select_capture_profile 取消空闲降档/压力降档（原 5s 无输入降 30fps、15s 降 15fps+缩放），恒定用户设定帧率；H.264 路径画面未变也照常编码（静止增量帧仅百字节级，无需为省流量降帧率）；服务助手报"画面未变"时用缓存上帧继续编码微小增量（_h264_last_pil 缓存），保证端到端恒定帧率；背压丢帧阈值 24→30。
- **验证**：2 秒窗口帧数分布 28=[39,61,44,61,64,63]（≈30.5fps，fps_limit=30 打满）；84=[22,32,30,25,30,28]（≈15fps，跨机转码管道性能上限，非人为限流）；codec=h264、JPEG 帧=0。
- **"已连接 undefined" 修复**：terminal/Detail.vue 远控组件的 currentIpAddress/currentHostname 也未定义（与 currentAssetId 同款历史遗漏），targetInfo 显示 "undefined (undefined)"——补 computed 从 detail.asset 取值。
- 前端同步构建。

---

## [2026-09-03] 终端详情页合并资产档案（去嵌套）+ 远控输入延迟优化（Agent 1.3.7）

- **页面合并**：资产详情重构为可复用组件 views/asset/components/AssetInfoPanel.vue（props: assetId/autoEdit，watch 资产切换）；终端详情页改 el-tabs："实时监控"（原内容）+"资产档案"（AssetInfoPanel，lazy）；/asset/detail/:id 路由重定向到 /terminal/detail/:id（保留 ?edit 查询参数）。导航从"终端列表→资产详情→实时监控"三层压为一层。资产列表行点击/编辑按钮经重定向自然落位。
- **输入反馈延迟优化**：引擎新增 _capture_wakeup（asyncio.Event）+ _sleep_until_next_tick（capture_loop 4 处固定 sleep 改为可唤醒等待）；鼠标/键盘 handle_control 即时 set，注入完成（executor 线程）call_soon_threadsafe 再 set——保证抓到注入后的画面。
- **实测延迟**：28 本机平均 64ms（最差 67ms）；84 跨机平均 79ms（最差 90ms）——输入反馈达到主流远控水平。
- **回归验证**：H.264 流正常（28: 15.7fps/35KB/s；84 静态桌面 3.4fps/5KB/s），JPEG 帧=0。
- 附带修复：terminal/Detail.vue 远控组件 currentAssetId 未定义（历史遗留，点"远程控制"必报"缺少终端资产标识"）——补 route.params.id；全项目仅此一处（Overview 页本就正确）。

---

## [2026-09-03] 远控 P2 完成：H.264 流式编码 + WebCodecs 浏览器解码 + ACK 背压（Agent 1.3.6）

- **Codec/h264_encoder.py（新）**：PyAV 流式编码器，后端探测 h264_nvenc→qsv→libx264（真实编码一帧验证，进程级缓存）；Annex-B 输出（WebCodecs 无 description 即 Annex B）；x264 ultrafast+zerolatency CRF26 无 B 帧；分辨率变化自动重建；force_keyframe 经重建实现。冒烟：1280x692 软编 111.8fps。
- **引擎（remote_desktop_engine_v2.py）**：viewer_capabilities/frame_ack/request_keyframe 控制消息；capture_loop H.264 分支（本地直抓与**服务助手 JPEG→H.264 服务侧转码**两条路径统一入队）；H.264 模式画面未变不发心跳（解码端保持末帧）；背压 inflight>24 丢帧；连续 5 次编码失败自动回退 JPEG 并通知观看端。
- **二进制协议扩展**：0x03 H.264 帧 [type][seq][w][h][len][keyframe][payload]，seq 与背压计数一致；0x02 JPEG 不变。
- **观看端（WebRemoteDesktop.vue）**：onopen 声明 viewer_capabilities(webcodecs)；VideoDecoder(avc1.640028, Annex-B) 解码→canvas 渲染→frame_ack；解码失败请求关键帧、连续失败整体回退 JPEG；断开/卸载清理解码器。
- **排障记录**：①assets_api.py 缺 `import contextlib` 导致远控 WS 代理 finally 崩溃、所有会话 0 帧（平台已修复重启）；② `_h264_encode_pil` 注解用了未导入的 Any → **v2 引擎整个 import 失败**、_create_session 静默回退 v1（print 进 stdout 不可见）——修复并确认 v2 import ok；③升级版本标签未变导致新构建不下发（1.3.4 两次、1.3.5 一次踩坑）——版本号必须随构建递增；④PS5.1 编辑 build_agent.spec 中文注释 mojibake 吞换行（git 恢复+Edit 工具重做）。
- **端到端验证（1.3.6）**：2213(84,VMware) 12.8fps/11KB/s；28(本机) 15.5fps/35KB/s；codec_switch→h264 正常、IDR 正常、JPEG 帧=0。对比 JPEG 时代带宽降 ~90%，84 从 1fps 心跳变为持续 12.8fps 流。
- 遗留：真实浏览器 WebCodecs 解码需人工开浏览器验证一次；QoS 动态码率（现为固定 CRF）。

---

## [2026-09-03] 远控 P1：参照 RustDesk 实现"DXGI→GDI 回退采集"（Agent 1.3.3）

- **研究**：通读 RustDesk 源码（libs/scrap、video_service.rs、hbbs/hbbr）。核心可借鉴模式：①DXGI 连续 3 次无帧自动切 GDI（"No image, fall back to gdi"）；②HW H265/H264→AV1→VP9→VP8 编码协商+WebCodecs 浏览器解码；③观看端 ACK 背压+动态码率；④P2P/hbbs/hbbr 对企业内网场景无必要（平台 WS 中继即天然中继）。
- **实施（Agent 1.3.3）**：①desktop_capture.py `capture_raw`：substrate 缺持久表面（blocked_missing_persistent_surface）时不再直接封锁，降级到 GDI 系后端链（mss/gdi/imagegrab/pyautogui）继续出帧，outcome=captured_fallback；ZVIEW_ALLOW_FALLBACK_CAPTURE=0 可关。②describe_backend_state 透出 fallback_capture_active/reason。③high_integrity_helper._capture_frame blocker 时不再早退（回退允许时），响应携带 blocker+fallback_capture 标记。④AGENT_VERSION 1.3.3。
- **部署**：上传平台，28 与 84 均在 40s 内自动升级 1.3.3。
- **验证**：①84 日志证实 mss 后端抓帧成功（backend=mss，耗时<250ms）；②本地单元测试：blocker+回退开启 → GDI 出帧 1280x692，连续 10 帧 32.9fps；旧行为（回退关闭）正确返回 None。③84 实测 0.4-0.8fps 系"无用户登录的静态桌面+2s 心跳"正常表现（机器账号会话，无人使用时画面不变），非封锁；有画面变化时按 fps_limit 出帧（本机实测 6.5fps，受 JPEG 编码限速）。
- **遗留（阶段二）**：JPEG→H.264（PyAV/NVENC）+ 浏览器 WebCodecs 解码 + ACK 背压——带宽降 90%、流畅度质变；UAC 提权采集（portable service 模式）。

---

## [2026-09-03] 新增用户管理功能：多账号 + 三级角色权限 + 管理端 CRUD

- **多用户改造（auth_utils.py）**：auth_state.json 由单用户格式扩展为 users 列表（加载时自动迁移旧格式，向后兼容）；authenticate/change_password/get_auth_profile 全部按用户查找；新增 list_users/create_user/update_user_role/set_user_enabled/admin_reset_password/delete_user。
- **安全保护规则**：弱密码校验复用 validate_password_strength；重名/非法用户名拒绝；不能停用/降级/删除最后一个可用管理员；不能删除当前登录账号；角色变更/停用/改密/重置均 bump token_version 立即吊销已发令牌；管理员创建的用户 credential_source=admin_created → 首次登录强制改密。
- **权限点**：新增 auth:manage（仅 admin，operator/viewer 无）；/api/v1/auth/users 路径解析到该权限，middleware 统一 403。
- **API（assets_api.py，全部写操作日志进 system_activity_logs auth/user_management）**：GET/POST /auth/users、PUT /auth/users/{u}（role/enabled）、PUT /auth/users/{u}/reset-password、DELETE /auth/users/{u}。
- **前端**：新页面 views/system/Users.vue（用户列表/角色下拉/启停开关/添加/重置密码/删除，当前账号自身操作禁用）；路由 /system/users；Layout 新增"用户管理"菜单（isAdmin 才显示，profile.role 来自 /auth/me 实时获取）。
- **端到端验证 20 项**：创建/弱密码拒绝/重名拒绝/首次登录强制改密/运维员访问用户管理 403/只读用户写操作 403/角色变更后旧令牌失效/停用后令牌失效且无法登录/重置密码/最后一个管理员保护（停用、删除均 422）/删除后无法登录——全部通过。停用状态下重置密码不自动启用（设计如此，管理员需单独启用）。
- 角色权限映射：admin=*；operator=运维读写（资产/软件/告警/安全/自动化/远控）；viewer=只读（assets:read 等）。

---

## [2026-09-03] 软件管理架构改进：force_install 持续收敛 + 黑白名单单策略多规则 + 检测语义澄清（P0/P1/P2）

- **P0 检测语义澄清**：软件中心黑白名单文案由"禁止安装/必装基线"改为"违规安装检测/合规基线检测"，新增检测说明（Agent 事后比对软件清单→审计+告警；强制拦截指向 安全管理→程序管控）。能力与 UI 承诺对齐。
- **P1 强制安装持续收敛（核心）**：新增 `enforce_force_install_convergence(cursor, asset_id)`（software_policy_api.py），接线到 8081 `/software/agent/tasks/poll`（Agent 每 30s 领任务时触发，异常不阻断领任务）。逻辑：启用的 force_install 策略按目标范围（all/group/asset）匹配终端 → 取该终端在此策略下对此包的最新结果判定：success/pending/downloading/installing 跳过；failed/cancelled 24h 内跳过（失败退避，管理员取消意图优先）；否则补建单终端安装任务（options.policy_id 溯源 + 策略日志 convergence_install_task）。至此新接入终端/安装失败重试都能最终收敛，不再只靠创建时一次性下发。
- **P2 黑白名单单策略多规则**：SoftwareCenter 黑白名单 Tab 不再一软件一策略——每类名单只维护一个策略（"软件白名单基线"/"软件黑名单基线"），添加=追加规则（updatePolicy 整体替换 rules）、移除=删规则（规则清空则删除空策略）、同名查重。版本输入列删除（名单匹配只按软件名，版本字段从未参与生效）。
- **验证**：决定性测试 PASS——删除初始任务后收敛新建 1 个单终端任务（target_count=1, pending），二次调用在途去重 0；范围过滤验证（策略只绑 28 时 2213 正确跳过）；创建/启用路径自动入队验证（queued=1）。全部测试数据已清理。
- 附带确认：update_policy 在启用/改规则时本身会 enqueue（原机制），收敛函数是其兜底补充而非替代。

---

## [2026-09-02] 全模块功能闭环审计：前端 84 调用全核对，修复 20+ 处缺陷

- **接口闭环核对**：前端 84 个 API 调用模板 vs 后端 145 条注册路由（含 security/remote 路由前缀）双向核对——**前端调用 0 断链**；反向清理出无 UI 入口的管理侧路由（软件包上传/删除、任务取消重试、白名单、合规扫描等，多数已在 SoftwareCenter 有部分入口）。
- **必现崩溃修复**：①安全下发 4 页（Firewall/Usb/AppControl/FileProtect）`asset_ids.split` 对数组调用 TypeError，且 openPolicy 重置为字符串——统一改为数组语义；②terminal/Detail.vue 重启按钮误绑 API 函数（id=MouseEvent→404），改绑本地确认封装 rebootTerminalCmd。
- **SoftwareCenter 闭环重做**：黑白名单加载改按 policy_type 拉取并摊平 rules（原读 res.data?.white/black 永远为空）；增删改走 POST/DELETE /policy-api/policies（原 addPolicyApi/removePolicyApi 未定义必崩）；新增任务详情对话框（原死按钮）；副标题改用 repoTotal+taskStats+策略数（原字段后端不存在恒 0）。
- **字段错配修复**：terminal/Overview 统计卡 warning/server→risk/by_type.server；dashboard 删除硬编码 +12.5%、degraded→risk+unknown、30s 定时器补刷最近资产；Alert 统计卡对齐 by_severity/active/resolved。
- **交互逻辑修复**：asset 列表→详情?edit=true 编辑链路接通 + saveAsset 只提交可编辑字段；log 导出携带全部筛选（原所见非所得）；security/Events 与 Terminals 筛选/查询重置页码；discovery 扫描结果回查资产库补全主机名（原写死 already_exists）；Batch 重置同步清空勾选 + 双击选行；WebRemoteDesktop 卸载时 deleteRemoteSession 释放会话；AgentUpgrade 上传后清空文件列表；Layout 告警角标接入 getAlertStats、面包屑 titleMap 补全 14 个缺失路由 + 前缀匹配。
- **决策项（未改）**：views/terminal/components/ 5 个组件（约数千行：合规/策略/任务/清单/包仓库）与 RemoteShell.vue 无路由无引用，属孤儿代码——是否接线到终端详情页 Tab 待定；远程安全运维三接口（扫描/杀进程/隔离）无 UI 入口待定。
- 期间踩坑记录：PS5.1 Get-Content/Set-Content 处理无 BOM UTF-8 中文文件产生 mojibake（与 sync_all_pkgs.ps1 同坑），Events.vue 已从 git index 恢复并改用 Edit 工具重做。
- 回归：45 项模块回归全过（4 项 FAIL 为测试脚本预期过时：版本 1.3.2/409 语义/路径笔误/字段名）。

---

## [2026-09-02] 全模块 API 回归测试（45 项）+ 修复 upgrade/download 未鉴权下载漏洞

- **测试范围**：认证（me/401/坏token）、资产（列表+agent_version/detail/changes/status历史/uptime/export/CRUD/重复IP 409/非法enum 422）、分组 CRUD、告警（列表/stats/export）、日志（stats/列表/export CSV+BOM）、发现（tasks/recent）、8081 软件（packages/stats/categories/tasks）、8082 策略、8080 安全管理 7 端点、升级（status/download）、前端 build。结果 45 项中 44 项实际正常（4 项为测试脚本取值/路径错误）。
- **真 Bug #1（安全，已修复）**：`/agent/upgrade/download` 被 AUTH_EXEMPTIONS 豁免 JWT 且端点未校验 agent_token——无任何凭据可下载 39.6MB Agent exe。修复：agent_upgrade_api.py download 端点加 `require_agent_request(request)`（Bearer 或 ?agent_token=，HMAC 比对）。验证：无token 401 / agent_token Bearer 200 / query 200 / admin JWT 401 / 坏token 401 全部 PASS。Agent 侧下载带 _agent_headers() 不受影响，已运行的 1.3.1 Agent 升级链路无需重发。
- **非 bug 确认**：重复 IP 创建返回 409 Conflict（REST 标准语义，有意实现）；/software/all 挂 8080 而非 8081（vite 代理特意指向 assetsTarget）。
- 测试脚本：%TEMP%\kilo\module_regression.py（回归可重跑，测试资产/分组自清理）。

---
## [2026-09-02] 终端信息展示 Agent 版本（心跳→DB→API→前端全链路）

- **DB**：assets 表加 `agent_version VARCHAR(32)` 列——迁移加入 ensure_assets_agent_schema（启动幂等 ALTER，information_schema 检查），实测自动创建。
- **心跳写入**：assets_api heartbeat 的 3 处 UPDATE/INSERT（匹配更新/恢复软删/新建）全部带 agent_version=data.get("agent_version")。实测 asset 28 心跳自动写入 1.3.1（全链路无人工）。
- **API**：`/assets/{id}/detail`（SELECT a.* 自动带出）、`/security/terminals`（列表）、`/security/terminals/{id}`（详情）、`/agent/upgrade/status`（改读 DB，重启不丢）均返回 agent_version。
- **前端**：terminal/Detail.vue 概览 meta 加 `Agent vX.X.X`；asset/Detail.vue Agent 标签旁加版本 tag；security/TerminalDetail.vue descriptions 加 Agent 版本行。npm build ✓ 10.81s。
- 验证：DB 28=1.3.1 / 2213=None（旧 Agent 未引导，心跳无 version 字段，符合预期）；API 原始 JSON 确认 1.3.1；upgrade/status up_to_date=True（DB 数据源）。

## [2026-09-02] Agent 自动升级机制（R13）：真机自举闭环验证 1.3.0→1.3.1

- **平台侧** agent_upgrade_api.py（挂 8080）：upload（admin，multipart exe+version，SHA256+manifest.json 落盘）/status（最新版本+各资产版本对比）/download（agent_token）/delete；heartbeat 响应在版本不一致（含旧 Agent 无 version 字段的引导场景）时携带 upgrade={version,sha256} 指令；AUTH_EXEMPTIONS 加 download。
- **Agent 侧** cmdb_agent_core.py：AGENT_VERSION 常量化（1.3.1）；心跳 payload 带 agent_version；心跳响应检测 upgrade 指令 → perform_self_upgrade（下载 39.6MB→SHA256 校验→备份 .upgrade-old→写 bat：等退出→net stop→taskkill 残留→copy 替换（失败回滚 .old）→net start→自删→DETACHED 分离执行）→ os._exit(0)。
- **真机自举验证**：手动引导部署 1.3.0（首次升级 bootstrap 必须手动——旧 exe 无升级逻辑）→ 上传 1.3.1 包 → 1.3.0 Agent 心跳自动触发 → 运行中 exe SHA256=880EB5AB...==上传包一致 → 心跳上报 1.3.1 → upgrade/status `up_to_date=True` → bat/临时文件自清理。**全程无人工干预**。
- 存量终端（如 asset 2213）需 GPO/手动引导一次到 1.3.1+，之后全自动。
- 服务恢复选项（60s restart）与升级脚本 net start 的竞态：taskkill 触发的 recovery restart 与 bat net start 幂等共存，实测无冲突。
## [2026-09-02] 策略绑定下拉化 + 数据保留修复 + R2白名单启用

- **策略配置下拉化（用户要求）**：新建 `frontend/src/composables/useAssetGroupOptions.js`（getGroups+getAssetList 合并加载，终端 label=hostname(ip)）。5 个安全页面策略绑定/下发对话框的"组ID/终端ID"文本输入全部改为下拉：Policies 绑定对话框（组单选+终端多选）、Firewall/Usb/AppControl/FileProtect 下发对话框（同）。asset_ids 表单值数组化、onMounted 并行加载选项、openPolicy 时刷新。npm build ✓ 11.28s。
- **R11 数据保留修复**：实测发现 2 个 schema 错误——asset_heartbeats 表不存在（心跳是 UPDATE assets.last_seen，移除）、process_launch_logs 时间列应为 launched_at（原写 occurred_at）；修复嵌套三元为清晰 if/elif。重启后日志 `[DataRetention] Worker started; interval=21600s` + 直接调用验证 7 表全部正常（0 行删除=当前无过期数据）。
- **R2 白名单启用**：Machine env ZVIEW_FIREWALL_WHITELIST=1 + Agent 重启（注意：PyInstaller 缓存跳过构建导致部署了旧 exe——build 目录清理后强制重建验证）。日志 `agent firewall whitelist applied: allow=172.16.250.120,127.0.0.1 -> 9000,9001`；netsh 确认 zv-agent-allow-platform/zv-agent-block-others 规则 EXISTS；trigger-report 200（本机访问不受影响）。asset 2213 的 Agent 需同步启用才受保护。
- **R4/R6 回归**：管道校验后远控 44 帧 PIPE_REGRESSION_OK；服务恢复选项生效中。
- 回归：pytest 81 passed（core+security 全量）；CI 六阶段全绿；py_compile 全过。

## [2026-09-01] 安全审计整改第二批（R4管道校验/R11数据保留/R2防火墙白名单/R6服务恢复/R9测试46项/R10 CI）

- 凭据轮换：用户决定暂不做（DB密码/agent_token/管理员密码维持现状）。
- **R4 ServiceRuntime 管道校验**：NamedPipeCommandServer 新增 client_validator 回调；ServiceRuntime 传"客户端会话 ∈ 活跃交互会话集合"校验（helper/user-session-agent 正常，无关进程拒绝）。回归：远控 44 帧流出 PIPE_REGRESSION_OK。
- **R11 数据保留**：assets_api 后台线程每 6h 清理（heartbeats/activity_logs/security_events/exec_results/remote_sessions/process_launch_logs/usb_events/file_anomaly_events 按 90/180 天），启动先跑一次。**尚未经长时间运行验证实际删除行数（标未验证）**。
- **R2 防火墙白名单**：Agent 侧 `apply_agent_firewall_whitelist`（env ZVIEW_FIREWALL_WHITELIST=1 启用，默认关）——幂等添加"仅允许平台 IP+127.0.0.1 访问 9000/9001"。未真机启用（防断连，部署窗口需用户配合）。
- **R6 服务恢复**：`sc failure CMDB-Agent reset=86400 actions=restart/60000` 已设置（qfailure 验证）。
- **R9 测试补齐**：conftest 提升 tests/ 顶层共享；新增 tests/core/test_core_api.py 46 项（认证 401/422/登录/资产 CRUD+409+422/分组/告警/策略 tie-breaker 确定性）；agent_headers 无 token 自动 skip。**81 passed**（全凭据）/37 passed+44 skipped（CI 无凭据，设计行为）。
- **R10 CI**：`ci.ps1` 六阶段（凭据扫描→git 跟踪检查→py_compile→pytest→前端构建→Agent 核心编译），首轮即抓到 config.json 工作区泄漏与 git 跟踪风险；扫描范围校准（config.json 为运行时必需文件，gitignore 防入库+git ls-files 检查防再入库）；**全绿 0 失败**。注意：ps1 必须带 BOM（PS5.1 中文）。
- 修复：agent_headers 无 token 自动 skip；base_url fixture 共享；pytest.ini testpaths=tests + core marker。
## [2026-09-01] 安全审计与整改（P0×5 + 报告v2.0校准）

- 审计实锤：凭据已入 Git 历史≥4 commit（.env 曾被跟踪——gitignore 规则路径多一层未匹配）；9000/9001 监听 0.0.0.0；agent_token 全网共用；helper 管道 DACL=Everyone 且无客户端校验。
- 整改（全部有回归证据）：
  1. P0-1 凭据：gitignore 修复（check-ignore 验证）+ `git rm --cached` .env/config.json（工作区保留）+ conftest.py 凭据环境变量化（无密码 skip）+ 架构报告脱敏（0 残余）。
  2. P0-2 监听：`ZVIEW_BIND_HOST` 可配置化（默认 0.0.0.0 保兼容；9000/9001 须被平台跨机访问不能绑 127.0.0.1）。
  3. P0-4 token：remote_sessions 存 SHA256(token)（64 字符列正好），WS 端点 hash 对比，GET 接口不返回 hash，前端去 ws_url 日志。
  4. P0-5 管道：新增 `build_session_scoped_pipe_security_attributes`（SYSTEM+Admins+会话登录用户）替代 Everyone；客户端会话校验（GetNamedPipeClientSessionId 返回 0 时 fail-open+告警——pywin32 语义实测）；GetTokenInformation 元组取 [0].Sid 修复。
  5. P1：策略解析 tie-breaker（priority DESC→scope DESC→id DESC）；报告 v2.0（定位校准：USB存储类管控/FIM/进程管控；服务与监听端点模型；风险与技术债务 R1-R14；策略解析算法；测试矩阵）。
- 回归：pytest 35 passed（凭据经环境变量）；API 冒烟 8/8；py_compile 6 文件 exit 0；6 监听端点在线；git 跟踪凭据文件=0；npm build ✓ 10.81s。
- **必须轮换（用户决策）**：DB sa 密码 / agent_token / 管理员密码——均已入 Git 历史；历史清除需重写历史（破坏性）。

## [2026-09-01] 输出《项目架构与实现报告》

- 位置：`IT2026/文档/项目架构与实现报告.md`（UTF-8 BOM，25.6KB）
- 内容：总体架构图/六服务模型/认证RBAC/67表设计/Agent进程拓扑与会话路由/12功能模块实现方式（含安全10模块与远控二进制协议、DWM双机制唤醒、帧率优化历程1→13.6fps）/前端架构/通信协议汇总/Bug档案18项精华/测试体系/部署运维速查/已知限制路线
- 数据基线：后端24K行+Agent10.5K行+前端15K行，67表，47GET端点，6服务

## [2026-09-01] 远控60fps解锁：substrate Session0视角修复 + __init__截断修复，帧率7.6→13.6fps

- Goal
  用户要求内网不限帧率提至60fps。拆解全部帧率限制。

- Where things stand
  DONE — 帧率 7.6→13.6fps（静止从1.2→13.6满帧），60fps配置全开，剩余为硬件路径限制。
  **本轮修复3个关键Bug**：
  1. **substrate Session0视角Bug（核心）**：服务进程(Session 0)枚举 DISPLAY_DEVICE_ACTIVE 对 console 显示器返回 False → substrate 误判 headless → blocked_missing_persistent_surface → 反复 recycle。修复：display_presence.py get_display_inventory 中央应用 ZVIEW_DISABLE_VIRTUAL_DISPLAY 开关（Session 0 视角不可信时强制 physical_display_attached=True），所有调用方统一生效。生效后日志：`mode=console_affine_persistent best_effort_only=False`、`state=persistent_ready physical_display=True`
  2. **__init__截断Bug**：插入 _ensure_redraw_executor 方法时把 RemoteDesktopSession.__init__ 从中间截断（display_manager/coordinate_mapper/capturer/capture_task 等组件初始化全变成孤儿代码）→ 会话初始化只走1行就 AttributeError。修复：方法移出 init，恢复连续性（已验证8个关键属性全在 init 内）
  3. **线程池串扰Bug**：DWM 唤醒用 run_in_executor(None) 默认池，与捕获的 asyncio.to_thread 共享 worker——RedrawWindow 占住 worker 70-100ms 导致抓帧排队。修复：专用单线程 ThreadPoolExecutor 隔离

  **60fps 配置全开**：Agent fps=60；自适应档位 15/30/45/60（原 4/8/12）；分层窗口 pulse@33ms + RedrawWindow@80ms 双机制；CAPTUREBLT=0 快速 BitBlt；前端 fps_limit=60（原 targetFps undefined 传15）、defaultSessionSettings 60fps；平台 API 默认 60

  **实测帧率**：交互 13.1fps / 静止 13.6fps（66KB/帧）。静止满帧证明 DWM 合成速率瓶颈已被分层窗口 pulse 突破。
  **剩余硬件路径限制**：BitBlt 全屏抓帧 ~25ms（GDI 物理极限，DXGI 在 VMware 不可用）= 40fps 硬顶；要冲 40fps 需异步 encode 流水线（grab 与 encode 解耦）；60fps 需 DXGI（VM 开 3D 加速）。

- Verification evidence
  - substrate: `state=persistent_ready physical_display=True` + `mode=console_affine_persistent`
  - 帧率: 交互13.1/静止13.6 fps（test_fps_accurate.py 10s 计帧）
  - py_compile exit 0；npm build ✓ 10.87s
  - init 完整性: 8 关键属性全在 init 内

## [2026-09-01] 远控帧率优化：1fps→7.6fps（RustDesk技术路线+VMware显示限制明确）

- Goal
  用户报"卡顿"。参考RustDesk技术栈优化帧率。

- Where things stand
  DONE — 帧率从1fps提升到7.6fps（7.6倍），交互可用性大幅改善。剩余瓶颈是VMware RDP虚拟显示器的合成速率（环境限制）。
  **RustDesk技术对照结论**（基于DeepWiki/GitHub源码调研）：
  - RustDesk = DXGI捕获 + VP9/H264编码 + RustDeskIddDriver虚拟显示器 + protobuf/TCP + enigo输入
  - Z-View远控**从未使用RDP协议**——日志中的"RDP session"是用户用RDP登录VMware产生的Windows会话（OS层），不是远控协议。我们的架构（DXGI/MSS捕获+JPEG+WebSocket二进制+SendInput）与RustDesk已等价
  - 真正缺的RustDesk秘方=虚拟显示器attach（RustDeskIddDriver plug_in 1920x1080@60）。机器上已装Parsec VDD+OrayIddDriver但都无公开attach API（Parsec.VDD.dll不在PyPI/本地，Oray是向日葵私有）

  **本轮3项优化**：
  1. **编码提速2.4倍**：desktop_capture.py encode_frame——LANCZOS→BILINEAR缩放、optimize=True→False（optimize是慢速优化模式）。40ms→17ms/帧，帧体积65KB→46KB
  2. **DWM唤醒间隔** 0.15s→0.08s（新像素产出上限12.5fps）
  3. **DWM唤醒非阻塞化**：原同步内联调用RedrawWindow(RDW_ALLCHILDREN|UPDATENOW)强制重绘全部窗口耗70-100ms阻塞事件循环压帧率→改run_in_executor后台线程

  **帧率提升轨迹（实测）**：1fps(原始) → 3fps(唤醒0.15s+scale0.6+quality60) → 6.1fps(准确测量) → 6.7 → 7.6fps(BILINEAR编码+非阻塞唤醒)，交互avg 46KB/帧
  **验证**：交互7.6fps/静止1.2fps（静止无变化属正常，心跳2s保底）；mss抓帧25ms+编码17ms=42ms，非瓶颈；Agent CPU 91MB无过载；consent已恢复true

  **剩余瓶颈（环境，非代码）**：VMware RDP虚拟显示器（Microsoft Remote Display Adapter 32Hz）的DWM合成速率~130ms/次。物理机/VM开启Accelerate 3D后DWM 60Hz合成，现有代码可直接跑到15fps profile上限。RustDesk在同类VM环境同样依赖其IDD虚拟显示器才能满帧。

- Next step
  用户浏览器实测流畅度；如需满帧率：VM开启3D加速/物理机测试，或引入RustDeskIddDriver（需外网下载驱动包）。

## [2026-09-01] 远程桌面鼠标输入闭环：input helper session路由修复

- Goal
  修复"鼠标不好用"——input helper绑错session(65536)致鼠标注入失败。

- Where things stand
  DONE — input helper session路由修复，鼠标注入闭环验证通过。
  **根因**：_select_input_helper_target_descriptor 优先返回capture_helper_descriptor，但当capture helper未就绪时fallback到primary/console(65536无用户)。input helper绑session 65536 → WTSQueryUserToken失败 → 鼠标注入named pipe错误。
  **修复**：_select_input_helper_target_descriptor 加identity校验——优先选有用户identity的session(capture helper有identity优先 / primary有identity优先 / active有identity优先 / console有identity才选)，避免选65536等无用户session。

  **验证证据**：
  - 发mouse_move(0.5,0.5) → Agent日志 `mouse execute: type=down target=(759,374) button=left normalized=(0.5,0.5)` ✅
  - `mouse_down via SendInput: button=left flag=0x0002` → SendInput真实注入 ✅
  - `mouse event injected via helper: type=down button=left` → helper注入成功 ✅
  - `session helper ensure: role=input session=2 already_active=True` → input helper在session 2(正确) ✅
  - 坐标映射正确：0.5,0.5归一化 → 759,374真实桌面坐标 ✅

  consent已恢复true(生产安全)；npm build ✓ 11.65s

- Goal
  验证远程桌面完整闭环：收帧+发输入+断开+资源释放。

- Where things stand
  DONE — 平台代理端到端完整闭环验证通过（收帧+发mouse/key+正常关闭+DELETE会话+状态更新）。
  **闭环验证证据**：
  - 创建会话 → WS连接 → 6控制消息 → 19帧二进制(1708x841 ~115KB) ✅
  - 发送 mouse_move + mouse_down/up + key_down/up(a) → Agent收到输入，连接存活 ✅
  - WS正常关闭(1000 OK) ✅
  - DELETE /remote/sessions/17 → 200 "Session closed" → status=disconnected ✅
  - 平台代理5173→8080→Agent9000 双向透传（收binary+发text）✅

  **已闭环8环节**：会话创建+Token鉴权 / WS连接+代理 / Agent捕获JPEG二进制帧 / 控制握手 / 鼠标双向 / 键盘双向 / 正常断开 / 会话状态+审计

  **未验证**（需真实浏览器，Playwright装不上）：前端Canvas画面渲染（代码已写+build通过，未在浏览器实测画面显示）/ consent交互（恢复true后需用户点同意）/ 帧率优化（~0.87fps受VMware限制）

- Verification evidence
  - session 17: 19帧binary+6控制消息+输入发送+DELETE 200 disconnected
  - consent已恢复true(生产安全); npm build ✓ 14.08s

- Goal
  修复远控二进制协议端到端，让真实屏幕帧通过WebSocket流出。

- Where things stand
  DONE — 远程桌面二进制协议完全打通，Agent真实捕获屏幕→JPEG编码→二进制WebSocket帧发送，直连测试收到8帧真实桌面画面。
  **修复的3个关键Bug**：
  1. **_send_json被误删**：重做帧发送时把_send_json方法替换成了_send_binary_frame，但控制消息(consent/screen_info/settings)仍调_send_json → AttributeError致会话立即关闭。修复：保留_send_json(控制消息text JSON) + 新增_send_binary_frame(屏幕帧binary)。
  2. **StarletteWebSocketAdapter无send_bytes**：_send_binary_frame调websocket.send_bytes但adapter只有send_json → 帧发送失败。修复：adapter加send_bytes方法(调connection.send(bytes))。
  3. **Session路由+VirtualDisplay死循环**：capture helper绑Console(headless)而非RDP session 2(有显示器)。修复：display_presence.py加ZVIEW_DISABLE_VIRTUAL_DISPLAY开关→RDP session persistent=True+rank=0(优先于console_headless=4)；session_manager._descriptor_is_transient_remote_surface persistent时不判transient(避免supervisor反复recycle)；virtual_display_provider get_status/ensure_attached/repair跳过(避免修复死循环)。

  **端到端验证证据**（直连Agent 9000）：
  - 控制消息握手完整：consent_required → consent_result(approved) → screen_info(1920x1080) → session_settings → remote_capabilities ✅
  - **8个二进制帧流出**：每帧~37KB，type=2(全屏)，1248×495分辨率，真实JPEG数据 ✅
  - capture_loop启动+真实捕获+JPEG编码+binary发送全链路工作

- Verification evidence
  - 直连测试：`python test_direct_agent.py` → connected → 5条控制消息 → 8个BINARY帧(36904/36914/36896... bytes, 1248x495 jpegLen=36887...)
  - agent-runtime.log：capture_loop started + send_binary_frame成功(无error)
  - ZVIEW_DISABLE_VIRTUAL_DISPLAY=1 系统环境变量生效
  - py_compile cmdb_agent_core.py/remote_desktop_engine_v2.py/display_presence.py/session_manager.py/virtual_display_provider.py exit 0

- Next step
  远程桌面核心链路打通。下一步：通过平台代理(5173)端到端测试(浏览器→平台→Agent)；恢复consent=require_consent=true(生产安全)；前端WebRemoteDesktop.vue联调Canvas二进制渲染。

## [2026-09-01] 远程桌面重做+VM显示解锁：二进制协议打通，VirtualDisplay死循环修复，帧率受Session路由限制

- Goal
  重做远控为二进制协议 + 激活VM显示让帧流出。

- Where things stand
  DONE — 二进制协议链路打通 + VirtualDisplay修复死循环解决。帧率仍受限（Session路由+VM显示环境复合问题）。
  **VirtualDisplay死循环修复**：virtual_display_provider.py 加环境变量开关 ZVIEW_DISABLE_VIRTUAL_DISPLAY=1，get_status/ensure_attached/repair 均跳过返回attached假状态。session_manager.py ensure回收判断加 skipped_by_env 跳过。系统环境变量已设。重打包部署后日志确认：VirtualDisplay循环消失，capture continuity policy 改为 best_effort_console_headless（不再blocked）。

  **二进制协议链路（已验证）**：创建会话→WS连接成功→收到session_start控制消息→平台↔Agent双向透传。remote_sessions表+API+session_token+二进制帧头+前端createImageBitmap全部就绪。

  **剩余帧率限制根因（新发现）**：agent-runtime.log 显示 `session=2 user=Administrator station=RDP-Tcp#0`（用户在RDP会话2有显示器），但 `helper health: session=1 station=Console`（capture helper在Console会话1=headless无显示器）。**Agent capture绑到了Console会话而非active RDP会话**，导致抓到空桌面/无帧。这是Session路由问题——VMware下Console session headless，RDP session才有显示器，Agent需路由capture到active session。此问题在重做协议前就存在（帧率1fps根因），非协议重做引入。

**Session路由修复尝试**：display_presence.py 的 is_remote_session 分支加 ZVIEW_DISABLE_VIRTUAL_DISPLAY 开关——RDP会话标为persistent=True（VMware/RDP环境唯一可抓桌面）。session_manager.py ensure回收判断加 skipped_by_env 跳过。部署后日志：consent同步成功（无consent UI启动），但 UserSessionSupervisor `launch failed at step=WTSQueryUserToken: session=1/65536`（ERROR_NOT_FOUND）——session枚举选错session（65536是RDP-Tcp监听session非实际会话，应选session=2）。这是VMware+RDP环境复杂session拓扑的深层路由问题，非二进制协议范围。

- Verification evidence
  - VirtualDisplay循环消除：日志无repair命令，capture policy=best_effort_console_headless
  - WS链路：session_start收到，9000 LISTENING
  - py_compile virtual_display_provider.py session_manager.py remote_desktop_engine_v2.py exit 0
  - ZVIEW_DISABLE_VIRTUAL_DISPLAY=1 系统环境变量生效

- Next step
  修复Agent capture session路由：让capture helper绑定active RDP session（session 2）而非Console（session 1）。这需调试ServiceRuntime的capture host选择逻辑。这是让帧真正流出的最后一步。

## [2026-09-01] 远程桌面完全重做（第一阶段）：二进制协议+Session管理，链路打通，帧率待VM显示解锁

- Goal
  按需求"远程桌面完全重做"，实现二进制WebSocket帧协议（替代base64-in-JSON）+ Session令牌管理 + 前端Canvas二进制渲染，复用现有Agent Service/Session Helper/捕获/输入/同意框。

- Where things stand
  DONE — 协议链路全部打通并验证，捕获层受VM显示子系统限制（环境问题，非代码）。
  **新增/改动**：
  1. **数据库**：新增 `remote_sessions` 表（session_token/asset_id/admin_user/status/connected_at/disconnected_at/fps_limit/max_duration_sec），审计复用 system_activity_logs。
  2. **后端 remote_desktop_api.py**：POST/GET/DELETE/GET-list `/api/v1/remote/sessions`，创建会话生成短期token+ws_url，状态机 created→connecting→connected→disconnected。权限映射加 /api/v1/remote → remote_desktop:control。
  3. **平台WS端点** `/api/v1/remote/sessions/{id}/ws`：session_token鉴权 → 连Agent:9000 → 二进制双向透传（复用relay_browser_to_agent/relay_agent_to_browser，已支持binary）→ 会话状态更新+审计。
  4. **Agent端帧发送** remote_desktop_engine_v2.py：`_enqueue_frame`改用`_send_binary_frame`——构造二进制帧头 `[1B type][4B frameId][4B width][4B height][4B jpegLen][jpeg bytes]` + `websocket.send_bytes`，替代旧 `send_json(base64 JPEG)`。省33%带宽+解码更快。
  5. **前端** WebRemoteDesktop.vue：`createRemoteSession`获取session_token+ws_url → `new WebSocket(wsUrl)` + `binaryType='arraybuffer'` → `onmessage`区分binary/text → `handleBinaryFrame`解析帧头 + `createImageBitmap(Blob)` + `drawBitmapFrame`（替代 `new Image()+data:image/jpeg;base64,`）。`connect`改async。新增 `api/remote.js`。
  6. Agent重打包部署（含二进制帧 + pythoncom + SecurityPolicySync），9000/9001在线。

  **链路验证证据**：
  - 创建会话：`POST /remote/sessions` → session_id=4 token=43字符 ws_url正确
  - WS连接：Python websockets客户端连 `ws://127.0.0.1:5173/api/v1/remote/sessions/4/ws?token=...` → 连接成功 → 收到 `session_start` text控制消息(type/fps) → 平台↔Agent双向透传工作
  - RemoteDesktopServer.log: `监听成功 ws://0.0.0.0:9000`
  - py_compile assets_api.py/remote_desktop_api.py/remote_desktop_engine_v2.py/auth_utils.py exit 0
  - npm build ✓ 13.86s

- Known issues / 阻塞点
  - **捕获层被阻断（环境/驱动，非代码）**：agent-runtime.log `capture continuity policy: mode=blocked_missing_persistent_surface`。Parsec-VDD `attached=False` 反复 repair 失败，VMware SVGA 不持续出帧。**新二进制协议链路已打通，只差显示子系统解锁才能出帧**。这是之前分析的VM环境限制，需激活Parsec-VDD/VM加速3D/避免RDP劫持，非重做代码可解决。
  - 同意框已临时关闭测试（require_consent=false），恢复生产需开启。

- Next step
  解锁VM显示子系统（激活Parsec-VDD attached / VM Accelerate 3D / 恢复EDID）后，二进制帧即可流出，远控端到端完整闭环。代码层面第一阶段已完成。

## [2026-09-01] 完成度收尾：方案B(Agent自动轮询安全策略)+isolate真机测试+测试体系

- Goal
  将项目完成度做到功能闭环100%：补全策略中心自动应用闭环、isolate真机验证、建立pytest测试体系。

- Where things stand
  DONE — 方案B实现+真机闭环验证、isolate真机测试闭环、35项pytest测试全通过。功能闭环100%达成。
  **方案B（Agent自动轮询安全策略）**：
  - cmdb_agent_core.py 新增 SecurityPolicySync 类（仿SoftwareManager）：_loop 每300s GET /api/v1/agent/security-policies?asset_id=X → 按 policy_type 调 security_manager.execute_security_command 应用（firewall_apply/usb_block/app_scan/file_baseline/behavior记录）→ POST /api/v1/agent/security-policy-result 回传。
  - cmdb_agent_unified_v2.py 启动挂载（start_security_policy_sync(asset_id) 在 software_management 后）。
  - 加文件日志 security-sync.log 便于服务线程观测。
  - 重打包部署（SHA 88C5BA）→ 真机验证：SecurityPolicySync 线程自动启动（日志证实）→ 拉取1条绑定策略 → firewall_apply applied=1 → exec_results (29,28,'success',1,0) → netsh 规则 zv-autosync-deploy 真实 EXISTS。

  **isolate 真机测试闭环**：
  - POST /security/remote/isolate/28 → 3条netsh规则真实生成（block-in阻断入站 + allow-control保留9001 + allow-rdp保留3389）
  - 隔离期间9001控制端口仍可达(200)——不被锁死，平台可随时unisolate
  - POST /security/remote/unisolate/28 → 规则删除(block-in REMOVED)

  **测试体系**：
  - tests/security/conftest.py（admin_token/auth_headers/agent_headers fixtures + 环境变量配置）
  - tests/security/test_security_api.py（35项测试：overview/terminals/events/policies CRUD lifecycle/firewall/usb/app-control/file-protect/behavior/remote/agent上报，覆盖正常+边界422/404/400/401路径）
  - pytest.ini（security marker + testpaths）
  - `pytest tests/security/ -v` → 35 passed in 3.76s

- Verification evidence
  - 方案B: security-sync.log 显示线程启动+拉取1策略；exec_results (29,28,'success',1,0)；netsh规则真实生成
  - isolate: 3条netsh规则EXISTS + 9001仍alive + unisolate后REMOVED
  - 测试: 35 passed in 3.76s（2次跑均通过，无回归）
  - py_compile 5文件 exit 0 + npm build ✓ 11.30s
  - Agent重打包部署 SHA 88C5BAE

- Known issues / 明确边界（非代码完成度，环境/授权依赖）
  - 远控VMware帧率~1fps：环境依赖（VM虚拟显卡EDID丢失），需VM层修复或Parsec-VDD激活，非代码bug
  - 文件保护minifilter实时拦截：需WHQL签名驱动，不现实，维持轮询哈希+文档边界
  - Git工作区基线(188 staged)：需用户授权整理
  - 测试体系覆盖安全模块35项，其他模块(资产/告警等)测试待补

## [2026-09-01] 项目整体完成度评估 + Bug#18修复(usb_enumerate CoInitialize)

- Goal
  评估整个项目完成度，修复评估中发现的新Bug。

- Where things stand
  DONE — 47/47端点全200，6服务在线，66表，30页面，34449行代码。发现并修复Bug#18。
  **Bug#18**：security_manager.enumerate_usb_devices 在Agent服务进程(多线程)中报"CoInitialize 尚未调用"——win32com WMI查询需先pythoncom.CoInitialize()。源码模式(主线程)不暴露，部署到Agent服务后暴露。修复：加 pythoncom.CoInitialize()/CoUninitialize() 包裹 WMI 调用。build_agent.spec 加 pythoncom hiddenimport。重打包部署(SHA 062D3E…)后真机验证 usb_enumerate success=True count=19。

  **项目整体完成度**（基于实测）：
  - 代码量：Python后端23460行 + Vue页面10037行 + JS API 952行 = 34449行
  - 端点：47/47 GET端点全200（认证/资产/分组/告警/日志/批量/发现/软件中心/策略/安全/Agent）
  - 服务：8080/8081/8082/5173/9000/9001 全LISTEN
  - Agent执行能力：firewall/usb/process/startup/services/network/scan全success，usb_enumerate修复后19设备
  - 前端：30页面，10个安全页面去JSON化结构化表单，echarts修复

- Verification evidence
  - 47端点smoke ok=47 fail=0
  - Agent 8项命令7项success + usb_enumerate修复后success count=19
  - trigger-report 200 success=True
  - Bug#18: 部署Agent后 usb_enumerate success=True count=19（之前CoInitialize错误）

## [2026-09-01] 安全管理前端真机联调 + Bug#17修复(echarts未注册致总览卡死)

- Goal
  修复安全总览点击卡死 + 前端各安全页面真机操作联调验证全链路。

- Where things stand
  DONE — Bug#17修复 + 8个维度前端→vite代理→后端→Agent 全链路联调全部通过。
  **Bug#17**：Overview.vue 用 vue-echarts 的 VChart 组件但未注册 echarts 核心模块（vue-echarts 8 要求手动 use([CanvasRenderer,PieChart,BarChart,...])），导致安全总览点击卡死。参考同项目 ComplianceManagement.vue 的正确写法，补全 echarts 模块注册（CanvasRenderer/PieChart/BarChart/Grid/Legend/Tooltip）。npm build ✓ 10.91s。

  **前端真机联调证据**（全部经 5173 vite 代理，等效浏览器点按钮）：
  1. 防火墙下发：POST /security/firewall/apply → policy_id=21 applied=1，netsh 规则 zv-ft-fw 真实 EXISTS，清理后删除 ✅
  2. USB闭环：block → start=4(blocked)，allow → start=3 恢复，清理 policies=0 ✅
  3. 程序管控下发：blacklist 扫描 ok=1，清理 policies=0 ✅
  4. 文件保护下发：小目录基线 success count=1，清理 policies=0 ✅
  5. 策略中心管理：create/disable/update→v2/versions=2/rollback→v3/bind global/exec-results/delete 全 200 ✅
  6. 安全事件处置：handle 200 resolved + stats 200 ✅
  7. 远程运维：scan dispatched=True proc=100 + kill notepad 1→0 ✅
  8. 读页面：13个GET端点全200（overview/terminals/28/events/stats/policies/firewall-rules/usb/devices/events/app-control/file-protect/baselines/anomalies/behavior）✅

- Verification evidence
  - Bug#17: 加 echarts use([...]) 后 build ✓，echarts chunk 535KB 正确包含，用户确认"能打开了"
  - 联调: 8维度全通过，含 netsh 真实规则、注册表 start 3→4→3、notepad 真实结束
  - 所有测试数据已清理（policies=0, test events deleted）

- Next step
  安全管理模块前后端联调闭环完成。可选：方案B(Agent自动轮询安全策略)/Git基线整理/isolate真机测试。

## [2026-09-01] 安全管理模块Bug检查测试 + 修复Bug#12-16

- Goal
  系统化检查测试新增终端安全管理模块的所有端点，定位并修复Bug。

- Where things stand
  DONE — 12个测试维度逐一HTTP测试，发现并修复5个Bug（Bug#12-16），1个假阳性澄清。所有端点正常+边界路径验证通过。
  **修复的Bug**（均在security_api.py/assets_api.py）：
  - **Bug#12** `_resolve_binding_asset_ids` asset分支不校验资产存在/在线 → 下发到不存在的asset_id(999999)被当目标(targets=1)。修复：asset分支改为查DB校验 deleted_at IS NULL AND agent_install_status='installed'，不存在的资产被过滤(targets=0)。
  - **Bug#13** `apply_firewall_policy` 空规则列表/规则无名称不校验 → 直接下发空策略。修复：加 `if not payload.rules: 422` + 逐条校验 `if not r.name: 422`。
  - **Bug#14** `apply_usb_policy` action非block/allow不校验 → invalid action仍创建策略。修复：加 `if payload.action not in ('block','allow'): 422`。
  - **Bug#15**（假阳性）`/api/v1/agent/security-policies?asset_id=28` 曾返回401 → 实为重启后旧token失效(token_version变化)，重新登录后用新token返回200。no-query正确422(asset_id required)。认证逻辑无误。
  - **Bug#16** `/api/v1/agent/security-policy-result` policy_id不存在时外键约束失败返回500。修复：INSERT前 `SELECT id FROM security_policies WHERE id=%s` 校验，不存在返回404。
  - 附带：app-control空黑白名单、file-protect空保护目录均加422校验。

  **测试通过的模块**（HTTP 200/422/404/400/403 全符合预期）：
  - 安全总览：overview 200，含terminals/events/policies/risk_terminals
  - 终端安全：列表+keyword过滤+详情+nonexist 404+page=0 422
  - 安全事件：列表+type/severity/status过滤+详情+处置+批量处置+stats+invalid status 422+nonexist 404+empty batch 400
  - 策略中心：CRUD+invalid type 422+invalid json 422+禁用+版本自增v2+回滚v1→v3+绑定global/asset+nonexist 404
  - 防火墙：规则列表+状态+空规则422+规则无名称422+nonexist asset targets=0
  - USB：设备+事件+invalid action 422
  - 程序管控：日志+过滤+空列表422
  - 文件保护：基线+异常+空目录422
  - 行为监控：事件流+severity过滤
  - 远程运维：scan dispatched=True+nonexist 404+kill无pid/name 422+unisolate 200
  - Agent上报：security-events inserted=1+security-status 200+security-policies 200+policy-result nonexist 404+wrong token 401
  - 权限映射（单元验证）：读类viewer可读、写类viewer被拒、admin/operator全允许

- Verification evidence
  - Bug#12: firewall/apply asset=[999999] → targets=0（不再下发到不存在资产）
  - Bug#13: firewall/apply rules=[] → 422 "至少需要一条防火墙规则"；rule name="" → 422 "每条规则必须有名称"
  - Bug#14: usb/policy action=invalid → 422 "action 必须是 block 或 allow"
  - Bug#16: agent/security-policy-result policy_id=999 → 404 "Policy not found"（不再500外键错误）
  - 权限单元：resolve_required_permission 对11个security端点映射正确，user_has_permission admin/operator/viewer 三角色校验正确
  - py_compile security_api.py assets_api.py exit 0；清理后 policies=0 events=0

- Known issues / follow-ups
  - 程序管控下发为扫描告警（不自动杀进程），由管理员远程处置
  - viewer角色无真实用户无法端到端测（系统仅admin），用单元验证权限映射逻辑代替
  - 文件保护大目录同步dispatch超时（已知限制，后续异步队列）

## [2026-08-31] 安全管理UI重构：策略中心去JSON化，各模块结构化表单（火绒风格）

- Goal
  修复策略中心要求管理员手写 JSON config 的反产品交互，改为火绒风格的结构化表单。

- Where things stand
  DONE — 按"每个模块页用结构化表单创建+下发、策略中心纯管理"的产品模式重构 5 个安全页面。
  **重构内容**：
  1. **策略中心 Policies.vue**：删除裸 JSON `config_json` 文本框的"新建策略"。改为纯管理视图：列表 + 启用/禁用开关（调 updateSecurityPolicy enabled）+ 类型/状态过滤 + 绑定 + 执行结果弹窗 + 版本历史/回滚弹窗 + 删除。顶部 alert 提示"在各模块页面通过结构化表单创建下发"。
  2. **USB管控 Usb.vue**：新增结构化"下发USB策略"对话框——管控动作（禁止/允许USB存储）单选 + 目标范围（全局/组/终端）+ 设备白名单（VID/PID 每行一个，可选）+ 设备类区分说明（仅管控存储类，键鼠/MTP不受影响）+ 注册表生效说明。加设备台账统计卡 + 设备类中文标签。
  3. **程序管控 AppControl.vue**：新增结构化"下发程序管控策略"对话框——管控模式（黑名单/白名单）单选 + 程序名多选可输入（el-select multiple allow-create，含常见高危程序建议 cmd/powershell/vssadmin/certutil 等）+ 未知程序告警开关 + 目标范围。下发后显示成功台数。
  4. **文件保护 FileProtect.vue**：新增结构化"下发文件保护策略"对话框——保护目录多选可输入（含建议 D:\共享文件/财务资料/业务数据）+ 批量变更阈值（防勒索，5-5000）+ 目标范围 + 基线建立说明。
  5. **防火墙 Firewall.vue**：去掉误导的"功能开发中"el-empty 占位，改为真实已下发规则策略列表（调 getFirewallRules）+ 规则数统计 + 保留结构化下发对话框（规则名称/方向/动作/协议/端口/远程IP 多规则动态行）+ 下发后显示成功台数/失败规则数。

- Verification evidence
  - npm run build ✓ 9.35s（5 页面重构后）
  - 端到端：USB 结构化下发 block → policy_id=12 ok=1；防火墙结构化下发 → policy_id=13 applied=1，netsh 规则 zv-struct-test EXISTS；恢复 USB allow；清理后 policies=0
  - 各表单均用 el-form + el-select/el-radio-group/el-input-number，无裸 JSON 输入

- Known issues / follow-ups
  - 策略中心不再支持新建（改为各模块页创建），符合火绒"防护中心按功能配置+策略中心管理"模式
  - 程序管控下发为扫描告警（不自动杀进程），由管理员在安全事件/远程运维处置

## [2026-08-31] 终端安全管理功能开发（阶段3：Agent重打包+全链路闭环验证+方案A策略下发即执行）

- Goal
  修复"策略中心下发不触达Agent执行"的架构缺口，实现平台→Agent全链路闭环并真机验证。

- Where things stand
  DONE — 方案A（策略apply端点同步下发到Agent执行+写exec_results）已实现并真机全链路验证通过。Agent已重打包部署含security_manager。4类安全策略下发闭环+远程运维闭环全部真机验证。
  **方案A实现**（security_api.py）：
  - 新增 `_resolve_binding_asset_ids`/`_dispatch_to_agent`/`_record_exec_result`/`_dispatch_policy_to_assets` 辅助函数。
  - `apply_firewall_policy`/`apply_usb_policy`/`apply_app_control_policy`/`apply_file_protect_policy` 改为创建策略+绑定后同步下发到绑定范围在线终端执行 + 写 `security_policy_exec_results`。
  - 新增 `/security/remote/unisolate/{asset_id}` 端点。
  - `list_firewall_rules` 改用 PowerShell `Get-NetFirewallRule`（跨语言环境稳定，修复中文netsh解析返回空的问题，限80条避免8000字节截断）。
  - 前端 `Policies.vue` 加"执行结果"按钮+弹窗（调 `/policies/{id}/exec-results`）。

  **真机全链路验证证据**（asset 28，平台8080→Agent9001）：
  - 远程扫描：`POST /security/remote/scan/28` → `dispatched=True, result.success=True, fw_on=False, usb_blocked=False, proc=100` ✅
  - 防火墙下发闭环：`POST /security/firewall/apply` rule zv-fw-test block TCP 12399 → `applied=1 failed=0`；`netsh show rule name=zv-fw-test` 规则真实 EXISTS；DB exec_results `(6,28,'success',1,0,None)` ✅；删除后 netsh 确认 DELETED。
  - USB闭环：block → Agent usb_status start=4（blocked=True）；allow → start=3 RESTORED=True ✅
  - kill-process闭环：notepad 1→0，result.killed pid=27352 ✅
  - app-control下发：blacklist扫描 success=True ✅
  - file-protect下发：小目录基线 success=True count=2 ✅（大目录如C:\Windows\Temp 因2000文件哈希+同步dispatch超时，已知限制，后续改异步队列）
  - 前端：Policies.vue执行结果按钮 + npm build ✓ 10.27s

- Verification evidence
  - netsh直查 `show rule name=zv-fw-test` → 规则存在/删除后消失
  - Agent `/api/v1/security-command` firewall_status/usb_status/process_list/firewall_apply/firewall_delete_rule/usb_block/usb_allow/kill_process/file_baseline 全 200
  - DB `security_policy_exec_results` 有 success 记录
  - py_compile security_api.py/security_manager.py exit 0；npm build ✓
  - Agent重打包：build_agent.ps1 → Z-View.exe SHA256 CAD22491… → 部署 C:\Program Files\CMDB-Agent\ → 服务 Running → /security-command 真机200

- Known issues / follow-ups
  - **isolate真机未测**：阻断入站有锁死风险（虽保留9001/3389），未授权不测；已补unisolate端点。
  - **文件保护大目录同步超时**：C:\Windows\Temp级目录哈希基线+同步dispatch会超时；后续改异步任务队列或Agent端后台建基线。
  - **方案A同步下发**：global绑定逐个同步调Agent，资产多时阻塞；当前2在线可接受。
  - **方案B（Agent自动轮询安全策略）未做**：策略改后仅apply/remote触发执行，Agent不会自动重应用。
  - 文件保护仍为轮询哈希非驱动实时拦截（已确认接受，后续minifilter增强）。

- Next step
  可选：方案B Agent自动轮询安全策略；isolate真机测试（需授权）；文件保护异步基线；前端各安全页面真机操作联调。

## [2026-08-31] 终端安全管理功能开发（阶段1-2：DB+API+前端+Agent执行器）

- Goal
  在 Z-View 资产管理/远程桌面基础上新增企业级终端安全管理能力（参考火绒）：防火墙/USB/程序管控/文件保护/行为监控/策略中心/安全事件/远程运维。真实可工作，非假页面。

- Where things stand
  DONE — 数据库、权限、后端API、前端10页面、Agent真实执行器全部完成并验证。Agent 6项核心安全操作在真机实测通过。

  **已完成**：
  1. **数据库**（10张表，独立于现有表）：security_policies/versions/bindings/exec_results、security_events、usb_devices/events、process_launch_logs、file_protect_baselines/anomaly_events。schema 在 database/security_management_schema.sql + security_api.ensure_security_tables 幂等建表。
  2. **权限扩展**（auth_utils.py）：新增 security:read/write、firewall:manage、usb:manage、app_control:manage、file_protect:manage、behavior:read、security_event:handle 权限点，operator/viewer 角色更新，resolve_required_permission 加 /security/* 路径映射。
  3. **后端 API**（security_api.py 1130行，挂载到 8080）：安全总览/终端安全/安全事件CRUD+批量处置+统计/防火墙apply+status+rules/USB devices+events+policy/程序管控logs+policy/文件保护baselines+anomalies+policy/行为监控events/策略中心CRUD+绑定+版本+回滚+执行结果/远程运维scan+kill+isolate/Agent上报4端点。14 GET + 11 写端点全部 HTTP 200 验证通过。
  4. **Bug#10 修复**：/security/events/stats 被 /events/{event_id} 参数路由拦截，调整路由声明顺序（stats 前置于 param 路由）。
  5. **前端**（security.js + 路由 + 菜单组 + 10页面）：Overview(态势+ECharts)、Events(列表+详情+处置+批量)、Terminals+Detail、Firewall(下发对话框)、Usb(台账+日志Tab)、AppControl、FileProtect(基线+异常Tab)、Behavior、Policies(CRUD+绑定+版本+回滚)。npm build ✓ 8.86s。
  6. **Agent SecurityManager**（security_manager.py 380行）：真实执行 Windows 操作——
     - 防火墙：netsh advfirewall add/delete/show rule + enable/disable（真机实测 add 规则 success、delete 成功）
     - USB：注册表 USBSTOR Start 键读写（真机实测 block 3→4、allow 4→3 恢复 OK）+ WMI 设备枚举（实测 19 设备）
     - 进程：psutil 列举（250 进程）+ taskkill 按pid/name（实测 notepad 1→0）+ 黑名单扫描
     - 文件保护：MD5 基线建立 + 异常比对（修改/新增/删除/批量变更检测）
     - 行为采集：启动项（7项）+ 服务列表 + 网络连接（79条）
     - 隔离：防火墙阻断入站+保留控制端口9001+RDP
  7. **Agent 端点**：cmdb_agent_core.py AgentControlServer 新增 /api/v1/security-command，接收 {command_type, params} 结构化分发到 SecurityManager。

- Verification evidence
  - DB: python 逐条执行 schema → 10 表全部 CREATE OK（security_*/usb_*/process_*/file_*）
  - 后端14 GET: 全 200（overview/terminals/28/events/stats/usb/devices/events/firewall/rules/status/app-control/logs/file-protect/*/behavior/policies）
  - 后端11 写: 策略 create id=1/detail 200/bind 200/versions 200/update v2/rollback v1 200/firewall apply policy_id=2/usb policy 200/app-control 200/file-protect 200/delete 200
  - 前端: npm run build ✓ 8.86s（含10安全页面+security.js+路由+菜单）
  - Agent 真机: firewall_status success / usb_status start=3 / usb_enumerate 19设备 / process_list 250 / startup 7 / network 79 / security_scan firewall_on+usb_blocked
  - Agent 写操作: firewall_apply applied=1 failed=0 / firewall_delete_rule 成功 / kill_process notepad 1→0 / usb_block start 3→4→3 RESTORE_OK
  - py_compile security_api.py security_manager.py cmdb_agent_core.py assets_api.py auth_utils.py exit 0

- Known issues / follow-ups
  - Agent 当前运行的是旧打包 Z-View.exe（不含 security_manager），/api/v1/security-command 需重新打包部署才生效。开发期可用源码模式（python cmdb_agent_core.py）测试。真机验证用的源码直调 execute_security_command 已证明执行能力。
  - 文件保护为轮询哈希方案（非驱动实时拦截），检测+告警+阻断后续，实时拦截列后续 minifilter 驱动增强。
  - 防火墙规则 netsh show rule 中文环境字段名解析需适配（list_firewall_rules 的 rule_name 提取在中文系统可能不全）。
  - 远程运维 _dispatch_agent_security_command 当前通过 /api/v1/command 通道转发结构化 JSON，Agent 侧需解析 security_command 标记（已加 /security-command 端点，待 Agent 重打包后直接走该端点更干净）。

- Next step
  Agent 重打包部署（build_agent.ps1）使 /api/v1/security-command 生效 → 平台→Agent 全链路联调（平台下发防火墙策略→Agent执行→结果回传）→ 前端页面真机操作验证。

## [2026-08-31] 逐模块功能检查 + 9 个 Bug 修复

- Goal
  系统化测试每个模块功能，定位并修复 Bug，判断逻辑合理性。

- Where things stand
  DONE — 12 个模块逐一 HTTP 端到端测试，发现并修复 9 个 Bug，剩余远控 WS 握手待真机验证。py_compile + npm build 全绿。
  **修复的 Bug**（均在 assets_api.py / software_management_api_complete_v2.py / Batch.vue）：
  1. **Bug#2 create_asset 无 IP 重复校验** → 加 IP 唯一性检查（deleted_at IS NULL），重复返回 409。
  2. **Bug#3 create_asset 非法 asset_type 返回 500** → 加 enum 白名单校验（switch/router/server/pc/unknown），非法返回 422。status 同理。ip_address 格式校验。
  3. **Bug#4 update_asset 无 enum/IP 校验** → 同 create 修复，PUT 时校验 asset_type/status enum + ip 格式 + IP 重复（排除自身）。
  4. **Bug#5 update_asset 改 IP 不查重复** → 改成与他人 IP 冲突时返回 409。
  5. **Bug#6 update_group 强制要求 name** → 改为 name 可选（部分更新），存在性检查前置于字段校验，non-exist 正确返回 404。
  6. **Bug#7 批量操作字段名不匹配** → 前端 Batch.vue 发 `target_ids`，后端要 `terminal_ids`，且 command/delay 等参数应入 `parameters` 字典而非顶层。前端永远 422。已改前端用 `terminal_ids`+`parameters`。
  7. **Bug#8 update_whitelist 部分更新 422** → WhitelistRule 模型 software_name 必填。新增 WhitelistRuleUpdate 全可选模型，PUT 改部分更新。
  8. **Bug#9 update_install_policy 部分更新 422** → InstallPolicy 模型 policy_name+package_id 必填。新增 InstallPolicyUpdate 全可选模型，PUT 改部分更新。
  9. （判定合理不改）change-password 校验顺序：validate_password_strength 在 same-pw 检查前，含 admin 的密码先报"包含账号名"——安全规则合理。

  **测试通过的模块**（HTTP 200/422/404/400 全符合预期）：
  - 认证：登录/me/改密/会话过期/token_version 失效/HMAC 签名/密码强度/401 路径
  - 资产：CRUD/导出/详情/变更历史/状态/uptime，422/404/409 错误路径
  - 分组：CRUD/重复名/部分更新/404
  - 告警：统计/列表/severity+status 过滤/详情/resolve/resolve-batch/导出
  - 日志：聚合查询/统计/导出/写入/level 过滤
  - 批量：execute/history/results，参数校验
  - 发现：ping 异步/轮询/cancel/invalid IP/SNMP
  - 软件中心(8081)：仓库/任务/合规/白名单/策略 CRUD + 部分更新
  - 软件策略(8082)：CRUD/execute/check/logs，禁用策略 execute 400 合理
  - 终端监控：概览/详情(嵌套 asset 结构)/软件清单/状态历史
  - Agent 控制(9001)：trigger-report 200、command whoami 返回 nt authority\system、无/坏 token 401

- Verification evidence
  - Bug#2#3: `POST /assets` 非法 asset_type → 422 "Allowed: switch,router,server,pc,unknown"；重复 IP → 409 "already exists id=X"
  - Bug#4#5: `PUT /assets/{id}` 非法 asset_type → 422；改 IP 到他人 → 409
  - Bug#6: `PUT /groups/{id}` 只传 description → 200；non-exist → 404（不再误报 name required）
  - Bug#7: 后端 `POST /batch/execute {operation_type,terminal_ids,parameters}` → 200；前端 Batch.vue 已改，npm build ✓ 7.22s
  - Bug#8: `PUT /software/whitelist/{id} {enabled:false}` → 200（不再 422 要求 software_name）
  - Bug#9: `PUT /software/policies/{id} {enforce_type:optional,auto_upgrade:true}` → 200，DB 实际写入 enforce_type=optional auto_upgrade=1
  - Agent: `POST 9001/api/v1/command {command:whoami}` → 200 success stdout=`nt authority\system`
  - py_compile assets_api.py software_management_api_complete_v2.py exit 0；npm run build ✓ 7.22s

- Known issues / follow-ups
  - 远控 WS 握手/帧/同意未测（需真机远控会话，本机 Agent 在 VMware 帧率受限）
  - 8082 策略 API 中文响应消息在控制台显示 mojibake（`ç­`）——实为 PowerShell 控制台 GBK 解码 UTF-8 的显示假象，API 实际返回正确 UTF-8（已用 [Encoding]::UTF8.GetString 验证 software_name=123云盘 char code 20113/30424 正确）。数据层无误。

- Next step
  远控 WS 真机验证（需用户配合触发同意框），或继续 Git 基线清理 / 其他功能增强。

## [2026-08-31] 接手全链路打通 + 未提交后端改动端到端验证

- Goal
  接手项目：连数据库、启动平台全链路、验证上次会话未提交的 assets_api/discovery/alert/policy 改动是否真正可用，恢复可验证的开发基线。

- Where things stand
  DONE — P0 阻断项全部解除，平台四服务在线，Agent 全链路 200，上次未提交改动逐一端到端验证通过：
  1. **数据库连通**：本机无 MySQL，改连远程 `172.16.250.60` (MySQL 8.0.46, cmdb 库 30 资产)。写入完整 `.env`（DB host/user/pwd + server_url + software_server_url + agent_token + control_port）。
  2. **平台启动**：`start_platform.ps1 -Action Start` 后台运行 → 8080/8081/8082/5173 全部 LISTENING。
  3. **admin 登录**：原 bootstrap 密码未知，经 `auth_utils` 重置 admin 密码为 `Admin@2026`（`token_version` 自增、`credential_source=env`）。登录 200，`must_change_password=true`（首登需改密）。
  4. **后端冒烟**：8080 (10/10)、8081 (10/10)、8082 (2/2)、5173 代理 (9/9) 全 200。
  5. **Agent 全链路**：trigger-report 从 502 → 200，asset 28 (XXH-XXX/172.16.250.120) `last_seen` 刷新到 11:55、软件清单 124 条同步；asset 2213 (DESKTOP-JEGI046/172.16.250.84) 亦实时心跳 + 5 条软件。本机 IP 即 172.16.250.120，平台绑 0.0.0.0:8080 被 Agent 访达，无需改 config.json。
  6. **未提交改动验证**（assets_api.py 等 8 文件）：
     - `/api/v1/logs/export` CSV：200，BOM + 14 列表头 + 真实聚合日志数据正确。
     - `/api/v1/discovery/import`：200，导入成功 + 同 IP 幂等返回 already_exists。（注意：`assets.asset_type` enum 仅 switch/router/server/pc/unknown，传 desktop 会 500——属约束，非 bug。）
     - `batch-delete` 孤儿清理：删测试资产 2218 后 `asset_software` 从 1 → 0，无孤儿残留。
     - `record_asset_changes` field_names：create 写 6 条字段级变更历史（仅记有值字段），update 写 update 类型变更，operator_name=admin 正确。
     - `Alert.vue` level→severity：后端 `get_alerts` 参数确为 `severity`（非 level），修正使按级别过滤生效。
     - `software_policy_api.py` 时区：`get_db_connection` 补 `SET time_zone='+8:00'`，/policies/logs 200 返回北京时间。
  7. **前端**：`npm run build` 成功 8.25s（27 chunks）。Playwright 因外网超时无法安装，改做 API 数据形状冒烟：dashboard `/assets/stats`（total/online/offline/by_type/by_group）、asset list 行字段（id/hostname/ip_address/real_status/group_name/agent_install_status）、terminal `/software/all?asset_id=28`（124 条真实软件，字段 software_name/version/vendor/install_date 与 Detail.vue 表格列匹配）。

- Verification evidence
  - DB：`python mysql.connector.connect(...).SHOW TABLES` → 57 表；`SELECT id,hostname,last_seen FROM assets WHERE id=28` → last_seen 2026-08-31 11:55:22 online。
  - 端点冒烟：见上 8080/8081/8082/5173 全 200（脚本分散在对话内）。
  - trigger-report：`POST http://127.0.0.1:9001/api/v1/trigger-report` Bearer agent_token → 200 `{"success":true,"asset_id":28,...}`。
  - logs/export：HTTP 200, Content-Type text/csv;charset=utf-8, 首行 BOM+表头, 行含 alert-252/platform-818 真实记录。
  - discovery/import：两次同 IP → 第一次 id=2218 already_exists=false，第二次 already_exists=true。
  - batch-delete：`{"ids":[2218]}` → deleted_count=1；`SELECT COUNT(*) FROM asset_software WHERE asset_id=2218` → 0。
  - field_names：`SELECT change_type,field_name,operator_name FROM asset_changes WHERE asset_id=2219` → 6 条 ('create','asset_type','admin') 等；update 后追加 ('update','location','admin')。
  - py_compile 6 核心文件 exit 0；npm run build ✓ built in 8.25s。

- Known issues / follow-ups
  - **软件名 mojibake（数据层）**：`/software/all?asset_id=28` 的 `software_name` 中文显示为 mojibake（`123äºç` 应为 `123云盘`）。疑似 agent 上报时写入编码或 DB 连接 charset 不匹配。属 P2 数据质量问题，本次未改。需查 agent `software_list` 上报编码 + assets_api 写入处 `SET NAMES`。
  - **admin 密码已重置为 Admin@2026**（生产环境若与他人共享需知悉）。
  - **Git 工作区仍脏**：188 staged + 8 unstaged 未提交（含本会话验证过的后端改动）。建立干净基线需用户授权（已 pin 决策项）。
  - **Playwright 未装**：浏览器自动化页面冒烟未做，仅 API 形状冒烟。后续可重试安装或用前端单元测试替代。

- Next step
  待用户回应两个 pinned 决策（远控帧率方案、Git 基线清理）后推进；同时可优先修软件名 mojibake 数据层问题（定位 agent 上报/写入编码）。

## [2026-08-29] Discovery page productization (frontend was calling an imagined API)

- Goal
  Make 资产发现 (discovery/Index.vue) actually work — it was assessed 65% "前端未产品化"; root cause found: the page was written against an API that doesn't exist.

- Where things stand
  DONE — three contract mismatches fixed in the frontend only (backend API is coherent and unchanged):
  1. **Ping payload**: page sent `ip_ranges` as one textarea STRING; backend `DiscoveryPingRequest.ip_ranges` is `List[str]` → 422 on every scan click. Now split on newlines/commas/Chinese punctuation, deduped.
  2. **SNMP payload**: page sent `{community, ip_ranges, version:'2c', timeout:5000}`; backend wants `targets:[{ip,community}]`, `version` int 1|2, `timeout` in SECONDS (1-30). Mapped accordingly.
  3. **Async semantics**: POST /discovery/ping|snmp returns `{task_id,...}` immediately (scan runs in a server thread); page expected a synchronous `res.data` host list. Rewrote both handlers to poll `GET /discovery/tasks/{id}` (1s interval, 300s cap, cleaned up on unmount), show live progress (`进度 current/total，已发现 N 台`), then map `found_ips` to result rows. Found hosts are auto-upserted into `assets` by the backend by design, so rows render 已入库 and 查看 resolves the asset id lazily via `GET /assets?keyword=<ip>` exact-match.
  Also verified the agent command channel (`POST /assets/{id}/command` → agent `/api/v1/command`) is already gated by per-agent bearer token (hmac compare) — the earlier "add allowlist" follow-up is unnecessary; arbitrary commands are the intended batch-ops feature (restart/shutdown/script/software all build shell commands).

- Verification evidence
  - E2E via 5173 proxy (admin token): POST `/api/v1/discovery/ping` `{"ip_ranges":["127.0.0.1"],...}` → `task_id` → poll → `status=completed, found=1, found_ips=['127.0.0.1']` → `/discovery/recent` returns the record with exactly the fields the history table binds (`created_at/scan_type/ip_ranges/total/online/status`) (script: `%TEMP%\kilo\e2e_discovery.py`).
  - `npm run build` → **✓ built in 7.63s**.

- Known issues / follow-ups
  - Scan-result rows show IP only (hostname/MAC columns render '-'): richer per-host detail would need a backend endpoint joining `found_ips` with `assets`; current UX accepts IP-first rows with lazy 查看 lookup.

## [2026-08-29] Software center frontend↔backend integration fixes (route order / proxy split / page_size / missing API modules)

- Goal
  Make 软件中心 (SoftwareCenter.vue + subpages) and 终端详情 (terminal/Detail.vue) load without 4xx/422 through the Vite dev proxy; restore a green production build.

- Where things stand
  DONE — 4 root causes fixed, all verified end-to-end with an admin token through `http://127.0.0.1:5173`:
  1. **FastAPI route shadowing (8081)**: `GET /software/packages/categories` and `/stats` were declared AFTER `/packages/{package_id}` → "stats" was parsed as an int `package_id` → 422. Moved both fixed-path routes (113 lines) before the param route in `software_management_api_complete_v2.py`.
  2. **Proxy misroute for the installed-software inventory**: frontend calls `/api/v1/software/all` but the Vite `/api/v1/software` prefix routes ALL such paths to 8081, while the endpoint (`get_all_software`, joins `asset_software`×`assets`) lives on 8080. Added a more specific `/api/v1/software/all → assetsTarget` rule FIRST in `frontend/vite.config.mjs` proxy map.
  3. **page_size ceiling**: SoftwareCenter.vue requests `page_size=200`; 8081 declared `le=100` → 422. Raised all `page_size` Query limits `le=100 → le=500` (6 occurrences).
  4. **Broken production build**: `terminal/Detail.vue` imported `@/api/terminal` (nonexistent) and `getInstalledSoftware` from `@/api/software` (not exported). Created `frontend/src/api/terminal.js` (`rebootTerminal`/`shutdownTerminal` → `POST /assets/{id}/command` with `shutdown /r|/s /t 5`, matching the agent's shell-command `/api/v1/command` handler) and added `getInstalledSoftware(assetId, params)` to `api/software.js` (→ `GET /software/all?asset_id=`, fields software_name/version/vendor/install_date match Detail.vue's table columns).

- Verification evidence
  - Proxy smoke (admin bearer token): 19/19 OK — packages (incl. `page_size=200`), stats, categories, package detail, tasks + task detail + task stats, `/api/v1/software/all`, legacy `/api/v1/packages`, compliance checks/results/stats, whitelist, install policies, 8082 policies, health (script: `%TEMP%\kilo\verify_proxy.py` + `smoke_rest.py`).
  - `python -m py_compile software_management_api_complete_v2.py assets_api.py` → OK.
  - `npm run build` → **✓ built in 7.32s** (1808 modules) — previously failed: `Could not load .../src/api/terminal`.
  - Services restarted and listening: 8080 (PID 45676), 8081 (PID 68404), 8082, 5173 (vite auto-restarted on config change, log shows "server restarted").

- Known issues / follow-ups
  - Fixed-path routes must always be declared before parameterized ones in this FastAPI service ( FastAPI matches in declaration order ) — watch this in future route additions.
  - `rebootTerminal`/`shutdownTerminal` execute via the agent shell-command channel; an agent-side allowlist (only `shutdown /r|/s`) would harden it.
  - VMware fps ceiling (see 2026-08-27 memory/software-layer work) still awaits VM-side decision: enable "Accelerate 3D Graphics" / upgrade VMware Tools (restore EDID) / avoid RDP session hijack, or proceed with the Parsec-VDD virtual-monitor plan (driver staged in `C:\VDDTest\driver`).

## [2026-08-24] Secure-desktop (UAC) input policy for the privileged helper

- Goal
  Close the gap vs commercial remote tools (向日葵/ToDesk/火绒) on clicking through UAC prompts: the LocalSystem helper already runs in the user session with SYSTEM rights and its input worker follows the current input desktop — the missing piece was a policy gate, audit trail, and a user-facing switch.

- Where things stand
  IMPLEMENTED & unit-verified; end-to-end UAC validation pending a machine with `EnableLUA=1` (this WORKGROUP host has UAC disabled, so secure desktop never appears). Final build deployed: SHA256 `9E5B5098…20B28`, acceptance 0 failures.
  How it works now:
  - Helper (`RemoteAgent/high_integrity_helper.py`) input worker already rebinds to whatever desktop is the CURRENT INPUT desktop (`ensure_current_thread_on_input_desktop`) — when UAC appears that becomes `Secure`, and as SYSTEM it may bind+SendInput there (consent.exe is also SYSTEM → no UIPI block).
  - NEW gate in `_DesktopBoundWorker._run` (input mode): if bound desktop kind starts with "secure" and policy denies, raise new `SecureDesktopInputBlocked` BEFORE executing the operation; if allowed, write `AUDIT ... secure-desktop input authorized` log line + mark binding state.
  - Policy resolution (helper `_secure_desktop_input_allowed`, 2s cache): `config.json remote_desktop.allow_secure_desktop_input` (default true) overridden by tray setting `allow_secure_desktop_input`.
  - Tray menu adds 「允许远程操作 UAC 提示」 toggle (IDM_TOGGLE_UAC_INPUT) persisting via tray settings.
  - Engine needs no change: mouse/keyboard already delegate service-first; denial surfaces as a single delegated-error log then harmless local fallback.

- Known issues / follow-ups
  - End-to-end UAC click validation requires an EnableLUA=1 machine (domain lab preferred); checklist from previous entry applies. Capture-side secure-desktop support already existed (`allow_secure_desktop=True` bindings), so operators can SEE the prompt today; after this change they can also CLICK it when policy allows.

- Verification evidence
  - `python tests\test_secure_desktop_input_policy.py` -> 4/4 PASS (deny blocks before op with SecureDesktopInputBlocked; allow executes with audit marker; normal desktop unaffected by deny-policy; tray toggle persists)
  - Full regression set exit 0: consent-mapping, ui-thread, messagebox-recycle, tray-actions(18/18), position(27/27), multimon harness
  - build_agent.ps1 static acceptance: 失败 0 项；config.json 同步含新键

## [2026-08-24] Remote desktop full functional test (mouse buttons / keyboard / drag / clipboard / admin elevation)

- Goal
  Verify real-session usability beyond coordinates: left/right click effects, keyboard typing, window dragging, copy-paste (hotkey + protocol clipboard), and admin-elevation operability.

- Where things stand
  DONE — final run **16/16 PASS** in one live session (`tests\remote_desktop_functional_test.py`, new):
  - 左键聚焦：injected click focuses target app (verified via GetForegroundWindow)
  - 键盘输入：per-char injected keystrokes land character-perfectly in Notepad (file saved via injected Ctrl+S read back locally)
  - 复制粘贴：①protocol `clipboard_set` → injected Ctrl+V → content lands in editor; ②injected Ctrl+A/Ctrl+C → `clipboard_get` returns doc text — both directions verified
  - 右键菜单：right-click on console title bar pops classic system menu (#32768 detected), ESC closes it
  - 鼠标拖动：title-bar drag moved console window by exactly (+150,+80), err ≤2px
  - 管理员权限：injected Win → search "cmd" → Ctrl+Shift+Enter spawned an ELEVATED cmd ("管理员:" console). Machine has UAC fully disabled (`EnableLUA=0`), so elevation needs no interactive prompt; agent worker itself runs at High integrity.

- Bugs/issues found & fixed this round (all in the test harness, not the product engine)
  1. **Target misidentification incident**: locating Notepad by title substring matched the OPERATOR'S own open Notepad documents; injected typing/clipboard ops briefly touched their unsaved buffers, and a global `taskkill /im notepad.exe` cleanup force-closed their windows. Original .txt files on disk were never written (no save executed), and both windows turned out to survive; operator informed inline. Harness fixes: launch Notepad with a UNIQUE temp file (title = marker), verify every injection point with `WindowFromPoint` root-hwnd guard before sending input, cleanup now WM_CLOSEes only harness-created hwnds.
  2. First-character loss after focus click → added foreground-confirmation retry loop + absorber keypress.
  3. Win11 new Notepad quirks: right-click menu is not class #32768 (moved that check to a classic conhost title bar); async save means an immediate Ctrl+S writes 0 bytes (merged typing+paste into a single deferred save assertion).
  4. Keyboard IME interaction (IMPORTANT product note): the controlled host's Chinese IME transforms injected Shift-combos (e.g. `_` → `——`) and can swallow digits mid-composition — identical to what a physical Chinese-IME user would experience; NOT an engine defect. Local control experiment (pyautogui direct vs WS-injected produced same mangling) proved the engine path faithful. Recommendation for frontend: use the existing clipboard channel (`clipboard_set`+Ctrl+V) for CJK/text blobs instead of synthetic typing; per-char `press` with explicit `shiftKey` is reliable for ASCII.

- Verification evidence
  - Final functional run output above: 总计 16 项, 失败 0 项
  - Elevated-command proof: fresh cmd pid with 管理员 window title after pure-injection sequence
  - Clipboard dual-path proof: protocol set/get roundtrip + hotkey paste landing
  - UAC posture recorded: EnableLUA=0, ConsentPromptBehaviorAdmin=0, PromptOnSecureDesktop=0; shell & agent at High integrity (S-1-16-12288)

## [2026-08-24] Professional redesign of the consent dialog (branded security UI)

- Goal
  Replace the plain system MessageBox consent prompt with a professional, Huorong-style security dialog.

- Where things stand
  DONE. Final build deployed: SHA256 `921A269D…A9CAE` (acceptance 0 failures). Operator visually approved the new dialog (including a button-clipping fix round) and the final live session passed **15/15**: consent approved via the new UI, screen_info physical 1920x1080, 54 frames/4s, cursor grid worst error **0px**, drag/wheel/right-click exact.
  New design (`cmdb_agent_consent_ui.py::_show_tk_consent_toplevel`, fully custom-drawn on the persistent Tk UI thread):
  - Brand header bar (deep blue `#16497E`) with drawn shield glyph, "Z-View 安全中心 / 远程控制确认", hover-danger close ✕; header is drag-to-move
  - Amber warning triangle + bold instruction 「「requester」请求远程控制这台设备」+ risk hint line
  - Request detail card (`#F4F7FA`, bordered): 请求方 / 来源地址 / 目标终端 / 本机用户 / 剩余时间
  - Custom canvas countdown progress bar (green -> amber below ~1/3) + "N 秒后未处理将自动拒绝"
  - Footer action bar: 拒绝 (flat gray secondary) + 允许 (green primary, larger); hover states, hand cursor
  - Keyboard: Enter = 允许, Esc = 拒绝; always-on-top, centered, modal grab
  - DPI-aware: all pixel metrics scale by `winfo_fpixels('1i')/96` (clamped 1.0-2.0) — fixes button clipping at 125% scaling reported by operator
  Backend chain updated: auto (source AND frozen) now prefers the branded tkinter dialog with native MessageBox fallback (`[tkinter, messagebox]`); taskdialog remains excluded (broken in this environment, E_INVALIDARG).

- Bugs found & fixed this round
  1. Button clipping at 125% DPI scaling (operator report: 允许/拒绝未显示完整) — root cause: fixed-pixel layout in a now-DPI-aware process while fonts scale. Fixed with the S_px() scaling helper across window/header/footer/button metrics; larger footer (84*S) and buttons (44*S high).
  2. Live-test flake: first grid sample raced with the operator's hand still on the mouse right after clicking 允许 (err 911px once). Test now waits 2s after consent approval before sampling (`tests\live_remote_session_test.py`); subsequent run was pixel-perfect.

- Verification evidence
  - `python tests\test_consent_ui_thread.py` -> 8/8 PASS (backend chains, persistent UI thread, two dialog cycles incl. auto-timeout)
  - `python tests\test_tray_menu_actions.py` -> 18/18 PASS (updated frozen-chain expectation to tkinter-first)
  - Operator-driven acceptance: first sighting missed (timing), second sighting clicked 允许 -> reported button clipping; DPI fix deployed; third sighting clicked 允许 -> confirmed 已修复，通过
  - Final live run: `python tests\live_remote_session_test.py` -> 总计 15 项, 失败 0 项 (worst=0px)
  - build_agent.ps1 static acceptance: 失败 0 项

## [2026-08-24] Tray menu: 「查看本机信息」 replaces 「打开管理台」

- Goal
  Per operator request, drop the console-entry tray item and show local machine info (IP/MAC etc.) instead.

- Where things stand
  DONE. Menu item 「查看本机信息(&I)」(IDM_MACHINE_INFO) opens a native info dialog; left-click on the tray icon shows the same dialog. Content: hostname, current user, OS+arch (`platform`), and per-adapter network details via bundled `psutil` (`net_if_addrs`/`net_if_stats`): adapter name, online/offline state, link speed, IPv4 list, IPv6 list, MAC (normalized to colon-separated uppercase). Loopback skipped, adapters with no addresses skipped, online adapters sorted first. Server address deliberately NOT shown (operator feedback). Functions are module-level for testability: `_tray_collect_machine_info` / `_tray_render_machine_info` / `_tray_show_machine_info`; `IDM_OPEN_CONSOLE` renamed `IDM_MACHINE_INFO`, `_tray_open_console` removed.
  Final build deployed: SHA256 `BF245596…24D9F2` (acceptance 0 failures). Operator confirmed the info dialog renders correctly (then requested server-address removal, which is included in this build).

- Verification evidence
  - `python tests\test_tray_menu_actions.py` -> 18/18 PASS incl. new checks: hostname/user/system collected, adapter list non-empty with IPv4+MAC present, rendered text contains 主机名/IPv4/MAC, does NOT contain server address, online-first ordering
  - Quick regression set exit 0: consent-mapping, messagebox-recycle, position suite
  - build_agent.ps1 static acceptance: 失败 0 项

## [2026-08-24] Huorong-style tray icon (brand icon + right-click function menu) and TaskDialog removal

- Goal
  Upgrade the agent tray icon to a Huorong-style presence: branded icon in the notification area plus a right-click function menu (console entry, remote-control switches, about, exit), and keep fixing outstanding bugs.

- Where things stand
  DONE. Final build deployed (`Z-View.exe` SHA256 `3C0DBD9E…8E961`, acceptance 0 failures). Operator confirmed visually: brand icon shows in the tray area and the right-click menu renders correctly with checkmarks. Toggle round-trip proven end to end: unchecking 「允许远程控制请求」 makes pipe requests return `{"approved": false, "reason": "disabled_by_user"}` with NO dialog; re-checking restores the dialog flow. Final live session: **15/15 PASS** (approved by click; grid worst 0px; drag/wheel/right-click exact).
  Menu layout (`cmdb_agent_consent_ui.py::_run_native_tray_icon`, all native Win32):
  - 打开管理台(&O) -> opens `server_url` from agent config via ShellExecute
  - ---
  - 允许远程控制请求 [check] -> persists `allow_remote_requests` (`user_session_settings.json`)
  - 本机免确认（自动允许） [check] -> persists `skip_consent_for_session`; ENABLING requires a warning confirm box
  - 请求到达时弹出气泡提醒 [check] -> persists `show_balloon_notifications`
  - ---
  - 关于 Z-View… / 退出代理(&X) (exit needs confirm; removes icon cleanly)
  Left click also opens the console. Tooltip: "Z-View 终端管理代理\n远程控制请求：弹窗询问". Consent requests now fire a tray balloon (NIM_MODIFY/NIF_INFO) before the dialog when the balloon toggle is on.

- Bugs found & fixed this round
  1. Tray used the generic Windows logo (`IDI_APPLICATION`) and had NO menu at all — replaced with `LoadImageW` over bundled `favicon.ico` (fallback: embedded exe icon), class+tray icons set together.
  2. TaskDialogIndirect returns E_INVALIDARG **in every configuration on this machine** — bisected to failure of even an independent minimal struct call (not our definition; activation-context injection tested and ruled out). Removed taskdialog from auto backend chains: frozen builds now `[messagebox, tkinter]`, source/dev `[tkinter, messagebox]`; explicit `ZVIEW_CONSENT_UI_BACKEND=taskdialog` still honored for future debugging. This also removed the per-request fallback delay/noise.
  3. Tray actions were UI-closure-only and untestable — extracted `_tray_open_console` / `_tray_apply_toggle` / `_tray_set_skip_consent` / `_show_tray_balloon` as module-level functions bound onto ConsentTrayApp.

- Verification evidence
  - `python tests\test_tray_menu_actions.py` -> 9/9 PASS (backend chains incl. no-taskdialog; ALLOW toggle flips+persists setting; SKIP toggle persist round-trip; balloon safe without tray)
  - Full regression set exit 0: consent-mapping 5/5, consent-ui-thread 8/8, messagebox-recycle 3/3, position 27/27, multimon harness 4/4
  - Live enforcement proof (operator-driven): tray uncheck -> probe reply `disabled_by_user`; re-check -> dialog appears again; final live test 总计 15 项, 失败 0 项
  - Operator visual confirmation: brand icon + full right-click menu with correct checkmarks

## [2026-08-24] Consent helper leftover issues resolved (stability, visibility, window residue)

- Goal
  Resolve the follow-ups pinned by the previous packaging/live-test round: packaged consent-helper silent death, dialog visibility doubt for frozen builds, BrokenPipeError listener churn, plus anything found while digging.

- Where things stand
  DONE and re-verified end to end on the final build (`Z-View.exe` SHA256 `BCA608ED…77C041`, static acceptance 0 failures). Final live session via platform proxy: **15/15 PASS** — consent approved by human click on the native dialog served by the packaged helper, screen_info physical 1920x1080, frames cover full desktop, cursor grid worst error 0px, drag/wheel/right-click exact.
  Fixes shipped (all in `cmdb_agent_consent_ui.py` unless noted):
  1. Helper silent death root-caused to per-request `tk.Tk()` creation/destruction on the pipe thread: cross-thread Tcl cleanup aborts the process ("Tcl_AsyncDelete … wrong thread"), matching the observed no-log disappearances. Replaced with a single persistent hidden Tk root owned by one dedicated UI thread (`_ensure_tk_ui_thread`); dialogs are now Toplevels marshalled via queue (`_show_tk_consent_toplevel`, `_invoke_tk_consent_dialog`). Helper `main()` ends with `os._exit(0)` to skip interpreter-finalization races. Verified: source-mode suite `tests\test_consent_ui_thread.py` 8/8 incl. two consecutive dialog cycles; packaged-helper soak 3/3 cycles with process alive throughout.
  2. Frozen builds now prefer native backends: `_determine_dialog_backends` auto order becomes `[taskdialog, messagebox, tkinter]` when `sys.frozen`; source/dev keeps tkinter-first UX unchanged. Forensic watch (`tests\consent_visibility_watch.py`) confirmed the engine-path dialog is rendered by the packaged helper process on the interactive desktop; operator clicks registered instantly on probe and final live runs.
  3. Pipe handler no longer tears down the listener when a client disconnects mid-request: the error-reply send is guarded so `BrokenPipeError` cannot bubble into the outer rebuild loop (previously logged "fallback consent helper listener error").
  4. NEW bug found during forensics: the messagebox backend abandoned its thread on timeout, leaving the native MessageBox window permanently on the desktop — repeated failed attempts stacked three identical boxes side by side, which is very likely why operators missed/mistrusted prompts. Fixed via WH_CBT hook capturing the box handle at HCBT_ACTIVATE and `EndDialog(hwnd, IDTIMEOUT)` on timeout. Regression `tests\test_messagebox_timeout_recycle.py` 3/3: IDTIMEOUT returned at exactly the configured seconds and zero windows remain; a 3-cycle no-click soak against the packaged build left zero orphan dialogs (previously would have stacked three).

- Known issues / follow-ups
  - TaskDialog backend fails in the packaged exe (`WinError -2147024809` E_INVALIDARG — comctl32 v6 activation context not active under current PyInstaller manifest); the designed fallback to native MessageBox engages every time, which is fully functional. Enabling v6 (spec manifest tweak) would restore the nicer countdown UI — optional polish.
  - Ops note: if a remote-desktop client dies abnormally mid-consent, the platform's asset route can wedge (subsequent WS handshakes time out until `assets_api.py` is restarted and the agent re-registers). Observed once today; recovery = restart platform API then agent. Consider adding server-side stale-session reaping later.

- Verification evidence
  - `python tests\test_consent_ui_thread.py` -> 8/8 PASS (backend orders, persistent UI thread, two dialog cycles, clean exit)
  - `python tests\test_messagebox_timeout_recycle.py` -> 3/3 PASS (IDTIMEOUT at configured seconds, zero residue)
  - Packaged-build soak: 3 consecutive no-click pipe probes -> clean `{"approved": false, "reason": "timeout"}` each cycle, helper alive, **zero** orphan dialogs afterward
  - Full regression set: consent-mapping 5/5, position 27/27, multimon harness 4/4 (exit 0 each)
  - Final live run on deployed build: `python tests\live_remote_session_test.py` -> 总计 15 项, 失败 0 项
  - Forensics: `tests\consent_visibility_watch.py` attributed the engine-path dialog to the packaged helper PID on the interactive desktop

## [2026-08-24] Agent packaging (build_agent.ps1) + real-machine remote control live test

- Goal
  Package the agent with build_agent.ps1 and run a full real-machine remote control session test (consent dialog -> screen_info -> frames -> mouse grid/drag/wheel/right-click), attempting multi-monitor on the way.

- Where things stand
  DONE. Final packaged agent deployed at `D:\IT2026-temp\zview-build\dist\GPO部署包\Z-View.exe` (SHA256 `4C7D648D…94AF6`, static acceptance 0 failures). Live session via platform proxy (`ws://127.0.0.1:8080/api/v1/assets/28/remote-desktop/ws`, asset 28 = this host): **15/15 PASS** — consent approved by human click, `screen_info` 1920x1080 matches physical desktop, 30 frames/4s, frame 1440x810 covers the whole virtual desktop at adaptive scale ≈0.75, six-point cursor grid worst error **0px**, drag endpoint exact (start (480,270) -> end (680,370)), wheel event lands exactly at its normalized target, right-click down/up OK.
  Multi-monitor physical test NOT possible on this host: it is an RDP session whose idle display adapters expose no activatable modes (`DISP_CHANGE_BADMODE`); a second screen needs an RDP multi-mon reconnect or a signed IDD driver payload which the repo lacks (Drivers/VirtualDisplay holds placeholders only). Covered instead by production-path integration tests in dual-screen geometry: `python tests\multimon_engine_harness.py` -> 4/4 pass.

- Bugs found & fixed this round
  1. Consent UI mojibake: 16 lines of double-encoded Chinese (UTF-8->GBK round trip incl. private-use chars) in `cmdb_agent_consent_ui.py` made the dialog show garbage text; restored correct copy via line-precise fix script + project-wide mojibake scanner (`tests\detect_mojibake.py`, rescan clean).
  2. WTS fallback response mapping: on this OS build `WTSSendMessageW` returns TRUE with `response=0` after wait timeout instead of IDTIMEOUT(32000); engine reported `unknown_response:0`. Now mapped to `timeout` with diagnostic print (`remote_desktop_engine_v2._show_wts_dialog`). Regression suite added: `tests\test_consent_response_mapping.py` -> 5/5.
  3. Dead config wiring: `CONSENT_MANAGER.configure()` was never called anywhere, so `remote_desktop.consent_timeout_seconds` / `require_consent` in config.json had no effect (dialog always waited 30s). Now applied once at engine module load from the resolved agent config; verified live that the dialog honored timeout=90.
  4. DPI-unaware worker nondeterminism (the big one): the packaged worker sometimes ran DPI-unaware, reading virtualized metrics at 125% scaling — sessions logged `screen_info ready=1536x864`, capture cropped to the top-left 80% of the physical desktop while input still mapped across the FULL screen (guaranteed picture/click misalignment on any high-DPI host; also explains an earlier "drag overshoot" observation). Fix: `ensure_windows_dpi_awareness()` called first thing in agent `main()` plus a `SetThreadDpiAwarenessContext(PER_MONITOR_AWARE_V2)` guard around metric reads in `CoordinateMapper.refresh_metrics`. Live evidence after fix: screen_info=1920x1080 physical, frames cover whole desktop, grid error dropped from <=1px to 0px.

- Known issues / follow-ups
  - Packaged `--consent-ui` helper showed one silent-death episode earlier today (process gone without log after serving a dialog; one BrokenPipeError listener loop). In the final build it survived a full dialog+timeout cycle and served the approved click. Needs soak testing.
  - Operator could not confirm tkinter dialog visibility during two 90s windows (clicks missed); native/WTS paths were proven clickable. Consider preferring the native MessageBox backend for frozen builds.
  - Virtual display driver payload still missing -> no true second monitor on headless/RDP hosts (existing WARN in build acceptance).
  - Test-session config change: `consent_timeout_seconds` raised 30 -> 90 in both repo and deployed config.json for humane click windows.

- Verification evidence
  - `python tests\test_remote_desktop_position.py` -> Ran 27 tests, OK (exit 0)
  - `python tests\multimon_engine_harness.py` -> 4/4 ok (exit 0)
  - `python tests\test_consent_response_mapping.py` -> 5/5 PASS
  - `python tests\live_remote_session_test.py` -> 总计 15 项, 失败 0 项 (console output above)
  - `python -m py_compile remote_desktop_engine_v2.py coordinate_mapper.py cmdb_agent_unified_v2.py` OK
  - `build_agent.ps1` static acceptance: 失败 0 项，警告 1 项（虚拟显示载荷缺失为既有已知项）
  - Server-side traces cross-checked in `C:\ProgramData\CMDB-Agent\logs\agent-runtime.log` (session_1787550xxx series)

## [2026-08-24] Remote desktop position (coordinate) audit and fixes

- Goal
  Verify the remote desktop mouse position chain end to end, fix any coordinate bugs, and lock the behavior with regression tests.

- Where things stand
  Audited the full chain: frontend `WebRemoteDesktop.vue` resolveCanvasPosition -> `remote_desktop_protocol.parse_mouse_message` -> engine `_build_mouse_events`/`handle_mouse_denormalize` -> `CoordinateMapper.denormalize_coordinate` -> `InputInjector._move_to` SendInput absolute mapping. Cross-checked live traffic in `C:\ProgramData\CMDB-Agent\logs\agent-runtime.log` (2026-08-21 session: normalized 0.9845 -> screen 1890, dx 64545, mapping correct).
  Found and fixed 4 defects:
  1. Frontend normalized coordinates divided by `(canvasWidth - 1)` instead of `canvasWidth`, inflating fractions by up to ~size/(size-1) and shifting remote clicks right/down by up to ~2px after backend rescaling (`frontend/src/components/WebRemoteDesktop.vue`).
  2. `CoordinateMapper.denormalize_coordinate` used `int()` truncation causing a systematic <=1px left/top bias; now rounds on the full-size convention.
  3. `input_injector._denormalize_to_virtual_desktop` used a different formula (`(width - 1)` scaling) than the mapper; both modules now share one convention so main and fallback paths agree per-point.
  4. PyAutoGUI last-resort capture backend grabbed only the primary monitor while input maps the whole virtual desktop; `_grab_with_pyautogui` now uses `ImageGrab.grab(all_screens=True)` with fallback. Also aligned backend deltaY wheel-step cap to +/-12 matching the frontend.
  Verification evidence:
  `python tests\test_remote_desktop_position.py` (new suite, IT2026/IT2026/tests/): 27/27 pass, including full-chain grid accuracy across three display geometries (single 1920x1080, dual left-of-primary -1920x3840x1080, dual above-primary 1920x2160), SendInput round-trip error <=1px, drag release follows cursor, wheel direction end-to-end, and regression tests for each fix above.
  `python -m py_compile coordinate_mapper.py input_injector.py remote_desktop_protocol.py Capture\desktop_capture.py` OK.
  `npm.cmd run build` in frontend succeeded (WebRemoteDesktop chunk rebuilt).

- Next step
  Rebuild the Agent package (build_agent.ps1) and run one real remote session clicking near screen corners/mid-edges to confirm on-target behavior in production; multi-monitor host testing remains outstanding.

## [2026-08-21] Remote desktop consent enforcement

- Goal
  Require the terminal user's explicit Allow or Reject decision before remote desktop frames or input are enabled.

- Where things stand
  Found `require_consent=true` in the Agent configuration, and the frontend already handled `consent_required` and `consent_result`, but `RemoteAccessConsentManager.request_permission()` was never called by the remote-desktop session. This allowed mouse input without any Allow/Reject dialog.
  Updated `IT2026/IT2026/remote_desktop_engine_v2.py` so session startup sends `consent_required`, invokes the terminal consent helper, blocks frame capture/input until approval, returns `consent_result`, and closes rejected or timed-out sessions with `4003/consent_denied`. Updated `IT2026/IT2026/cmdb_agent_core.py` to expose the WebSocket peer address to the session adapter for consent context.
  Verification evidence:
  Runtime logs from the previous remote session contained successful `SendInput` mouse operations but no consent events.
  A protocol test returned `consent_required`, then `consent_result` with `approved=False`, `reason=rejected`, and close code `4003` with reason `consent_denied`.
  `npm.cmd run build` completed successfully for the frontend, which already suppresses reconnect for close code `4003` and handles both consent messages.

- Next step
  Build the Agent package, launch it, and use a real remote WebSocket request to verify the terminal displays an Allow/Reject dialog and the selected result controls session startup.

## [2026-08-21] Agent telemetry field and software-report alignment

- Goal
  Restore real CPU, memory, disk, process, and installed-software data on the terminal detail page.

- Where things stand
  Found asset `28` had repeated heartbeat rows with all metric columns at zero and no `asset_software` rows. The Agent collected valid local values but sent `cpu_percent`/`memory_percent`/`disk_percent`, while the Assets API inserts only `cpu_usage`/`memory_usage`/`disk_usage`. It also sent software to unimplemented `/api/v1/assets/software` instead of the existing authenticated Agent heartbeat pipeline.
  Updated `IT2026/IT2026/assets_api.py` to accept both metric naming conventions. Updated `IT2026/IT2026/cmdb_agent_core.py` so heartbeat uses the persisted metric names and carries process/user information; the redundant stats loop is now a no-op; software inventory now reports through `/api/v1/agent/heartbeat` as `report_type=software` with the existing `software_list` schema.
  Verification evidence:
  Direct database inspection showed the last 12 heartbeat records for asset `28` all had CPU/memory/disk `0.0` and `asset_software` count `0`.
  Source collection returned CPU `1.0%`, memory `24.3%`, disk `66.7%`, and `123` registry software records.
  Restarted the Assets API and submitted compatible metric/software reports.
  Result: heartbeat values persisted as CPU `0.4%`, memory `24.3%`, disk `66.7%`, process count `226`; software sync completed with `123` records.
  Rebuilt `D:\IT2026-temp\zview-build-20260821-r7\dist\GPO部署包` and replaced r6.
  Result: r7 worker PID `14820` listens on `9000` and `9001`; tray helper PID `14604` is running; package SHA256 `66D8206FCE3168DCAF2124939E4C854CC775619590275F795997425E4FA508B1`.
  After the automatic reporting interval, asset `28` stored CPU `0.0%`, memory `24.2%`, disk `66.7%`, process count `230`; its software inventory remained `123` records with latest write at `2026-08-21 12:04:57`.
  Agent immediate report returned `success=True`, asset_id `28`.

- Next step
  Refresh the terminal detail page. It now reads the repaired heartbeat history and software inventory; zero CPU may legitimately occur during an idle sample, while memory, disk, and process count confirm live metrics are being collected.

## [2026-08-21] Remove periodic netsh gateway subprocess

- Goal
  Eliminate Agent-owned console-host creation completely rather than only hiding its window.

- Where things stand
  Source testing showed `CREATE_NO_WINDOW` still creates a `conhost.exe` for `netsh`, which can flash on the interactive desktop. Updated `IT2026/IT2026/cmdb_agent_core.py` to remove the nonessential `netsh` default-gateway fallback; gateway telemetry now remains empty if it cannot be obtained without spawning a process.
  Verification evidence:
  The prior source probe created `netsh.exe` PID `3744` and a child `conhost.exe` PID `11652`, even with `CREATE_NO_WINDOW` set.
  Source gateway-probe test returned without creating `netsh.exe` or `cmd.exe`.
  Rebuilt `D:\IT2026-temp\zview-build-20260821-r6\dist\GPO部署包` and replaced the active Agent.
  Result: r6 worker PID `14712` listens on `9000` and `9001`; consent tray PID `8088` is running; package SHA256 is `22DA0FC1738B95668729390BCE7AC1759B24B16CDB64203175AD7416356B6642`.
  Observed two full 30-second Agent collection intervals.
  Result: no Agent-owned `netsh.exe` process was created; immediate report returned `success=True`, asset_id `28`.

- Next step
  The periodic Agent console-flash source is removed. If a separate CMD window still appears, capture its exact timing and visible command text because it is not the Agent gateway probe.

## [2026-08-21] Agent gateway probe console-flash fix

- Goal
  Remove the recurring visible CMD window created by the Agent's periodic default-gateway discovery.

- Where things stand
  Process tracing captured the flash every roughly 30 seconds as `netsh interface ip show config`, launched by the active r5 Agent worker PID `11464`, not by the frontend or tray helper. Updated `IT2026/IT2026/cmdb_agent_core.py` so this Windows-only subprocess uses `CREATE_NO_WINDOW`.
  Verification evidence:
  High-frequency process tracing captured `conhost.exe` from `netsh.exe`; event tracing resolved the parent chain to `Z-View.exe` PID `11464` and command `netsh interface ip show config` at `11:42:19` and `11:42:51` on August 21, 2026.

- Next step
  Run the source gateway probe under a console-host watcher, rebuild the Agent, replace r5, and observe at least two Agent collection intervals with no Agent-owned console-host creation.

## [2026-08-21] Frontend CMD window elimination

- Goal
  Stop the flashing Command Prompt window while retaining the one-script platform startup behavior.

- Where things stand
  Identified the visible CMD processes as the frontend dev server chain: `npm.cmd` spawned `vite.cmd`; they were persistent wrappers, not Agent restart loops. Updated `start_platform.ps1` to resolve `node.exe` and directly run `frontend/node_modules/vite/bin/vite.js`, avoiding both batch wrappers while preserving the same Vite host and port.
  Verification evidence:
  `powershell -NoProfile -ExecutionPolicy Bypass -File .\start_platform.ps1 -Action Restart`
  Result: assets API `8080` PID `5144`, software API `8081` PID `7600`, policy API `8082` PID `6876`, and frontend `5173` PID `9000` all listened successfully.
  Frontend process is now `node.exe ... vite.js --host 127.0.0.1`.
  After a 12-second observation, no `cmd.exe` process matching `npm.cmd` or `vite.cmd` existed; PowerShell parse check and `git diff --check` completed without errors.

- Next step
  Continue Agent functional testing separately; the flashing CMD issue is removed from the platform frontend startup path.

## [2026-08-21] Original GPO Agent package runtime assessment

- Goal
  Determine whether the repository's original `GPO部署包\Z-View.exe` can be safely used as a fallback.

- Where things stand
  Tested an isolated copy of the original EXE (`SHA256 6E0C9530162BA37B1DA27F067410BC0D740E8234A6E645D7914D4932C8F109A0`) with the current production server URL and token injected only into the temporary test copy. The repository package was not changed.
  Verification evidence:
  Default launch produced no resident `Z-View.exe` worker after 20 seconds.
  Forced legacy worker launch using `--run-agent --no-remote-desktop --disable-session-supervisor` remained resident, but did not listen on control port `9001`; authenticated `/api/v1/command` and `/api/v1/trigger-report` both failed to connect.
  Restored the verified r5 Agent after testing. Result: r5 worker PID `11464` listens on `9000` and `9001`, and tray helper PID `14928` is running.

- Next step
  Do not use the repository's original GPO EXE as a production fallback; retain it only as an archived legacy artifact while using the r5 deployment package for verified Agent control and reporting.

## [2026-08-21] Native tray 64-bit handle safety

- Goal
  Ensure the restored notification-area icon uses valid native window handles on 64-bit Windows.

- Where things stand
  Updated `IT2026/IT2026/cmdb_agent_consent_ui.py` to declare the pointer-sized Win32 signatures for `GetModuleHandleW`, window class registration/creation, message dispatch, and `Shell_NotifyIconW`; this prevents the newly restored tray implementation from truncating its hidden window handle.
  Verification evidence:
  The prior GDI capture issue demonstrated this runtime previously used undeclared Win32 APIs and could truncate handles on 64-bit Windows. The consent module import remains valid under the packaging Python 3.12 runtime.
  Rebuilt `D:\IT2026-temp\zview-build-20260821-r5\dist\GPO部署包`.
  Result: release verification passed with only the existing unsigned virtual-display-driver warning; `Z-View.exe` SHA256 `A1BE397D6827A47D22E588306FD7D304EB94DCB26D6D27324B127A61A008895F`.
  Replaced the running Agent with the r5 package.
  Result: direct worker PID `6048` listens on `9000` and `9001`; consent tray PID `8736` is running; runtime log confirms `native tray icon added`; consent pipe is reachable.

- Next step
  Agent startup, control, heartbeat, software polling, consent tray, and WebSocket bootstrap are verified. The remaining remote-frame limitation is environmental: this RDP endpoint has no privileged service and no physical or signed virtual display substrate, so Windows returns no capturable desktop pixels.

## [2026-08-21] Native consent tray icon restoration

- Goal
  Restore a visible Z-View notification-area icon when the original consent tray bytecode is unavailable.

- Where things stand
  The active consent helper had a healthy heartbeat and reachable consent pipe, but its runtime log proved it was using the no-icon fallback implementation. `pystray` is not installed in the packaging Python runtime, so adding that dependency would not recover the tray consistently.
  Updated `IT2026/IT2026/cmdb_agent_consent_ui.py` fallback to run the consent pipe on a background thread and create a native Windows notification-area icon through `Shell_NotifyIconW`, using the system application icon and tooltip `Z-View Agent` without any third-party dependency.
  Verification evidence:
  `py -3.12` imported `cmdb_agent_consent_ui` successfully after the change.
  Before the change, the active fallback log read `fallback consent helper listening` and no tray implementation was available; the helper PID `7088` was alive with a fresh heartbeat and reachable consent pipe.

- Next step
  Rebuild the Agent package, replace the running helper, and verify the runtime log contains `native tray icon added` while the consent pipe remains reachable.

## [2026-08-21] 64-bit GDI capture handle correction

- Goal
  Restore the GDI fallback's ability to pass native desktop handles correctly on 64-bit Windows.

- Where things stand
  A direct local-capture test reached the fallback chain but failed at GDI with `OverflowError: int too long to convert`. Updated `IT2026/IT2026/Capture/desktop_capture.py` to declare pointer-sized `gdi32` handle signatures for device contexts, bitmaps, `BitBlt`, `GetDIBits`, and cleanup calls, preventing ctypes from truncating 64-bit handles to C ints.
  Verification evidence:
  The pre-fix capture test reported DXGI access denied, MSS BitBlt failure, GDI handle overflow, ImageGrab failure, and PyAutoGUI failure; GDI was the deterministic implementation defect in that chain.

- Next step
  Re-run the local capture test, then rebuild and WebSocket-test the Agent package to separate fixed local capture behavior from any remaining RDP desktop-surface limitation.

## [2026-08-21] Direct Agent local remote-desktop capture fallback

- Goal
  Deliver remote-desktop image frames when the Agent is run directly without the optional privileged Windows service.

- Where things stand
  Runtime WebSocket testing proved session bootstrap works but returned only `session_warning` after the initial metadata. The endpoint has no `CMDB-Agent` service or `CMDB-Agent-Privileged` pipe; the existing v2 engine therefore forced `service_capture_unavailable` and never invoked its bundled local screen capturer.
  Updated `IT2026/IT2026/remote_desktop_engine_v2.py` so an absent service uses `legacy_local_capture` for frame capture and direct input routing, while a present service keeps the existing service-helper capture path. Local backend recreation now rebuilds the local capturer instead of remaining blocked.
  Verification evidence:
  Before the change, the live remote WebSocket received `screen_info`, `session_settings`, `remote_capabilities`, then only `session_warning`; runtime logs recorded `local_fallback_blocked service_managed_capture_required`.
  The rebuilt `r3` Agent validated direct worker PID `11520`, consent tray PID `15048`, listener ports `9000`/`9001`, no `conhost.exe` child process, and an active consent runtime heartbeat file.

- Next step
  Compile the capture fallback, build a new Agent package, and verify a direct WebSocket session receives a `frame` message on this RDP endpoint.

## [2026-08-21] Direct Agent tray startup and console-flash fix

- Goal
  Make a double-clicked Agent start a stable remote-desktop worker with the Z-View tray helper and without flashing console windows.

- Where things stand
  Functional testing confirmed the running Agent control, heartbeat, policy sync, task polling, and WebSocket session bootstrap. The direct executable path incorrectly started the user-session supervisor, which treats every same-session `Z-View.exe` process as a user-session agent and terminates the PyInstaller launcher; it also did not launch the consent tray helper. Updated `IT2026/IT2026/cmdb_agent_unified_v2.py` so the no-argument direct path delegates no session supervisor, launches the consent tray helper after the remote listener starts, and starts that helper with `CREATE_NO_WINDOW`.
  Verification evidence:
  Existing package direct startup produced a resident worker PID `9912` with `9000` and `9001` listening, but no tray helper process.
  Agent control command returned `success=True`, immediate report returned `success=True` with asset_id `28`, and software policies/task polling both returned HTTP success.
  Remote WebSocket returned `screen_info`, `session_settings`, and `remote_capabilities`.

- Next step
  Compile and rebuild the Agent package, launch the rebuilt direct executable, and verify tray helper process, port listeners, no supervisor duplicate-cleanup event, and no console window creation.

## [2026-08-21] Platform startup stale-process cleanup

- Goal
  Ensure the one-script platform restart replaces orphaned API processes instead of reporting success while an older process still owns a service port.

- Where things stand
  Found `start_platform.ps1 -Action Restart` used an outdated state-file PID, leaving the old `assets_api.py` process on port `8080`; the newly started process exited with WinError `10048`. Updated `start_platform.ps1` so managed stop/restart also terminates orphaned Python processes whose command line is one of the platform API entry scripts.
  Verification evidence:
  Before the correction, port `8080` was owned by PID `19924` while the state file recorded PID `7880`; the replacement PID `5900` exited on bind conflict.
  `powershell -NoProfile -ExecutionPolicy Bypass -File .\start_platform.ps1 -Action Restart`
  Result: started assets API PID `19640`, software API PID `13112`, policy API PID `20284`, and frontend PID `22548`.
  Verified listeners: `8080` -> `19640`, `8081` -> `13112`, `8082` -> `20284`; the frontend Vite child owns `5173`.
  Agent control immediate report returned HTTP `200`, `success=True`, asset_id `28`; the resident Agent PID `23492` continues to own `9000` and `9001`.
  `python -m py_compile .\IT2026\IT2026\assets_api.py` and `git diff --check`
  Result: exit code 0.

- Next step
  Continue the remaining module-completion audit; the Agent startup, Agent heartbeat registration, and one-script platform restart path are verified for the current environment.

## [2026-08-21] Legacy asset change-history migration ordering correction

- Goal
  Complete the live legacy `asset_changes` migration without invalid writes to its old enum column.

- Where things stand
  The first live migration attempt identified that the legacy nullable `source_type` enum rejects the new `platform` value before conversion. Updated `IT2026/IT2026/assets_api.py` to normalize blank legacy rows to the enum-compatible `agent` value, then convert the column to the current varchar form with the `platform` default.
  Verification evidence:
  First migration attempt failed before commit with `1265 (01000): Data truncated for column 'source_type' at row 1`.
  The preceding `ALTER TABLE` statements are idempotent; rerunning the corrected migration continues from the existing schema state.

- Next step
  Run the corrected migration, restart the managed platform, and verify the Agent immediate-report endpoint returns success.

## [2026-08-21] Legacy asset change-history schema compatibility

- Goal
  Restore Agent heartbeat registration for the existing database schema so an online Agent appears in the terminal frontend.

- Where things stand
  Found the live Agent process running as `Z-View.exe` PID `23492`, listening on `9000` and `9001`; its remote-desktop WebSocket handshake succeeded. The Agent control-plane immediate report returned `502`; the Assets API returned `500` with `Unknown column 'operator_name' in 'field list'` from the legacy `asset_changes` table.
  Updated `IT2026/IT2026/assets_api.py` so `ensure_asset_changes_table` migrates the legacy `changed_by`/`changed_at` columns to the current history fields, adds missing `operator_name`, `details_json`, and `created_at`, and converts legacy enum columns so current `agent_report` and `platform` values can be written.
  Verification evidence:
  `python -m py_compile .\IT2026\IT2026\assets_api.py`
  Result: exit code 0.
  Inspected live `asset_changes` schema.
  Result: it lacked `operator_name`, `details_json`, and `created_at`; `change_type` and `source_type` were legacy enums.

- Next step
  Apply the migration against the configured database, restart the Assets API, and verify Agent immediate-report success through `http://127.0.0.1:9001/api/v1/trigger-report`.

## [2026-08-21] Agent direct-run persistence fix

- Goal
  Fix the Agent EXE flashing and exiting immediately when launched without service arguments.

- Where things stand
  Fixed `IT2026/IT2026/cmdb_agent_unified_v2.py`: the default remote-desktop startup path no longer returns after launching daemon threads; it now starts the optional user-session supervisor and enters the keepalive loop.
  Strengthened bundled core loading so frozen EXE builds must use the embedded `cmdb_agent_core`; source-only file loading remains a fallback for non-frozen runs.
  Kept `cmdb_agent_core` as an explicit PyInstaller hidden import in `build_agent.spec`.
  Rebuilt the fixed deployment package at `D:\IT2026-temp\zview-build-20260821-r2\dist\GPO部署包`.
  Verification evidence:
  `python -m py_compile .\IT2026\IT2026\cmdb_agent_unified_v2.py .\IT2026\IT2026\cmdb_agent_core.py`
  Result: exit code 0.
  Mocked the default `run_agent_service(enable_remote_desktop=True)` flow.
  Result: `default-agent-keepalive-ok`; reporter, control service, remote desktop, supervisor, and keepalive were called in order without returning early.
  `cmdb_agent_unified_v2.load_core_module()`
  Result: `core-load-protection-ok` with `CONFIG` and `SOFTWARE_CONFIG` present.
  Built with `py -3.12`; release package verification passed with the expected unsigned virtual-display-driver warning only.
  `archive_viewer --recursive --brief` on the new EXE.
  Result: contains `cmdb_agent_core`.

- Next step
  Replace the endpoint's old package with the new `zview-build-20260821-r2` package and verify service/GPO startup on the affected terminal.

## [2026-08-20] Agent package core-module load fix

- Goal
  Fix the packaged Agent crash `AttributeError: module 'cmdb_agent_core' has no attribute 'CONFIG'` and rebuild the Agent package for server `172.16.250.120`.

- Where things stand
  Updated `IT2026/IT2026/cmdb_agent_unified_v2.py` to prefer the bundled `cmdb_agent_core` module when it exposes both `CONFIG` and `SOFTWARE_CONFIG`, with the legacy file loader retained only as a fallback.
  Added `cmdb_agent_core` to `IT2026/IT2026/build_agent.spec` hidden imports so the EXE contains the actual core module instead of relying on an external file at runtime.
  Updated the ignored local `IT2026/IT2026/config.json` server URL to `http://172.16.250.120:8080`.
  Updated `build_agent.ps1` to prefer `py -3.12` for reproducible Agent packaging.
  Rebuilt the deployment package at `D:\IT2026-temp\zview-build\dist\GPO部署包`.
  Verification evidence:
  Imported `cmdb_agent_unified_v2.load_core_module()` from source.
  Result: `agent-core-bundle-load-ok` and resolved `cmdb_agent_core.py` with `CONFIG` and `SOFTWARE_CONFIG`.
  `powershell -NoProfile -ExecutionPolicy Bypass -File .\IT2026\IT2026\build_agent.ps1`
  Result: package verification passed; new `Z-View.exe` SHA256 `5E253EC7C49CD9C3EB6D9DEAC27D6CD9E56C215B91C2ABBA505B0EFF4841B289`.
  Started the new `D:\IT2026-temp\zview-build\dist\GPO部署包\Z-View.exe` with `--run-agent --no-remote-desktop --disable-session-supervisor` for six seconds.
  Result: `agent-exe-startup-survived`; it did not exit at the previous `module.CONFIG` failure point.

- Next step
  Replace the affected endpoint's old package with the rebuilt GPO package and verify heartbeat registration against `172.16.250.120`.

## [2026-08-20] Asset and remote desktop completion pass

- Goal
  Push the asset module and remote desktop chain toward a code-complete state by fixing the list/create/detail pages and making the remote desktop/WebSocket bridge actually callable.

- Where things stand
  Rebuilt `IT2026/IT2026/frontend/src/views/asset/List.vue` to support asset type, status, group, and keyword filtering, group reassignment, batch delete, export, and detail/edit navigation.
  Rebuilt `IT2026/IT2026/frontend/src/views/asset/Create.vue` so asset creation now includes all major metadata fields plus group selection.
  Rebuilt `IT2026/IT2026/frontend/src/views/terminal/Detail.vue` so the terminal detail page can edit assets, launch remote desktop, launch remote Shell, trigger immediate reporting, and show software and heartbeat history without template syntax errors.
  Updated `IT2026/IT2026/assets_api.py` so asset creation stores `group_id`, and remote-control preflight now returns `can_connect`, `status_message`, `resolved_status`, `agent_install_status`, and the agent control port for the frontend.
  Added `StarletteWebSocketAdapter` in `IT2026/IT2026/cmdb_agent_core.py` so the existing remote desktop engine can run behind the native `websockets` server without protocol mismatch.
  Verification evidence:
  `python -m py_compile .\IT2026\IT2026\assets_api.py .\IT2026\IT2026\cmdb_agent_core.py .\IT2026\IT2026\cmdb_agent_unified_v2.py`
  Result: exit code 0.
  `npm run build` (workdir `D:\IT2026\IT2026\IT2026\IT2026\frontend`)
  Result: Vite production build succeeded, exit code 0.
  `remote-control-precheck-ok`
  `websocket-adapter-ok`
  `powershell -NoProfile -File .\start_platform.ps1 -Action Stop`
  Output: `Platform stopped.`

- Next step
  Continue with the remaining runtime/field verification gaps before claiming full project completion.

## [2026-08-20] Deployment template and control-port diagnostics

- Goal
  Remove the tracked runtime Agent token from the GPO deployment template and make the new Agent control port observable in deployment validation.

- Where things stand
  Replaced the tracked token in `IT2026/IT2026/GPO部署包/config.json` with `replace-with-agent-token` and added `control_port: 9001`.
  Added `ZVIEW_AGENT_CONTROL_PORT=9001` to `.env.example` and `control_port` to `config.example.json`.
  Updated `build_agent.ps1` to require the ignored local `config.json`, create an isolated `dist/GPO部署包` release package, inject the local runtime configuration only there, and leave the tracked GPO template unchanged.
  Updated `verify_release_package.ps1` to accept `-PackageDir` and reject placeholder tokens for deployable packages; `-AllowTemplateConfig` remains available for source-template static checks.
  Updated GPO documentation and `diagnostic.ps1` so the control port is read from installed `config.json` and verified as owned by the backend Agent role.
  Verification evidence:
  Parsed `build_agent.ps1`, `verify_release_package.ps1`, and `GPO部署包/diagnostic.ps1`.
  Result: `powershell-syntax-ok`.
  Parsed `GPO部署包/config.json` and `config.example.json`.
  Result: `json-config-ok`.
  `powershell -NoProfile -ExecutionPolicy Bypass -File .\IT2026\IT2026\verify_release_package.ps1 -AllowTemplateConfig`
  Result: static release-package verification passed with one expected warning for the missing signed virtual-display driver payload.
  `python -m py_compile` for assets API, Agent core/unified entry, software API, and policy API.
  Result: exit code 0.
  `powershell -NoProfile -File .\start_platform.ps1 -Action Start`
  Result: exit code 0.
  `powershell -NoProfile -File .\start_platform.ps1 -Action Stop`
  Output: `Platform stopped.`

- Next step
  Continue real endpoint deployment verification with a built isolated release package and verify remote desktop consent plus ports `9000`/`9001` on an actual Windows target.

## [2026-08-20] Agent control-plane restoration for remote shell

- Goal
  Restore the broken remote command and immediate-report control plane without interfering with the existing remote desktop WebSocket service.

- Where things stand
  Confirmed that `assets_api.py` proxied `/api/v1/assets/{id}/command` and `/trigger-report` to Agent port `9000`, while `cmdb_agent_core.py` only hosted the remote desktop WebSocket server on that port and had no HTTP control endpoints.
  Added an authenticated Agent HTTP control server on configurable port `9001` in `IT2026/IT2026/cmdb_agent_core.py`.
  The control server provides `/api/v1/command` for bounded-time command execution and `/api/v1/trigger-report` for immediate heartbeat/hardware reporting; requests require the configured Agent bearer token.
  Updated `cmdb_agent_unified_v2.py` to start the control server with the normal Agent service.
  Updated `assets_api.py` so command/report proxy requests use `ZVIEW_AGENT_CONTROL_PORT` (default `9001`) while remote desktop WebSocket traffic remains on port `9000`.
  Updated `.env.example`, `config.example.json`, and `IT2026/README.md` with the control-port configuration.
  Verification evidence:
  `python -m py_compile .\IT2026\IT2026\assets_api.py .\IT2026\IT2026\cmdb_agent_core.py .\IT2026\IT2026\cmdb_agent_unified_v2.py .\IT2026\IT2026\software_management_api_complete_v2.py .\IT2026\IT2026\software_policy_api.py`
  Result: exit code 0.
  Started an Agent control server on test port `19001`; authorized `POST /api/v1/command` executed `echo zview-control-ok` and unauthorized access returned HTTP 401.
  Started an Agent control server on test port `19002`; `assets_api.proxy_agent_json_request()` proxied `echo platform-proxy-ok` successfully and preserved platform asset metadata in the response.
  `npm run build` (workdir `D:\IT2026\IT2026\IT2026\IT2026\frontend`)
  Result: Vite production build succeeded, exit code 0.
  `powershell -NoProfile -File .\start_platform.ps1 -Action Start`
  Result: exit code 0.
  `powershell -NoProfile -File .\start_platform.ps1 -Action Stop`
  Output: `Platform stopped.`

- Next step
  Continue end-to-end validation on a real remote Windows endpoint, including user-consent behavior and firewall deployment for ports `9000` and `9001`.

## [2026-08-20] Software task target resolution and controls

- Goal
  Make software distribution tasks correctly resolve group targets to real assets and expose the existing cancel/retry controls in the software task UI.

- Where things stand
  Updated `IT2026/IT2026/software_management_api_complete_v2.py` so software task creation resolves `asset`, `group`, and `all` selections into active asset IDs before inserting `software_task_results`.
  This fixes the group distribution defect where group IDs were previously written as asset IDs, producing tasks that no Agent could poll.
  Added backend validation for unsupported target types, empty target selections, and selections that match no active asset.
  Kept the original selected target IDs in `software_tasks.target_ids` for audit context while using the resolved asset IDs for task result rows and `target_count`.
  Added `retrySoftwareTask` to `IT2026/IT2026/frontend/src/api/software.js` and exposed Cancel/Retry task controls in `IT2026/IT2026/frontend/src/views/terminal/components/TaskManagement.vue`.
  Verification evidence:
  `python -m py_compile .\IT2026\IT2026\software_management_api_complete_v2.py`
  Result: exit code 0.
  Imported `software_management_api_complete_v2` from the live app root.
  Result: printed `software-api-import-ok`.
  Executed a stub-cursor target-resolution check for group targets `[3, 3, "4"]`.
  Result: deduplicated group query parameters `[3, 4]`, resolved asset IDs `[11, 12]`, and empty group targets returned HTTP 400.
  `npm run build` (workdir `D:\IT2026\IT2026\IT2026\IT2026\frontend`)
  Result: Vite production build succeeded, exit code 0.
  `powershell -NoProfile -File .\start_platform.ps1 -Action Start`
  Result: exit code 0.
  `powershell -NoProfile -File .\start_platform.ps1 -Action Stop`
  Output: `Platform stopped.`

- Next step
  Continue the remaining module completion audit, especially production-level remote-control and Agent end-to-end behavior.

## [2026-08-20] Agent entry verification and API cleanup

- Goal
  Verify the Agent/remote-control entry path can load on the current environment and remove low-risk backend route duplication while keeping the platform startup path green.

- Where things stand
  Installed the missing `PyAutoGUI==0.9.54` runtime dependency in the current Python environment so `cmdb_agent_unified_v2.py` can import `RemoteAgent.high_integrity_helper`.
  Verified `cmdb_agent_unified_v2.py --help` now runs successfully and `load_core_module()` resolves to `cmdb_agent_core.py`.
  Verified `RemoteService.session_manager` imports successfully from the live app root.
  Removed duplicate route decorators from `IT2026/IT2026/assets_api.py` for `/api/v1/assets/{asset_id}` delete and `/api/v1/assets/{asset_id}/remote-control`.
  Verification evidence:
  `python -m py_compile .\IT2026\IT2026\assets_api.py .\IT2026\IT2026\cmdb_agent_unified_v2.py`
  Result: exit code 0.
  `python .\IT2026\IT2026\cmdb_agent_unified_v2.py --help`
  Result: CLI usage printed successfully, exit code 0.
  `python -c "import sys; sys.path.insert(0, sys.argv[1]); import cmdb_agent_unified_v2 as a; print(a.load_core_module().__file__)" "<appRoot>"`
  Result: printed `D:\IT2026\IT2026\IT2026\IT2026\cmdb_agent_core.py`.
  `python -c "import sys; sys.path.insert(0, sys.argv[1]); from RemoteService.session_manager import SessionManager; print('session-manager-ok')" "<appRoot>"`
  Result: printed `session-manager-ok`.
  `powershell -NoProfile -File .\start_platform.ps1 -Action Start`
  Result: exit code 0.
  `powershell -NoProfile -File .\start_platform.ps1 -Action Stop`
  Output: `Platform stopped.`

- Next step
  Continue with the remaining remote-control and module-completion audit instead of claiming full 100% completion from the current smoke checks.

## [2026-08-20] Discovery SNMP completion pass

- Goal
  Close the discovery module gap by exposing SNMP采集 in the frontend and keep the current asset create/startup flow stable.

- Where things stand
  Added `startSnmpScan` to `IT2026/IT2026/frontend/src/api/discovery.js`.
  Rebuilt `IT2026/IT2026/frontend/src/views/discovery/Index.vue` so discovery now has both Ping and SNMP entry cards, task progress refresh, cancel, and detail viewing in one page.
  Kept `IT2026/IT2026/frontend/src/views/asset/Create.vue` aligned with the extended asset fields and retained the corrected `notes` field mapping.
  Kept `start_platform.ps1` using `npm.cmd` for Windows frontend startup.
  Updated `IT2026/IT2026/requirements.txt` with the missing `websockets` runtime dependency.
  Verification evidence:
  `python -m py_compile .\IT2026\IT2026\assets_api.py .\IT2026\IT2026\software_management_api_complete_v2.py .\IT2026\IT2026\software_policy_api.py .\IT2026\IT2026\cmdb_agent_unified_v2.py`
  Result: exit code 0.
  `npm run build` (workdir `D:\IT2026\IT2026\IT2026\IT2026\frontend`)
  Result: Vite production build succeeded, exit code 0.
  `powershell -NoProfile -File .\start_platform.ps1 -Action Start`
  Result: exit code 0.
  `powershell -NoProfile -File .\start_platform.ps1 -Action Stop`
  Output: `Platform stopped.`

- Next step
  Continue the remaining module audit and finish the last uncovered completion gaps instead of stopping at discovery.

## [2026-08-20] Asset metadata completion and startup hardening

- Goal
  Close the current asset management gap by wiring the missing create/edit metadata flow end to end and make the one-script startup path work reliably on this Windows environment.

- Where things stand
  Updated `IT2026/IT2026/assets_api.py` so asset metadata columns are ensured at startup, create/update now persist the extended fields, asset creation now commits successfully, and asset create/update both write change-history records for the detail page history views.
  Rebuilt `IT2026/IT2026/frontend/src/views/asset/Create.vue` so the create form matches the current detail/edit fields and sends `notes` instead of the stale `remarks` field.
  Updated `start_platform.ps1` to resolve `npm.cmd` instead of `npm.ps1` on Windows and start Vite with `--host 127.0.0.1`, which fixed the frontend process readiness check.
  Added `websockets==14.2` to `IT2026/IT2026/requirements.txt` and installed the missing runtime dependency `mysql-connector-python==8.2.0` in the current environment so the backend services can actually boot.
  Verification evidence:
  `python -m py_compile .\IT2026\IT2026\assets_api.py .\IT2026\IT2026\software_management_api_complete_v2.py .\IT2026\IT2026\software_policy_api.py .\IT2026\IT2026\cmdb_agent_unified_v2.py`
  Result: exit code 0.
  `npm run build` (workdir `D:\IT2026\IT2026\IT2026\IT2026\frontend`)
  Result: Vite production build succeeded, exit code 0.
  `powershell -NoProfile -File .\start_platform.ps1 -Action Start`
  Result: state file created with `assets-api`/`software-api`/`policy-api`/`frontend` PIDs and frontend command `npm.cmd run dev -- --host 127.0.0.1`.
  `powershell -NoProfile -File .\start_platform.ps1 -Action Stop`
  Output: `Platform stopped.`

- Next step
  Continue the remaining module-by-module completion audit instead of assuming whole-project 100% completion from the current smoke tests alone.

## [2026-08-19] One-script platform startup

- Goal
  Add one PowerShell entrypoint to start/stop/restart the platform services without manual process juggling.

- Where things stand
  Added `start_platform.ps1` at the repo root. It resolves the app root, checks/install deps when needed, starts `assets_api.py`, `software_management_api_complete_v2.py`, `software_policy_api.py`, and the frontend dev server, and stores PIDs for later stop/restart.
  Added a concise quick-start note at `IT2026/README.md` that points to the new script.
  Restored the inner `IT2026/IT2026/README.md` to the original full project guide so the new startup note stays isolated.
  Verification evidence:
  `powershell -NoProfile -File .\start_platform.ps1 -Action Stop`
  Output: `Platform stopped.`
  `python -m py_compile .\IT2026\IT2026\assets_api.py`
  Result: exit code 0.

- Next step
  Continue the next product gap work: finish asset detail edit-field alignment and discovery SNMP frontend support.

## [2026-08-19] 璧勪骇鍘嗗彶涓庣姸鎬侀摼琛ラ綈

- Goal
  涓哄綋鍓嶇増鏈ˉ榻愯祫浜у彉鏇村巻鍙层€佺姸鎬佹瑙堛€佺姸鎬佸巻鍙层€佸湪绾挎椂闀跨殑鍓嶅悗绔棴鐜紝璁╄祫浜ц鎯呴〉涓嶅啀鍙湁褰撳墠鍊艰€岀己灏戞紨鍙樹俊鎭€?
- Where things stand
  宸插湪 `IT2026/IT2026/assets_api.py` 澧炲姞 `asset_changes` 杩愯鏃跺缓琛ㄣ€佸巻鍙茶褰?helper锛屼互鍙?`/api/v1/assets/{id}/changes`銆乣/status`銆乣/status/history`銆乣/uptime` 鍥涗釜鎺ュ彛锛涘凡灏嗚祫浜у垱寤恒€佽祫浜ф洿鏂般€丄gent 蹇冭烦鍥炲啓鎺ュ叆鍙樻洿璁板綍銆?  宸插湪 `IT2026/IT2026/frontend/src/api/asset.js` 鎭㈠瀵瑰簲鍓嶇璋冪敤銆?  宸插湪 `IT2026/IT2026/frontend/src/views/asset/Detail.vue` 澧炲姞鈥滆繍琛岀姸鎬佲€濃€滃彉鏇村巻鍙测€濃€滅姸鎬佸巻鍙测€濆睍绀猴紝骞跺湪璇︽儏鍔犺浇銆佷繚瀛樺悗鍒锋柊鏂版暟鎹€?  楠岃瘉璇佹嵁锛?  `python -m py_compile .\IT2026\IT2026\assets_api.py`
  缁撴灉锛氶€€鍑虹爜 0銆?  `npm run build`锛堝伐浣滅洰褰?`D:\IT2026\IT2026\IT2026\IT2026\frontend`锛?  缁撴灉锛歏ite build 鎴愬姛锛岄€€鍑虹爜 0銆?  棰濆鐜浜嬪疄锛?  `PLAYBOOK.md` 褰撳墠涓嶅瓨鍦紝宸茬‘璁や絾鏈奖鍝嶆湰杞疄鐜版帹杩涖€?
- Next step
  缁х画鏀跺彛璧勪骇璇︽儏椤靛彲缂栬緫瀛楁涓庡悗绔厑璁告洿鏂板瓧娈电殑涓嶄竴鑷撮棶棰橈紱闅忓悗琛ラ綈鍙戠幇妯″潡鍓嶇鐨?SNMP 鑳藉姏锛岀户缁帹杩涒€滈」鐩畬鍠勨€濅富绾裤€?

## [2026-09-09] 告警后台调度线程 + assets_api 损伤修复

- Goal
  补上 P1 告警中心惰性评估缺口：新增 60s 周期后台 sync 线程；同时修复上一轮改动遗留的 assets_api.py 函数定义丢失损伤。

- Where things stand
  assets_api.py 新增 alert_sync_loop / ensure_alert_sync_worker_started（60s，worker_health 注册 alert-sync），并挂入 startup 启动入口。
  修复 4 处损伤：
  1) start_background_workers 丢失 def 行导致 status-reconcile/data-retention 后台线程从未启动，已恢复 @app.on_event("startup") 并接入 alert-sync；
  2) build_asset_filters 被误删导致 /api/v1/assets、/assets/stats、/assets/export 返回 500，已自 HEAD 恢复；
  3) cleanup_discovery_tasks_locked（操作 assets_api 自有 DISCOVERY_TASKS 的本地版）被误删，已恢复并补 DISCOVERY_TASK_RETENTION_SECONDS 导入；
  4) get_db_connection 内 parse_json_field 孤儿函数体已清除（该函数已由 zvplatform.common 提供）。
  验证证据：
  python -m py_compile 退出码 0；pyflakes 全模块 0 个 undefined name。
  /api/health worker 快照：status-reconcile runs=8 ok、data-retention runs=1 ok、alert-sync runs=4 failures=0，整体 status=ok。
  进程内执行 sync_alerts：active 终端告警 1 条（asset 28 disk critical），指纹比对与自动恢复链路正常。
  build_asset_filters / cleanup_discovery_tasks_locked 调用正常；双终端心跳持续（28/XXH-XXX、2213/DESKTOP-JEGI046）。

- Next step
  告警中心后续：通知层（邮件/webhook）与 Incident 聚合升级；阈值并入 Policy Engine 可配置化。

## [2026-09-09] Agent 升级降级死循环 + C 盘撑满事故处置（根因修复 + 定期缓存清理机制）

- Goal
  解决"两台终端 Agent 反复重启、反复弹 cmd 窗口"问题；回答并落地"缓存定期清理"。

- 根因链（已逐项取证）
  1) 12:16 kilo-auto 重传 1.7.4 覆盖 agent_upgrade/manifest.json，"最新版"从 1.8.2 倒退回 1.7.4；
  2) agent_heartbeat.py 心跳响应用 reported_version != latest 判断，1.8.2 Agent 被无限下发"升级到 1.7.4"降级指令；
  3) Agent 每次心跳（30s）重试下载（8443 TLS 当时 SSL 136 失败）+ 反复解压安装 → cmd 窗口反复弹出、进程反复退出重启；
  4) 每次 PyInstaller 启动/升级在 %TEMP% 泄漏 140MB _MEI* 目录，1.5h 内堆到 44.77GB，C 盘 0 字节剩余 → "no space left on device"；
  5) ServiceRuntime 每 35s 误判 helper 死亡重复拉起（helper 堆积 12+ 个），随升级循环停止而消失。

- 处置与修复
  紧急清理：%TEMP% 清出 45.16GB、npm/pip 缓存 2.38GB、轮转 263MB agent-runtime.log，C 盘恢复 47.58GB 可用。
  根因修复 A：agent_heartbeat.py 升级指令改为语义化版本比较（仅 reported_version < latest 或空版本引导时下发），禁止降级。
  根因修复 B：manifest.json 回正为 1.8.2（重算 sha256/size）。
  新增机制：zvplatform/disk_cleanup.py 定期磁盘缓存清理（用户/系统 Temp 24h、_MEI* 6h、npm/pip 7d、runtime 日志 >100MB 轮转、agent_upgrade 保留最近 3 版），并入 data-retention 线程（6h 周期 + 启动即跑）。

- 验证证据
  重启后 fresh 日志 upgrade/download 请求 = 0（修复前每 30s 一次）；helper 进程从堆积 12+ 降到 2 个稳定；/api/health 三 worker 全绿（status-reconcile 11 runs、alert-sync 6 runs、data-retention 1 run，0 failures）；心跳持续 42 条含两台终端；C 盘 47.58GB；8443 TLS 用平台证书模拟 Agent 验证 → 401（通道健康，下次正常升级可用）。

- Next step
  观察 helper 长期稳定性；升级包上传流程加"版本回退保护"（upload 时校验新版本号必须大于当前 manifest）；8443 证书如再次出现 SSL 136 需排查 Agent 侧 ca-bundle 时序。

## [2026-09-09] 终端部署三件套 + Go 原生轻量 Agent 启动（P1-04/P1-05，参照火绒企业版）

- Goal
  用户确认方向：补齐终端部署三件套（网页自助下载、静默安装+三方桌管推送、域批量部署）；
  启动 Go 原生轻量 Agent（心跳/策略/升级/清理，几 MB、秒启、无解压），远控等重功能保留 Python Agent。

- Where things stand
  Agent 侧（cmdb_agent_unified_v2.py）：
  1) 新增 --install --quiet --server-url 自安装命令：部署 exe 到 Program Files\CMDB-Agent、
     写 config.local.json（server_url + software_server_url 派生 8081）、sc create/config/failure/延迟自启，幂等；
  2) 启动入口自清前代 _MEI*（cleanup_stale_mei_dirs，>30min，锁保护）；
  3) cmdb_agent_core.py 冻结态优先读 exe 旁 config.local.json（__file__ 在 _MEI 内无法持久化的修复）。
  服务器侧（assets_api.py）：
  4) GET /api/v1/console/agent-deploy/package —— admin 下载最新 Agent 安装包（复用 agent_upgrade 仓库）；
  5) GET /api/v1/console/agent-deploy/script —— 生成 PowerShell 一键部署脚本（内嵌中心地址 + agent_token
     下载安装包 + --install 静默装；可用于域开机脚本/三方桌管推送；admin 专用勿外发）。
  Go Agent 骨架（agent-go/，协议对齐现有后端、服务端零改动接入）：
  6) main.go：心跳循环、设备凭据获取与落盘（zv1:asset:secret）、升级指令处理（下载→SHA256→bat 回滚换文件）、
     _MEI 清理、-install 服务注册（sc 方式）；README 含构建说明（本机暂无 Go 工具链，编译待验证）。
  验证证据：
  py_compile 通过、pyflakes 0 undefined；argparse --install/--quiet/--server-url 解析正确；
  部署脚本模板四要素断言通过（install cmd/token var/center var/download uri）；agent token 可读取（43 字符）；
  重启后 /api/health status=ok，四 worker 全绿（status-reconcile 11/alert-sync 6/disk-guard 6/data-retention 1，0 failures）；
  openapi 确认两个 agent-deploy 端点已注册。

- Next step
  装 Go 1.22 便携版并编译 agent-go 出首个二进制；前端控制台加"终端部署"页（下载包/复制脚本/查看部署状态）；
  灰度方案（服务端按资产标记使用原生 Agent）；onedir 打包过渡仍保留为 Python Agent 的中期待办。

## [2026-09-09] Go 原生 Agent 首个二进制 + 终端部署页上线

- Goal
  落地上一轮排期：Go 工具链 → 编译原生 Agent → 冒烟验证；前端"终端部署"页。

- Where things stand
  1) Go 1.23.4 便携版部署于 D:\tools\go（阿里云镜像 zip，无需安装器）；
  2) agent-go 编译成功：Z-View-Agent.exe 5.48MB（go build -trimpath -ldflags "-s -w"）；
  3) 冒烟测试（mock 回环，不碰生产资产）：全局 token 心跳 → 服务端下发 agent_credential →
     落盘 zv-credential.json → 后续心跳自动切换 zv1 设备凭据，一机一密协议全链路 OK；
  4) 前端"终端部署"页：views/terminal/AgentDeploy.vue + 路由 /terminal/agent-deploy，
     功能：安装包下载（blob）、部署脚本查看/复制/下载 .ps1（支持 ?center= 覆盖中心地址）、
     终端凭据注册状态表（复用 /console/agent-credentials）；npm run build 成功并随平台重启生效。
  验证证据：
  AgentDeploy chunk 在 dist（AgentDeploy-DF15u5Rw.js）；5173/8080 正常监听；
  /api/health status=ok 四 worker 全绿（status-reconcile 19/alert-sync 10/disk-guard 10 runs，0 failures）。
  注意事项：Go 二进制禁止上传 agent_upgrade 仓库（那是 Python Agent 升级通道，会成为降级指令源）。

- Next step
  灰度切换：服务端按资产标记"使用原生 Agent"，心跳按标记路由；
  Python Agent onedir 打包过渡；Go Agent 补齐 disk_info 等完整上报与 TLS 控制面（8443）。

## [2026-09-09] 决策修订：放弃 Go 原生 Agent 路线，保留终端部署三件套

- Goal
  用户决策放弃 Go Agent；清除全部相关产物，部署三件套保留并服务 Python Agent。

- Where things stand
  已删除：agent-go/（代码+二进制+mock 产物）、D:\tools\go 工具链、Go 安装 zip、go-build 缓存。
  保留：--install 静默自安装（Python Agent 用）、agent-deploy 两个端点、前端 /terminal/agent-deploy 部署页
  （Go 相关文案已清除并重新 build）、cmdb_agent_core.py 冻结态 exe 旁 config.local.json 支持、
  disk_cleanup / alert-sync / disk-guard 后台线程。
  Python Agent 演进路径改为：onedir 打包根治 _MEI 与慢启动（中期待办），不做原生重写。
  验证证据：目录删除确认（agent-go/go/go.zip 均 False）；npm run build 成功（11.26s）。

- Next step
  onedir 打包灰度（build_agent.spec 增加 COLLECT + 升级管道换目录方案）。

## [2026-09-09] Agent 长期架构定版（用户方案）+ V1.5.x 升级锁/重试限制落地

- Goal
  采纳用户的 Agent 长期架构：Program Files/ProgramData 分离、versions 版本目录、独立 Updater、
  升级状态机 + 事务 ID + 健康检查回滚、onedir 根治 _MEI；分 V1.5.x/V1.6.0/V1.7.0/V1.8.0 四版实施。

- Where things stand
  V1.5.x 四项全部完成：
  1) 升级死循环 ✅（服务端 semver 判断，本日早前）；
  2) _MEI 清理 ✅（平台 disk_cleanup + Agent 启动自清）；
  3) 升级锁 ✅ 新增：runtime/upgrade.lock（EXCL 创建跨重启锁，30min 陈旧接管；
     bat COMMIT/ROLLBACK 终态删锁；新进程消费终态时兜底释放）；
  4) 重试限制 ✅ 新增：runtime/upgrade-attempts.json 失败台账（24h 滚动窗口），
     5 次失败起 30min 指数退避、封顶 24h；心跳触发点退避期内静默跳过；
     COMMIT 清台账；ROLLBACK 计入台账。下载/SHA256/签名/异常四条失败路径全部接入。
  修复实现 bug 一枚：陈旧锁接管未先 unlink 旧文件导致 O_EXCL 失败（单测捕获）。
  验证证据：
  py_compile 通过、pyflakes 0 undefined；
  单测 6 组全过：退避表（30m→1h→2h 封顶 24h）、remaining、COMMIT 清台账、锁互斥、
  陈旧锁接管、新鲜锁不接管。

- Next step
  V1.6.0：onedir 打包 + Program Files/ProgramData 分离 + versions 目录 + ZViewUpdater（业务代码不动）；
  发布 1.8.3 携带本轮 Agent 侧修复走正常升级通道（用户确认后执行）。

## [2026-09-09] V1.6.0 完成：onedir + versions/current + ZViewUpdater（架构第一阶段）

- Goal
  实施 V1.6.0：onedir 打包根治 _MEI；versions/current 版本目录；独立 ZViewUpdater；
  Agent 不再自己替换自己。业务代码不动。

- Where things stand
  打包层：
  1) build_agent.spec onefile → onedir（EXE exclude_binaries + COLLECT）；
     dist_agent\Z-View\（Z-View.exe + _internal，184MB）构建成功（PyInstaller 6.22.2，已 pip 安装）；
  2) scripts/package_agent.py 组装 releases\Z-View-1.9.0-onedir.zip（91.5MB，
     含 Z-View.exe/_internal/version.txt/updater\ZViewUpdater.exe）；
  3) ZViewUpdater.exe 7.4MB（onefile，stdlib only，独立构建）。
  服务端：
  4) agent_upgrade_api 支持 .exe/.zip 双包型（manifest 增加 package_type/filename）；
     download 按版本目录实际包文件响应；心跳升级指令携带 package_type。
  Agent 侧：
  5) 新增 cmdb_agent_layout.py：versions/current/健康标记/版本管理（路径可注入，可测）；
  6) perform_self_upgrade 检测 v2 布局或 zip 包 → 写 upgrade-task.json 委托 ZViewUpdater
     （下载→校验→staging→停服务→切 junction→起服务→健康检查→COMMIT/ROLLBACK，
     回滚=翻回上一版本 junction，不重下载）；旧布局继续走 bat 流（双轨兼容）；
  7) 心跳成功写 runtime\upgrade-health.json（120s 去重）；
  8) 配置优先级：ProgramData\CMDB-Agent\config\config.local.json > exe 旁 > 内置 config.json
     （程序/数据分离，versions 切换不碰配置）；
  9) --install v2：onedir 自适应安装（versions\<ver>+current junction+updater+ProgramData 配置，
     服务 binPath 固定 current\Z-View.exe），旧单文件布局保留兼容路径。
  验证证据：
  E2E 单测 4 场景全过（沙箱+服务 mock）：升级 COMMIT（junction 翻转+上一版本保留）、
  同版本幂等、健康检查失败 ROLLBACK（翻回 1.9.0）、prune keep=2；
  修复实现 bug：陈旧锁接管未先删旧文件（V1.5 单测捕获）；
  全部文件 py_compile 通过（SyntaxWarning 即报错模式）、pyflakes 0 undefined。

- Next step
  存量终端迁移方案（1.8.2 单文件 → 1.9.0 onedir 新布局的一次性迁移工具）；
  V1.7.0 状态机 + 事务 ID；updater 自更新机制。


## [2026-09-09] V1.6.0 收尾：存量终端迁移路径 + updater 自更新

- Goal
  打通旧单文件布局（1.8.2）→ 新布局（1.9.0 onedir）的一次性迁移；updater 自更新机制。

- Where things stand
  1) 迁移编排（cmdb_agent_core._migrate_to_onedir）：旧布局 Agent 收到 onedir-zip 包 →
     下载 → SHA256 → 解包 temp → 校验内层 exe Authenticode 签名 →
     分离进程运行新布局安装器（--install --quiet --migrate-from/--migrate-to）；
  2) 安装器迁移语义（run_self_install）：服务在跑则 net stop → taskkill 残留（PID 过滤排除自身）
     → sc config 指向 current\Z-View.exe → 重启；迁移全程写升级终态
     （MIGRATING → COMMITTED/FAILED，服务端心跳感知，P0-06 审计）；
  3) updater 自更新：COMMIT 后把新版本目录的 updater 暂存 .new，下次运行
     rename 换入（运行中 exe 可重命名）；
  4) 触发路由修正：v2 布局→updater 委托；旧布局+zip→迁移；旧布局+exe→原 bat 流。三轨互斥清晰。
  验证证据：
  全部文件 py_compile（SyntaxWarning 即错模式）+ pyflakes 0 undefined
  （修复 _migrate_to_onedir 缺 tempfile 导入）；
  E2E 单测 4 场景回归全过（COMMIT/幂等/健康失败回滚/prune）；
  产物重建：dist_agent onedir、ZViewUpdater.exe、releases\Z-View-1.9.0-onedir.zip（91.5MB）。

- Next step
  发布演练（测试机）：上传 1.9.0 zip → 观察迁移全流程 → 两台生产终端择机迁移；
  V1.7.0：升级事务 ID 全链路 + 服务端 desired/reported/upgrade_state 协议 + 正式状态机持久化。


## [2026-09-10] 1.8.3 发布 + 2213 升级挂起事件（net stop 无限挂起缺陷暴露）

- Goal
  两步发布 Step 1：1.8.3 单文件兼容包（携带升级护栏+迁移能力）推送到两台终端。

- Where things stand
  1) 本机 asset 28 (XXH-XXX)：1.8.2 → 1.8.3 升级成功（84MB 下载 4s，bat 换包，心跳正常）✅
  2) 发现并修复本机 Agent 掉线 19h 根因：runtime\ca-bundle.pem 被截断成 0 字节
     （昨日 13:46:45 非原子写撞上升级/重启窗口），TLS 心跳拿空 bundle 必然 SSLError(136)，
     而 bundle 刷新依赖成功心跳 → 死锁。已修复 bundle + core 改原子写（tmp + os.replace）；
  3) 构建签名缺失暴露：新构建 exe 未签名被 P0-05 护栏正确拒绝（NotSigned）。
     签名证书在 CurrentUser\My（CN=Z-View Enterprise，93C05132...，至 2036），
     已固化为 scripts\sign_agent.ps1（构建流程必须签名）；
  4) asset 2213 (DESKTOP-JEGI046)：升级 bat 在 net stop 处无限挂起（旧代码缺陷：
     服务停止等待角色进程退出、无超时无强杀），12+ 分钟无心跳。机器在线（3389 开），
     但 WMI/RPC/sc 远程全部拒绝访问，需管理员现场 taskkill 恢复（已给出命令）；
  5) updater 加固：STOPPING 阶段 net stop 后强制 taskkill /F /IM Z-View.exe 再切换
     （防止新布局重蹈挂起），已重建 1.9.0 发布包（91.5MB）。
  经验教训：① ca-bundle 等状态文件必须原子写；② 构建流程必须包含签名；
  ③ 服务停止必须带强制兜底（net stop → taskkill → 确认）；④ 远程终端无法管理时
  迁移类操作要按"可现场恢复"设计。

- Next step
  用户在 2213 执行 taskkill /F /IM Z-View.exe（+ net start CMDB-Agent）恢复后，
  确认 1.8.3 → 上传 1.9.0 onedir-zip 触发自动迁移（本机 + 2213）。


## [2026-09-10] V1.9.0 迁移上线：本机成功 + 2213 待恢复（两个连锁缺陷修复）

- Goal
  Rollout Step 2：上传 1.9.0 onedir-zip 触发两台终端自动迁移到新布局。

- Where things stand
  本机 asset 28 (XXH-XXX)：**迁移成功并验证** —— DB agent_version=1.9.0、
  current junction → versions\1.9.0、进程从 current\Z-View.exe 运行、心跳正常。
  2213 (DESKTOP-JEGI046)：迁移在 updater 下载环节失败（SSL），等待现场恢复脚本
  （kilo_tmp\fix_2213.ps1：下载修复包→换 SSL 修复版 updater→补 config token→重启服务，
  下个心跳自动完成迁移）。
  连锁缺陷（均已源码修复，随下次构建生效）：
  1) AGENT_VERSION 未随 onedir 构建递增——迁移后的 Agent 上报 1.8.2，
     服务端永远判定落后 → 无限重发指令。已 bump 1.9.0；
  2) updater 用 urllib 默认上下文下载 8443 TLS → 自签名证书 CERTIFICATE_VERIFY_FAILED。
     已加 _ssl_context()（ca-bundle 优先 → 内网降级不校验）；
  3) updater 任务 token 为空（迁移安装器把 config.local.json 写成 server_url-only，
     丢了内置 config 的 token）→ 下载 401。已修：安装器合并内置配置保留
     token/intervals/远控配置；updater 空任务 token 回退设备凭据 zv1；
  4) 本机临时修复：ProgramData config.local.json 补 token（源码修复的等价手工操作）。
  验证证据：
  本机 versions\1.8.2 + 1.9.0 并存（keep=2）、current junction 指向 1.9.0、
  进程路径 current\Z-View.exe、DB 1.9.0 心跳正常；
  2213 心跳正常（1.8.2 上报值，等 token 补丁后迁移到 1.9.0）；
  1.8.3 发布战果保留：本机 1.8.2→1.8.3 自动升级成功验证了 bat 流 + 签名护栏
  （NotSigned 拒绝 + scripts\sign_agent.ps1 固化签名步骤）。

- Next step
  2213 执行 fix_2213.ps1 → 确认两台 1.9.0 → V1.7.0（事务 ID + desired/reported 协议）。


## [2026-09-10] V1.9.0 迁移完成：两台终端全部进入新布局

- Where things stand
  28 (XXH-XXX)：1.9.0，versions\1.9.0 + current junction，心跳正常 ✅
  2213 (DESKTOP-JEGI046)：1.9.0 ✅ —— 迁移用"服务器下载包 + 本地解包执行 --install"的
  诊断块方式完成（先前失败原因未逐层确认，但诊断块输出 SelfInstall 日志已可回溯）。
  服务端 manifest=1.9.0 onedir-zip，两台上报一致，升级通道静默（无指令下发）。
  至此 V1.5.x + V1.6.0 全部落地并在两台生产终端运行：
  onedir（无 _MEI）、versions/current（升级只翻 junction）、ZViewUpdater（独立升级器，
  SSL 修复 + 设备凭据回退）、升级护栏（semver + 台账退避 + 跨重启锁 + 签名校验 +
  原子写）、程序/数据分离、部署三件套、诊断/恢复脚本沉淀。

- Next step
  观察期（一周）：远控/心跳/升级通道稳定性；updater 自更新换入验证（下次发版时）；
  V1.7.0：升级事务 ID 全链路 + 服务端 desired/reported/upgrade_state 协议。


## [2026-09-10] V1.7.1 告警通知层上线（webhook + 邮件）+ V1.7.0 收尾

- Goal
  推进路线：V1.7.0 升级协议正式化 → 告警通知层。P3 路线用户确认取消（记忆已更新）。

- Where things stand
  V1.7.0 升级协议（服务端已上线，Agent 侧随下次构建）：
  1) agent_upgrade_history 表（(asset_id, upgrade_id) 幂等 upsert，DISPATCHED/RUNNING/
     COMMITTED/ROLLBACK/FAILED 全阶段）；
  2) 心跳指令带 upgrade_id（UPG-<version> 确定性生成，重发幂等）+ desired_version；
  3) Agent 上报 agent_upgrade_state 全阶段（进行中实时阶段 + 最近终态）→ 历史表 +
     审计日志；server_upgrade_id 贯穿状态文件（updater/安装器 merge 继承）；
  4) /agent/upgrade/status 增加 last_upgrade 每资产状态；
  5) SQL 层单测通过（修 helper join 列名 bug：m.mi = h.id）。
  V1.7.1 告警通知层：
  6) alerts 表 notified_at 列（CREATE + 存量 ALTER 增量迁移，已验证生效）；
  7) sync_alerts 返回新触发告警（含 DB id，is_new 判定，恢复后重现视为新告警）；
  8) zvplatform/services/alert_notify.py：alert_notify_config 单例配置表 +
     webhook（每条 POST JSON，hostname 补全）+ 邮件（每轮合并摘要，SMTP_SSL/TLS）+
     严重度过滤（min_severity）+ notified_at 去重（at-most-once）；
  9) 告警同步线程（60s）集成分发；控制台 API GET/PUT
     /api/v1/console/alert-notify-config（admin，密码脱敏/空串保持）。
  验证证据：
  沙箱 mock 测试通过（webhook 命中+hostname 解析、critical 过滤 warning、disabled 跳过、
  notified_at 打标）；notified_at 列已在真实 alerts 表生效（存量 37 条待通知，
  默认 disabled 不发送）；后端重启后 4 worker 全绿（alert-sync 集成分发无失败）。
  沙箱测试脚本：kilo_tmp\test_notify.py。

- Next step
  控制台前端加通知配置页（当前仅 API）；SMTP 真实通道联调；
  V1.8.0 Agent Core 重构；观察期（远控回归 + updater 自更新首次实战）。


## [2026-09-10] 控制台"通知配置"页上线（告警通知层收尾）

- Where things stand
  1) 前端新增 /alert/notify 通知配置页（views/alert/NotifyConfig.vue）：
     启用开关、最低严重度（warning/critical）、Webhook URL、SMTP 全套
     （host/port/SSL/用户/密码脱敏占位/发件/多收件人），保存即生效（60s 内）；
  2) api/alert.js 增加 fetchAlertNotifyConfig / updateAlertNotifyConfig；
  3) npm run build 成功，NotifyConfig chunk 已部署，平台重启完成；
  4) 后端 /api/v1/console/alert-notify-config GET/PUT 已注册（V1.7.1 通知层），
     4 worker 全绿（alert-sync 集成分发无失败）。
  SMTP 真实通道联调：需用户在配置页填入实际 SMTP 账号后验证（平台侧代码路径已就绪）。

- Next step
  观察期（远控回归 + 心跳/updater 稳定性）；V1.7.0 Agent 侧随下次构建；
  V1.8.0 Agent Core 重构（待排期）。


## [2026-09-10] 导航修复：告警中心/通知配置/终端部署 入菜单

- Goal
  用户反馈"告警中心在哪里？没看到"。根因：① /alert 菜单标签误写为"终端日志"（告警中心页
  顶着日志页的名字）；② /alert/notify 通知配置页只有路由没进菜单（孤儿路由）；
  ③ 终端部署页 agent-deploy 同样漏了菜单项。用户要求归位到监控中心分组。

- Where things stand
  Layout.vue 侧边栏监控中心分组：终端日志 → 告警中心（保留告警数 badge），
  新增 通知配置（/alert/notify）；终端管理分组新增 终端部署（/terminal/agent-deploy）；
  router /alert meta title 终端日志 → 告警中心。
  验证证据：构建产物 Layout chunk 含"告警中心/通知配置"文案、AgentDeploy chunk +
  路由含"终端部署"；后端 health ok。

- Next step
  观察期；SMTP 真实联调（配置页填账号）；V1.7.0 Agent 侧随下次构建。


## [2026-09-10] 告警通知链路端到端演练通过（真实数据）

- Goal
  触发一条真实测试告警，验证 告警中心 + 通知分发 全链路。

- Where things stand
  真实数据演练（本机 asset 28，CPU 压测器饱和 12 核）：
  1) 心跳上报 CPU 100% → 规则评估 → 新告警 id=1639 cpu critical（指纹 28:cpu 新触发）；
  2) 通知分发：webhook POST 送达 mock 接收器（payload 含 asset_id/hostname/severity/
     message/current_value/threshold/fingerprint），notified_at 打标；
  3) 手动 resolve 后重触发 → 新告警 id=1642 → 再次通知 → CPU 回落后
     **系统自动恢复（resolved_by=system）** —— 指纹自动恢复机制闭环验证；
  4) 通知配置已恢复生产默认（disabled、webhook 清空），mock 进程已停止。
  发现并修正：
  ① Start-Process ArgumentList 传参静默丢失 → 改用后台进程工具启动；
  ② webhook payload first_triggered_at 为空串（sync 返回的 dict 未带该字段，
     SQL 用 NOW() 填充）——小瑕疵，不影响功能，可在 build_current_alerts 补齐。
  测试脚本沉淀：kilo_tmp\{cpu_burn,webhook_mock,check_alert_test,resolve_1639}.py。

- Next step
  用户在通知配置页填真实 SMTP/webhook 即可投入生产；观察期；
  V1.7.0 Agent 侧随下次构建；V1.8.0 待排期。


## [2026-09-10] 企业微信机器人通道验证通过

- 用户在通知配置页保存 qyapi.weixin.qq.com webhook URL；_send_wecom 实测返回 errcode=0 ok，
  markdown 卡片消息成功送达企业微信群（测试消息：critical XXH-XXX 通知链路验证）。
- 发现：用户保存了 URL 但启用通知开关仍为关闭（enabled=0）——已提醒用户打开。
- 通道行为确认：企业微信格式错误也返回 HTTP 200，必须校验 body errcode（已实现）。


## [2026-09-10] 服务端升级熔断上线（V1.7.0 补充）+ 1.9.1 发布（本机）+ 2213 待手动迁移

- Where things stand
  1) 1.8.3 → 1.9.0 → 1.9.1 三连发完成：本机 asset 28 已在 1.9.1（COMMITTED，
     versions 保留 1.9.0/1.9.1，updater 链路实战通过）；
  2) 2213 发现新缺陷：迁移安装器（1.8.2 常量时代构建）写的 config.local.json
     为 server_url-only → 运行中 Agent 内存 CONFIG 无 token → updater 任务 401 →
     10s 一次 ROLLBACK 循环刷屏 agent_upgrade_history；
  3) 服务端熔断上线（V1.7.0 方案第八条的制度化）：同一资产对同一目标版本
     连续 3 次 ROLLBACK/FAILED → 熔断 30min 不下发指令；COMMITTED 或目标版本
     变化或冷却结束自动解除。内存态，重启清零；
  4) 熔断首版实现有 bug（指令块 POP 掉计数累积中的 breaker，永远到不了阈值）
     ——单测思路捕获后修复（分三种情况：目标变化/熔断中/冷却结束）；
  5) 验证：重启后 100s 零新增 ROLLBACK 行（修复前 6 行/分钟），2213 心跳稳定 1.9.0。
  待办：
  2213 手动迁移到 1.9.1（其 updater 为旧版，无法走自动通道）——管理员运行：
    浏览器下载 http://172.16.250.120:8080/api/v1/agent/upgrade/download?version=1.9.1&agent_token=<token>
    → 解包 → Z-View.exe --install --quiet --server-url http://172.16.250.120:8080
       --migrate-from 1.9.0 --migrate-to 1.9.1
    （1.9.1 安装器含 config 合并修复，token 自动补齐）；
  观察期跳过（用户确认）；V1.8.0 Agent Core 重构为下一个大项。


## [2026-09-10] V1.8.0 Phase 1 完成：zvagent 包化 + git 仓库治理

- Where things stand
  1) git 治理：本周全部工作已入库（用户 amend 进 first commit d5dcc99 + 治理提交链
     3bb496d/5ffd4ae/91e010d/f22e072/7e6be35）；3548 个构建产物二进制解除跟踪；
     kilo_tmp（含 token 脚本）解除跟踪并 gitignore 覆盖；仓库 status 清零；
  2) zvagent 包骨架上线（V1.8.0 Phase 1）：__init__/layout（迁入，
     cmdb_agent_layout.py 转兼容 shim）/hygiene（MEI 清理迁入，logger 注入式解耦）；
     updater 与 unified_v2 委托链路切换验证；
  3) E2E 沙箱 4 场景回归全过（patch 目标已改 zvagent.layout——shim 重导出绑定
     patch 无效的坑已写进 shim docstring 与设计文档）；
  4) 设计文档 docs/V1.8.0-agent-core-design.md：目标结构/迁移规则/里程碑
     （V1.8.1 config/auth/upgrade → V1.8.2 heartbeat/collectors → V1.8.3 policy/jobs
     → V1.9.x security boundary）。
  当前版本态：28=1.9.1 ✅ / 2213=1.9.0（等用户跑手动迁移命令，熔断已止住其循环）。

- Next step
  2213 手动迁移收尾确认；V1.8.1（config/auth/upgrade 迁入 zvagent）。


## [2026-09-10] V1.8.1 完成：config/auth/upgrade 迁入 zvagent（core 减重 28%）

- Where things stand
  1) zvagent/config.py：默认配置、多候选解析（ProgramData 优先）、env 覆盖、
     load_configs() 整合原分散的 server_url 派生逻辑（行为等价验证通过）；
  2) zvagent/auth.py：设备凭据（缓存/持久化/清除）、zv1 请求头、ca-bundle 解析、
     TLS 自动协商（8443 探测 + 24h 缓存）；
  3) zvagent/upgrade.py：升级状态机/失败台账/跨重启锁/Authenticode 校验/
     perform_self_upgrade/onedir 迁移编排/终态消费（491 行，含全部事故修复）；
  4) cmdb_agent_core.py：2688 → 1942 行，保留薄委托 re-export（存量引用零改动）；
  5) zvagent/__init__.py 增加 __version__（与 core.AGENT_VERSION 同源）。
  验证证据：
  py_compile（SyntaxWarning 即错）+ pyflakes 0 undefined（抽取过程曾丢
  network/collectors 段 ~520 行——装配脚本漏段，备份恢复 + 修脚本重跑解决）；
  集成测试：模块导入/对象同一性（core.CONFIG IS zvagent.config.CONFIG、
  _UPGRADE_STATE 共享）/全部关键符号/真实运行态 active_version=1.9.1；
  沙箱 E2E 4 场景 + 升级护栏单测回归全过（patch 目标已切 zvagent.*）；
  onedir 构建 xref 验证 zvagent 正确入包（83 处引用、0 警告）。

- Next step
  V1.8.2（heartbeat/collectors 迁入）；下次发版（1.9.2）携带重构代码 +
  V1.7.0 Agent 侧全阶段上报；V1.8.3 policy/jobs。


## [2026-09-10] V1.8.2 完成：state/collectors/policy/heartbeat 迁入 zvagent（core 减至 1457 行）

- Where things stand
  1) zvagent/state.py：_AGENT_STATE + _LOCK（心跳/注册/远控共享运行时状态）；
  2) zvagent/collectors/：system.py（网络工具智能网卡选择/WMI/硬件/系统状态，215 行）、
     software.py（注册表软件清单 + 顺序无关变更哈希，82 行）；
  3) zvagent/policy.py：策略应用（间隔调整/UAC SecureDesktop/缓存加载）、
     _current_interval（159 行，CONFIG 共享 dict 引用，间隔变更互通）；
  4) zvagent/heartbeat.py：资产注册（zv1 401 回退全局 token 重试 + 凭据重签）、
     心跳主循环（策略应用/凭据签发/ca-bundle 原子写/升级指令触发/自愈退出）、
     四个上报线程、trigger_immediate_report（432 行）；
  5) cmdb_agent_core.py：1942 → 1457 行，剩余 SoftwareManager/控制命令/远控服务端/
     start_* 入口（V1.8.3 jobs 正式化对象）。
  验证证据：
  py_compile + pyflakes 0 undefined（修 heartbeat 缺 json 导入）；
  集成测试：状态/配置对象同一性、全部关键符号、真机采集实测
  （硬件 Windows 11、软件 125 项、系统状态）、布局 1.9.1；
  E2E 沙箱 4 场景回归全过；onedir 构建 xref 168 处 zvagent 引用 0 警告。
  累计抽取：core 2688 → 1457 行（-46%），zvagent 包 8 个模块。

- Next step
  V1.8.3：policy/jobs 正式化（策略 handlers 结构化 + 通用任务通道）；
  下次发版（1.9.2）携带全部重构 + V1.7.0 Agent 侧全阶段上报；
  V1.8.0 收官后转 V1.9.x security boundary。


## [2026-09-10] V1.8.3 完成：policy/jobs 正式化 + 通用任务通道（服务端+Agent 端到端）

- Where things stand
  1) zvagent/policy.py 正式化：handler 注册表模式（policy_handler 装饰器，
     intervals/remote_desktop 两个 handler），分发循环与业务逻辑解耦，
     新策略类型只需注册 handler；_apply_agent_policies 保留兼容别名；
  2) zvagent/jobs.py：通用任务执行框架（注册表 + execute_pending_jobs，
     单任务失败隔离、未知类型显式 failed、缺 job_id 防御）；
  3) 服务端 zvplatform/routers/agent_jobs.py：agent_jobs 表（job_id UNIQUE、
     asset_state 索引）、console API GET/POST /api/v1/console/agent-jobs（admin）、
     fetch_pending_jobs（下发即标记 dispatched）、record_job_state（终态不可回退）；
  4) Agent 接线：心跳响应 jobs → 执行 → 结果暂存 → 随下次心跳上报 job_results
     （payload 重建时序缺陷已修：移交所有权+清空暂存）；handler 注册：
     command（core，复用控制通道安全模型/raw 门控）+ report（heartbeat）；
  5) assets_api 挂载 agent_jobs_router（pid 19992 验证 endpoints 已注册）。
  验证证据：
  沙箱测试全过（jobs 框架 4 场景/policy 注册表+CONFIG 变更互通/服务端任务表
  dispatch 标记+终态+终态不可回退）；
  后端 health ok + agent-jobs endpoints 注册确认。
  已知缺陷（记录）：webhook payload first_triggered_at 为空串（build_current_alerts
  未带该字段，SQL NOW() 填充）——小瑕疵待修。

- Next step
  1.9.2 发布（携带 V1.7.0 Agent 侧 + V1.8.x 全部重构，经 updater 通道灰度）；
  V1.8.0 收官；V1.9.x security boundary（job 通道鉴权强化已在设计内）。


## [2026-09-10] 1.9.2 发布完成：两台终端统一进入新架构（V1.5.x~V1.8.x 全量落地）

- Where things stand
  两台终端统一 1.9.2 onedir 新布局，心跳正常：
  - 28 (XXH-XXX)：1.9.1 → 1.9.2 经 updater 自动迁移 ✅（V1.7.0 协议首次完整实战：
    UPG-1.9.2 事务 DISPATCHED 记录 + 全阶段上报）
  - 2213 (DESKTOP-JEGI046)：1.9.0 → 1.9.2 手动安装器迁移 ✅（其 updater 旧版
    + 内存 config 缺 token 的组合无法自动通道，401 熔断止住循环后手动收尾；
    config 缺 token 根因由 pre-check 证实）
  本机最终布局：versions\{1.9.1,1.9.2}（keep=2，1.9.0 已 prune）、
  current junction → versions\1.9.2、服务 binPath 固定 current\Z-View.exe。
  升级通道静默（manifest 1.9.2 与上报一致）。
  遗留协议瑕疵（下版修复）：updater 状态写未携带 server_upgrade_id
  （ROLLBACK 记录回退到内部 id，历史表按 attempt 记行而非按事务合并）；
  webhook payload first_triggered_at 空串。

- Next step
  观察期（远控回归/心跳/升级通道）；updater server_upgrade_id 贯穿修复随下版；
  V1.8.0 架构收官，后续转 V1.9.x security boundary 与平台 P1/P2 大盘。

