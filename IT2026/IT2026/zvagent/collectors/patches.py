# -*- coding: utf-8 -*-
"""Windows Update 补丁采集/诊断/安装（P1 Patch Management，V1.9.x）。

基于 Windows Update COM 接口，不自建补丁服务器：
- collect_windows_update_status: 查询待安装补丁（IsInstalled=0），TTL 缓存；
- wu_diagnose: WU 服务/策略/连通性诊断（任务通道 wu_diag）；
- wu_install: 安装待安装补丁（任务通道 wu_install，异步 + deferred 结果）。
"""
from __future__ import annotations

import json
import re
import subprocess
import threading
import time
from typing import Optional

from console_utils import safe_console_print

_PS_SCRIPT = r"""
$ErrorActionPreference = 'Stop'
$out = [ordered]@{
    last_scan = (Get-Date -Format 'yyyy-MM-dd HH:mm:ss')
    pending   = @()
    reboot_required = $false
}
try {
    $session = New-Object -ComObject Microsoft.Update.Session
    $searcher = $session.CreateUpdateSearcher()
    $result = $searcher.Search("IsInstalled=0 and IsHidden=0")
    $reboot = $false
    foreach ($u in $result.Updates) {
        if ($u.InstallationBehavior.RebootBehavior -ge 1) { $reboot = $true }
        $kb = $null
        foreach ($id in $u.KBArticleIDs) { $kb = "KB$id"; break }
        $out.pending += [ordered]@{
            kb             = $kb
            title          = $u.Title
            severity       = [string]$u.MsrcSeverity
            size_mb        = [math]::Round($u.MaxDownloadSize / 1MB, 1)
            reboot_required = ($u.InstallationBehavior.RebootBehavior -ge 1)
        }
    }
    $out.reboot_required = $reboot
    $out.pending_count = @($out.pending).Count
} catch {
    $out.error = $_.Exception.Message
}
$out | ConvertTo-Json -Depth 4
"""

_LAST_RESULT: Optional[dict] = None
_LAST_SCAN_TS = 0.0
_CACHE_LOCK = threading.Lock()
_CACHE_TTL_SECONDS = 3600  # WU 搜索代价高，1 小时内复用
_PS_TIMEOUT_SECONDS = 420  # WU online 搜索可能较慢


def _parse_output(stdout: str) -> dict:
    """解析 PowerShell JSON 输出（单条补丁时 ConvertTo-Json 不产生数组，需归一）。"""
    data = json.loads(stdout)
    pending = data.get("pending") or []
    if isinstance(pending, dict):
        pending = [pending]
    data["pending"] = pending
    data["pending_count"] = len(pending)
    return data


def collect_windows_update_status(force: bool = False) -> dict:
    """查询 Windows Update 待安装补丁状态（带 TTL 缓存）。

    返回: {last_scan, pending: [{kb,title,severity,size_mb,reboot_required}],
           pending_count, reboot_required, error?}
    """
    global _LAST_RESULT, _LAST_SCAN_TS
    with _CACHE_LOCK:
        if not force and _LAST_RESULT is not None and time.time() - _LAST_SCAN_TS < _CACHE_TTL_SECONDS:
            return _LAST_RESULT
        try:
            completed = subprocess.run(
                ["powershell", "-NoProfile", "-NonInteractive", "-Command", _PS_SCRIPT],
                capture_output=True, text=True, timeout=_PS_TIMEOUT_SECONDS,
                encoding="utf-8", errors="replace",
            )
            if completed.returncode != 0:
                raise RuntimeError(f"powershell exit {completed.returncode}: {completed.stderr[:300]}")
            data = _parse_output(completed.stdout)
            data.setdefault("pending", [])
            data["pending_count"] = len(data["pending"])
            _LAST_RESULT = data
            _LAST_SCAN_TS = time.time()
            return data
        except Exception as exc:
            safe_console_print(f"[Patches] WU 采集失败: {exc}")
            fallback = dict(_LAST_RESULT or {})
            fallback["error"] = f"{type(exc).__name__}: {exc}"
            fallback["last_scan"] = time.strftime("%Y-%m-%d %H:%M:%S")
            return fallback


def clear_cache() -> None:
    """强制下轮重新扫描（手动触发/安装完成后调用）。"""
    global _LAST_RESULT, _LAST_SCAN_TS
    with _CACHE_LOCK:
        _LAST_RESULT = None
        _LAST_SCAN_TS = 0.0


# =============================================================================
# V1.9.x Patch Management Phase 2：WU 诊断与安装
# =============================================================================

_WU_DIAG_PS = r"""
$ErrorActionPreference = 'Continue'
$out = [ordered]@{}
$svc = Get-Service wuauserv -ErrorAction SilentlyContinue
$out.wuauserv_status = if ($svc) { [string]$svc.Status } else { "NotFound" }
$out.wuauserv_starttype = (Get-ItemProperty "HKLM:\SYSTEM\CurrentControlSet\Services\wuauserv" -ErrorAction SilentlyContinue).Start
$au = Get-ItemProperty "HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\WindowsUpdate\Auto Update" -ErrorAction SilentlyContinue
$out.au_options = $au.AUOptions
$pol = "HKLM:\SOFTWARE\Policies\Microsoft\Windows\WindowsUpdate"
$polAu = "$pol\AU"
$out.wsus_wuserver = (Get-ItemProperty $pol -ErrorAction SilentlyContinue).WUServer
$out.wsus_usewuserver = (Get-ItemProperty $polAu -ErrorAction SilentlyContinue).UseWUServer
$out.do_not_connect_internet = (Get-ItemProperty $pol -ErrorAction SilentlyContinue).DoNotConnectToWindowsUpdateInternetLocations
try {
    $r = Invoke-WebRequest -UseBasicParsing -Uri "https://fe2.update.microsoft.com/v6/ClientWebService/client.asmx" -TimeoutSec 10
    $out.ms_update_reachable = $true
} catch {
    $out.ms_update_reachable = $false
    $out.ms_update_error = [string]$_.Exception.Message
}
try {
    $session = New-Object -ComObject Microsoft.Update.Session
    $searcher = $session.CreateUpdateSearcher()
    $result = $searcher.Search("IsInstalled=0 and IsHidden=0")
    $out.com_search_ok = $true
    $out.com_pending = @($result.Updates).Count
} catch {
    $out.com_search_ok = $false
    $out.com_search_error = [string]$_.Exception.Message
}
$out | ConvertTo-Json -Depth 3
"""


def wu_diagnose() -> dict:
    """WU 诊断：服务状态/策略配置/微软更新服务连通性/COM 搜索复现。"""
    try:
        completed = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", _WU_DIAG_PS],
            capture_output=True, text=True, timeout=180,
            encoding="utf-8", errors="replace",
        )
        if completed.returncode != 0:
            raise RuntimeError(f"powershell exit {completed.returncode}: {completed.stderr[:300]}")
        return json.loads(completed.stdout)
    except Exception as exc:
        return {"error": f"{type(exc).__name__}: {exc}"}


def wu_install(kb: Optional[str] = None) -> dict:
    """安装 WU 待安装补丁（默认全部；可按 KB 过滤）。

    下载+安装可能耗时数十分钟——调用方应使用异步任务模式
    （handler 返回 _async 标记，完成后 push_deferred_result 上报结果）。
    不自动重启（reboot_required 由调用方决策）。
    """
    kb_filter = f'($u.KBArticleIDs -contains "{kb}")' if kb else "$true"
    script = r"""
$ErrorActionPreference = 'Continue'
$out = [ordered]@{}
try {
    $session = New-Object -ComObject Microsoft.Update.Session
    $searcher = $session.CreateUpdateSearcher()
    $result = $searcher.Search("IsInstalled=0 and IsHidden=0")
    $coll = New-Object -ComObject Microsoft.Update.UpdateColl
    foreach ($u in $result.Updates) {
        if ($u.InstallationBehavior.CanRequestUserInput) { continue }
        KB_FILTER
        if ($u.EulaAccepted -eq $false) { $u.AcceptEula() }
        [void]$coll.Add($u)
    }
    $out.selected = @($coll).Count
    if (@($coll).Count -eq 0) {
        $out.note = "no installable updates matched"
    } else {
        $installer = $session.CreateUpdateInstaller()
        $installer.Updates = $coll
        $res = $installer.Install()
        $out.result_code = $res.ResultCode
        $out.hresult = $res.HResult
        $results = @()
        for ($i = 0; $i -lt $coll.Count; $i++) {
            $ur = $res.GetUpdateResult($i)
            $kb = $null
            foreach ($id in $coll.Item($i).KBArticleIDs) { $kb = "KB$id"; break }
            $results += [ordered]@{
                title = $coll.Item($i).Title
                kb = $kb
                result_code = $ur.ResultCode
                hresult = $ur.HResult
            }
        }
        $out.updates = $results
    }
} catch {
    $out.error = [string]$_.Exception.Message
}
$out | ConvertTo-Json -Depth 4
"""
    script = script.replace("KB_FILTER", f"if ({kb_filter}) {{ [void]$coll.Add($u) }}")
    try:
        completed = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
            capture_output=True, text=True, timeout=3600,
            encoding="utf-8", errors="replace",
        )
        if completed.returncode != 0 and not completed.stdout:
            raise RuntimeError(f"powershell exit {completed.returncode}: {completed.stderr[:300]}")
        return json.loads(completed.stdout)
    except Exception as exc:
        return {"error": f"{type(exc).__name__}: {exc}"}


# =============================================================================
# V1.9.x 通用任务通道：注册补丁管理任务 handlers（注册须在函数定义之后）
# =============================================================================

def _register_patch_job_handlers() -> None:
    try:
        from zvagent import jobs as _jobs

        def _wu_diag_handler(payload: dict) -> dict:
            safe_console_print("[V1910DBG] wu_diag handler entered")
            result = wu_diagnose()
            safe_console_print(f"[V1910DBG] wu_diag done: pending={result.get('com_pending')} err={result.get('com_search_error')}")
            return result
        _jobs.register_job_handler("wu_diag", _wu_diag_handler)

        def _wu_install_handler(payload: dict) -> dict:
            kb = payload.get("kb") or None
            job_id = payload.get("job_id") or ""

            def _worker():
                try:
                    result = wu_install(kb)
                    from zvagent.jobs import push_deferred_result
                    code = result.get("result_code")
                    ok = code in (2, 3) and not result.get("error")
                    push_deferred_result(job_id, "succeeded" if ok else "failed", result)
                except Exception as exc:
                    from zvagent.jobs import push_deferred_result
                    push_deferred_result(job_id, "failed", {"error": str(exc)})

            threading.Thread(target=_worker, name="agent-wu-install", daemon=True).start()
            return {"_async": True, "note": "WU install started in background thread"}

        _jobs.register_job_handler("wu_install", _wu_install_handler)
        _jobs.register_job_handler("wu_collect", lambda payload: collect_windows_update_status())
    except Exception as exc:
        safe_console_print(f"[Patches] job handlers registration failed: {exc}")


_register_patch_job_handlers()
