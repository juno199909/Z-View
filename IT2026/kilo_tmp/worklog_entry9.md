
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

