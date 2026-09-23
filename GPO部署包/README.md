# Z-View 部署包说明

## 文件清单

- `Z-View.exe`：单 EXE 主程序
- `config.json`：Agent 配置
- The repository `config.json` is a template. Build the release package with a local runtime `config.json` before GPO deployment.
- `install.bat`：交互式安装入口
- `deploy.bat`：静默/覆盖升级入口，适合 GPO
- `uninstall.bat`：客户端卸载入口，需要管理员权限
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

1. 右键 `install.bat` 选择“以管理员身份运行”
2. 安装脚本会调用 `deploy.bat`
3. 安装完成后会自动：
   - 复制 `Z-View.exe` 和 `config.json`
   - 注册 `CMDB-Agent` 服务
   - 启动服务
   - 配置防火墙规则

安装目录：

- 程序目录：`C:\Program Files\CMDB-Agent`
- 数据目录：`C:\ProgramData\CMDB-Agent`

## 手动卸载

右键 `uninstall.bat` 选择“以管理员身份运行”。

卸载脚本会清理：

1. `CMDB-Agent` 和历史 `CMDBAgent` Windows 服务
2. 历史 `CMDB Agent Backend`、`CMDB Agent User Session` 计划任务
3. `CMDB Agent`、`Z-View Agent` 防火墙规则
4. `HKLM\SOFTWARE\Microsoft\Windows\CurrentVersion\Run` 中的 `CMDB-Agent-ConsentUI`
5. `C:\Program Files\CMDB-Agent` 程序目录
6. `C:\ProgramData\CMDB-Agent` 数据目录

默认会删除程序目录和数据目录。若需要保留采集数据，可使用：

```bat
uninstall.bat --keep-data
```

供软件分发平台或 GPO 静默调用：

```bat
uninstall.bat --silent
```

卸载日志：

```text
C:\Windows\Temp\cmdb-agent-uninstall.log
```

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

## 当前会话宿主策略

2026-07-20 当前构建开始明确执行“单主远控宿主”策略：

- 同一时刻只允许 1 个交互式会话持有 `--user-session-agent` 远控宿主职责
- service 会根据当前交互式会话拓扑选出 `primary remote host session`
- 非主会话中的 `--user-session-agent` 会自退或被回收，避免重复宿主竞争 `9000`

这能减少以下现场问题：

- 重复执行 `deploy.bat` 后，看起来像起了两个 Agent
- 多个登录会话并存时，远控连接命中错误的会话宿主
- `9000` 端口所有权来回漂移

同时保留以下边界：

- `deploy.bat` 不会在部署后强制执行 `post-deploy restart + console 校验`
- 也不会主动执行 `tscon` 把会话切回 console
- 这样做是为了避免再次中断用户当前的 `mstsc` 远程桌面

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
  "token": "replace-with-agent-token",
  "control_port": 9001,
  "intervals": {
    "heartbeat": 30,
    "system_status": 30,
    "software": 30,
    "hardware": 86400
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

- 软件服务地址由 Agent 根据顶层 `server_url` 自动从端口 `8080` 派生为 `8081`
- `require_consent=true`：有人值守时远控前需要确认
- `allow_if_no_user=false`：无登录用户时默认不开放远控会话
- `consent_helper_enabled=true`：优先由用户会话托盘 UI 处理远控授权

## 验证安装

发布到共享目录前，在项目根目录执行静态验收：

```powershell
powershell -ExecutionPolicy Bypass -File .\verify_release_package.ps1
```

默认情况下，缺少真实虚拟显示驱动会输出警告但不阻断普通终端部署。如果目标环境必须支持无显示器终端，使用严格模式：

```powershell
powershell -ExecutionPolicy Bypass -File .\verify_release_package.ps1 -RequireVirtualDisplayDriver
```

严格模式要求部署包同时包含 `driver_manifest.json`、`.inf`、`.cat` 和 `.sys`。

安装到客户端后，继续执行以下运行态检查。

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
netstat -ano | findstr ":9001"
```

说明：

- `9000` 端口应由 `--user-session-agent` 角色持有
- 当前正常情况下，应该只有 1 个主 `--user-session-agent` 持有 `9000`
- 如果当前无人登录，默认不会有 `9000` 监听，这是当前“服务 + 用户会话代理”方案的正常表现
- `9001` 端口应由 `--run-agent --no-remote-desktop` 后台 Agent 角色持有，用于经过 token 鉴权的远程命令和即时上报

检查远程桌面连续性：

```powershell
powershell -ExecutionPolicy Bypass -File .\check_remote_desktop_continuity.ps1
powershell -ExecutionPolicy Bypass -File .\verify_remote_desktop_frame_liveness.ps1 -Samples 8 -IntervalSeconds 1
```

结果说明：

- `READY` / `frame_liveness=result value=live`：具备持续刷新条件，且采样帧签名发生变化。
- `BLOCKED` / `frame_liveness=result value=blocked`：缺少物理显示器、dummy HDMI 或真实签名虚拟显示驱动等持久显示基底。
- `stale_or_not_live`：服务可抓帧，但采样期间画面签名未变化，需结合目标机画面是否本身静止判断。

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
6. 远程桌面持续刷新依赖持久显示基底：物理显示器、dummy HDMI，或真实签名 Windows IDD/虚拟显示驱动。
7. 如果只有 RDP 临时显示表面，脚本会标记 `BLOCKED`；这是避免把最后一帧误判成实时画面的保护。

## 技术支持

- 资产 API：`http://172.16.250.58:8080/docs`
- 部署日志：`C:\Windows\Temp\cmdb-agent-deploy.log`
- 运行日志：`C:\ProgramData\CMDB-Agent\logs\agent-runtime.log`
