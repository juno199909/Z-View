# -*- coding: utf-8 -*-
"""磁盘缓存定期清理（P1-09）。

2026-09-09 事故背景：Agent 升级循环把 %TEMP% 撑到 44GB（PyInstaller _MEI*
泄漏），C 盘 0 字节剩余，导致 Agent 升级解压失败（no space left on device）
与后端临时文件写入失败。本模块由 data-retention 后台线程每轮调用，
按"路径 + 最大保留时长"规则清理缓存类目录，只删过期条目，
被占用的文件由操作系统锁保护、删除失败静默跳过。
"""
from __future__ import annotations

import os
import re
import shutil
import time
from pathlib import Path
from typing import Any, Dict, List

from console_utils import safe_console_print

_USER_PROFILE = Path(os.environ.get("USERPROFILE") or r"C:\Users\Administrator")
_LOCAL_APPDATA = Path(os.environ.get("LOCALAPPDATA") or (_USER_PROFILE / "AppData" / "Local"))

TEMP_DIR = Path(os.environ.get("TEMP") or (_LOCAL_APPDATA / "Temp"))
WINDOWS_TEMP = Path(r"C:\Windows\Temp")

# 平台 Agent 运行日志：append_runtime_log 每次写入都 open/close，
# 无内建轮转，事故时涨到 263MB
AGENT_RUNTIME_LOG = Path(r"C:\ProgramData\CMDB-Agent\logs\agent-runtime.log")
AGENT_RUNTIME_LOG_MAX_BYTES = 100 * 1024 * 1024
AGENT_RUNTIME_LOG_TAIL_LINES = 3000

# 升级包仓库（各 67MB，历史版本只保留最近几个）
AGENT_UPGRADE_DIR = Path(__file__).resolve().parent.parent / "agent_upgrade"
AGENT_UPGRADE_KEEP_VERSIONS = 3

# (目录, 常规保留小时数, 紧急保留小时数, 说明)
AGE_RULES: List[Dict[str, Any]] = [
    {"path": TEMP_DIR, "hours": 24, "hours_emergency": 1, "desc": "user Temp"},
    {"path": WINDOWS_TEMP, "hours": 24, "hours_emergency": 1, "desc": "windows Temp"},
    {"path": _LOCAL_APPDATA / "npm-cache", "hours": 24 * 7, "hours_emergency": 24, "desc": "npm cache"},
    {"path": _LOCAL_APPDATA / "pip" / "cache", "hours": 24 * 7, "hours_emergency": 24, "desc": "pip cache"},
]

# PyInstaller onefile 解压目录：进程正常退出会自清，崩溃/被杀即泄漏，
# 升级风暴时每 30s 泄漏 140MB，故用更短的保留窗口
MEI_MAX_AGE_HOURS = 6
MEI_MAX_AGE_HOURS_EMERGENCY = 0.5
_MEI_PATTERN = re.compile(r"^_MEI\d+$")

_VERSION_DIR_PATTERN = re.compile(r"^\d+(?:\.\d+)+$")


def _safe_remove(path: Path) -> bool:
    try:
        if path.is_dir():
            shutil.rmtree(path, ignore_errors=False)
        else:
            path.unlink()
        return True
    except Exception:
        return False


def _clean_dir_by_age(path: Path, cutoff_ts: float, results: Dict[str, Any]) -> None:
    if not path.is_dir():
        return
    removed = 0
    failed = 0
    try:
        entries = list(path.iterdir())
    except Exception:
        return
    for entry in entries:
        try:
            if entry.stat().st_mtime >= cutoff_ts:
                continue
        except Exception:
            continue
        if _safe_remove(entry):
            removed += 1
        else:
            failed += 1
    if removed or failed:
        results[str(path)] = {"removed": removed, "locked_or_failed": failed}


def _clean_mei_dirs(temp_dir: Path, cutoff_ts: float, results: Dict[str, Any]) -> None:
    if not temp_dir.is_dir():
        return
    removed = 0
    for entry in temp_dir.iterdir():
        if not _MEI_PATTERN.match(entry.name):
            continue
        try:
            if entry.stat().st_mtime >= cutoff_ts:
                continue
        except Exception:
            continue
        if _safe_remove(entry):
            removed += 1
    if removed:
        results["pyinstaller_mei_removed"] = removed


def _rotate_agent_runtime_log(results: Dict[str, Any]) -> None:
    log_path = AGENT_RUNTIME_LOG
    try:
        if not log_path.exists() or log_path.stat().st_size <= AGENT_RUNTIME_LOG_MAX_BYTES:
            return
        with open(log_path, "r", encoding="utf-8", errors="replace") as f:
            tail = f.readlines()[-AGENT_RUNTIME_LOG_TAIL_LINES:]
        tail_path = log_path.with_suffix(".log.tail")
        with open(tail_path, "w", encoding="utf-8", errors="replace") as f:
            f.writelines(tail)
        with open(log_path, "w", encoding="utf-8", errors="replace") as f:
            f.write(
                f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] [DiskCleanup] "
                f"runtime log rotated (>{AGENT_RUNTIME_LOG_MAX_BYTES // (1024 * 1024)}MB); "
                f"prior tail in {tail_path.name}\n"
            )
        results["agent_runtime_log_rotated_bytes"] = log_path.stat().st_size
    except Exception as exc:
        results["agent_runtime_log_rotate_error"] = str(exc)


def _prune_agent_upgrade_versions(results: Dict[str, Any]) -> None:
    root = AGENT_UPGRADE_DIR
    if not root.is_dir():
        return
    try:
        manifest_path = root / "manifest.json"
        import json

        latest_version = ""
        if manifest_path.exists():
            latest_version = str((json.loads(manifest_path.read_text(encoding="utf-8")) or {}).get("version") or "")
        version_dirs = []
        for entry in root.iterdir():
            if not entry.is_dir() or not _VERSION_DIR_PATTERN.match(entry.name):
                continue
            version_dirs.append((entry.name, entry))
        version_dirs.sort(key=lambda item: [int(p) for p in item[0].split(".")])
        excess = version_dirs[:-AGENT_UPGRADE_KEEP_VERSIONS] if len(version_dirs) > AGENT_UPGRADE_KEEP_VERSIONS else []
        removed = []
        for name, entry in excess:
            if name == latest_version:
                continue
            if _safe_remove(entry):
                removed.append(name)
        if removed:
            results["agent_upgrade_pruned"] = removed
    except Exception as exc:
        results["agent_upgrade_prune_error"] = str(exc)


def run_disk_cache_cleanup(emergency: bool = False) -> Dict[str, Any]:
    """清理平台所在机器的缓存目录，返回结果摘要（含释放字节估算）。

    emergency=True（磁盘水位守护触发）：使用更激进的保留窗口，
    立即回收空间而不等常规 24h/6h 窗口过期。
    """
    started = time.time()
    before = _free_bytes(TEMP_DIR.anchor or "C:\\")
    results: Dict[str, Any] = {}
    if emergency:
        results["mode"] = "emergency"

    now = time.time()
    for rule in AGE_RULES:
        hours = float(rule["hours_emergency"] if emergency else rule["hours"])
        _clean_dir_by_age(Path(rule["path"]), now - hours * 3600, results)

    mei_hours = MEI_MAX_AGE_HOURS_EMERGENCY if emergency else MEI_MAX_AGE_HOURS
    _clean_mei_dirs(TEMP_DIR, now - mei_hours * 3600, results)
    _rotate_agent_runtime_log(results)
    _prune_agent_upgrade_versions(results)

    after = _free_bytes(TEMP_DIR.anchor or "C:\\")
    if after is not None and before is not None:
        results["freed_bytes"] = max(0, after - before)
    results["elapsed_seconds"] = round(time.time() - started, 1)
    return results


def _free_bytes(root: str):
    try:
        usage = shutil.disk_usage(root)
        return usage.free
    except Exception:
        return None


def log_disk_cache_cleanup() -> None:
    """data-retention 线程调用：执行清理并打印摘要。"""
    try:
        results = run_disk_cache_cleanup()
        freed = results.pop("freed_bytes", 0) or 0
        if freed > 0 or any(v for k, v in results.items() if k != "elapsed_seconds"):
            safe_console_print(
                f"[DiskCleanup] freed={freed / (1024 * 1024):.1f}MB detail={results}"
            )
    except Exception as exc:
        safe_console_print(f"[DiskCleanup] error: {exc}")
