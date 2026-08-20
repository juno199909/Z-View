
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
