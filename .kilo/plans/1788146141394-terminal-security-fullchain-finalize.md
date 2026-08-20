# 项目完成度 100% 收尾计划

## 当前状态（已验证）
- 6 服务在线、66 表、30 页面、47/47 端点 200、34449 行代码
- 18 个 Bug 已全部修复（含 Bug#18 usb_enumerate CoInitialize）
- 整体完成度 ~85%

## 剩余限制项 → 处理方案

### 限制 1：方案 B（Agent 自动轮询安全策略）未做【功能闭环缺口，必须做】
**现状**：策略中心改策略后，Agent 不会自动重应用，只有手动"下发"按钮才执行。这违背"集中策略管理"的自动化预期。
**方案**：在 `cmdb_agent_core.py` 新增 `SecurityPolicySync` 类（仿 `SoftwareManager` 模式），由 `cmdb_agent_unified_v2.py` 在拿到 asset_id 后启动。
- 类 `SecurityPolicySync(asset_id)`：`_loop` 每 300s（复用 intervals.security_policy_sync，默认 300）`GET {CONFIG['server_url']}/api/v1/agent/security-policies?asset_id={asset_id}`（agent_token header）。
- 拉到 policies 列表后，按 policy_type 调 `security_manager.execute_security_command` 应用：
  - firewall → `firewall_apply` params={rules: config.rules}
  - usb → `usb_block`(action=block) / `usb_allow`(action=allow)
  - app_control → `process_scan_blacklist` params={blacklist: config.blacklist}
  - file_protect → `file_baseline` 对每个 protected_dirs
  - behavior → 仅记录策略（无即时执行动作）
- 每条应用后 `POST {server_url}/api/v1/agent/security-policy-result` 回传 {policy_id, asset_id, scope_type, status, applied_rules, failed_rules, error_detail}。
- 启动挂载：`cmdb_agent_unified_v2.py` line 1439 `start_software_management(asset_id)` 后加 `start_security_policy_sync(asset_id)`。
- `cmdb_agent_core.py` 加全局 `_security_policy_sync_instance` + `start_security_policy_sync(asset_id)` 函数。
- CONFIG intervals 加 `security_policy_sync: 300` 默认。
- 改后需**重打包 Agent**（build_agent.ps1）+ 部署生效。
- 验证：创建策略→绑定 asset 28→等 Agent 轮询→查 `security_policy_exec_results` 有自动记录。

### 限制 2：isolate 真机测试【可做，需谨慎】
**现状**：isolate 端点已实现（阻断入站+保留 9001/3389），但未真机测。
**方案**：在 asset 28（本机）测 `POST /security/remote/isolate/28` → 验证 `netsh advfirewall firewall show rule name=zv-isolate-block-in` 存在 → 立即 `POST /security/remote/unisolate/28` → 验证规则删除。
**风险**：本机即平台+Agent，阻断入站期间 9001 控制端口仍通（保留规则），平台仍可下发 unisolate。可接受。
**验证**：isolate 后 netsh 规则存在 + 9001 仍可访问 + unisolate 后规则删除。

### 限制 3：文件保护实时性【增强，非阻塞】
**现状**：轮询哈希基线，非实时拦截。
**方案**：加 WMI `__InstanceOperationEvent` 订阅监控保护目录文件变更（接近实时告警），作为轮询补充。属增强非必需，**本轮可选做**（标记完成度为"轮询+WMI事件双重检测"）。
**建议**：本轮跳过深度实现（minifilter 才真实时），保持现状 + 文档明确边界。完成度维持 75%。

### 限制 4：远控 VMware 帧率【环境依赖，非代码】
**现状**：~1fps，根因 VMware 虚拟显卡 EDID 丢失，需 VM 层修复。
**方案**：这不是代码 bug，是环境限制。尝试激活 Parsec-VDD 虚拟显示器（驱动已 staged）或指导 VM 配置。属用户决策项（已 pinned）。
**建议**：本轮不改代码，明确为环境限制。如需推进，单独处理 VM 配置。

### 限制 5：测试体系【应做】
**现状**：手写脚本无统一 runner，无 CI。
**方案**：建 `tests/security/test_security_api.py`（pytest），覆盖主要安全端点的正常+边界路径。建 `conftest.py` 提供 admin_token fixture（调 login）+ base_url fixture。复用现有端点测试逻辑。
**范围**：安全模块端点单测（47 端点选关键 20 个）+ Agent security_manager 单测（mock/源码直调）。
**验证**：`pytest tests/security/ -v` 通过。

### 限制 6：Git 工作区基线整理【应做】
**现状**：188 staged + 未提交改动混杂。
**方案**：用户已 pinned 为决策项。本轮**不改 Git**，除非用户授权。

## 执行顺序（实现代理执行）
1. **方案 B 实现**（cmdb_agent_core.py + cmdb_agent_unified_v2.py）→ py_compile → 重打包部署 → 验证自动轮询闭环。
2. **isolate 真机测试**（下发→验证 netsh→unisolate→验证删除）。
3. **测试体系**（tests/security/ pytest + conftest）→ `pytest tests/security/ -v`。
4. **WORKLOG + 记忆更新**（方案B闭环 + isolate验证 + 测试体系）。
5. 远控帧率/文件实时/minifilter/Git 基线 → 明确为环境/授权限制，不做。

## 受影响文件
- `cmdb_agent_core.py`（新增 SecurityPolicySync + start_security_policy_sync）
- `cmdb_agent_unified_v2.py`（启动挂载）
- `build_agent.spec`（无新依赖，security_manager 已含）
- `tests/security/test_security_api.py` + `tests/security/conftest.py`（新增）
- `WORKLOG.md`

## 不做（明确边界）
- minifilter 驱动（需 WHQL 签名，不现实）
- 远控 VMware 帧率（环境依赖，VM 层修复）
- Git 基线（需用户授权）
- 文件保护 WMI 实时事件（增强，维持轮询现状+文档边界）

## 验收清单
- [ ] 方案B：Agent 自动轮询安全策略 → exec_results 有自动记录
- [ ] isolate 真机：netsh 规则下发+删除闭环验证
- [ ] tests/security/ pytest 通过
- [ ] py_compile + npm build 通过
- [ ] WORKLOG + 记忆更新
- [ ] 剩余限制项明确边界文档化（远控帧率/minifilter/Git）

## 需用户确认
1. isolate 在本机（asset 28，即平台+Agent 同机）真机测是否授权？（保留 9001+3389，可 unisolate）
2. Git 基线整理是否本轮授权？
3. 文件保护 WMI 实时事件增强是否本轮做？（建议跳过，维持轮询+文档边界）