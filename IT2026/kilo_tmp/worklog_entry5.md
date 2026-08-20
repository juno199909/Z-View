
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
