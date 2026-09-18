# -*- coding: utf-8 -*-
"""Z-View Agent 图形化安装向导（双击 → 下一步 → 完成）。

打包：scripts/build_setup_exe.py 通过 build_setup.spec 生成单文件
Z-View-Setup-<ver>.exe（内嵌 payload.zip = onedir 完整包）。

静默模式（域脚本/桌管推送）：Z-View-Setup-<ver>.exe /S --server-url https://center:8443
自检模式：--dry-run（只校验载荷与计划，不落盘不装服务）
"""
import json
import os
import queue
import subprocess
import sys
import tempfile
import threading
import time
import zipfile
from pathlib import Path

CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
SERVICE_NAME = "CMDB-Agent"
LOG_PATH = Path(tempfile.gettempdir()) / "zview-setup.log"


def _log(message: str):
    try:
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(time.strftime("[%Y-%m-%d %H:%M:%S] ") + message + "\n")
    except Exception:
        pass


def _resource(name: str) -> Path:
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
    return base / name


def _load_meta() -> dict:
    try:
        return json.loads(_resource("setup_meta.json").read_text(encoding="utf-8"))
    except Exception:
        return {"version": "", "default_server_url": ""}


def _service_state() -> str:
    try:
        out = subprocess.run(
            ["sc", "query", SERVICE_NAME],
            capture_output=True, text=True,
            creationflags=CREATE_NO_WINDOW, timeout=15,
        ).stdout or ""
        for line in out.splitlines():
            if "STATE" in line and "RUNNING" in line:
                return "RUNNING"
        return "INSTALLED_STOPPED" if out.strip() else "NOT_INSTALLED"
    except Exception:
        return "UNKNOWN"


def run_install(server_url: str, progress, dry_run: bool=False) -> int:
    """解压 payload → 运行 Z-View.exe --install → 轮询服务直到 RUNNING。"""
    payload_zip = _resource("payload.zip")
    if not payload_zip.is_file():
        progress("error", "安装载荷缺失（payload.zip 未打包）")
        return 2
    workdir = Path(tempfile.gettempdir()) / f"zview-setup-{os.getpid()}"
    payload_dir = workdir / "payload"
    try:
        with zipfile.ZipFile(payload_zip) as zf:
            names = zf.namelist()
            if "Z-View.exe" not in names:
                progress("error", "载荷异常：缺少 Z-View.exe")
                return 2
            if dry_run:
                progress("status", f"[dry-run] 载荷校验通过：{len(names)} 个文件，"
                                   f"将安装服务 {SERVICE_NAME} 并指向 {server_url or '(默认)'}")
                progress("done", "dry-run 完成")
                return 0
            progress("status", "正在解压程序文件...")
            total = len(names)
            for idx, name in enumerate(names):
                zf.extract(name, payload_dir)
                if idx % 200 == 0 or idx == total - 1:
                    progress("percent", int(70 * (idx + 1) / total))
                    progress("status", f"正在解压程序文件... {idx + 1}/{total}")
    except Exception as exc:
        _log(f"extract failed: {exc!r}")
        progress("error", f"解压失败：{exc}")
        return 2

    exe = payload_dir / "Z-View.exe"
    cmd = [str(exe), "--install", "--quiet"]
    if server_url:
        cmd += ["--server-url", server_url]
    progress("status", "正在注册并启动服务（CMDB-Agent）...")
    try:
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                stdin=subprocess.DEVNULL,
                                text=True, creationflags=CREATE_NO_WINDOW)
        out, err = proc.communicate(timeout=600)
        rc = proc.returncode
        _log(f"install rc={rc} out={(out or '')[-400:]!r} err={(err or '')[-400:]!r}")
        if rc != 0:
            # 同版本重装等场景：拷贝失败但 Agent 已在运行 —— 视为已安装
            if _service_state() == "RUNNING":
                _log(f"install rc={rc} but service RUNNING -> treat as already-installed")
                progress("percent", 100)
                progress("status", "检测到 Agent 已安装且服务运行中")
                progress("done", "")
                return 0
            progress("error", f"安装程序返回码 {rc}，"
                              f"详情见日志 {LOG_PATH}")
            return rc or 1
    except Exception as exc:
        import traceback
        _log("install exec failed: " + traceback.format_exc())
        progress("error", f"安装执行失败：{exc}")
        return 1

    progress("percent", 85)
    deadline = time.time() + 90
    while time.time() < deadline:
        state = _service_state()
        if state == "RUNNING":
            progress("percent", 100)
            progress("done", "")
            return 0
        progress("status", "等待服务启动...")
        time.sleep(2)
    progress("error", f"服务未能在 90 秒内进入 RUNNING 状态，"
                      f"请查看 C:\\ProgramData\\CMDB-Agent\\logs\\agent-runtime.log")
    return 3


def run_silent(server_url: str, dry_run: bool=False) -> int:
    def progress(kind, payload):
        _log(f"{kind}: {payload}")
        print(f"{kind}: {payload}")
    _log(f"silent install begin server={server_url!r} dry_run={dry_run}")
    return run_install(server_url, progress, dry_run=dry_run)


class Wizard:
    def __init__(self, meta: dict):
        import tkinter as tk
        from tkinter import ttk
        self.tk, self.ttk = tk, ttk
        self.meta = meta
        self.root = tk.Tk()
        self.root.title("Z-View Agent 安装向导")
        self.root.geometry("560x360")
        self.root.resizable(False, False)
        self.root.configure(bg="#f4f6fa")
        self.queue = queue.Queue()
        self.result_rc = 1
        self._build()
        self.root.after(120, self._pump_queue)

    def _enqueue(self, kind, payload=""):
        self.queue.put((str(kind), payload))

    def _brand_header(self, title, subtitle):
        header = self.tk.Frame(self.root, bg="#1f3b57")
        header.pack(fill="x")
        self.tk.Label(header, text="Z-View", font=("Microsoft YaHei UI", 15, "bold"),
                      fg="white", bg="#1f3b57").pack(anchor="w", padx=24, pady=(14, 0))
        self.tk.Label(header, text=title, font=("Microsoft YaHei UI", 11),
                      fg="#dbe7f3", bg="#1f3b57").pack(anchor="w", padx=24, pady=(0, 2))
        self.tk.Label(header, text=subtitle, font=("Microsoft YaHei UI", 9),
                      fg="#9fb6cc", bg="#1f3b57").pack(anchor="w", padx=24, pady=(0, 12))
        return header

    def _build(self):
        ver = self.meta.get("version") or "(dev)"
        self._brand_header(f"Agent 安装向导  v{ver}",
                           "注册系统服务并接入管理中心，安装完成后终端自动上线")
        self.page_welcome = self._page_welcome()
        self.page_progress = self._page_progress()
        self.page_done = self._page_done()
        self.page_error = self._page_error()
        self._show(self.page_welcome)

    def _page_welcome(self):
        page = self.tk.Frame(self.root, bg="#f4f6fa")
        self.ttk.Label(page, text="本向导将把 Z-View Agent 安装为系统服务：",
                       background="#f4f6fa").pack(anchor="w", padx=28, pady=(20, 4))
        tips = ("· 安装位置：C:\\Program Files\\CMDB-Agent（自动管理版本目录）\n"
                "· 安装完成后自动注册并启动服务 CMDB-Agent\n"
                "· 终端将自动注册到下方管理中心并上线")
        self.ttk.Label(page, text=tips, background="#f4f6fa",
                       justify="left").pack(anchor="w", padx=28)
        row = self.tk.Frame(page, bg="#f4f6fa")
        row.pack(anchor="w", padx=28, pady=(16, 4))
        self.ttk.Label(row, text="管理中心地址：", background="#f4f6fa").pack(side="left")
        self.server_var = self.tk.StringVar(value=self.meta.get("default_server_url") or "")
        entry = self.ttk.Entry(row, textvariable=self.server_var, width=34)
        entry.pack(side="left", padx=6)
        note = self.ttk.Label(page, text="通常无需修改；如不确定请保持默认。也可留空由已有配置接管。",
                              background="#f4f6fa", foreground="#6b7a89")
        note.pack(anchor="w", padx=28)
        btns = self.tk.Frame(page, bg="#f4f6fa")
        btns.pack(side="bottom", anchor="e", padx=24, pady=16)
        self.ttk.Button(btns, text="下一步  >", command=self._start_install,
                        width=12).pack(side="left")
        self.ttk.Button(btns, text="取消", command=self.root.destroy,
                        width=8).pack(side="left", padx=8)
        return page

    def _page_progress(self):
        page = self.tk.Frame(self.root, bg="#f4f6fa")
        self.progress_var = self.tk.IntVar(value=0)
        bar = self.ttk.Progressbar(page, variable=self.progress_var, maximum=100, length=460)
        bar.pack(padx=40, pady=(70, 12))
        self.progress_text = self.ttk.Label(page, text="准备安装...", background="#f4f6fa")
        self.progress_text.pack(padx=40)
        return page

    def _page_done(self):
        page = self.tk.Frame(self.root, bg="#f4f6fa")
        self.ttk.Label(page, text="✔ 安装完成", font=("Microsoft YaHei UI", 14, "bold"),
                       background="#f4f6fa", foreground="#1d7a46").pack(pady=(70, 8))
        self.ttk.Label(page, text="服务已启动，终端将在一分钟内自动出现在管理中心。\n"
                                  "如需查看运行状态：C:\\ProgramData\\CMDB-Agent\\logs\\agent-runtime.log",
                       background="#f4f6fa", justify="center").pack()
        btns = self.tk.Frame(page, bg="#f4f6fa")
        btns.pack(side="bottom", anchor="e", padx=24, pady=16)
        self.ttk.Button(btns, text="完成", command=self._finish_ok, width=12).pack()
        return page

    def _page_error(self):
        page = self.tk.Frame(self.root, bg="#f4f6fa")
        self.ttk.Label(page, text="✘ 安装未完成", font=("Microsoft YaHei UI", 14, "bold"),
                       background="#f4f6fa", foreground="#b02a2a").pack(pady=(60, 8))
        self.error_text = self.ttk.Label(page, text="", background="#f4f6fa",
                                         justify="left", wraplength=480)
        self.error_text.pack(padx=28)
        btns = self.tk.Frame(page, bg="#f4f6fa")
        btns.pack(side="bottom", anchor="e", padx=24, pady=16)
        self.ttk.Button(btns, text="关闭", command=self._finish_fail, width=12).pack()
        return page

    def _show(self, page):
        for p in (self.page_welcome, self.page_progress, self.page_done, self.page_error):
            p.pack_forget()
        page.pack(fill="both", expand=True)

    def _start_install(self):
        server_url = self.server_var.get().strip()
        self._show(self.page_progress)
        threading.Thread(target=run_install,
                         args=(server_url, self._enqueue), daemon=True).start()

    def _pump_queue(self):
        try:
            while True:
                kind, payload = self.queue.get_nowait()
                if kind == "percent":
                    self.progress_var.set(int(payload))
                elif kind == "status":
                    self.progress_text.config(text=str(payload))
                elif kind == "done":
                    self._show(self.page_done)
                    return
                elif kind == "error":
                    self.error_text.config(text=str(payload))
                    self._show(self.page_error)
                    return
        except queue.Empty:
            pass
        except Exception:
            pass
        self.root.after(120, self._pump_queue)

    def _finish_ok(self):
        self.result_rc = 0
        self.root.destroy()

    def _finish_fail(self):
        self.result_rc = 1
        self.root.destroy()

    def run(self):
        self.root.mainloop()
        return self.result_rc


def main() -> int:
    meta = _load_meta()
    argv = sys.argv[1:]
    silent = any(a.lower() in ("/s", "-s", "--quiet", "/quiet") for a in argv)
    dry_run = "--dry-run" in argv
    server_url = ""
    for a in argv:
        if a.lower().startswith("--server-url="):
            server_url = a.split("=", 1)[1].strip()
    if not server_url:
        server_url = (meta.get("default_server_url") or "").strip()
    _log(f"wizard start argv={argv!r} meta={meta}")

    if os.name != "nt":
        print("setup requires Windows")
        return 1
    if silent or dry_run:
        return run_silent(server_url, dry_run=dry_run)
    if _service_state() == "RUNNING":
        pass  # 幂等：仍允许向导重跑（自安装本身幂等）
    return Wizard(meta).run()


if __name__ == "__main__":
    raise SystemExit(main())
