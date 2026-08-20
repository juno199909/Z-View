
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
