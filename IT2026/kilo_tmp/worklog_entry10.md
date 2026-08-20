
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

