# WT 服务端→客户端回程帧问题 — 调试状态存档（未解决）

> 更新：2026-09-17 14:30 ｜ 状态：**UDP 暂停（wtTransportEnabled=false，8d5e7f6f），远程桌面回退纯 TCP 工作正常**
> 本文档供恢复 UDP 攻坚时使用。

## 已确认事实

1. **浏览器→网关→Agent 方向完全工作**：鼠标/键盘/capabilities 经 WT 流到达引擎（2213 agent-runtime.log 多次 handle_mouse 实证）。
2. **Agent→网关→浏览器方向断裂**：网关 send_wt_data 写入 stream_id=4（Chrome 建的双向流）后，Chrome 报 `QUIC_NETWORK_IDLE_TIMEOUT`；aioquic 客户端报 `0x105 DATA frame is not allowed in this state`。
3. **localhost 探针同样失败**（单实例新代码网关）——非网络/防火墙问题，是帧封装与客户端 H3 解析模型的匹配问题。
4. **注入 [0x41][session_id] 头的尝试已撤销**：aioquic 客户端需要它（其 H3 层对 WT 流按 H3 帧解析），但 Chrome 的适配器把流当裸字节流（session id 前缀由 Chrome 栈在建流时自动处理），注入反而破坏适配器的 [4B len] 帧解析。
5. 2241 探针曾 PASS（925 帧）——**复测未复现**，当时 4433 上疑似有多进程混投（见 ops_multi_gateway_zombie），PASS 可能是假阳性。

## 核心矛盾

aioquic 的 H3Connection 对 bidi 流按 H3 帧解析（[varint frame_type][varint len][payload]）：
- 网关写入裸 [4B len][1B type][payload] → 客户端 H3 读 frame_type=0x00=DATA → 0x105 关闭。
- 加 [0x41][session_id] → aioquic 客户端识别 WT 流 ✓，但 Chrome 栈把同样的字节当应用数据 → 适配器解析错乱。

## 下一步攻坚路径（按优先级）

1. **研读 aioquic 官方 webtransport 示例**（GitHub aioquic repo examples/webtransport_client.py + webtransport_server.py）——确认官方 server→client 的数据封装方式（是否经 H3DataReceived/WT 帧封装、session id 出现几次）。
2. **用 pktmon/wireshark 抓包对照**：抓 RustDesk/其他 WT 服务端（或直接抓 Chrome 对官方 aioquic 示例服务器的会话），对照 Z-View 网关的线上字节。
3. **确认 aioquic 版本**的 H3Connection 是否有 `send_session_data` / `create_webtransport_stream` 对应的接收端注册 API（h3/connection.py:415 附近有 create_webtransport_stream，检查是否有配套的 session 数据接收注册表 `_webtransport_sessions`）。
4. **若 aioquic 模型与 Chrome 模型确实不兼容**：备选方案——网关在 **QUIC 数据报（datagram）通道**承载视频（Chrome WT 支持 datagrams，无流帧头问题），或升级 aioquic 至最新版（WT 实现可能已修正）。
5. **最终兜底**：保持 TCP-only（当前状态），UDP 作为长期演进项。

## 相关代码与工具

- 网关：`webtransport_gateway.py`（AgentBridge.send_wt_data / set_data_stream / _upstream_loop / _handle_h3_event）
- 探针：`C:\Users\ADMINI~1\AppData\Local\Temp\kilo\wt_probe.py`（v3：PROBE_HOST 参数 + 多子帧解析）
- 运维记忆：ops_wt_no_header_injection / ops_wt_gateway_cert_reload / ops_multi_gateway_zombie / ops_wt_cert_trust_hashpin / ops_gateway_restart_for_code_changes
- 铁律：改网关代码后**必须杀光全部 webtransport_gateway 进程再启一个**（多实例绑定同端口会随机分投 QUIC 包）；验证用 127.0.0.1 与 172.16.250.120 双探针。
