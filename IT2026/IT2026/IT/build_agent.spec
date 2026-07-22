# -*- mode: python ; coding: utf-8 -*-

from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_submodules, copy_metadata


project_root = Path.cwd()

datas = [
    (str(project_root / "config.json"), "."),
    (str(project_root / "cmdb_agent_unified_v2.py.backup"), "."),
    (str(project_root / "cmdb_agent_consent_ui.legacy.pyc"), "."),
    (str(project_root / "frontend" / "public" / "favicon.ico"), "."),
]
datas += collect_data_files("pyautogui")
datas += collect_data_files("mouseinfo")
datas += copy_metadata("fastapi")
datas += copy_metadata("pydantic")
datas += copy_metadata("starlette")
datas += copy_metadata("requests")
datas += copy_metadata("uvicorn")

hiddenimports = sorted(
    set(
        collect_submodules("fastapi")
        + collect_submodules("starlette")
        + collect_submodules("pydantic")
        + collect_submodules("uvicorn")
        + collect_submodules("PIL")
        + [
            "agent_consent_ipc",
            "cmdb_agent_consent_ui",
            "console_utils",
            "coordinate_mapper",
            "input_injector",
            "pyautogui",
            "mouseinfo",
            "pywintypes",
            "pyscreeze",
            "pymsgbox",
            "pygetwindow",
            "pytweening",
            "pyrect",
            "remote_desktop_engine_v2",
            "remote_desktop_protocol",
            "requests",
            "psutil",
            "servicemanager",
            "tkinter",
            "tkinter.ttk",
            "win32api",
            "win32con",
            "win32event",
            "win32gui",
            "win32process",
            "win32profile",
            "win32security",
            "win32service",
            "win32serviceutil",
            "win32timezone",
            "win32ts",
            "winreg",
        ]
    )
)

a = Analysis(
    ["cmdb_agent_unified_v2.py"],
    pathex=[str(project_root)],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name="Z-View",
    icon=str(project_root / "frontend" / "public" / "favicon.ico"),
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
)
