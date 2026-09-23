# -*- coding: utf-8 -*-
"""构建 Z-View 图形化安装向导（双击下一步式单文件 exe）。

用法（需先完成 Agent onedir 构建与打包）：
    python scripts/build_setup_exe.py [version] [--server-url https://center:8443]

流程：
1. 校验 dist_agent\\Z-View 与 releases\\Z-View-<ver>-onedir.zip 存在（缺则先打包）
2. 生成 setup_meta.json（版本 + 默认管理中心地址）
3. PyInstaller build_setup.spec → 单文件 ZViewSetup.exe（UAC 提权）
4. 产物复制为 releases\\Z-View-Setup-<ver>.exe；--publish 时同步 agent_upgrade\\<ver>\\
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_SERVER_URL = "https://172.16.250.120:8443"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("version", nargs="?", default="")
    parser.add_argument("--server-url", default=DEFAULT_SERVER_URL)
    parser.add_argument("--publish", action="store_true",
                        help="同步产物到 agent_upgrade\\<ver>\\ 供控制台下载")
    args = parser.parse_args()

    dist_onedir = PROJECT_ROOT / "dist_agent" / "Z-View"
    if not (dist_onedir / "Z-View.exe").is_file():
        print(f"onedir build missing: {dist_onedir}（先跑 build_agent.spec）")
        return 1

    sys.path.insert(0, str(PROJECT_ROOT))
    from cmdb_agent_core import AGENT_VERSION
    version = args.version or AGENT_VERSION

    release_zip = PROJECT_ROOT / "releases" / f"Z-View-{version}-onedir.zip"
    if not release_zip.is_file():
        print(f"release zip missing: {release_zip}（先跑 scripts/package_agent.py {version}）")
        return 1

    meta_path = PROJECT_ROOT / "build_setup_meta.json"
    meta_path.write_text(json.dumps({
        "version": version,
        "default_server_url": args.server_url,
    }, ensure_ascii=False), encoding="utf-8")

    # datas 目标列是目录、保留源文件名：先整理成固定文件名再打包
    bundle_dir = PROJECT_ROOT / "build_setup_work" / "bundle"
    bundle_dir.mkdir(parents=True, exist_ok=True)
    bundle_zip = bundle_dir / "payload.zip"
    bundle_meta = bundle_dir / "setup_meta.json"
    shutil.copy2(release_zip, bundle_zip)
    shutil.copy2(meta_path, bundle_meta)

    env = os.environ.copy()
    env["ZVIEW_SETUP_BUNDLE"] = str(bundle_dir)
    cmd = [
        sys.executable, "-m", "PyInstaller", "build_setup.spec",
        "--noconfirm", "--distpath", "dist_setup", "--workpath", "build_setup_work",
    ]
    print("building setup exe ...")
    started = time.time()
    completed = subprocess.run(cmd, cwd=str(PROJECT_ROOT), env=env)
    if completed.returncode != 0:
        print("PyInstaller failed")
        return completed.returncode

    src = PROJECT_ROOT / "dist_setup" / "ZViewSetup.exe"
    out = PROJECT_ROOT / "releases" / f"Z-View-Setup-{version}.exe"
    shutil.copy2(src, out)
    print(f"built: {out} ({out.stat().st_size / (1024*1024):.1f} MB, {time.time()-started:.0f}s)")

    if args.publish:
        vdir = PROJECT_ROOT / "agent_upgrade" / version
        vdir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(out, vdir / out.name)
        print(f"published: {vdir / out.name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
