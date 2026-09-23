# -*- mode: python ; coding: utf-8 -*-
# Z-View Setup 向导单文件构建（由 scripts/build_setup_exe.py 设置环境变量后调用）
import os
from pathlib import Path

project_root = Path.cwd()
bundle_dir = Path(os.environ["ZVIEW_SETUP_BUNDLE"])

a = Analysis(
    ["setup_wizard.py"],
    pathex=[str(project_root)],
    binaries=[],
    datas=[
        (str(bundle_dir / "payload.zip"), "."),
        (str(bundle_dir / "setup_meta.json"), "."),
    ],
    hiddenimports=[],
    hookspath=[],
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    name="ZViewSetup",
    debug=False,
    strip=False,
    upx=False,
    console=False,
    uac_admin=True,
    icon="assets/favicon.ico" if Path("assets/favicon.ico").exists() else None,
)
