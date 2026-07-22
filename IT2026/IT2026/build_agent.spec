# -*- mode: python ; coding: utf-8 -*-

import importlib.util
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_submodules, copy_metadata


project_root = Path.cwd()


def _safe_collect_data_files(package_name: str):
    try:
        return collect_data_files(package_name)
    except Exception:
        return []


def _safe_collect_submodules(package_name: str):
    try:
        return collect_submodules(package_name)
    except Exception:
        return []


def _safe_copy_metadata(distribution_name: str):
    try:
        return copy_metadata(distribution_name)
    except Exception:
        return []


def _safe_collect_importable_module(module_name: str):
    try:
        spec = importlib.util.find_spec(module_name)
    except Exception:
        spec = None
    if spec is None:
        return []
    return [module_name]


_WGC_OPTIONAL_DISTRIBUTIONS = (
    "winrt-runtime",
    "winrt-Windows.Foundation",
    "winrt-Windows.Foundation.Collections",
    "winrt-Windows.Graphics",
    "winrt-Windows.Graphics.Capture",
    "winrt-Windows.Graphics.Capture.Interop",
    "winrt-Windows.Graphics.DirectX",
    "winrt-Windows.Graphics.DirectX.Direct3D11",
    "winrt-Windows.Graphics.DirectX.Direct3D11.Interop",
)

_WGC_OPTIONAL_MODULES = (
    "winrt",
    "winrt.system",
    "winrt.windows.foundation",
    "winrt.windows.foundation.collections",
    "winrt.windows.graphics",
    "winrt.windows.graphics.capture",
    "winrt.windows.graphics.capture.interop",
    "winrt.windows.graphics.directx",
    "winrt.windows.graphics.directx.direct3d11",
    "winrt.windows.graphics.directx.direct3d11.interop",
)

datas = []
favicon_candidate = project_root / "frontend" / "public" / "favicon.ico"
# 空图标会让 PyInstaller 在最终生成 EXE 时失败，缺失时使用 Windows 默认图标。
executable_icon = (
    str(favicon_candidate)
    if favicon_candidate.is_file() and favicon_candidate.stat().st_size > 0
    else None
)
backup_candidate = project_root / "cmdb_agent_unified_v2.py.backup"
if not backup_candidate.exists():
    legacy_backup_candidate = project_root / "IT" / "cmdb_agent_unified_v2.py.backup"
    if legacy_backup_candidate.exists():
        backup_candidate = legacy_backup_candidate
for candidate in [
    project_root / "config.json",
    backup_candidate,
    project_root / "cmdb_agent_consent_ui.legacy.pyc",
]:
    if candidate.exists():
        datas.append((str(candidate), "."))
if executable_icon:
    datas.append((executable_icon, "."))
drivers_root = project_root / "Drivers"
if drivers_root.exists():
    for driver_file in drivers_root.rglob("*"):
        if driver_file.is_file():
            relative_parent = driver_file.parent.relative_to(project_root)
            datas.append((str(driver_file), str(relative_parent)))
datas += _safe_collect_data_files("pyautogui")
datas += _safe_collect_data_files("mouseinfo")
datas += _safe_collect_data_files("dxcam")
datas += _safe_collect_data_files("mss")
datas += _safe_collect_data_files("winrt")
datas += _safe_copy_metadata("fastapi")
datas += _safe_copy_metadata("dxcam")
datas += _safe_copy_metadata("mss")
datas += _safe_copy_metadata("numpy")
datas += _safe_copy_metadata("pydantic")
datas += _safe_copy_metadata("starlette")
datas += _safe_copy_metadata("requests")
datas += _safe_copy_metadata("uvicorn")
for distribution_name in _WGC_OPTIONAL_DISTRIBUTIONS:
    datas += _safe_copy_metadata(distribution_name)

hiddenimports = sorted(
    set(
        _safe_collect_submodules("fastapi")
        + _safe_collect_submodules("starlette")
        + _safe_collect_submodules("pydantic")
        + _safe_collect_submodules("uvicorn")
        + _safe_collect_submodules("PIL")
        + _safe_collect_submodules("dxcam")
        + _safe_collect_submodules("mss")
        + _safe_collect_submodules("comtypes")
        + _safe_collect_submodules("numpy")
        + _safe_collect_submodules("Common")
        + _safe_collect_submodules("IPC")
        + _safe_collect_submodules("RemoteService")
        + _safe_collect_submodules("RemoteAgent")
        + _safe_collect_submodules("Input")
        + _safe_collect_submodules("Capture")
        + _safe_collect_submodules("Network")
        + _safe_collect_submodules("Codec")
        + sum((_safe_collect_submodules(module_name) for module_name in _WGC_OPTIONAL_MODULES), [])
        + [
            "agent_consent_ipc",
            "auth_utils",
            "cmdb_agent_consent_ui",
            "comtypes",
            "console_utils",
            "coordinate_mapper",
            "dxcam",
            "input_injector",
            "mss",
            "numpy",
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
        + sum((_safe_collect_importable_module(module_name) for module_name in _WGC_OPTIONAL_MODULES), [])
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
    icon=executable_icon,
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
)
