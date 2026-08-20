
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

