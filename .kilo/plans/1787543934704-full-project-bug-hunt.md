# 全项目 Bug 排查与修复计划

## 目标
对 IT2026 CMDB 平台（前端 + 三个后端 + agent 代码层）做逐模块测试，找出并修复 bug 与逻辑不合理处，最终全量回归通过。

## 已确认的决策（用户拍板）
1. **写路径测试**：允许写 live MySQL，但只用 `TEST-` 前缀/标记的测试记录，每模块测完清理；删除/批量操作只作用于测试数据。
2. **远程桌面/agent**：不做实际连接回归；只做代码级审查 + 跑 `tests/` 已有离线单测；不重建/重部署 agent exe。
3. **修复尺度**：允许前后端协同修改（保持/改善 API 契约），单点修复不做重构；每个行为变更写入 WORKLOG（原因 + 验证 + 回滚）。

## 环境事实（接手者必读）
- 服务：8080=`assets_api.py`（主后端）、8081=`software_management_api_complete_v2.py`、8082=`software_policy_api.py`、5173=vite dev（代理规则见 `frontend/vite.config.mjs`）。
- admin token 获取（免登录）：`python -c "import sys; sys.path.insert(0, r'D:\IT2026\IT2026\IT2026\IT2026'); from auth_utils import issue_access_token; print(issue_access_token('admin')['access_token'])"`
- 测试脚本放 `%TEMP%\kilo\`，经 `http://127.0.0.1:5173` 代理调用（同时覆盖代理规则）。
- 后端改动后重启方式：`Get-NetTCPConnection -LocalPort <port> -State Listen` 找 PID → Stop-Process → `Start-Process python <file> -WorkingDirectory D:\IT2026\IT2026\IT2026\IT2026 -RedirectStandardOutput C:\VDDTest\api_<port>.log ...`。
- 已修复并验证过的部分（勿重复排查）：软件中心 4 项集成修复、资产发现前端产品化（见 WORKLOG 2026-08-29 两条）。
- **安全红线**：绝不向真实 asset_id 发送 reboot/shutdown/script/software 命令或批量操作（会真重启/真装机！）。命令与批量写路径只用「不存在的 id（预期 404/409）」或「临时 TEST 资产」验证。

## 已知高发 bug 类（本会话已证实存在，优先系统性排查）
A. FastAPI 路由遮蔽：固定路径路由定义在参数路由之后（8081 已犯过）。
B. 前端调用「想象中的 API」：请求体/响应结构/异步语义与后端不符（discovery 已犯过）。
C. page_size 等参数校验上限与前端不匹配。
D. vite 代理前缀冲突（更具体前缀必须排在宽前缀前）。
E. 响应封装不一致（`{data}` vs 裸数组 vs `{total,data}`），前端解构错误。
F. 写路径缺陷：异常不回滚、删除留孤儿子记录、状态机不严密（cancel/retry）、重复创建校验缺失。
G. SQL f-string 拼接注入风险（assets_api 存在 f""" SQL 模式，逐处核对参数化）。
H. 鉴权豁免过宽（AUTH_EXEMPTIONS 逐条复核是否必要、方法是否最小化）。

## 执行步骤（顺序执行，每阶段：测试→记录 bug→修复→验证该修复）

### 阶段 0：盘点（不改代码）
1. 写脚本从 3 个后端源码枚举全部路由（方法/路径/参数/Query 约束），与 `frontend/src/api/*.js`（11 个文件）每个调用做契约矩阵：路径可达性、参数名/类型/上限、响应结构 vs 前端解构。产出 `contracts.md`（放 %TEMP%\kilo）。
2. 路由遮蔽扫描：每个后端检查固定路径是否排在同类参数路由之后。
3. 落盘初始 bug 清单 `bugledger.md`：编号、模块、复现命令、根因、拟修法、状态。

### 阶段 1：认证与权限
- login（对/错密码）、me、change-password（用临时测试账号，没有则跳过改密实测）、无 token 401、坏 token 401。
- `require_request_permission` 的路径→权限映射抽查（普通用户 token 若可创建则测 403）。
- AUTH_EXEMPTIONS 逐条验证（H 类）：无 token 请求豁免路径确认行为符合设计意图，过宽的收紧。

### 阶段 2：资产 + 分组 + 导出
- CRUD 全链路（TEST- 资产）：创建→查询→编辑→详情→删除（软删）→ 再查确认。
- 分组 CRUD；导出（blob 下载 200 且非空）。
- 删除资产后子记录（asset_software、心跳）是否清理/隔离（F 类）。
- 关键字搜索中文、特殊字符（引号——同时覆盖 G 类注入探测，期望参数化无错且查得回）。

### 阶段 3：终端监控 + 命令错误路径（安全红线内）
- Overview/Detail 读接口；详情含已装软件。
- `POST /assets/{id}/command`：只测 404 id、离线资产（409/失败封装）、空 command 422。**不测真实在线资产的命令。**

### 阶段 4：软件包仓库 + 任务（8081，TEST 数据）
- 上传（小文件 TEST 包）→ 列表/详情/统计/分类 → 编辑 → 下载（hash 校验）→ 删除（文件+记录都清）。
- 任务：创建（目标=TEST 资产）→ 列表/详情/统计 → cancel（状态机）→ retry 逻辑审查 → delete。
- agent 豁免端点（poll/task-results/download）无 token 可达性验证。

### 阶段 5：合规 + 白名单 + 安装策略（8081）
- 合规 checks CRUD（TEST 规则）→ results/stats/export（blob）→ scan（只扫 TEST 资产或空目标）。
- 白名单/安装策略 CRUD + 参数校验。

### 阶段 6：软件策略 API（8082）
- policies CRUD（TEST 策略）+ 执行/日志端点审查；与 8080 console 策略页的字段一致性。

### 阶段 7：告警 + 日志中心（8080）
- 统计/列表/详情/批量解决（TEST 告警或对已解决告警幂等操作）/导出。
- 日志聚合/统计/详情，中文内容乱码检查（E 类 + 编码）。

### 阶段 8：批量操作（安全红线内）
- 仅验证：空目标列表、全无效 id 的校验/失败封装；对 1 个 TEST 资产跑 restart 命令类型（目标是 TEST 资产，不存在真实机器，预期失败封装正确）。审查 build_*_command 的转义正确性（G 类）。

### 阶段 9：发现 + Agent 策略控制台
- 发现：超限目标数（4xx）、任务详情 404、cancel 后状态；前端新逻辑回归（已修部分勿重复改）。
- `/console/agent-policies` GET/PUT 往返 + 心跳 policies 下发字段审查。

### 阶段 10：前端整体
- 按契约矩阵修完所有前端调用 bug 后：`npm run build` 必须 ✓。
- 逐页核对解构（`.data`/`res.data`/裸字段）与后端实际封装一致（E 类）。

### 阶段 11：横切代码审查（读代码为主）
- G：所有 f-string SQL 逐处核对（exists_clauses 动态拼接、ORDER BY 注入点）。
- F：事务回滚、孤儿记录、状态机。
- 时区（SET time_zone='+8:00' 的一致性）、分页总数计算、异常返回 200 包 error 的地方。
- agent/RD 代码层：明显 bug 与逻辑不合理处照修（不构建不部署），跑 `tests/` 离线单测全绿。

### 阶段 12：收尾
1. 清理：删除全部 TEST- 数据（写清理脚本，按前缀扫 assets/software_packages/tasks/policies/alerts 等），对比测试前后各表行数。
2. 全量回归：重跑阶段 1-9 的全部冒烟脚本（应全绿）+ `npm run build` + 3 个后端 `py_compile`。
3. WORKLOG.md 追加一条总账（bug 数、修复清单、验证证据、回滚说明）；kilo 记忆保存。
4. `bugledger.md` 归档到 WORKLOG 引用（不单独建第二进度文档，账本仍只有 WORKLOG）。

## 验证标准（完成的定义）
- 契约矩阵中所有前端调用 ↔ 后端路由 100% 匹配（方法/参数/响应结构）。
- 全部冒烟脚本绿；build ✓；py_compile ✓；离线单测 ✓。
- bugledger 每条 bug 状态为「已修复+已验证」或「已记录+明确不修的理由」。
- TEST 数据零残留（行数对账）。

## 风险与回滚
- 每个修复是独立小改动，回滚=单独 revert 该处代码并重启对应服务。
- 写测试全程 TEST- 前缀 + 收尾对账，若误写真实数据按 WORKLOG 记录的字段恢复。
- 命令/批量类只碰无效 id 或 TEST 资产，杜绝影响真实终端。
