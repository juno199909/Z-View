# -*- coding: utf-8 -*-
"""组装 Agent 发布包（V1.6.0 onedir 布局）。

用法（先完成两个 PyInstaller 构建）：
    python -m PyInstaller build_agent.spec --noconfirm --distpath dist_agent --workpath build_agent_work
    python -m PyInstaller --onefile --name ZViewUpdater --paths . updater/updater.py --distpath dist_updater --workpath build_updater_work --specpath build_updater_work
    python scripts/package_agent.py [version]

产物：releases\\Z-View-<version>-onedir.zip
    ├── Z-View.exe            onedir 入口
    ├── _internal\\
    ├── version.txt
    └── updater\\ZViewUpdater.exe
注意：产物放 releases\\，不要直接放入 agent_upgrade\\（那会立即触发终端升级）。
"""
from __future__ import annotations

import io
import sys
import zipfile
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from cmdb_agent_core import AGENT_VERSION  # noqa: E402

DIST_ONEDIR = PROJECT_ROOT / "dist_agent" / "Z-View"
DIST_UPDATER = PROJECT_ROOT / "dist_updater" / "ZViewUpdater.exe"
RELEASES_DIR = PROJECT_ROOT / "releases"


def main() -> int:
    version = sys.argv[1] if len(sys.argv) > 1 else AGENT_VERSION
    if not DIST_ONEDIR.is_dir() or not (DIST_ONEDIR / "Z-View.exe").exists():
        print(f"onedir build missing: {DIST_ONEDIR}")
        return 1
    if not DIST_UPDATER.is_file():
        print(f"updater build missing: {DIST_UPDATER}")
        return 1

    RELEASES_DIR.mkdir(parents=True, exist_ok=True)
    out_path = RELEASES_DIR / f"Z-View-{version}-onedir.zip"
    with zipfile.ZipFile(out_path, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
        for item in DIST_ONEDIR.rglob("*"):
            if item.is_file():
                zf.write(item, item.relative_to(DIST_ONEDIR).as_posix())
        zf.writestr("version.txt", version)
        zf.write(DIST_UPDATER, "updater/ZViewUpdater.exe")

    size_mb = out_path.stat().st_size / (1024 * 1024)
    print(f"packaged: {out_path} ({size_mb:.1f} MB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
