# 企业级远程桌面系统架构设计

## 一、整体架构

```
[管理控制端 Web Console]
        |
   WebSocket / TCP
        |
[User Session Agent]
  控制面 / 网络面 / 会话编排
        |
        +-------------------------------+
        |                               |
        v                               v
[RemoteService / LocalSystem]     [Consent UI]
  Session Supervisor
  Named Pipe / WTS / Token
  Helper Routing / UAC合规
        |
        +-------------------------------+
        |                               |
        v                               v
[Capture Helper Host]             [Input Helper Host]
  DXGI / GDI / MSS                 SendInput / 桌面切换
  可与控制面宿主分离               可与抓屏宿主对齐
```

## 一点五、当前代码落地状态（2026-07-20）

- 已新增 `RemoteService / RemoteAgent / IPC / Common / Input / Capture / Network / Codec` 分层目录。
- `System Service` 现在负责：
  - Session supervisor
  - 本机 `Named Pipe` IPC 服务
  - 启动/拉起 User Session Agent
- `User Agent` 现在负责：
  - 远程桌面会话
  - 控制端交互
  - 继续复用现有稳定的采集、输入、Consent UI 逻辑
- 当前仍为第一阶段兼容实现：
  - 视频链路仍是 `JPEG over WebSocket`
  - 输入注入仍复用现有 `SendInput`
  - `DXGI/H.264/TLS` 已预留架构位置，但尚未完整替换当前稳定链路
- 当前已经不再把“远程桌面服务端所在 Session”默认等同于“抓屏 Session”：
  - `User Session Agent` 负责网络连接和控制面
  - `Capture Helper Host` 由 `RemoteService` 独立选择
  - `Input Helper Host` 默认跟随 `Capture Helper Host`

## 二、抓屏连续性架构

### 1. 为什么不能只靠 GDI / ImageGrab 重试

- `mstsc` 最小化、RDP 断开、锁屏、Session 切换时，问题往往不是“抓屏 API 临时异常”。
- 真正的问题是：当前 helper 仍挂在一个瞬态 RDP 渲染表面上，而 Windows 已经不再持续产出新像素。
- 这时继续在同一 Session 内切换 `DXGI / MSS / GDI / ImageGrab`，只能算换抓法，不是换抓屏宿主。

### 2. 当前实现的真实修复方向

- `RemoteService` 维护独立的 `SessionManager`，持续观察：
  - console Session
  - primary remote host Session
  - preferred capture host Session
  - active capture host Session
  - input / capture helper host Session
- 抓屏宿主选择优先级：
  1. 与 primary identity 一致的 `console-affine` Session
  2. 最优 console Session
  3. 其他持久交互 Session
  4. 仅在无持久表面时才退回 remote Session
- 当服务检测到：
  - 当前 active capture host 仍在 transient remote surface
  - 但已经出现更高持久性的 console / interactive host
  - 或当前 active host 已断开、拓扑已变化
  服务会强制清空 active capture authority，并回收旧 helper，再把抓屏 authority 迁移到新的持久宿主。

### 2.0 Service Authoritative Capture Routing

- 现在 `requested_session_id` 只再被视为 `hint`，不再被视为抓屏宿主的最终裁决权。
- 最终裁决统一由 `RemoteService/SessionManager` 基于以下信息完成：
  - Session 拓扑
  - capture persistence rank
  - display substrate 评估
  - primary identity 与 console affinity
- 这意味着：
  - Web 会话当前连在哪个 Session，不再足以把抓屏强行钉回该 Session
  - `restart_capture_helper / ensure_capture_helper / capture_frame` 都会先服从 service 认定的 authoritative host
  - 如果调用方请求的是 `RDP transient surface / disconnected session / lower-persistence host`，service 会自动降级到更稳定的抓屏宿主
- 这样修复的不是“某一帧失败”，而是整个抓屏 authority 模型，避免 helper 在 RDP 最小化、断开或切换后又被错误地拉回瞬态表面

### 2.1 新增 Display Presence 层

- 现在抓屏链路不再只按 `console / remote / disconnected` 粗分类。
- 新增 `Capture/display_presence.py`，在 `SessionManager` 与 `HighIntegrityHelper` 中统一做：
  - `EnumDisplayDevicesW` 级别的显示设备盘点
  - `physical display / virtual display / remote display adapter` 区分
  - 当前 capture target 的 substrate 评估
- 当前会显式区分的 substrate 包括：
  - `physical_console_surface`
  - `virtual_display_surface`
  - `secure_console_surface`
  - `interactive_display_surface`
  - `remote_session_surface`
  - `disconnected_surface`
  - `console_headless_surface`
  - `unknown_best_effort`
- 这意味着服务不再把“是 console session”直接等价成“就一定能持续刷新”。
- 如果当前机器没有 `physical / virtual display substrate`，系统会明确上报：
  - 当前 continuity 只能 `best_effort`
  - 要达到商业级全场景持续刷新，需要补上 `virtual display / IDD`

### 2.2 新增 Persistent Display Substrate 层

- 现在 `RemoteService` 不只知道“抓屏 helper 应该挂在哪个 Session”，还会单独判断：
  - 当前机器是否真的存在持续显示基底
  - 这个基底是否足以支撑无人值守连续刷新
  - 如果没有，当前阻塞是不是已经超出 session migration 的修复范围
- 新增：
  - `Capture/display_substrate.py`
  - `RemoteService/display_substrate_manager.py`
  - `RemoteService/virtual_display_provider.py`
- 这层会把以下状态变成标准化能力输出：
  - `persistent_available`
  - `persistent_ready_for_unattended`
  - `continuity_blocked_by_missing_substrate`
  - `requires_virtual_display_for_full_continuity`
  - `can_provision_virtual_display`
  - `virtual_display_provisioning_state`
- 关键语义变化：
  - `helper migration` 只解决“抓错宿主 / 抓在瞬态 RDP surface 上”
  - `display substrate` 负责判断“Windows 现在还有没有持续产出新像素”
  - 当系统进入 `blocked_missing_persistent_surface` 时，日志会明确说明：
    - 当前不是简单 GDI/ImageGrab 失败
    - 也不是单纯切 session 就能恢复
    - 真正缺的是持久显示基底

### 2.3 当前构建的官方支持边界

- 本次重构已经把“持续显示基底”纳入正式架构与 service capability。
- 当前构建会如实暴露：
  - 机器是否有物理显示器
  - 是否已有虚拟显示设备
  - 当前是否被 `missing persistent substrate` 阻塞
- 当前构建已补上 `RemoteService/virtual_display_provider.py`，并开放 service 侧动作：
  - `get_virtual_display_status`
  - `ensure_virtual_display`
  - `repair_virtual_display`
- `build_agent.ps1` 现在会把 `Drivers/VirtualDisplay` 同步到：
  - `dist/Drivers`
  - `GPO部署包/Drivers`
- `GPO部署包/deploy.bat` 现在会继续把 `Drivers/VirtualDisplay` 同步到：
  - `C:\Program Files\CMDB-Agent\Drivers\VirtualDisplay`
  - `C:\ProgramData\CMDB-Agent\Drivers\VirtualDisplay`
- 同步策略带有保护规则：
  - 如果部署包里只有占位文件或不完整载荷，不会覆盖目标机上已经存在的完整真实驱动载荷
  - 如果部署包里存在完整载荷，则会把它覆盖同步到安装目录和 ProgramData 目录，供 service 侧发现与修复
- 它会：
  - 盘点 `Drivers/VirtualDisplay` 驱动包
  - 读取可选的 `driver_manifest.json`，识别：
    - `hardware_ids`
    - `friendly_name_keywords`
    - `instance_id_keywords`
    - `attach_keywords`
    - `preferred_install_method`
    - `devcon_relative_path`
  - 识别 PnP 中已安装的虚拟显示设备
  - 在 LocalSystem / 管理员上下文下按 manifest 选择安装策略：
    - `pnputil /add-driver /install`
    - `pnputil /scan-devices`
    - `pnputil /enable-device /restart-device`
    - 或在存在 `devcon.exe` 与 `hardware_ids` 时执行 `devcon install / update / rescan / restart`
  - 排除 `Microsoft Remote Display Adapter / RDPIDD` 这类瞬态 RDP 适配器，避免把它误判成持久显示基底
- 但当前仓库仍未自带 Windows 官方支持的 `IDD / Virtual Display Driver` 实际驱动载荷。
- 因此：
  - 如果目标机器本身已有物理或虚拟持久显示基底，这套架构能正确选宿主并保持连续刷新能力
  - 如果后续把受支持的驱动包放入 `Drivers/VirtualDisplay`，构建与部署链路会把它一并带上，service 也能直接执行安装/修复
  - 如果目标机器在某些拓扑下完全失去持久显示基底，而现场又没有物理显示器或受支持虚拟显示驱动，系统会准确报告真实阻塞点，但还达不到 TeamViewer / AnyDesk 级别的全场景持续出图

### 3. 输入与抓屏宿主的关系

- 输入注入不再盲目跟着 WebSocket 所在 Session。
- 默认策略是：
  - `Input Helper Host` 跟随 `Capture Helper Host`
  - 由服务负责 helper 的会话放置与回收
  - helper 内部再切到正确的 input desktop / capture desktop
- 这样可以避免“画面来自 A Session，鼠标却打进 B Session”的错位。

### 4. 拓扑变化时的处理

- 触发源包括：
  - `mstsc` 最小化
  - RDP 断开 / 重连
  - 锁屏 / 解锁
  - 用户切换
  - console 与 remote 之间来回切换
- 处理策略不是只记日志，而是：
  - 重算 topology fingerprint
  - 重算 preferred capture host
  - 如有必要，强制迁移 capture authority
  - 回收旧 helper
  - 在新的 authoritative host 上重建 helper

### 5. 诊断与真正修复的边界

- 冻结检测、空帧检测、stale-frame 检测现在只用于：
  - 日志
  - 诊断
  - 辅助 helper 重建
- 它们不是最终修复手段。
- 最终修复依赖于：`抓屏 authority` 必须迁移到一个仍然持续产出像素的宿主表面。

### 6. 商业级全场景持续刷新的最终边界

- 目前代码已经尽量把抓屏 authority 从瞬态 RDP 表面迁走。
- 但如果机器在某一时刻根本不存在持久显示基座，Windows 就可能不再渲染任何新桌面内容。
- 要做到 TeamViewer / AnyDesk / 向日葵那种“无论 RDP 怎么切都持续刷新”的最终形态，通常还需要持久显示基座，例如：
  - IDD / Virtual Display Driver
  - 受支持的虚拟显示设备
  - 或等价的长期存在渲染表面
- 也就是说：
  - `Session Supervisor + Helper Migration` 解决“抓错 Session / 抓在瞬态 surface 上”的问题
  - `Persistent Display Substrate` 解决“系统根本不再产出新像素”的最后一公里问题

### 6.1 `Drivers/VirtualDisplay` 建议目录

```text
Drivers/
  VirtualDisplay/
    driver_manifest.json
    devcon.exe                  # 可选，但建议随驱动一起提供
    <driver>.inf
    <driver>.cat
    <driver>.sys
```

`driver_manifest.json` 建议字段：

```json
{
  "preferred_install_method": "auto",
  "hardware_ids": ["ROOT\\\\MyVirtualDisplay"],
  "friendly_name_keywords": ["my virtual display", "idd"],
  "instance_id_keywords": ["root\\\\myvirtualdisplay"],
  "attach_keywords": ["attached", "active"],
  "inf_relative_path": "MyDriver.inf",
  "catalog_relative_path": "MyDriver.cat",
  "binary_relative_path": "MyDriver.sys",
  "devcon_relative_path": "devcon.exe"
}
```

说明：
- `preferred_install_method=auto` 时，service 会优先走 `pnputil`，若安装后仍未生成设备实例，且包内带有 `devcon.exe + hardware_ids`，则自动补做 root-enumerated 实例化。
- `preferred_install_method=devcon_install` 适合必须显式创建设备实例的 IDD/Virtual Display 驱动。
- `preferred_install_method=devcon_update` 适合已存在实例、需要更新驱动绑定的场景。
- 没有真实受支持驱动载荷时，当前系统仍只能如实报告 `missing persistent substrate`，不能伪造商业级连续刷新能力。
- 也就是说，这次架构调整已经把“会话路由正确性”和“显示基底部署链路”都纳入正式能力范围，但最终连续刷新能力仍取决于目标机上是否真的存在物理显示或真实受支持虚拟显示驱动。

## 二、模块划分

### 1. 屏幕采集模块 (ScreenCapture)
- **主方案**: DXGI Desktop Duplication (Windows 8+)
- **备选**: GDI BitBlt (兼容性)
- **特性**: 
  - 差分区域检测
  - ROI裁剪
  - GPU加速

### 2. 编码压缩模块 (Encoder)
- **视频编码**: 当前 JPEG over WebSocket；目标 H.264（硬件加速）
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
- **视频通道**: WebSocket Binary (当前 JPEG 帧，目标 H.264)
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
