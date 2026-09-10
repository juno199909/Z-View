# -*- coding: utf-8 -*-
r"""ZViewUpdater —— 独立升级器（V1.6.0）。

职责单一：下载 → 校验 → staging → 停服务 → 切换 → 起服务 → 健康检查 → COMMIT/ROLLBACK。
不做：心跳、远控、安全、软件分发。由 Agent 在收到升级指令时以任务文件方式调用：
    ProgramData\CMDB-Agent\runtime\upgrade-task.json
    {"server_url", "token", "target_version", "sha256", "download_path"}

状态机（写入 runtime\upgrade-state.json，服务端经心跳终态感知）：
    CHECKING → DOWNLOADING → VERIFYING → STAGED → STOPPING → SWITCHING
    → STARTING → HEALTH_CHECK → COMMITTED | ROLLBACK

构建（stdlib only）：
    python -m PyInstaller --onefile --name ZViewUpdater updater/updater.py
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import ssl
import subprocess
import sys
import tempfile
import time
import urllib.request
import zipfile
from pathlib import Path

SERVICE_NAME = "CMDB-Agent"
HEALTH_TIMEOUT_SECONDS = 180
HEALTH_POLL_SECONDS = 5
DOWNLOAD_TIMEOUT_SECONDS = 900

CREATION_FLAGS = 0x08000000  # CREATE_NO_WINDOW

STATE_PATH = Path(os.environ.get("ProgramData", ".")) / "CMDB-Agent" / "runtime" / "upgrade-state.json"
TASK_PATH = Path(os.environ.get("ProgramData", ".")) / "CMDB-Agent" / "runtime" / "upgrade-task.json"
LOCK_PATH = Path(os.environ.get("ProgramData", ".")) / "CMDB-Agent" / "runtime" / "upgrade.lock"
HEALTH_PATH = Path(os.environ.get("ProgramData", ".")) / "CMDB-Agent" / "runtime" / "upgrade-health.json"

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import zvagent.layout as layout  # noqa: E402


def state_write(stage: str, **extra) -> None:
    try:
        state = {}
        if STATE_PATH.exists():
            try:
                state = json.loads(STATE_PATH.read_text(encoding="utf-8"))
            except Exception:
                state = {}
        state.update({"stage": stage, "updated_at": time.strftime("%Y-%m-%d %H:%M:%S")})
        state.update(extra)
        STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
        STATE_PATH.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as exc:
        print(f"[Updater] state write failed: {exc}")


def log(message: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] [Updater] {message}", flush=True)


def run_hidden(cmd: list[str], timeout: int = 90) -> int:
    try:
        return subprocess.run(cmd, capture_output=True, text=True,
                              creationflags=CREATION_FLAGS, timeout=timeout).returncode
    except Exception:
        return -1


def _ssl_context():
    """HTTPS 下载证书上下文：优先平台下发的 ca-bundle（自签名），缺失时降级不校验。"""
    ca = Path(os.environ.get("ProgramData", ".")) / "CMDB-Agent" / "runtime" / "ca-bundle.pem"
    try:
        if ca.exists() and ca.stat().st_size > 0:
            return ssl.create_default_context(cafile=str(ca))
    except Exception:
        pass
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx


def download(url: str, token: str, dest: Path) -> bool:
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
    with urllib.request.urlopen(req, timeout=DOWNLOAD_TIMEOUT_SECONDS, context=_ssl_context()) as resp, open(dest, "wb") as fh:
        while True:
            chunk = resp.read(1024 * 256)
            if not chunk:
                break
            fh.write(chunk)
    return dest.exists() and dest.stat().st_size > 0


def verify_zip(zip_path: Path, expected_sha: str, expected_version: str) -> bool:
    digest = hashlib.sha256(zip_path.read_bytes()).hexdigest()
    if digest.lower() != expected_sha.lower():
        log(f"sha256 mismatch: {digest} != {expected_sha}")
        return False
    with zipfile.ZipFile(zip_path) as zf:
        names = zf.namelist()
        root_files = {n.split("/")[0] + ("/" if "/" in n else "") for n in names}
        has_exe = any(n in ("Z-View.exe",) or n.endswith("/Z-View.exe") for n in names)
        if not has_exe:
            log("package missing Z-View.exe")
            return False
    _ = root_files, expected_version
    return True


def extract_staged(zip_path: Path, version: str) -> bool:
    stage_root = layout.STAGING_DIR
    stage_root.mkdir(parents=True, exist_ok=True)
    extract_dir = stage_root / f".extract-{version}"
    if extract_dir.exists():
        shutil.rmtree(extract_dir, ignore_errors=True)
    with zipfile.ZipFile(zip_path) as zf:
        zf.extractall(extract_dir)
    # 兼容 zip 内含单层根目录的情况
    exe = extract_dir / "Z-View.exe"
    if not exe.exists():
        subdirs = [d for d in extract_dir.iterdir() if d.is_dir()]
        for sub in subdirs:
            if (sub / "Z-View.exe").exists():
                extract_dir = sub
                break
    if not (extract_dir / "Z-View.exe").exists():
        log("extracted layout invalid")
        shutil.rmtree(extract_dir, ignore_errors=True)
        return False
    final_stage = stage_root / version
    if final_stage.exists():
        shutil.rmtree(final_stage, ignore_errors=True)
    extract_dir.rename(final_stage)
    version_note = final_stage / "version.txt"
    if not version_note.exists():
        version_note.write_text(version, encoding="utf-8")
    return True


def wait_service_started(timeout_seconds: int = 90) -> bool:
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        if layout.service_query_running():
            return True
        time.sleep(5)
    return False


def wait_health(version: str, timeout_seconds: int = HEALTH_TIMEOUT_SECONDS) -> bool:
    """健康检查：新版本 Agent 心跳成功（upgrade-health 版本匹配且 heartbeat 为真）。"""
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        health = layout.read_upgrade_health()
        if health and health.get("version") == version and health.get("heartbeat"):
            return True
        time.sleep(HEALTH_POLL_SECONDS)
    return False


def rollback(previous_version: str, target_version: str, reason: str) -> None:
    log(f"ROLLBACK: {reason}; restore {previous_version}")
    layout.service_stop(SERVICE_NAME)
    layout.flip_current_junction(previous_version)
    layout.service_start(SERVICE_NAME)
    state_write("ROLLBACK", failure_reason=reason,
                from_version=previous_version, to_version=target_version,
                rollback_to=previous_version)


def self_update_swap() -> None:
    """启动时换入上一轮升级暂存的 .new（运行中的 exe 可被重命名）。"""
    try:
        self_exe = Path(sys.executable) if getattr(sys, "frozen", False) else None
        if self_exe is None or not self_exe.exists():
            return
        new_file = self_exe.with_suffix(".exe.new")
        if not new_file.is_file():
            return
        old_file = self_exe.with_suffix(".exe.old")
        if old_file.exists():
            old_file.unlink()
        self_exe.rename(old_file)
        new_file.rename(self_exe)
        log("updater self-update swapped; new version active from next run")
    except Exception as exc:
        log(f"self-update swap skipped: {exc}")


def stage_self_update(target_version: str) -> None:
    """COMMIT 后把新版本目录里的 updater 暂存为 .new，下次运行换入。"""
    try:
        version_dir_updater = layout.version_dir(target_version) / "updater" / "ZViewUpdater.exe"
        self_exe = Path(sys.executable) if getattr(sys, "frozen", False) else None
        if self_exe is None or not self_exe.exists() or not version_dir_updater.is_file():
            return
        if version_dir_updater.read_bytes() == self_exe.read_bytes():
            return
        shutil.copyfile(version_dir_updater, self_exe.with_suffix(".exe.new"))
        log("updater .new staged; swapped on next run")
    except Exception as exc:
        log(f"self-update staging skipped: {exc}")


def main() -> int:
    self_update_swap()
    if not TASK_PATH.exists():
        log("no upgrade task file; exit")
        return 0
    try:
        task = json.loads(TASK_PATH.read_text(encoding="utf-8"))
    except Exception as exc:
        log(f"task read failed: {exc}")
        return 1

    target_version = str(task.get("target_version") or "")
    expected_sha = str(task.get("sha256") or "")
    server_url = str(task.get("server_url") or "").rstrip("/")
    token = str(task.get("token") or "")
    previous_version = str(task.get("from_version") or layout.active_version() or "")

    # V1.6.0 修复：任务 token 为空时回退到设备凭据（迁移安装器曾把
    # config.local.json 写成 server_url-only，CONFIG token 丢失 → 下载 401）
    if not token:
        try:
            cred_path = layout.PROGRAM_DATA_ROOT / "runtime" / "agent-credentials.json"
            cred = json.loads(cred_path.read_text(encoding="utf-8"))
            token = f"zv1:{cred.get('agent_id')}:{cred.get('device_secret')}"
            log("task token empty; using device credential")
        except Exception:
            pass

    if not target_version or not expected_sha or not server_url:
        log("task incomplete")
        return 1

    # 任务开始前清掉旧健康标记，避免读到上一轮的健康数据
    try:
        HEALTH_PATH.unlink(missing_ok=True)
    except Exception:
        pass

    try:
        state_write("CHECKING", to_version=target_version, from_version=previous_version,
                    upgrade_id=task.get("upgrade_id") or "")
        if previous_version == target_version:
            log("already on target version; treat as COMMIT")
            state_write("COMMITTED", to_version=target_version)
            return 0

        state_write("DOWNLOADING", to_version=target_version)
        zip_dest = layout.STAGING_DIR / f"{target_version}.zip"
        zip_dest.parent.mkdir(parents=True, exist_ok=True)
        if zip_dest.exists():
            zip_dest.unlink()
        url = f"{server_url}/api/v1/agent/upgrade/download?version={target_version}"
        if not download(url, token, zip_dest):
            raise RuntimeError("download failed")

        state_write("VERIFYING", to_version=target_version)
        if not verify_zip(zip_dest, expected_sha, target_version):
            raise RuntimeError("verify failed (sha256 or package layout)")

        state_write("STAGED", to_version=target_version)
        if not extract_staged(zip_dest, target_version):
            raise RuntimeError("staging failed")
        zip_dest.unlink(missing_ok=True)

        state_write("STOPPING", to_version=target_version)
        layout.service_stop(SERVICE_NAME)
        # V1.6.0 加固（2026-09-10 2213 迁移事故）：net stop 可能因角色进程
        # 未退出而无限挂起——强制清掉全部 Agent 进程，确保服务真正停止。
        run_hidden(["taskkill", "/F", "/IM", "Z-View.exe"])
        time.sleep(2)

        state_write("SWITCHING", to_version=target_version)
        if not layout.install_version_from_staging(target_version):
            raise RuntimeError("promote staged version failed")

        previous_active = previous_version
        if not layout.flip_current_junction(target_version):
            raise RuntimeError("junction switch failed")

        state_write("STARTING", to_version=target_version)
        layout.service_start(SERVICE_NAME)
        if not wait_service_started():
            raise RuntimeError("service did not reach RUNNING")

        state_write("HEALTH_CHECK", to_version=target_version)
        if not wait_health(target_version):
            raise RuntimeError("health check failed (no heartbeat from new version)")

        log(f"upgrade COMMIT: {previous_active} -> {target_version}")
        state_write("COMMITTED", from_version=previous_active, to_version=target_version)
        removed = layout.prune_old_versions(keep=2, current=target_version)
        if removed:
            log(f"pruned old versions: {removed}")
        return 0

    except Exception as exc:
        log(f"upgrade failed: {exc}")
        try:
            rollback(previous_version, target_version, str(exc))
        except Exception as rb_exc:
            log(f"rollback failed: {rb_exc}")
            state_write("FAILED", failure_reason=f"{exc}; rollback error: {rb_exc}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
