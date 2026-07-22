# WORKLOG

> Newest entry on top. See CLAUDE.md for the ledger rules.

## ??? Awaiting your decision (ball in your court)
> When a blocker needs a decision only you can make, an entry lands here and stays
> pinned above the log stream. Empty = nothing is waiting on you.

<!-- format: - [ ] [YYYY-MM-DD] what's blocked ??? the one-sentence decision ??? where the evidence is -->

---

<!-- log entries below, newest first -->
## [2026-08-20] Deployment template and control-port diagnostics

- Goal
  Remove the tracked runtime Agent token from the GPO deployment template and make the new Agent control port observable in deployment validation.

- Where things stand
  Replaced the tracked token in `IT2026/IT2026/GPO部署包/config.json` with `replace-with-agent-token` and added `control_port: 9001`.
  Added `ZVIEW_AGENT_CONTROL_PORT=9001` to `.env.example` and `control_port` to `config.example.json`.
  Updated `build_agent.ps1` to require the ignored local `config.json`, create an isolated `dist/GPO部署包` release package, inject the local runtime configuration only there, and leave the tracked GPO template unchanged.
  Updated `verify_release_package.ps1` to accept `-PackageDir` and reject placeholder tokens for deployable packages; `-AllowTemplateConfig` remains available for source-template static checks.
  Updated GPO documentation and `diagnostic.ps1` so the control port is read from installed `config.json` and verified as owned by the backend Agent role.
  Verification evidence:
  Parsed `build_agent.ps1`, `verify_release_package.ps1`, and `GPO部署包/diagnostic.ps1`.
  Result: `powershell-syntax-ok`.
  Parsed `GPO部署包/config.json` and `config.example.json`.
  Result: `json-config-ok`.
  `powershell -NoProfile -ExecutionPolicy Bypass -File .\IT2026\IT2026\verify_release_package.ps1 -AllowTemplateConfig`
  Result: static release-package verification passed with one expected warning for the missing signed virtual-display driver payload.
  `python -m py_compile` for assets API, Agent core/unified entry, software API, and policy API.
  Result: exit code 0.
  `powershell -NoProfile -File .\start_platform.ps1 -Action Start`
  Result: exit code 0.
  `powershell -NoProfile -File .\start_platform.ps1 -Action Stop`
  Output: `Platform stopped.`

- Next step
  Continue real endpoint deployment verification with a built isolated release package and verify remote desktop consent plus ports `9000`/`9001` on an actual Windows target.

## [2026-08-20] Agent control-plane restoration for remote shell

- Goal
  Restore the broken remote command and immediate-report control plane without interfering with the existing remote desktop WebSocket service.

- Where things stand
  Confirmed that `assets_api.py` proxied `/api/v1/assets/{id}/command` and `/trigger-report` to Agent port `9000`, while `cmdb_agent_core.py` only hosted the remote desktop WebSocket server on that port and had no HTTP control endpoints.
  Added an authenticated Agent HTTP control server on configurable port `9001` in `IT2026/IT2026/cmdb_agent_core.py`.
  The control server provides `/api/v1/command` for bounded-time command execution and `/api/v1/trigger-report` for immediate heartbeat/hardware reporting; requests require the configured Agent bearer token.
  Updated `cmdb_agent_unified_v2.py` to start the control server with the normal Agent service.
  Updated `assets_api.py` so command/report proxy requests use `ZVIEW_AGENT_CONTROL_PORT` (default `9001`) while remote desktop WebSocket traffic remains on port `9000`.
  Updated `.env.example`, `config.example.json`, and `IT2026/README.md` with the control-port configuration.
  Verification evidence:
  `python -m py_compile .\IT2026\IT2026\assets_api.py .\IT2026\IT2026\cmdb_agent_core.py .\IT2026\IT2026\cmdb_agent_unified_v2.py .\IT2026\IT2026\software_management_api_complete_v2.py .\IT2026\IT2026\software_policy_api.py`
  Result: exit code 0.
  Started an Agent control server on test port `19001`; authorized `POST /api/v1/command` executed `echo zview-control-ok` and unauthorized access returned HTTP 401.
  Started an Agent control server on test port `19002`; `assets_api.proxy_agent_json_request()` proxied `echo platform-proxy-ok` successfully and preserved platform asset metadata in the response.
  `npm run build` (workdir `D:\IT2026\IT2026\IT2026\IT2026\frontend`)
  Result: Vite production build succeeded, exit code 0.
  `powershell -NoProfile -File .\start_platform.ps1 -Action Start`
  Result: exit code 0.
  `powershell -NoProfile -File .\start_platform.ps1 -Action Stop`
  Output: `Platform stopped.`

- Next step
  Continue end-to-end validation on a real remote Windows endpoint, including user-consent behavior and firewall deployment for ports `9000` and `9001`.

## [2026-08-20] Software task target resolution and controls

- Goal
  Make software distribution tasks correctly resolve group targets to real assets and expose the existing cancel/retry controls in the software task UI.

- Where things stand
  Updated `IT2026/IT2026/software_management_api_complete_v2.py` so software task creation resolves `asset`, `group`, and `all` selections into active asset IDs before inserting `software_task_results`.
  This fixes the group distribution defect where group IDs were previously written as asset IDs, producing tasks that no Agent could poll.
  Added backend validation for unsupported target types, empty target selections, and selections that match no active asset.
  Kept the original selected target IDs in `software_tasks.target_ids` for audit context while using the resolved asset IDs for task result rows and `target_count`.
  Added `retrySoftwareTask` to `IT2026/IT2026/frontend/src/api/software.js` and exposed Cancel/Retry task controls in `IT2026/IT2026/frontend/src/views/terminal/components/TaskManagement.vue`.
  Verification evidence:
  `python -m py_compile .\IT2026\IT2026\software_management_api_complete_v2.py`
  Result: exit code 0.
  Imported `software_management_api_complete_v2` from the live app root.
  Result: printed `software-api-import-ok`.
  Executed a stub-cursor target-resolution check for group targets `[3, 3, "4"]`.
  Result: deduplicated group query parameters `[3, 4]`, resolved asset IDs `[11, 12]`, and empty group targets returned HTTP 400.
  `npm run build` (workdir `D:\IT2026\IT2026\IT2026\IT2026\frontend`)
  Result: Vite production build succeeded, exit code 0.
  `powershell -NoProfile -File .\start_platform.ps1 -Action Start`
  Result: exit code 0.
  `powershell -NoProfile -File .\start_platform.ps1 -Action Stop`
  Output: `Platform stopped.`

- Next step
  Continue the remaining module completion audit, especially production-level remote-control and Agent end-to-end behavior.

## [2026-08-20] Agent entry verification and API cleanup

- Goal
  Verify the Agent/remote-control entry path can load on the current environment and remove low-risk backend route duplication while keeping the platform startup path green.

- Where things stand
  Installed the missing `PyAutoGUI==0.9.54` runtime dependency in the current Python environment so `cmdb_agent_unified_v2.py` can import `RemoteAgent.high_integrity_helper`.
  Verified `cmdb_agent_unified_v2.py --help` now runs successfully and `load_core_module()` resolves to `cmdb_agent_core.py`.
  Verified `RemoteService.session_manager` imports successfully from the live app root.
  Removed duplicate route decorators from `IT2026/IT2026/assets_api.py` for `/api/v1/assets/{asset_id}` delete and `/api/v1/assets/{asset_id}/remote-control`.
  Verification evidence:
  `python -m py_compile .\IT2026\IT2026\assets_api.py .\IT2026\IT2026\cmdb_agent_unified_v2.py`
  Result: exit code 0.
  `python .\IT2026\IT2026\cmdb_agent_unified_v2.py --help`
  Result: CLI usage printed successfully, exit code 0.
  `python -c "import sys; sys.path.insert(0, sys.argv[1]); import cmdb_agent_unified_v2 as a; print(a.load_core_module().__file__)" "<appRoot>"`
  Result: printed `D:\IT2026\IT2026\IT2026\IT2026\cmdb_agent_core.py`.
  `python -c "import sys; sys.path.insert(0, sys.argv[1]); from RemoteService.session_manager import SessionManager; print('session-manager-ok')" "<appRoot>"`
  Result: printed `session-manager-ok`.
  `powershell -NoProfile -File .\start_platform.ps1 -Action Start`
  Result: exit code 0.
  `powershell -NoProfile -File .\start_platform.ps1 -Action Stop`
  Output: `Platform stopped.`

- Next step
  Continue with the remaining remote-control and module-completion audit instead of claiming full 100% completion from the current smoke checks.

## [2026-08-20] Discovery SNMP completion pass

- Goal
  Close the discovery module gap by exposing SNMP采集 in the frontend and keep the current asset create/startup flow stable.

- Where things stand
  Added `startSnmpScan` to `IT2026/IT2026/frontend/src/api/discovery.js`.
  Rebuilt `IT2026/IT2026/frontend/src/views/discovery/Index.vue` so discovery now has both Ping and SNMP entry cards, task progress refresh, cancel, and detail viewing in one page.
  Kept `IT2026/IT2026/frontend/src/views/asset/Create.vue` aligned with the extended asset fields and retained the corrected `notes` field mapping.
  Kept `start_platform.ps1` using `npm.cmd` for Windows frontend startup.
  Updated `IT2026/IT2026/requirements.txt` with the missing `websockets` runtime dependency.
  Verification evidence:
  `python -m py_compile .\IT2026\IT2026\assets_api.py .\IT2026\IT2026\software_management_api_complete_v2.py .\IT2026\IT2026\software_policy_api.py .\IT2026\IT2026\cmdb_agent_unified_v2.py`
  Result: exit code 0.
  `npm run build` (workdir `D:\IT2026\IT2026\IT2026\IT2026\frontend`)
  Result: Vite production build succeeded, exit code 0.
  `powershell -NoProfile -File .\start_platform.ps1 -Action Start`
  Result: exit code 0.
  `powershell -NoProfile -File .\start_platform.ps1 -Action Stop`
  Output: `Platform stopped.`

- Next step
  Continue the remaining module audit and finish the last uncovered completion gaps instead of stopping at discovery.

## [2026-08-20] Asset metadata completion and startup hardening

- Goal
  Close the current asset management gap by wiring the missing create/edit metadata flow end to end and make the one-script startup path work reliably on this Windows environment.

- Where things stand
  Updated `IT2026/IT2026/assets_api.py` so asset metadata columns are ensured at startup, create/update now persist the extended fields, asset creation now commits successfully, and asset create/update both write change-history records for the detail page history views.
  Rebuilt `IT2026/IT2026/frontend/src/views/asset/Create.vue` so the create form matches the current detail/edit fields and sends `notes` instead of the stale `remarks` field.
  Updated `start_platform.ps1` to resolve `npm.cmd` instead of `npm.ps1` on Windows and start Vite with `--host 127.0.0.1`, which fixed the frontend process readiness check.
  Added `websockets==14.2` to `IT2026/IT2026/requirements.txt` and installed the missing runtime dependency `mysql-connector-python==8.2.0` in the current environment so the backend services can actually boot.
  Verification evidence:
  `python -m py_compile .\IT2026\IT2026\assets_api.py .\IT2026\IT2026\software_management_api_complete_v2.py .\IT2026\IT2026\software_policy_api.py .\IT2026\IT2026\cmdb_agent_unified_v2.py`
  Result: exit code 0.
  `npm run build` (workdir `D:\IT2026\IT2026\IT2026\IT2026\frontend`)
  Result: Vite production build succeeded, exit code 0.
  `powershell -NoProfile -File .\start_platform.ps1 -Action Start`
  Result: state file created with `assets-api`/`software-api`/`policy-api`/`frontend` PIDs and frontend command `npm.cmd run dev -- --host 127.0.0.1`.
  `powershell -NoProfile -File .\start_platform.ps1 -Action Stop`
  Output: `Platform stopped.`

- Next step
  Continue the remaining module-by-module completion audit instead of assuming whole-project 100% completion from the current smoke tests alone.

## [2026-08-19] One-script platform startup

- Goal
  Add one PowerShell entrypoint to start/stop/restart the platform services without manual process juggling.

- Where things stand
  Added `start_platform.ps1` at the repo root. It resolves the app root, checks/install deps when needed, starts `assets_api.py`, `software_management_api_complete_v2.py`, `software_policy_api.py`, and the frontend dev server, and stores PIDs for later stop/restart.
  Added a concise quick-start note at `IT2026/README.md` that points to the new script.
  Restored the inner `IT2026/IT2026/README.md` to the original full project guide so the new startup note stays isolated.
  Verification evidence:
  `powershell -NoProfile -File .\start_platform.ps1 -Action Stop`
  Output: `Platform stopped.`
  `python -m py_compile .\IT2026\IT2026\assets_api.py`
  Result: exit code 0.

- Next step
  Continue the next product gap work: finish asset detail edit-field alignment and discovery SNMP frontend support.

## [2026-08-19] 璧勪骇鍘嗗彶涓庣姸鎬侀摼琛ラ綈

- Goal
  涓哄綋鍓嶇増鏈ˉ榻愯祫浜у彉鏇村巻鍙层€佺姸鎬佹瑙堛€佺姸鎬佸巻鍙层€佸湪绾挎椂闀跨殑鍓嶅悗绔棴鐜紝璁╄祫浜ц鎯呴〉涓嶅啀鍙湁褰撳墠鍊艰€岀己灏戞紨鍙樹俊鎭€?
- Where things stand
  宸插湪 `IT2026/IT2026/assets_api.py` 澧炲姞 `asset_changes` 杩愯鏃跺缓琛ㄣ€佸巻鍙茶褰?helper锛屼互鍙?`/api/v1/assets/{id}/changes`銆乣/status`銆乣/status/history`銆乣/uptime` 鍥涗釜鎺ュ彛锛涘凡灏嗚祫浜у垱寤恒€佽祫浜ф洿鏂般€丄gent 蹇冭烦鍥炲啓鎺ュ叆鍙樻洿璁板綍銆?  宸插湪 `IT2026/IT2026/frontend/src/api/asset.js` 鎭㈠瀵瑰簲鍓嶇璋冪敤銆?  宸插湪 `IT2026/IT2026/frontend/src/views/asset/Detail.vue` 澧炲姞鈥滆繍琛岀姸鎬佲€濃€滃彉鏇村巻鍙测€濃€滅姸鎬佸巻鍙测€濆睍绀猴紝骞跺湪璇︽儏鍔犺浇銆佷繚瀛樺悗鍒锋柊鏂版暟鎹€?  楠岃瘉璇佹嵁锛?  `python -m py_compile .\IT2026\IT2026\assets_api.py`
  缁撴灉锛氶€€鍑虹爜 0銆?  `npm run build`锛堝伐浣滅洰褰?`D:\IT2026\IT2026\IT2026\IT2026\frontend`锛?  缁撴灉锛歏ite build 鎴愬姛锛岄€€鍑虹爜 0銆?  棰濆鐜浜嬪疄锛?  `PLAYBOOK.md` 褰撳墠涓嶅瓨鍦紝宸茬‘璁や絾鏈奖鍝嶆湰杞疄鐜版帹杩涖€?
- Next step
  缁х画鏀跺彛璧勪骇璇︽儏椤靛彲缂栬緫瀛楁涓庡悗绔厑璁告洿鏂板瓧娈电殑涓嶄竴鑷撮棶棰橈紱闅忓悗琛ラ綈鍙戠幇妯″潡鍓嶇鐨?SNMP 鑳藉姏锛岀户缁帹杩涒€滈」鐩畬鍠勨€濅富绾裤€?
