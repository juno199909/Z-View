
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
