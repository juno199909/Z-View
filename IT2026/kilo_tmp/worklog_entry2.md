
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
