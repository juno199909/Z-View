# -*- coding: utf-8 -*-
"""Agent 升级状态机（V1.8.1 迁入自 cmdb_agent_core.py）。

职责：心跳升级指令的执行编排（exe 单文件流 / onedir 迁移流）、
失败台账（指数退避）、跨重启升级锁、Authenticode 校验、
状态持久化与终态上报（server_upgrade_id 贯穿）。

阶段：CHECK → DOWNLOAD → VERIFY → BACKUP → INSTALL(bat) / 委托 ZViewUpdater
      → COMMIT / ROLLBACK / FAILED（终态经心跳上报，服务端事务历史记录）。
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, Optional

import requests

from console_utils import safe_console_print
from zvagent import __version__ as AGENT_VERSION
from zvagent.auth import _agent_headers, _agent_requests_verify, _platform_base
from zvagent.config import CONFIG

print = safe_console_print


_UPGRADE_STATE = {"in_progress": False}

# 任一步失败 → ROLLBACK（bat 内自动执行并写结果标记）→ FAILED
# 状态持久化：ProgramData\CMDB-Agent\runtime\upgrade-state.json（worker 侧）
#             upgrade-bat-result.txt（bat 终态标记，新进程启动时消费）
# ============================================================

_UPGRADE_STATE_PATH = (
    Path(os.environ.get("ProgramData", ".")) / "CMDB-Agent" / "runtime" / "upgrade-state.json"
)
_UPGRADE_BAT_RESULT_PATH = (
    Path(os.environ.get("ProgramData", ".")) / "CMDB-Agent" / "runtime" / "upgrade-bat-result.txt"
)


def _upgrade_state_write(stage: str, **extra: Any) -> None:
    """持久化升级状态机当前阶段。"""
    try:
        _UPGRADE_STATE["stage"] = stage
        state = {}
        if _UPGRADE_STATE_PATH.exists():
            try:
                state = json.loads(_UPGRADE_STATE_PATH.read_text(encoding="utf-8"))
            except Exception:
                state = {}
        state.update({"stage": stage, "updated_at": time.strftime("%Y-%m-%d %H:%M:%S")})
        # V1.7.0：服务端事务 ID 一经写入随状态文件贯穿全部阶段（updater/安装器 merge 继承）
        if _UPGRADE_STATE.get("server_upgrade_id"):
            state.setdefault("server_upgrade_id", _UPGRADE_STATE["server_upgrade_id"])
        state.update(extra)
        _UPGRADE_STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
        _UPGRADE_STATE_PATH.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as exc:
        print(f"[Upgrade] state write failed: {exc}")


# V1.5 升级风暴修复：失败台账（指数退避）+ 跨重启升级锁。
# 2026-09-09 事故：下载失败 → 内存 in_progress 复位 → 每 30s 重试 → 1.5h 内
# 泄漏 44GB 临时文件撑满 C 盘。台账跨重启计数，失败达到阈值后指数退避，
# 从机制上禁止"无限重试"。
_UPGRADE_LEDGER_PATH = (
    Path(os.environ.get("ProgramData", ".")) / "CMDB-Agent" / "runtime" / "upgrade-attempts.json"
)
_UPGRADE_LOCK_PATH = (
    Path(os.environ.get("ProgramData", ".")) / "CMDB-Agent" / "runtime" / "upgrade.lock"
)
_UPGRADE_MAX_ATTEMPTS = 5
_UPGRADE_BACKOFF_BASE_SECONDS = 1800        # 第 5 次失败起退避 30min
_UPGRADE_BACKOFF_MAX_SECONDS = 86400        # 退避上限 24h
_UPGRADE_LOCK_STALE_SECONDS = 1800          # 锁 30min 未释放视为崩溃残留，可接管


def _upgrade_ledger_read() -> dict:
    try:
        data = json.loads(_UPGRADE_LEDGER_PATH.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _upgrade_ledger_write(ledger: dict) -> None:
    try:
        _UPGRADE_LEDGER_PATH.parent.mkdir(parents=True, exist_ok=True)
        _UPGRADE_LEDGER_PATH.write_text(json.dumps(ledger, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as exc:
        print(f"[Upgrade] ledger write failed: {exc}")


def _record_upgrade_failure(target_version: str, reason: str) -> float:
    """记录一次失败尝试（24h 滚动窗口），返回下次允许重试的时间戳（0 = 立即）。"""
    ledger = _upgrade_ledger_read()
    entry = ledger.get(target_version) or {}
    attempts = int(entry.get("attempts") or 0)
    window_started_at = float(entry.get("window_started_at") or 0)
    if time.time() - window_started_at > 86400:
        attempts = 0
        window_started_at = time.time()
    attempts += 1
    entry.update({
        "attempts": attempts,
        "window_started_at": window_started_at,
        "last_error": str(reason)[:300],
        "last_attempt_at": time.time(),
    })
    next_retry_at = 0.0
    if attempts >= _UPGRADE_MAX_ATTEMPTS:
        backoff = min(
            _UPGRADE_BACKOFF_BASE_SECONDS * (2 ** (attempts - _UPGRADE_MAX_ATTEMPTS)),
            _UPGRADE_BACKOFF_MAX_SECONDS,
        )
        next_retry_at = time.time() + backoff
        entry["next_retry_at"] = next_retry_at
        entry["backoff_seconds"] = backoff
        print(f"[Upgrade] target {target_version} failed {attempts} times; "
              f"backoff {int(backoff)}s")
    ledger[target_version] = entry
    _upgrade_ledger_write(ledger)
    return next_retry_at


def _clear_upgrade_failure(target_version: str) -> None:
    """升级成功（COMMIT）后清除该版本的失败台账。"""
    ledger = _upgrade_ledger_read()
    if target_version in ledger:
        ledger.pop(target_version, None)
        _upgrade_ledger_write(ledger)


def _upgrade_backoff_remaining(target_version: str) -> float:
    entry = _upgrade_ledger_read().get(target_version) or {}
    return max(0.0, float(entry.get("next_retry_at") or 0) - time.time())


def _acquire_upgrade_lock(upgrade_id: str) -> bool:
    """跨重启升级锁（EXCL 创建）；存在但超过陈旧阈值视为崩溃残留，允许接管。"""
    try:
        if _UPGRADE_LOCK_PATH.exists():
            payload = {}
            try:
                payload = json.loads(_UPGRADE_LOCK_PATH.read_text(encoding="utf-8"))
            except Exception:
                payload = {}
            if time.time() - float(payload.get("started_at") or 0) < _UPGRADE_LOCK_STALE_SECONDS:
                return False
            print("[Upgrade] stale upgrade lock detected; taking over")
            _UPGRADE_LOCK_PATH.unlink(missing_ok=True)
        _UPGRADE_LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(str(_UPGRADE_LOCK_PATH), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(json.dumps({"pid": os.getpid(), "upgrade_id": upgrade_id,
                                 "started_at": time.time()}))
        return True
    except FileExistsError:
        return False
    except Exception as exc:
        print(f"[Upgrade] lock acquire error: {exc}")
        return False


def _release_upgrade_lock() -> None:
    try:
        _UPGRADE_LOCK_PATH.unlink(missing_ok=True)
    except Exception:
        pass


def _verify_authenticode_signature(file_path: str) -> tuple[bool, str, str]:
    """升级前 Authenticode 校验（P0-05/P0-06 联动）。

    实现：PowerShell Get-AuthenticodeSignature（本机实测可靠；ctypes 直连
    WinVerifyTrust 在部分环境返回 0x57 参数错误，故弃用）。每次升级仅调用一次。

    返回 (trusted, reason, availability)：
    - valid（Valid）→ 继续升级
    - invalid（NotSigned / HashMismatch，篡改证据）→ 终止升级
    - unavailable（NotTrusted 证书未铺 / UnknownError 环境异常）→ 告警继续
    """
    try:
        literal = str(file_path).replace("'", "''")
        script = (
            "$s = Get-AuthenticodeSignature -LiteralPath '" + literal + "'; "
            "Write-Output $s.Status; "
            "if ($s.SignerCertificate) { Write-Output $s.SignerCertificate.Subject }"
        )
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        proc = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command",
             "[Console]::OutputEncoding = [System.Text.Encoding]::UTF8\n" + script],
            capture_output=True, text=True, timeout=90, creationflags=flags,
            encoding="utf-8", errors="replace",
        )
        lines = [line.strip() for line in (proc.stdout or "").splitlines() if line.strip()]
        status = lines[0] if lines else "UnknownError"
        subject = lines[1] if len(lines) > 1 else ""
        if status == "Valid":
            return True, f"signature valid: {subject}", "valid"
        if status in ("NotSigned", "HashMismatch"):
            return False, status, "invalid"
        return False, f"status={status}", "unavailable"
    except Exception as exc:
        return False, f"verify unavailable: {exc}", "unavailable"


def _migrate_to_onedir(new_version: str, expected_sha256: str, upgrade_id: str, from_version: str) -> None:
    """V1.6.0 存量终端一次性迁移：旧单文件布局 → onedir versions/current 新布局。

    下载 onedir-zip → SHA256 → 解包 temp → 校验内层 exe 签名 →
    分离进程运行新布局安装器（--install --migrate-from/--migrate-to），
    安装器负责停旧服务、更新 binPath 到 current\\Z-View.exe 并重启。
    """
    import hashlib
    import shutil
    import tempfile
    import zipfile

    log_tag = "[Upgrade]"
    platform_base = _platform_base().rstrip("/")
    url = f"{platform_base}/api/v1/agent/upgrade/download?version={new_version}"
    extract_root = Path(tempfile.gettempdir()) / f"zv-migrate-{new_version}"
    try:
        _upgrade_state_write("DOWNLOAD", upgrade_id=upgrade_id, to_version=new_version)
        zip_path = extract_root.parent / f"Z-View-{new_version}-onedir.zip"
        headers = _agent_headers()
        resp = requests.get(url, headers=headers, timeout=900, stream=True, verify=_agent_requests_verify())
        if resp.status_code != 200:
            raise RuntimeError(f"download http {resp.status_code}")
        with open(zip_path, "wb") as fh:
            for chunk in resp.iter_content(1024 * 256):
                fh.write(chunk)

        _upgrade_state_write("VERIFYING", upgrade_id=upgrade_id, to_version=new_version)
        digest = hashlib.sha256(zip_path.read_bytes()).hexdigest()
        if digest.lower() != expected_sha256.lower():
            raise RuntimeError("sha256 mismatch")
        if extract_root.exists():
            shutil.rmtree(extract_root, ignore_errors=True)
        extract_root.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(zip_path) as zf:
            zf.extractall(extract_root)
        new_exe = extract_root / "Z-View.exe"
        if not new_exe.exists():
            for sub in extract_root.iterdir():
                if sub.is_dir() and (sub / "Z-View.exe").exists():
                    extract_root = sub
                    new_exe = sub / "Z-View.exe"
                    break
        if not new_exe.exists():
            raise RuntimeError("package missing Z-View.exe")

        trusted, reason, availability = _verify_authenticode_signature(str(new_exe))
        if availability == "invalid":
            raise RuntimeError(f"signature invalid: {reason}")
        if availability == "unavailable":
            print(f"{log_tag} 签名校验基础设施不可用（{reason}），继续迁移")

        _upgrade_state_write("MIGRATING", upgrade_id=upgrade_id,
                             from_version=from_version, to_version=new_version)
        flags = subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP
        subprocess.Popen([
            str(new_exe), "--install", "--quiet",
            "--server-url", platform_base,
            "--migrate-from", from_version,
            "--migrate-to", new_version,
        ], creationflags=flags, close_fds=True, cwd=str(extract_root))
        print(f"{log_tag} migration installer launched; service will switch to current\\Z-View.exe")
    except Exception as exc:
        print(f"{log_tag} migration failed: {exc}")
        _record_upgrade_failure(new_version, f"migration: {exc}")
        _upgrade_state_write("FAILED", failure_reason=f"migration: {exc}")


def perform_self_upgrade(new_version: str, expected_sha256: str) -> None:
    """升级状态机（P0-06）：

    CHECK → DOWNLOAD → VERIFY_HASH → VERIFY_SIGNATURE → BACKUP → INSTALL(bat)。
    bat 负责 INSTALL 之后的部分：START → HEALTH_CHECK → COMMIT / ROLLBACK / FAILED，
    终态写入 upgrade-bat-result.txt，由升级后的新进程启动时消费。
    失败自动回滚；服务起不来时尝试自愈重建服务注册（360 事件教训）。
    """
    import hashlib
    import subprocess
    import tempfile

    log_tag = "[Upgrade]"
    from_version = AGENT_VERSION
    upgrade_id = f"{from_version}->{new_version}@{time.strftime('%Y%m%d%H%M%S')}"
    _UPGRADE_STATE["upgrade_id"] = upgrade_id
    _UPGRADE_STATE["to_version"] = new_version
    try:
        _UPGRADE_STATE["in_progress"] = True
        _upgrade_state_write("CHECK", upgrade_id=upgrade_id,
                             from_version=from_version, to_version=new_version)
        print(f"{log_tag} [{upgrade_id}] 开始升级")

        # V1.6.0 新布局（versions + current junction）：委托独立 ZViewUpdater 执行
        # 下载/校验/staging/切换/健康检查/回滚，Agent 不再自己替换自己。
        package_type = str(_UPGRADE_STATE.get("package_type") or "")
        try:
            import zvagent.layout as _layout
        except Exception:
            _layout = None
        if _layout is not None and _layout.is_v2_layout():
            updater_exe = _layout.UPDATER_DIR / "ZViewUpdater.exe"
            if not updater_exe.exists():
                _upgrade_state_write("FAILED", failure_reason="updater missing for v2 layout")
                _UPGRADE_STATE["in_progress"] = False
                return
            task = {
                "server_url": _platform_base(),
                "token": str(CONFIG.get("token") or ""),
                "target_version": new_version,
                "sha256": expected_sha256,
                "from_version": from_version,
                "upgrade_id": upgrade_id,
                # V1.7.0 协议补全：服务端事务 ID 传给 updater，贯穿全部状态写
                "server_upgrade_id": str(_UPGRADE_STATE.get("server_upgrade_id") or ""),
            }
            _layout.UPGRADE_TASK_FILE.parent.mkdir(parents=True, exist_ok=True)
            _layout.UPGRADE_TASK_FILE.write_text(
                json.dumps(task, ensure_ascii=False, indent=2), encoding="utf-8")
            flags = subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP
            subprocess.Popen([str(updater_exe)], creationflags=flags, close_fds=True)
            print(f"{log_tag} delegated to ZViewUpdater; state tracked in upgrade-state.json")
            _UPGRADE_STATE["in_progress"] = False
            return

        # V1.6.0 迁移：旧单文件布局收到 onedir-zip 包 → 一次性迁移到新布局
        if _layout is not None and package_type == "onedir-zip":
            _migrate_to_onedir(new_version, expected_sha256, upgrade_id, from_version)
            _UPGRADE_STATE["in_progress"] = False
            return

        if not _acquire_upgrade_lock(upgrade_id):
            print(f"{log_tag} 另一个升级持有锁，跳过本次触发")
            _UPGRADE_STATE["in_progress"] = False
            return

        base = _platform_base().rstrip("/")
        headers = _agent_headers()
        url = f"{base}/api/v1/agent/upgrade/download?version={new_version}"

        # DOWNLOAD
        _upgrade_state_write("DOWNLOAD", upgrade_id=upgrade_id)
        resp = requests.get(url, headers=headers, timeout=300, stream=True, verify=_agent_requests_verify())
        if resp.status_code != 200:
            print(f"{log_tag} 下载失败 HTTP {resp.status_code}")
            _record_upgrade_failure(new_version, f"download http {resp.status_code}")
            _release_upgrade_lock()
            _upgrade_state_write("FAILED", failure_reason=f"download http {resp.status_code}")
            _UPGRADE_STATE["in_progress"] = False
            return
        new_exe = os.path.join(tempfile.gettempdir(), f"Z-View-{new_version}.exe")
        with open(new_exe, "wb") as fh:
            for chunk in resp.iter_content(1024 * 256):
                fh.write(chunk)

        # VERIFY_HASH
        _upgrade_state_write("VERIFY_HASH", upgrade_id=upgrade_id)
        digest = hashlib.sha256(open(new_exe, "rb").read()).hexdigest()
        if digest.lower() != expected_sha256.lower():
            print(f"{log_tag} SHA256 校验失败: {digest} != {expected_sha256}")
            os.remove(new_exe)
            _record_upgrade_failure(new_version, "sha256 mismatch")
            _release_upgrade_lock()
            _upgrade_state_write("FAILED", failure_reason="sha256 mismatch")
            _UPGRADE_STATE["in_progress"] = False
            return

        # VERIFY_SIGNATURE：确证签名无效 → 终止；基础设施不可用 → 告警继续
        _upgrade_state_write("VERIFY_SIGNATURE", upgrade_id=upgrade_id)
        trusted, reason, availability = _verify_authenticode_signature(new_exe)
        if availability == "invalid":
            print(f"{log_tag} 签名校验失败，终止升级: {reason}")
            os.remove(new_exe)
            _record_upgrade_failure(new_version, f"signature invalid: {reason}")
            _release_upgrade_lock()
            _upgrade_state_write("FAILED", failure_reason=f"signature invalid: {reason}")
            _UPGRADE_STATE["in_progress"] = False
            return
        if availability == "unavailable":
            print(f"{log_tag} 警告：签名校验基础设施不可用（{reason}），继续升级")

        # BACKUP
        _upgrade_state_write("BACKUP", upgrade_id=upgrade_id)
        target_exe = os.path.abspath(sys.argv[0]) if sys.argv and sys.argv[0] else r"C:\Program Files\CMDB-Agent\Z-View.exe"
        backup_exe = target_exe + ".upgrade-old"
        try:
            if os.path.exists(target_exe):
                if os.path.exists(backup_exe):
                    os.remove(backup_exe)
                import shutil
                shutil.copy2(target_exe, backup_exe)
        except Exception as exc:
            print(f"{log_tag} 备份失败（继续）: {exc}")

        # INSTALL： bat 负责 net stop → 替换 → net start → 健康检查 → COMMIT/ROLLBACK
        _upgrade_state_write("INSTALL", upgrade_id=upgrade_id)
        service_name = "CMDB-Agent"
        service_bin = f'"{target_exe}" --service-host'
        bat_path = os.path.join(tempfile.gettempdir(), "zv-agent-upgrade.bat")
        marker = str(_UPGRADE_BAT_RESULT_PATH)
        with open(bat_path, "w", encoding="gbk", errors="replace") as fh:
            fh.write("@echo off\r\n")
            fh.write("timeout /t 3 /nobreak >nul\r\n")
            fh.write(f"net stop {service_name} >nul 2>&1\r\n")
            fh.write("timeout /t 2 /nobreak >nul\r\n")
            fh.write("taskkill /F /IM Z-View.exe >nul 2>&1\r\n")
            fh.write("timeout /t 2 /nobreak >nul\r\n")
            fh.write(f'copy /Y "{new_exe}" "{target_exe}" >nul 2>&1\r\n')
            fh.write("if errorlevel 1 goto rollback\r\n")
            fh.write(f"net start {service_name} >nul 2>&1\r\n")
            # HEALTH_CHECK：轮询服务 RUNNING 最多 60s
            fh.write("set /a ZV_TRIES=0\r\n")
            fh.write(":waitstart\r\n")
            fh.write(f'sc query {service_name} | find "RUNNING" >nul 2>&1\r\n')
            fh.write("if not errorlevel 1 goto commit\r\n")
            fh.write("set /a ZV_TRIES+=1\r\n")
            fh.write("if %ZV_TRIES% geq 12 goto starthealthfail\r\n")
            fh.write("timeout /t 5 /nobreak >nul\r\n")
            fh.write("goto waitstart\r\n")
            fh.write(":commit\r\n")
            fh.write(f'echo COMMIT> "{marker}"\r\n')
            fh.write(f'del "{_UPGRADE_LOCK_PATH}" >nul 2>&1\r\n')
            fh.write(f'del "{new_exe}" >nul 2>&1\r\n')
            fh.write('del "%~f0" >nul 2>&1\r\n')
            fh.write("exit /b 0\r\n")
            # ROLLBACK：先回滚旧版，再救服务
            fh.write(":rollback\r\n")
            fh.write(f'echo ROLLBACK_OK> "{marker}"\r\n')
            fh.write(f'del "{_UPGRADE_LOCK_PATH}" >nul 2>&1\r\n')
            fh.write(f'copy /Y "{backup_exe}" "{target_exe}" >nul 2>&1\r\n')
            fh.write(f"net start {service_name} >nul 2>&1\r\n")
            fh.write("set /a ZV_TRIES2=0\r\n")
            fh.write(":waitstart2\r\n")
            fh.write(f'sc query {service_name} | find "RUNNING" >nul 2>&1\r\n')
            fh.write("if not errorlevel 1 goto done\r\n")
            fh.write("set /a ZV_TRIES2+=1\r\n")
            fh.write("if %ZV_TRIES2% geq 6 goto serviceheal\r\n")
            fh.write("timeout /t 5 /nobreak >nul\r\n")
            fh.write("goto waitstart2\r\n")
            # 自愈：服务注册被破坏时重建（360 事件教训）
            fh.write(":serviceheal\r\n")
            fh.write(f'sc delete {service_name} >nul 2>&1\r\n')
            fh.write(f'sc create {service_name} binPath= "{service_bin}" start= auto obj= LocalSystem >nul 2>&1\r\n')
            fh.write(f"net start {service_name} >nul 2>&1\r\n")
            fh.write(":done\r\n")
            fh.write('del "%~f0" >nul 2>&1\r\n')
            fh.write("exit /b 1\r\n")

        flags = subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS
        subprocess.Popen(
            ["cmd", "/c", bat_path],
            creationflags=flags,
            close_fds=True,
        )
        print(f"{log_tag} 升级脚本已启动，本进程即将退出，等待服务自动恢复为新版本 {new_version}")
        time.sleep(1.5)
        os._exit(0)
    except Exception as exc:
        print(f"{log_tag} 升级失败: {exc}")
        _record_upgrade_failure(new_version, str(exc))
        _release_upgrade_lock()
        _upgrade_state_write("FAILED", failure_reason=str(exc))
        _UPGRADE_STATE["in_progress"] = False


def _consume_upgrade_result_after_start() -> None:
    """升级后新进程启动时消费 bat 终态标记（P0-06 健康检查闭环）。"""
    try:
        if not _UPGRADE_BAT_RESULT_PATH.exists():
            return
        marker = _UPGRADE_BAT_RESULT_PATH.read_text(encoding="utf-8", errors="replace").strip().splitlines()
        result = marker[0].strip() if marker else ""
        state = {}
        if _UPGRADE_STATE_PATH.exists():
            try:
                state = json.loads(_UPGRADE_STATE_PATH.read_text(encoding="utf-8"))
            except Exception:
                state = {}
        upgrade_id = state.get("upgrade_id", "unknown")
        to_version = str(state.get("to_version") or "")
        if result == "COMMIT":
            print(f"[Upgrade] 升级成功 (COMMIT): {upgrade_id}")
            state["stage"] = "COMMIT"
            _UPGRADE_STATE_PATH.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
            if to_version:
                _clear_upgrade_failure(to_version)
        elif result.startswith("ROLLBACK"):
            print(f"[Upgrade] 升级失败已回滚 ({result}): {upgrade_id}")
            state["stage"] = "ROLLBACK"
            state["failure_reason"] = "service health check failed after install"
            _UPGRADE_STATE_PATH.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
            if to_version:
                _record_upgrade_failure(to_version, "rollback after install")
        else:
            print(f"[Upgrade] 升级终态未知 ({result or 'empty'}): {upgrade_id}")
        _release_upgrade_lock()
        _UPGRADE_BAT_RESULT_PATH.unlink(missing_ok=True)
    except Exception as exc:
        print(f"[Upgrade] consume upgrade result failed: {exc}")


def _get_last_upgrade_state() -> Optional[Dict[str, Any]]:
    """心跳上报用：读取最近一次升级状态（存在且为终态时）。"""
    try:
        if _UPGRADE_STATE_PATH.exists():
            state = json.loads(_UPGRADE_STATE_PATH.read_text(encoding="utf-8"))
            # COMMITTED = updater 成功写法；COMMIT = bat 流成功写法（两者等价）
            if state.get("stage") in ("COMMIT", "COMMITTED", "ROLLBACK", "FAILED"):
                return state
    except Exception:
        pass
    return None



