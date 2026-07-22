# 企业级远程桌面系统架构设计

## 一、整体架构

```
[管理控制端 Web Console]
        |
   WebSocket 加密信道
        |
[终端Agent服务]
        |
-----------------------------
|  屏幕采集模块 (DXGI/GDI)  |
|  差分编码模块              |
|  输入注入模块 (SendInput)  |
|  会话管理模块              |
|  编解码/压缩模块 (H.264)   |
-----------------------------
        |
     Windows API
```

## 二、模块划分

### 1. 屏幕采集模块 (ScreenCapture)
- **主方案**: DXGI Desktop Duplication (Windows 8+)
- **备选**: GDI BitBlt (兼容性)
- **特性**: 
  - 差分区域检测
  - ROI裁剪
  - GPU加速

### 2. 编码压缩模块 (Encoder)
- **视频编码**: H.264 (主流) / MJPEG (兼容)
- **压缩**: zlib/lz4
- **差分帧**: 只传输变化区域

### 3. 输入注入模块 (InputInjector)
- **鼠标**: SendInput with MOUSEEVENTF_ABSOLUTE
- **键盘**: SendInput with KEYEVENTF_SCANCODE
- **线程安全**: 事件队列 + 锁机制

### 4. 坐标映射模块 (CoordinateMapper)
- **DPI感知**: GetDpiForMonitor
- **多显示器**: 虚拟屏幕偏移
- **归一化坐标**: 0.0-1.0 相对坐标

### 5. 双通道通信 (Communication)
- **控制通道**: WebSocket (信令、鼠标、键盘)
- **视频通道**: WebSocket Binary (H.264帧)
- **心跳**: Ping/Pong 机制

### 6. 会话管理 (SessionManager)
- **状态机**: IDLE → AUTHENTICATING → ACTIVE → STREAMING
- **断线重连**: 自动重连机制
- **并发控制**: 单会话锁定

## 三、鼠标控制核心逻辑

### 1. 坐标归一化 (控制端)
```python
# 发送归一化坐标 (0.0-1.0)
normalized_x = mouse_x / canvas_width
normalized_y = mouse_y / canvas_height
```

### 2. 坐标还原 (被控端)
```python
# 考虑DPI和多显示器
screen_x = normalized_x * virtual_screen_width + virtual_screen_x
screen_y = normalized_y * virtual_screen_height + virtual_screen_y
```

### 3. 拖拽状态机
```
IDLE
  ↓ mousedown
DRAGGING (持续move)
  ↓ mouseup (必须在最终位置)
IDLE
```

### 4. 关键修复点
- ✅ MouseUp必须携带最终坐标
- ✅ 使用绝对坐标 (MOUSEEVENTF_ABSOLUTE)
- ✅ 拖拽期间禁止坐标回传
- ✅ 事件顺序保证: move → down → move* → up

## 四、性能优化

### 1. 差分帧编码
- 只传输变化区域
- 首帧全量，后续差分

### 2. 帧率控制
- 高质量: 30 FPS
- 省带宽: 10 FPS
- 监控: 1 FPS

### 3. 带宽自适应
- 网络慢 → 降低分辨率
- 网络慢 → 降低帧率
- 网络慢 → 切换编码器

## 五、接口设计

### Agent端接口

```python
class RemoteDesktopEngine:
    # 屏幕采集
    def capture_screen() -> Frame
    
    # 输入注入
    def inject_mouse(event: MouseEvent) -> bool
    def inject_keyboard(event: KeyboardEvent) -> bool
    
    # 会话管理
    def start_session(session_id: str) -> Session
    def stop_session(session_id: str) -> bool
    
    # 编码压缩
    def encode_frame(frame: Frame) -> bytes
```

### 控制端接口

```javascript
class RemoteDesktopClient {
    // 连接管理
    connect(url: string): Promise<void>
    disconnect(): void
    
    // 事件发送
    sendMouseEvent(event: MouseEvent): void
    sendKeyboardEvent(event: KeyboardEvent): void
    
    // 画面接收
    onFrameReceived(callback: (frame: ImageData) => void): void
}
```

## 六、实现优先级

1. ✅ 基础WebSocket通信
2. ✅ GDI屏幕采集
3. ✅ JPEG编码传输
4. ⚠️  **修复鼠标坐标映射** (当前问题)
5. 🔄 实现差分编码
6. 🔄 升级DXGI采集
7. 🔄 实现H.264编码
8. 🔄 实现断线重连
