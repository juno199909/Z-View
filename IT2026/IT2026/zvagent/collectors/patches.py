# -*- coding: utf-8 -*-
"""Windows Update 补丁状态采集（P1 Patch Management Phase 1，V1.9.x）。

基于 Windows Update COM 接口查询待安装补丁（IsInstalled=0），不自建补丁服务器。
WU Online 搜索可能耗时 10~60s，采集结果按 TTL 缓存；扫描在调用线程执行，
由上报循环的专用线程承载（不阻塞心跳/远控）。
"""
from __future__ import annotations

import json
import re
import subprocess
import threading
import time
from typing import Any, Optional

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
