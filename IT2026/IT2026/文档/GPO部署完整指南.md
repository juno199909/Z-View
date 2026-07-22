# Z-View Agent GPO 部署完整指南

## 部署目标

当前正式方案已经切换为：

- 单 EXE：`Z-View.exe`
- 单服务：`CMDB-Agent`
- 服务身份：`LocalSystem`
- 服务负责后台采集、上报、策略执行，以及监督拉起用户会话远控代理

说明：程序品牌名已统一为 `Z-View`，但为兼容历史部署，Windows 服务名、安装目录和数据目录仍保留 `CMDB-Agent`。

这比“双计划任务”更接近企业正式产品的运行形态，也更适合域环境长期维护。

## 部署包内容

```text
GPO部署包\
├── Z-View.exe
├── config.json
└── deploy.bat
```

## GPO 部署步骤

### 1. 准备共享目录

```text
\\DC01\SYSVOL\yourdomain.com\scripts\CMDB\
```

权限建议：

- `Domain Computers`：读取
- `Authenticated Users`：读取

将部署包复制到共享目录。

### 2. 创建 GPO

1. 打开 `gpmc.msc`
2. 新建 GPO：`CMDB Agent 自动部署`

### 3. 配置启动脚本

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
\\DC01\SYSVOL\yourdomain.com\scripts\CMDB\deploy.bat
```

### 4. 链接到目标 OU

将 GPO 链接到目标终端所在 OU。

### 5. 验证

客户端执行：

```cmd
gpupdate /force
shutdown /r /t 0
```

## deploy.bat 实际做了什么

`deploy.bat` 现在的职责是：

1. 校验管理员权限
2. 停止并删除旧 `CMDB-Agent` 服务
3. 清理旧计划任务：
   - `CMDB Agent Backend`
   - `CMDB Agent User Session`
   - `CMDB Agent`
4. 清理旧 `HKLM\Run` 启动项
5. 停止旧源码版 Python Agent
6. 覆盖复制新文件到 `C:\Program Files\CMDB-Agent`
7. 通过 `sc create` 注册 `CMDB-Agent` 服务
8. 启动服务并验证：
   - 服务已 `Running`
   - 后台 worker 已以 `--run-agent --no-remote-desktop` 运行
9. 如果当前已经有交互式用户会话，则等待 supervisor 拉起 `--user-session-agent`

## 运行模型

### 服务层

服务注册命令本质等价于：

```cmd
sc create CMDB-Agent binPath= "\"C:\Program Files\CMDB-Agent\Z-View.exe\" --service-host" start= auto obj= LocalSystem
```

### 工作子进程

服务启动后，会拉起：

```text
Z-View.exe --run-agent --no-remote-desktop
```

它负责：

- 资产采集与心跳上报
- 软件策略/任务执行
- 用户会话 supervisor

### 用户会话代理

当终端存在已登录用户时，supervisor 会在该会话中拉起：

```text
Z-View.exe --user-session-agent
```

它负责：

- 远程桌面 WebSocket
- 托盘图标
- 接受/拒绝远控弹窗

## 验证方法

### 1. 检查服务

```cmd
sc query CMDB-Agent
sc qc CMDB-Agent
```

应看到：

- `STATE : 4 RUNNING`
- `BINARY_PATH_NAME` 包含 `--service-host`

### 2. 检查进程角色

```cmd
wmic process where "name='Z-View.exe'" get processid,commandline
```

正常情况下会看到：

- `--service-host`
- `--run-agent --no-remote-desktop`

如果当前有登录用户，还应看到：

- `--user-session-agent`

### 3. 检查日志

```cmd
type C:\Windows\Temp\cmdb-agent-deploy.log
type C:\ProgramData\CMDB-Agent\logs\agent-runtime.log
```

重点关注：

- `interactive sessions: ...`
- `launch attempt for session=...`
- `WTSQueryUserToken succeeded`
- `DuplicateTokenEx succeeded`
- `CreateEnvironmentBlock succeeded`
- `user-session remote desktop role launched`

### 4. 检查远控端口

```cmd
netstat -ano | findstr ":9000"
```

说明：

- 有登录用户时，`9000` 应由 `--user-session-agent` 角色监听
- 无登录用户时，默认不会监听 `9000`

## 常见问题

### 1. 资产正常上报，但远控不可用

这通常说明后台 worker 正常，但用户会话代理没拉起来。

排查顺序：

1. `sc query CMDB-Agent`
2. `wmic process where "name='Z-View.exe'" get processid,commandline`
3. `type C:\ProgramData\CMDB-Agent\logs\agent-runtime.log`

重点确认是否存在：

- `--user-session-agent`
- supervisor 拉起失败日志

### 2. 点击接受后仍无法进入远控

先确认当前运行的是新包，而不是旧 EXE：

```cmd
dir "C:\Program Files\CMDB-Agent"
```

再确认用户会话代理是否真的存在。

### 3. deploy.bat 提示复制失败

最常见原因：

- 没有管理员权限
- 旧 `Z-View.exe` 或历史 `CMDB-Agent.exe` 仍被占用
- 杀软拦截覆盖写入

先查看：

```cmd
type C:\Windows\Temp\cmdb-agent-deploy.log
```

### 4. 无人登录时没有授权弹窗

这是当前架构的设计结果，不是异常。

因为授权弹窗由 `--user-session-agent` 负责，而它只会在用户会话内运行。

如果业务上需要无人值守接管，需要单独评估并放宽：

- `remote_desktop.allow_if_no_user`

但这属于策略决策，不建议默认打开。

## 升级和卸载

### 升级

只需要替换共享目录里的：

- `Z-View.exe`
- `config.json`
- `deploy.bat`

客户端下次开机执行 GPO 启动脚本时会自动覆盖升级。

### 卸载

```cmd
sc stop CMDB-Agent
sc delete CMDB-Agent
rd /s /q "C:\Program Files\CMDB-Agent"
rd /s /q "C:\ProgramData\CMDB-Agent"
netsh advfirewall firewall delete rule name="CMDB Agent"
```

## 适用环境

- `Windows 7`
- `Windows 10`
- `Windows 11`

## 相关文件

- [deploy.bat](/c:/Users/Administrator/Desktop/IT2026/IT2026/GPO部署包/deploy.bat)
- [install.bat](/c:/Users/Administrator/Desktop/IT2026/IT2026/GPO部署包/install.bat)
- [cmdb_agent_unified_v2.py](/c:/Users/Administrator/Desktop/IT2026/IT2026/cmdb_agent_unified_v2.py)
