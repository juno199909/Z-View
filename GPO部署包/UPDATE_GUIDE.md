# Z-View 快速更新指南

## 🔧 本次修复内容

**问题**: 首次上报时所有数据类型（heartbeat、software、hardware）同时上报，但`report_type`只能是一个值，导致软件和硬件数据被后端忽略。

**修复**: 分3批上报，每批独立的`report_type`
- 第1批: 硬件信息 (report_type=hardware)
- 第2批: 软件清单 (report_type=software)
- 第3批: 心跳+状态 (report_type=heartbeat)

---

## 📦 在 Juno 上更新 Z-View

### 步骤1: 停止旧版 Z-View
```cmd
taskkill /F /IM Z-View.exe
taskkill /F /IM python.exe
```

### 步骤2: 安装新版本
```cmd
# 以管理员身份运行
install.bat
```

### 步骤3: 观察启动日志
应该看到：
```
📤 首次上报 - 分批上报所有数据...
🚀 首次启动，立即上报硬件信息
🖥️  上报硬件信息
✅ 上报成功 [hardware]: 17:05:30

📦 上报软件清单: 92 个
✅ 上报成功 [software]: 17:05:31

✅ 上报成功 [heartbeat]: 17:05:32
```

**关键**: 应该看到**3次上报成功**，不是1次！

### 步骤4: 验证数据
打开前端，查看Juno的终端详情：
- ✅ 操作系统: Windows 11
- ✅ 序列号: 5CD9527R56
- ✅ 制造商: HP
- ✅ 型号: OMEN by HP Laptop 15-dc1xxx
- ✅ CPU: 4核心
- ✅ 已安装软件: 92个

### 步骤5: 测试远程桌面
点击"远程"按钮，应该能正常连接。

---

## ⚠️ 如果还是有问题

### 问题1: 还是只看到1次上报成功
→ Agent没有更新到最新版本
→ 检查安装目录的文件时间：`dir "C:\Program Files\CMDB-Agent"`
→ 应该是17:10左右

### 问题2: 数据库还是空
→ 后端API没有重启
→ 重启后端: `python assets_api.py`

### 问题3: 远程桌面还是连不上
→ 测试WebSocket: `python test_websocket.py`
→ 检查是否有ImageGrab相关错误

---

## 🎯 预期结果

更新后：
- ✅ Juno的所有硬件信息完整显示
- ✅ 92个已安装软件正确显示
- ✅ 远程桌面能正常连接和控制
- ✅ Brain和Juno数据一致

---

**版本**: Z-View v1.1 (2026-06-11 17:10)
**修复**: 分批上报数据，解决report_type冲突
