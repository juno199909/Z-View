# -*- coding: utf-8 -*-
"""V1.6.0 布局管理 + ZViewUpdater 升级/回滚 E2E 单测（全沙箱，服务操作 mock）。"""
import io
import json
import shutil
import sys
import tempfile
import time
import zipfile
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, r"D:\IT2026\IT2026\IT2026\IT2026")
import os
os.chdir(r"D:\IT2026\IT2026\IT2026\IT2026")

import cmdb_agent_layout as layout
import updater.updater as updater_mod

SANDBOX = Path(tempfile.mkdtemp(prefix="zv-v16-test-"))

# ---- 沙箱化 layout 路径 ----
layout.PROGRAM_FILES_ROOT = SANDBOX / "ProgramFiles" / "CMDB-Agent"
layout.PROGRAM_DATA_ROOT = SANDBOX / "ProgramData" / "CMDB-Agent"
layout.VERSIONS_DIR = layout.PROGRAM_FILES_ROOT / "versions"
layout.CURRENT_LINK = layout.PROGRAM_FILES_ROOT / "current"
layout.UPDATER_DIR = layout.PROGRAM_FILES_ROOT / "updater"
layout.STAGING_DIR = layout.PROGRAM_DATA_ROOT / "upgrade" / "staging"
layout.CURRENT_VERSION_FILE = layout.PROGRAM_DATA_ROOT / "runtime" / "current-version.json"
layout.UPGRADE_HEALTH_FILE = layout.PROGRAM_DATA_ROOT / "runtime" / "upgrade-health.json"
layout.UPGRADE_TASK_FILE = layout.PROGRAM_DATA_ROOT / "runtime" / "upgrade-task.json"

# ---- 沙箱化 updater 路径与行为 ----
updater_mod.STATE_PATH = layout.PROGRAM_DATA_ROOT / "runtime" / "upgrade-state.json"
updater_mod.TASK_PATH = layout.PROGRAM_DATA_ROOT / "runtime" / "upgrade-task.json"
updater_mod.LOCK_PATH = layout.PROGRAM_DATA_ROOT / "runtime" / "upgrade.lock"
updater_mod.HEALTH_PATH = layout.PROGRAM_DATA_ROOT / "runtime" / "upgrade-health.json"
updater_mod.HEALTH_TIMEOUT_SECONDS = 8
updater_mod.HEALTH_POLL_SECONDS = 1

SERVICE_CALLS = {"stop": 0, "start": 0, "running": True}
layout.service_stop = lambda name=layout.SERVICE_NAME, timeout=60: SERVICE_CALLS.__setitem__("stop", SERVICE_CALLS["stop"] + 1)
layout.service_start = lambda name=layout.SERVICE_NAME: SERVICE_CALLS.__setitem__("start", SERVICE_CALLS["start"] + 1) or 0
layout.service_query_running = lambda name=layout.SERVICE_NAME: SERVICE_CALLS["running"]


def make_dummy_version(version: str) -> None:
    d = layout.version_dir(version)
    d.mkdir(parents=True, exist_ok=True)
    (d / "Z-View.exe").write_bytes(b"dummy exe " + version.encode())
    internal = d / "_internal"
    internal.mkdir(exist_ok=True)
    (internal / "marker.dat").write_text(version)


def make_package_zip(version: str) -> Path:
    zip_path = SANDBOX / f"pkg-{version}.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr("Z-View.exe", "dummy exe " + version)
        zf.writestr("_internal/marker.dat", version)
        zf.writestr("version.txt", version)
        zf.writestr("updater/ZViewUpdater.exe", "dummy updater")
    return zip_path


def setup_initial(version: str = "1.8.2") -> None:
    SERVICE_CALLS.update({"stop": 0, "start": 0, "running": True})
    make_dummy_version(version)
    assert layout.flip_current_junction(version) is True
    layout.write_current_version(version)


def write_task(from_v: str, to_v: str, sha: str = "0" * 64) -> None:
    task = {"server_url": "http://mock", "token": "T", "target_version": to_v,
            "sha256": sha, "from_version": from_v, "upgrade_id": f"UPG-{to_v}"}
    updater_mod.TASK_PATH.parent.mkdir(parents=True, exist_ok=True)
    updater_mod.TASK_PATH.write_text(json.dumps(task), encoding="utf-8")


# ================= 场景 1：升级成功（健康检查通过） =================
setup_initial("1.8.2")
pkg = make_package_zip("1.9.0")
fixture_sha = __import__("hashlib").sha256(pkg.read_bytes()).hexdigest()

real_download = updater_mod.download
updater_mod.download = lambda url, token, dest: shutil.copyfile(pkg, dest) or True
layout.read_upgrade_health = lambda: {"version": "1.9.0", "heartbeat": True, "ts": time.time()}
write_task("1.8.2", "1.9.0", sha=fixture_sha)

rc = updater_mod.main()
assert rc == 0, f"upgrade rc={rc}"
state = json.loads(updater_mod.STATE_PATH.read_text(encoding="utf-8"))
assert state["stage"] == "COMMITTED", state
assert layout.active_version() == "1.9.0", layout.active_version()
assert (layout.CURRENT_LINK / "Z-View.exe").exists()
assert (layout.VERSIONS_DIR / "1.8.2").exists(), "上一版本必须保留供回滚"
assert not (layout.VERSIONS_DIR / "1.9.0.old-").exists()
print("SCENE 1 upgrade COMMIT: PASS")

# ================= 场景 2：同版本重复任务 → 直接 COMMIT =================
write_task("1.9.0", "1.9.0")
rc = updater_mod.main()
assert rc == 0 and json.loads(updater_mod.STATE_PATH.read_text(encoding="utf-8"))["stage"] == "COMMITTED"
print("SCENE 2 same-version idempotent: PASS")

# ================= 场景 3：健康检查失败 → 回滚 =================
layout.read_upgrade_health = lambda: {"version": "1.8.2", "heartbeat": True, "ts": time.time()}
write_task("1.9.0", "1.9.1")  # 升到 1.9.1 但健康标记永远是旧版本
pkg2 = make_package_zip("1.9.1")
updater_mod.download = lambda url, token, dest: shutil.copyfile(pkg2, dest) or True
rc = updater_mod.main()
assert rc == 1, f"rollback rc={rc}"
state = json.loads(updater_mod.STATE_PATH.read_text(encoding="utf-8"))
assert state["stage"] == "ROLLBACK", state
assert layout.active_version() == "1.9.0", f"应回滚到 1.9.0，实际 {layout.active_version()}"
assert (layout.CURRENT_LINK / "Z-View.exe").exists()
print("SCENE 3 health-fail ROLLBACK: PASS")

# ================= 场景 4：旧版本清理（keep=2 + 不删 current） =================
make_dummy_version("1.7.0")
make_dummy_version("1.6.0")
removed = layout.prune_old_versions(keep=2, current="1.9.0")
assert set(removed) >= {"1.7.0", "1.6.0"}, removed
assert (layout.VERSIONS_DIR / "1.9.0").exists() and (layout.VERSIONS_DIR / "1.8.2").exists()
print("SCENE 4 prune keep-2: PASS")

shutil.rmtree(SANDBOX, ignore_errors=True)
print("ALL V1.6.0 LAYOUT/UPDATER TESTS PASS")
