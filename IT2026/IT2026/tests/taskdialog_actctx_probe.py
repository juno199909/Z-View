# -*- coding: utf-8 -*-
"""验证 comctl32 v6 激活上下文是否为 TaskDialog E_INVALIDARG 的根因。"""

import ctypes
import sys
import threading
from ctypes import wintypes
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import cmdb_agent_consent_ui as cui  # noqa: E402

MANIFEST = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<assembly xmlns="urn:schemas-microsoft-com:asm.v1" manifestVersion="1.0">
  <assemblyIdentity type="win32" name="ZView.ComCtl6" version="1.0.0.0"/>
  <dependency>
    <dependentAssembly>
      <assemblyIdentity type="win32" name="Microsoft.Windows.Common-Controls" version="6.0.0.0" publicKeyToken="6595b64144ccf1df" language="*" processorArchitecture="*"/>
    </dependentAssembly>
  </dependency>
</assembly>"""


def main():
    manifest_path = Path(r"D:\IT2026-temp\zview-comctl6.manifest")
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(MANIFEST, encoding="utf-8")

    kernel32 = ctypes.windll.kernel32

    class ACTCTX(ctypes.Structure):
        _fields_ = [
            ("cbSize", wintypes.ULONG),
            ("dwFlags", wintypes.DWORD),
            ("lpSource", wintypes.LPCWSTR),
            ("wProcessorArchitecture", wintypes.WORD),
            ("wLangId", wintypes.WORD),
            ("lpAssemblyDirectory", wintypes.LPCWSTR),
            ("lpResourceName", wintypes.LPCWSTR),
            ("lpApplicationName", wintypes.LPCWSTR),
            ("hModule", wintypes.HMODULE),
        ]

    kernel32.CreateActCtxW.restype = wintypes.HANDLE
    kernel32.CreateActCtxW.argtypes = [ctypes.POINTER(ACTCTX)]
    actctx = ACTCTX()
    actctx.cbSize = ctypes.sizeof(ACTCTX)
    actctx.lpSource = str(manifest_path)
    handle = kernel32.CreateActCtxW(ctypes.byref(actctx))
    invalid_handle = wintypes.HANDLE(-1).value
    print(f"[ctx] CreateActCtxW handle valid: {handle not in (0, invalid_handle)}")
    if handle in (0, invalid_handle):
        print(f"[ctx] error={ctypes.get_last_error()}")
        return 1

    kernel32.ActivateActCtx.restype = wintypes.BOOL
    cookie = ctypes.c_size_t()
    ok = bool(kernel32.ActivateActCtx(handle, ctypes.byref(cookie)))
    print(f"[ctx] ActivateActCtx: {ok}")

    app = cui.ConsentTrayApp()

    result = {}

    def run():
        try:
            result["value"] = cui._invoke_task_dialog(
                app, "Z-View 远程控制确认", "td-probe", "127.0.0.1", "LOCAL", 4
            )
        except Exception as exc:
            result["error"] = f"{type(exc).__name__}: {exc}"

    thread = threading.Thread(target=run)
    thread.start()
    thread.join(20)
    print(f"[td] result: {result}")

    import os

    os._exit(0 if "value" in result else 1)


if __name__ == "__main__":
    main()
