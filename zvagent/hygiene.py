# -*- coding: utf-8 -*-
"""Agent 环境卫生清理（V1.8.0 迁入自 cmdb_agent_unified_v2.py）。

职责：临时文件/进程残留的周期清理，与业务逻辑无耦合。
- cleanup_stale_mei_dirs: PyInstaller onefile _MEI* 泄漏目录自清。
"""
from __future__ import annotations

import os
import sys
import time
from typing import Callable, Optional


def cleanup_stale_mei_dirs(
    max_age_minutes: int = 30,
    own_meipass: Optional[str] = None,
    log: Optional[Callable[[str, str], None]] = None,
) -> int:
    """自清前代已死进程遗留的 _MEI* 临时解压目录（2026-09-09 事故修复）。

    onefile 进程每次启动解压 ~140MB 到 %TEMP%\\_MEI<rand>，正常退出时
    bootloader 自删；被杀/崩溃/子进程仍存活时泄漏。升级风暴曾在 1.5h 内
    泄漏 320 个目录（44GB）撑满 C 盘。每个进程启动时清理前代残留，
    使堆积始终被钳制在"最近一轮"量级；存活进程的在用目录受文件锁
    保护，删除失败静默跳过。

    own_meipass: 当前进程的 sys._MEIPASS（跳过在用目录）；
    log: 可选回调 (event, message)，由调用方注入运行日志通道。
    """
    if os.name != "nt" or not getattr(sys, "frozen", False):
        return 0
    import tempfile

    own_meipass = str(own_meipass or "")
    temp_root = tempfile.gettempdir()
    cutoff = time.time() - max_age_minutes * 60
    removed = 0
    try:
        entries = list(os.scandir(temp_root))
    except Exception:
        return 0
    for entry in entries:
        name = entry.name
        if not (name.startswith("_MEI") and name[4:].isdigit()):
            continue
        try:
            if own_meipass and os.path.samefile(entry.path, own_meipass):
                continue
        except Exception:
            continue
        try:
            if entry.stat().st_mtime >= cutoff:
                continue
            import shutil

            shutil.rmtree(entry.path, ignore_errors=True)
            removed += 1
        except Exception:
            continue
    if removed and log:
        log(
            "MEICleanup",
            f"removed {removed} stale _MEI dirs (age>{max_age_minutes}min)",
        )
    return removed
