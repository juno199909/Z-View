# Z-View 部署包说明

## 文件清单

- `Z-View.exe`：单 EXE 主程序
- `config.json`：Agent 配置
- `install.bat`：交互式安装入口
- `deploy.bat`：静默/覆盖升级入口，适合 GPO
- `user-session-task-template.xml`：历史文件，当前稳态部署不再依赖

`deploy.bat` 支持：

- `--silent`：静默输出，适合脚本调用
- `--wait`：结束后保留窗口
- `--embedded`：供 `install.bat` 包装调用

## 当前推荐架构

稳态运行模型已经切换为：

- `Z-View Agent` Windows Service
- 服务进程身份：`LocalSystem`
- 服务启动命令：`Z-View.exe --service-host`
- 服务工作子进程：`Z-View.exe --run-agent --no-remote-desktop`
- 用户会话远控/UI：由后台 supervisor 自动拉起 `Z-View.exe --user-session-agent`

说明：为了兼容历史部署，Windows 服务内部名称、程序目录和数据目录仍保留 `CMDB-Agent`。

也就是说，现在不再把历史上的 `CMDB Agent Backend` / `CMDB Agent User Session` 两个计划任务作为正式运行模型，只在升级时顺手清理它们的残留。

## 手动安装

1. 右键 [install.bat](/c:/Users/Administrator/Desktop/IT2026/IT2026/GPO部署包/install.bat) 选择“以管理员身份运行”
2. 安装脚本会调用 `deploy.bat`
3. 安装完成后会自动：
   - 复制 `Z-View.exe` 和 `config.json`
   - 注册 `CMDB-Agent` 服务
   - 启动服务
   - 配置防火墙规则

安装目录：

- 程序目录：`C:\Program Files\CMDB-Agent`
- 数据目录：`C:\ProgramData\CMDB-Agent`

## GPO 部署

共享目录最少保留：

```text
\\DC\SYSVOL\yourdomain.com\scripts\CMDB\
├── Z-View.exe
├── config.json
└── deploy.bat
```

GPO 中配置“计算机启动脚本”指向：

```text
\\DC\SYSVOL\yourdomain.com\scripts\CMDB\deploy.bat
```

客户端开机后会自动完成覆盖升级和服务重启。

## 覆盖升级行为

`deploy.bat` 会自动执行：

1. 停止旧 `CMDB-Agent` 服务
2. 清理旧计划任务和旧 `HKLM\Run` 残留
3. 停止手工启动的源码版 `python cmdb_agent_unified_v2.py`
4. 覆盖复制 `Z-View.exe` 与 `config.json`
5. 重新注册 `CMDB-Agent` Windows 服务
6. 启动服务并验证后台 worker
7. 如果当前存在交互式登录会话，则等待 supervisor 拉起 `--user-session-agent`

## 部署后自动验活日志

`deploy.bat` 现在会在 `C:\Windows\Temp\cmdb-agent-deploy.log` 里补充更细的验活日志，方便区分“服务已启动”和“远控用户会话已就绪”这两件事。

重点新增：

- `backend worker detail: pid=... session=... command=...`
- `interactive session: session=... pid=... user=...`
- `session-agent heartbeat: file=... session=... pid=... alive=... age_seconds=... updated=...`
- `session-agent process: pid=... session=... command=...`
- `runtime-log tail begin` / `runtime-log: ...` / `runtime-log tail end`

现场排障时可以这样理解：

- 看到了 `backend worker detected` + `backend worker detail`，说明后台采集角色已经起来了
- 看到了 `interactive session: ...`，说明机器上确实存在桌面登录会话
- 看到了 `user-session agent heartbeat detected` 或 `...detected after interactive bootstrap`，说明远控/UI 角色已经就绪
- 如果只有 `interactive session exists but user-session agent heartbeat was not detected yet`，说明服务起来了，但用户会话代理还没就绪，需要继续看下面自动附带的 `agent-runtime.log` 尾部

## 配置说明

`config.json` 示例：

```json
{
  "server_url": "http://172.16.250.58:8080",
  "token": "cmdb-agent-secret-2024",
  "software_management": {
    "server_url": "http://172.16.250.58:8081",
    "policy_api_url": "http://172.16.250.58:8082",
    "download_path": "C:\\ProgramData\\CMDB-Agent\\Downloads"
  },
  "remote_desktop": {
    "require_consent": true,
    "consent_timeout_seconds": 30,
    "allow_if_no_user": false,
    "consent_helper_enabled": true,
    "consent_helper_connect_timeout_seconds": 4
  }
}
```

关键说明：

- `require_consent=true`：有人值守时远控前需要确认
- `allow_if_no_user=false`：无登录用户时默认不开放远控会话
- `consent_helper_enabled=true`：优先由用户会话托盘 UI 处理远控授权

## 验证安装

检查服务：

```cmd
sc query CMDB-Agent
sc qc CMDB-Agent
```

检查后台 worker：

```cmd
wmic process where "name='Z-View.exe'" get processid,commandline
```

应看到：

- `--service-host`
- `--run-agent --no-remote-desktop`

如果当前有登录用户，再检查：

```cmd
wmic process where "name='Z-View.exe'" get processid,commandline
```

应额外看到：

- `--user-session-agent`

检查运行日志：

```cmd
type C:\ProgramData\CMDB-Agent\logs\agent-runtime.log
type C:\Windows\Temp\cmdb-agent-deploy.log
```

部署日志重点检查：

- `service is Running`
- `backend worker detected`
- `backend worker detail: ...`
- `interactive session: ...`
- `user-session agent heartbeat detected`
- 或 `runtime-log tail begin` 到 `runtime-log tail end`

检查远控端口：

```cmd
netstat -ano | findstr ":9000"
```

说明：

- `9000` 端口应由 `--user-session-agent` 角色持有
- 如果当前无人登录，默认不会有 `9000` 监听，这是当前“服务 + 用户会话代理”方案的正常表现

## 卸载

```cmd
sc stop CMDB-Agent
sc delete CMDB-Agent
rd /s /q "C:\Program Files\CMDB-Agent"
rd /s /q "C:\ProgramData\CMDB-Agent"
netsh advfirewall firewall delete rule name="Z-View Agent"
```

## 注意事项

1. `deploy.bat` 和 `install.bat` 都需要管理员权限运行。
2. 域环境稳态只保留一个 Windows 服务，不再依赖双计划任务。
3. 远控授权弹窗依赖用户会话代理；如果当前没有登录用户，默认不会出现接受/拒绝弹窗。
4. 若现场远控不可用但资产心跳正常，先查 `C:\ProgramData\CMDB-Agent\logs\agent-runtime.log`，确认 supervisor 是否成功拉起 `--user-session-agent`。
5. 当前脚本和采集链路按 `Windows 7 / 10 / 11` 做了兼容处理，但服务安装和覆盖升级仍建议统一使用管理员上下文。

## 技术支持

- 资产 API：`http://172.16.250.58:8080/docs`
- 部署日志：`C:\Windows\Temp\cmdb-agent-deploy.log`
- 运行日志：`C:\ProgramData\CMDB-Agent\logs\agent-runtime.log`
