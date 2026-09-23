# -*- coding: utf-8 -*-
r"""V1.6.0 [V1.8.0 迁入 zvagent 包，cmdb_agent_layout.py 为兼容 shim] 版本目录与布局管理。

长期架构（用户定版）：
    Program Files\CMDB-Agent\
    ├── versions\<ver>\          onedir 程序目录（Z-View.exe + _internal）
    ├── current                  → junction，指向 versions\<active>
    └── updater\ZViewUpdater.exe 独立升级器（不随版本切换）
    ProgramData\CMDB-Agent\
    ├── config\config.local.json 配置（升级不碰）
    ├── runtime\...              运行状态（升级不碰）
    └── upgrade\staging\         升级暂存

设计要点：
- Windows 服务 binPath 永远指向 current\Z-View.exe，升级 = 翻转 junction，服务注册不变。
- current_version.json 记录 active 版本（可观测/健康检查用），机制本体是 junction。
- 所有根路径为模块常量，测试可 monkeypatch。
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
from pathlib import Path

SERVICE_NAME = "CMDB-Agent"

PROGRAM_FILES_ROOT = Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "CMDB-Agent"
PROGRAM_DATA_ROOT = Path(os.environ.get("ProgramData", ".")) / "CMDB-Agent"

VERSIONS_DIR = PROGRAM_FILES_ROOT / "versions"
CURRENT_LINK = PROGRAM_FILES_ROOT / "current"
UPDATER_DIR = PROGRAM_FILES_ROOT / "updater"
STAGING_DIR = PROGRAM_DATA_ROOT / "upgrade" / "staging"
CURRENT_VERSION_FILE = PROGRAM_DATA_ROOT / "runtime" / "current-version.json"
UPGRADE_HEALTH_FILE = PROGRAM_DATA_ROOT / "runtime" / "upgrade-health.json"
UPGRADE_TASK_FILE = PROGRAM_DATA_ROOT / "runtime" / "upgrade-task.json"
UPGRADE_LOCK_PATH = PROGRAM_DATA_ROOT / "runtime" / "upgrade.lock"
UPGRADE_STATE_PATH = PROGRAM_DATA_ROOT / "runtime" / "upgrade-state.json"
UPGRADE_BAT_RESULT_PATH = PROGRAM_DATA_ROOT / "runtime" / "upgrade-bat-result.txt"

CREATION_FLAGS = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def is_v2_layout() -> bool:
    """是否为 V1.6.0 新布局（versions + current junction 存在即认定）。"""
    return (VERSIONS_DIR.is_dir() and CURRENT_LINK.exists())


def active_version() -> str | None:
    """当前激活版本：优先 current-version.json，其次解析 junction 目标。"""
    try:
        data = json.loads(CURRENT_VERSION_FILE.read_text(encoding="utf-8"))
        version = str(data.get("active_version") or "").strip()
        if version:
            return version
    except Exception:
        pass
    try:
        target = os.readlink(str(CURRENT_LINK))
        return Path(target).name
    except Exception:
        return None


def write_current_version(version: str) -> None:
    CURRENT_VERSION_FILE.parent.mkdir(parents=True, exist_ok=True)
    CURRENT_VERSION_FILE.write_text(
        json.dumps({"active_version": version, "updated_at": time.strftime("%Y-%m-%d %H:%M:%S")},
                   ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def active_exe() -> Path:
    return CURRENT_LINK / "Z-View.exe"


def version_dir(version: str) -> Path:
    return VERSIONS_DIR / version


def _run_hidden(cmd: list[str], timeout: int = 60) -> int:
    try:
        return subprocess.run(cmd, capture_output=True, text=True,
                              creationflags=CREATION_FLAGS, timeout=timeout).returncode
    except Exception:
        return -1


def service_query_running(service_name: str = SERVICE_NAME) -> bool:
    """V1.9.51 语义修复：真正查询服务状态为 RUNNING。

    此前只看 sc query 退出码（服务存在即 0，STOPPED 也返回 0），
    "服务存在"被误当"服务运行中"。现解析输出中的 RUNNING 状态。
    """
    try:
        # 不用 text=True：中文 Windows sc 输出为 GBK，UTF-8 读线程会解码崩溃；
        # 只判 ASCII 的 "RUNNING"，errors=replace 忽略其余乱码
        proc = subprocess.run(["sc", "query", service_name], capture_output=True,
                              creationflags=CREATION_FLAGS, timeout=30)
        return "RUNNING" in (proc.stdout or b"").decode("utf-8", errors="replace")
    except Exception:
        return False


def service_stop(service_name: str = SERVICE_NAME, timeout: int = 60) -> None:
    _run_hidden(["net", "stop", service_name], timeout=timeout)


def service_start(service_name: str = SERVICE_NAME) -> int:
    return _run_hidden(["sc", "start", service_name])


def flip_current_junction(version: str) -> bool:
    r"""原子切换 current junction → versions\<version>。

    要求：切换前服务已停止（运行中的 exe 通过 junction 持有旧目标句柄）。
    """
    target = version_dir(version)
    exe = target / "Z-View.exe"
    if not exe.exists():
        print(f"[Layout] version dir invalid: {exe} missing")
        return False
    try:
        if CURRENT_LINK.exists() or CURRENT_LINK.is_symlink():
            # rmdir 只删除 junction 本身，不触碰目标目录
            rc = _run_hidden(["cmd", "/c", "rmdir", str(CURRENT_LINK)])
            if rc != 0:
                print(f"[Layout] rmdir current failed rc={rc}")
                return False
        rc = _run_hidden(["cmd", "/c", "mklink", "/J", str(CURRENT_LINK), str(target)])
        if rc != 0:
            print(f"[Layout] mklink /J failed rc={rc}")
            return False
        write_current_version(version)
        return True
    except Exception as exc:
        print(f"[Layout] flip junction failed: {exc}")
        return False


def install_version_from_staging(version: str) -> bool:
    r"""staging\<ver> → versions\<ver>（目标已存在则先移走备份）。"""
    staged = STAGING_DIR / version
    target = version_dir(version)
    if not staged.is_dir():
        print(f"[Layout] staging dir missing: {staged}")
        return False
    try:
        VERSIONS_DIR.mkdir(parents=True, exist_ok=True)
        if target.exists():
            backup = VERSIONS_DIR / f"{version}.old-{int(time.time())}"
            target.rename(backup)
        staged.rename(target)
        return True
    except Exception as exc:
        print(f"[Layout] promote staging failed: {exc}")
        return False


def _prune_entry(entry: Path, removed: list[str], protected: str = "") -> None:
    """删除单个版本目录（rmtree 幂等），成功后记录名称；保护名不删。"""
    if protected and entry.name == protected:
        return
    shutil.rmtree(entry, ignore_errors=True)
    if not entry.exists():
        removed.append(entry.name)


def prune_old_versions(keep: int = 2, current: str = "") -> list[str]:
    """保留当前 + 最近 keep-1 个历史版本目录，并清理 .old-* 升级备份残留。

    V1.9.51 修复：.old-<ts> 备份目录此前与真实目录一起按版本号排序参与
    保留窗口竞争，同一版本的多个备份互相顶替，导致 versions 目录下累积
    十余个 .old-* 目录。现按基础版本号分组处理：
    - 窗口外整组删除；
    - 窗口内有真实目录 → 备份全部清理（回滚走真实版本目录，备份无消费方）；
    - 窗口内仅剩备份 → 保留最新一个作为该版本唯一副本。
    返回被删除的目录名列表。
    """
    removed: list[str] = []
    try:
        groups: dict[tuple, list[Path]] = {}
        for entry in VERSIONS_DIR.iterdir():
            if not entry.is_dir() or entry.name.startswith("."):
                continue
            base = entry.name.split(".old-")[0]
            try:
                key = tuple(int(p) for p in base.split("."))
            except ValueError:
                continue
            groups.setdefault(key, []).append(entry)
        ordered = sorted(groups.items(), key=lambda item: item[0])
        keep_keys = {key for key, _ in ordered[-keep:]}
        for key, entries in ordered:
            if key not in keep_keys:
                for entry in entries:
                    _prune_entry(entry, removed, protected=current)
                continue
            real = [e for e in entries if ".old-" not in e.name]
            backups = sorted((e for e in entries if ".old-" in e.name), key=lambda e: e.name)
            if real:
                for entry in backups:
                    _prune_entry(entry, removed)
            else:
                for entry in backups[:-1]:
                    _prune_entry(entry, removed)
    except Exception as exc:
        print(f"[Layout] prune failed: {exc}")
    return removed


def write_upgrade_health(version: str, heartbeat_ok: bool) -> None:
    """Agent 首次心跳成功后写入健康标记，updater 据此判定 COMMIT/ROLLBACK。
    内容未变化时 120s 内跳过重写，避免每心跳周期刷盘。"""
    try:
        existing = read_upgrade_health()
        if (existing and existing.get("version") == version
                and bool(existing.get("heartbeat")) == bool(heartbeat_ok)
                and time.time() - float(existing.get("ts") or 0) < 120):
            return
        UPGRADE_HEALTH_FILE.parent.mkdir(parents=True, exist_ok=True)
        UPGRADE_HEALTH_FILE.write_text(json.dumps({
            "version": version,
            "heartbeat": bool(heartbeat_ok),
            "ts": time.time(),
        }, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass


def read_upgrade_health() -> dict | None:
    try:
        data = json.loads(UPGRADE_HEALTH_FILE.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except Exception:
        return None
