# Z-View 域环境部署标准操作单

## 1. 适用范围

适用于当前 Z-View Agent 的正式域环境部署，运行模型为：

- 单程序：`Z-View.exe`
- 单服务：`CMDB-Agent`
- 服务身份：`LocalSystem`
- 服务宿主参数：`--service-host`
- 后台采集子进程：`--run-agent --no-remote-desktop`
- 用户会话远控/UI 子进程：`--user-session-agent`

说明：

- 对外品牌名为 `Z-View`
- 为兼容历史版本，服务名、安装目录、数据目录仍保留 `CMDB-Agent`

## 2. 部署目标

部署完成后，每台终端应满足：

- 开机自动安装或覆盖升级 Agent
- 自动注册并启动 `CMDB-Agent` 服务
- 正常上报资产、软硬件、心跳
- 有登录用户时可弹出远控接受/拒绝框
- 有登录用户时可建立远程桌面连接

## 3. 部署前检查

### 3.1 平台服务检查

确认平台端服务正常：

- 资产 API：`http://<平台IP>:8080`
- 软件管理 API：`http://<平台IP>:8081`
- 策略 API：`http://<平台IP>:8082`
- 前端页面可正常访问

建议检查：

```cmd
curl http://172.16.250.58:8080/health
curl http://172.16.250.58:8081/health
curl http://172.16.250.58:8082/health
```

### 3.2 部署包检查

确认部署包目录至少包含：

```text
GPO部署包\
├── Z-View.exe
├── config.json
└── deploy.bat
```

### 3.3 配置文件检查

部署前先修改 [GPO部署包/config.json](../GPO部署包/config.json)：

```json
{
  "server_url": "http://172.16.250.58:8080",
  "token": "cmdb-agent-secret-2024",
  "intervals": {
    "heartbeat": 30,
    "system_status": 30,
    "software": 30,
    "hardware": 30
  },
  "remote_desktop": {
    "require_consent": true,
    "consent_timeout_seconds": 30,
    "allow_if_no_user": false
  },
  "log_level": "INFO"
}
```

重点确认：

- `server_url`：必须指向正式资产 API 地址
- `token`：必须与平台保持一致
- `require_consent=true`：有人值守时需要同意远控
- `allow_if_no_user=false`：无人登录时默认不开放远控

## 4. 域控侧部署步骤

### 4.1 准备共享目录

在域控上创建共享目录，例如：

```text
\\DC01\SYSVOL\yourdomain.com\scripts\ZView\
```

将以下文件复制进去：

- `Z-View.exe`
- `config.json`
- `deploy.bat`

建议权限：

- `Domain Computers`：读取
- `Authenticated Users`：读取

### 4.2 创建 GPO

在域控执行：

1. 打开 `gpmc.msc`
2. 新建 GPO：`Z-View Agent 自动部署`
3. 链接到目标终端所在 OU

### 4.3 配置计算机启动脚本

路径：

```text
计算机配置
  -> 策略
    -> Windows 设置
      -> 脚本(启动/关机)
        -> 启动
```

脚本填写：

```text
\\DC01\SYSVOL\yourdomain.com\scripts\ZView\deploy.bat
```

说明：

- 这里必须配置“计算机启动脚本”，不要配置为用户登录脚本
- 通过 GPO 启动脚本执行时，默认就是系统上下文，不需要手工提权

## 5. 客户端执行步骤

### 5.1 触发策略

在测试终端执行：

```cmd
gpupdate /force
shutdown /r /t 0
```

说明：

- 首次部署建议直接重启
- 启动脚本通常在开机过程中执行

### 5.2 手工安装场景

如果不是通过 GPO，而是现场手工安装：

1. 进入部署包目录
2. 右键 `deploy.bat`
3. 选择“以管理员身份运行”

说明：

- 手工双击不是管理员上下文，容易导致复制失败、服务注册失败
- 域内 GPO 启动脚本方式不需要人工右键管理员运行

## 6. 部署后验证

### 6.1 检查服务

```cmd
sc query CMDB-Agent
sc qc CMDB-Agent
```

正常结果：

- `STATE : 4 RUNNING`
- `BINARY_PATH_NAME` 包含 `Z-View.exe --service-host`

### 6.2 检查进程角色

```cmd
wmic process where "name='Z-View.exe'" get processid,commandline
```

正常至少应看到：

- `--service-host`
- `--run-agent --no-remote-desktop`

如果当前已有登录用户，还应看到：

- `--user-session-agent`

### 6.3 检查日志

```cmd
type C:\Windows\Temp\cmdb-agent-deploy.log
type C:\ProgramData\CMDB-Agent\logs\agent-runtime.log
```

重点看：

- 是否成功复制文件
- 是否成功注册并启动服务
- 是否识别到交互式用户会话
- 是否成功拉起 `--user-session-agent`

建议重点识别这些验活日志关键字：

- `backend worker detected`
- `backend worker detail: pid=... session=... command=...`
- `interactive session: session=... pid=... user=...`
- `user-session agent heartbeat detected`
- `user-session agent heartbeat detected after interactive bootstrap`
- `runtime-log tail begin`

### 6.4 检查远控端口

```cmd
netstat -ano | findstr ":9000"
```

说明：

- 有登录用户时，`9000` 应由 `--user-session-agent` 持有
- 无登录用户时，默认没有 `9000` 监听是正常现象

### 6.5 检查平台状态

在 Z-View 平台中确认：

- 终端已上线
- CPU、内存、软件信息正常上报
- 发起远控时，如终端有人登录，会出现接受/拒绝弹窗

## 7. 远程桌面专项验证

### 7.1 验证条件

要满足以下条件，远控才会正常：

- 终端在线
- `CMDB-Agent` 服务正常运行
- 平台与终端网络互通
- 当前终端存在已登录用户会话
- `--user-session-agent` 已被拉起

### 7.2 典型正常现象

- 平台点击发起远控
- 被控端弹出 30 秒倒计时的接受/拒绝框
- 点击接受后建立画面连接
- 托盘图标存在

### 7.3 典型误判

以下情况不是程序异常：

- 终端无人登录，没有接受/拒绝弹窗
- 终端无人登录，`9000` 没监听

这是当前“服务 + 用户会话代理”的设计结果。

## 8. 常见故障与处理

### 8.1 资产正常上报，但远控不可用

排查顺序：

1. `sc query CMDB-Agent`
2. `wmic process where "name='Z-View.exe'" get processid,commandline`
3. `type C:\ProgramData\CMDB-Agent\logs\agent-runtime.log`

重点确认：

- 是否存在 `--user-session-agent`
- 是否有 supervisor 拉起失败日志

### 8.2 deploy.bat 执行失败

先看：

```cmd
type C:\Windows\Temp\cmdb-agent-deploy.log
```

常见原因：

- 手工执行时没有管理员权限
- 旧 EXE 被占用
- 杀软拦截覆盖写入
- 共享目录权限不足

如果部署日志显示：

- `service is Running` 但没有 `backend worker detected`
说明服务宿主起来了，但后台采集角色没有被拉起

- `backend worker detected` 但没有 `user-session agent heartbeat detected`
说明后台正常，但当前登录桌面的远控/UI 角色没有起来

- 末尾自动带出 `runtime-log tail begin` 到 `runtime-log tail end`
说明脚本已经自动把运行日志最后几行摘出来了，优先看这里，不用先手工再翻一次完整日志

### 8.3 有弹窗，但点击接受后仍然一直连接中

检查：

```cmd
wmic process where "name='Z-View.exe'" get processid,commandline
netstat -ano | findstr ":9000"
type C:\ProgramData\CMDB-Agent\logs\agent-runtime.log
```

重点确认：

- `--user-session-agent` 是否存活
- `9000` 是否由该进程监听
- 日志里是否有远控 WebSocket 启动失败

### 8.4 终端重启后服务不工作

检查：

```cmd
sc query CMDB-Agent
sc qc CMDB-Agent
```

若服务不存在或路径不对，重新执行一次部署脚本。

## 9. 升级操作单

升级时只需要替换共享目录中的：

- `Z-View.exe`
- `config.json`
- `deploy.bat`

然后在客户端执行：

```cmd
gpupdate /force
shutdown /r /t 0
```

客户端会在开机时自动覆盖升级并重启服务。

## 10. 回滚/卸载操作单

客户端执行：

```cmd
sc stop CMDB-Agent
sc delete CMDB-Agent
rd /s /q "C:\Program Files\CMDB-Agent"
rd /s /q "C:\ProgramData\CMDB-Agent"
netsh advfirewall firewall delete rule name="Z-View Agent"
```

## 11. 现场交付验收标准

一台测试终端至少满足以下全部条件才算通过：

- 能通过 GPO 自动安装
- `CMDB-Agent` 服务为 `RUNNING`
- 平台可看到该终端在线
- 软硬件信息能正常上报
- 有登录用户时，远控弹窗正常
- 点击接受后能进入远程桌面

## 12. 标准执行清单

### 部署前

- 已确认平台 8080/8081/8082 正常
- 已确认 `config.json` 地址和 token 正确
- 已确认共享目录权限正确
- 已确认部署包文件齐全

### 域控操作

- 已创建共享目录
- 已复制 `Z-View.exe`、`config.json`、`deploy.bat`
- 已新建 GPO
- 已配置“计算机启动脚本”
- 已链接到目标 OU

### 客户端验证

- 已执行 `gpupdate /force`
- 已重启终端
- 已确认 `CMDB-Agent` 服务运行
- 已确认后台子进程存在
- 已确认有人登录时 `--user-session-agent` 存在
- 已确认平台侧在线和远控可用
