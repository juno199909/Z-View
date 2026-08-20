
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

