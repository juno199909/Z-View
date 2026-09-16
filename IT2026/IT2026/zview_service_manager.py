# -*- coding: utf-8 -*-
"""Z-View 服务管理器（V1.0）：平台前后端服务的状态监视与启动/重启/停止。

独立单文件组件（与 Agent 无关），PyInstaller onefile 打包为
ZViewServiceManager.exe。品牌化 Tk 窗口（与 Agent 托盘同一视觉语言），
全部 UI 操作在主线程（after 轮询），服务进程经 Popen/taskkill 管理。
"""
from __future__ import annotations

import ctypes
import os
import socket
import subprocess
import sys
import threading
import time
import tkinter as tk

APP_DIR = r"D:\IT2026\IT2026\IT2026\IT2026"
FRONTEND_DIR = APP_DIR + r"\frontend"
PY_EXE = r"C:\Users\Administrator\AppData\Local\Programs\Python\Python313\python.exe"
NODE_EXE = r"C:\Program Files\nodejs\node.exe"
VITE_JS = FRONTEND_DIR + r"\node_modules\vite\bin\vite.js"
LOG_DIR = os.environ.get("TEMP", r"C:\Windows\Temp") + r"\ZView\logs"

SERVICES = [
    {"key": "assets", "label": "平台 API", "ports": (8080, 8443),
     "cmd": [PY_EXE, "assets_api.py"], "cwd": APP_DIR,
     "match": "assets_api.py", "log": "assets-api.out.log"},
    {"key": "sw_mgmt", "label": "软件管理 API", "ports": (8081,),
     "cmd": [PY_EXE, "software_management_api_complete_v2.py"], "cwd": APP_DIR,
     "match": "software_management_api_complete_v2.py", "log": "sw-mgmt.out.log"},
    {"key": "sw_policy", "label": "软件策略 API", "ports": (8082,),
     "cmd": [PY_EXE, "software_policy_api.py"], "cwd": APP_DIR,
     "match": "software_policy_api.py", "log": "sw-policy.out.log"},
    {"key": "wt_gateway", "label": "WT 网关", "ports": (4433,), "proto": "udp",
     "cmd": [PY_EXE, "webtransport_gateway.py", "--port", "4433"], "cwd": APP_DIR,
     "match": "webtransport_gateway.py", "log": "wt-gateway.out.log"},
{"key": "frontend", "label": "控制台前端（生产）", "ports": (4173,),
"cmd": [NODE_EXE, VITE_JS, "preview", "--host", "0.0.0.0", "--port", "4173"],
"cwd": FRONTEND_DIR, "match": "vite", "log": "frontend.out.log"},
]

MUTEX_NAME = "ZViewServiceManager-SingleInstance"


def port_open(port: int) -> bool:
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(0.6)
        ok = s.connect_ex(("127.0.0.1", port)) == 0
        s.close()
        return ok
    except Exception:
        return False


def find_pids(match: str) -> list[int]:
    """按命令行关键字收编遗留进程（管理器重启后 adopt 既有服务）。"""
    pids: list[int] = []
    try:
        import psutil

        for proc in psutil.process_iter(["pid", "cmdline"]):
            try:
                cl = proc.info.get("cmdline") or []
                text = " ".join(cl)
                if match in text and "ZViewServiceManager" not in text:
                    pids.append(proc.info["pid"])
            except Exception:
                continue
    except Exception:
        pass
    return pids


def kill_tree(pid: int) -> None:
    try:
        subprocess.run(
            ["taskkill", "/F", "/T", "/PID", str(pid)],
            capture_output=True, timeout=15,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except Exception:
        pass


class ServiceManager:
    def __init__(self) -> None:
        self.procs: dict[str, subprocess.Popen] = {}
        self.adopt_existing()

    def adopt_existing(self) -> None:
        for svc in SERVICES:
            pids = find_pids(svc["match"])
            if pids:
                self.procs[svc["key"]] = {"pid": min(pids), "adopted": True}

    def state(self, svc: dict) -> str:
        pids = find_pids(svc["match"])
        if not pids:
            return "stopped"
        entry = self.procs.get(svc["key"])
        if entry is None:
            self.procs[svc["key"]] = {"pid": min(pids), "adopted": True}
        if svc.get("proto") == "udp":
            # UDP 端口无法 TCP 探测：进程存活即视为运行中
            return "running"
        if all(port_open(p) for p in svc["ports"]):
            return "running"
        return "starting"

    def start(self, svc: dict) -> None:
        if self.state(svc) in ("running", "starting"):
            return
        os.makedirs(LOG_DIR, exist_ok=True)
        log_path = os.path.join(LOG_DIR, svc["log"])
        log_fh = open(log_path, "ab")
        flags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0) | getattr(subprocess, "CREATE_NO_WINDOW", 0)
        proc = subprocess.Popen(
            svc["cmd"], cwd=svc["cwd"], stdout=log_fh, stderr=log_fh,
            creationflags=flags,
        )
        self.procs[svc["key"]] = {"pid": proc.pid, "adopted": False}

    def stop(self, svc: dict) -> None:
        entry = self.procs.get(svc["key"])
        pids = find_pids(svc["match"])
        if entry and entry.get("pid"):
            kill_tree(int(entry["pid"]))
        for pid in pids:
            kill_tree(int(pid))
        self.procs.pop(svc["key"], None)
        time.sleep(0.8)


class App:
    def __init__(self) -> None:
        import tkinter as tk

        self.tk = tk
        self.root = tk.Tk()
        self.root.withdraw()
        self.mgr = ServiceManager()
        self.rows: dict[str, dict] = {}

        try:
            _dpi = float(self.root.winfo_fpixels("1i"))
        except Exception:
            _dpi = 96.0
        S = max(1.0, min(2.0, _dpi / 96.0))

        def S_px(v: int) -> int:
            return max(1, int(round(v * S)))

        self.S_px = S_px
        W = S_px(720)

        self.top = tk.Toplevel(self.root)
        self.top.withdraw()
        self.top.title("Z-View 服务管理")
        self.top.resizable(False, False)
        self.top.configure(bg="white")

        BRAND_DARK = "#0E3358"
        BRAND = "#16497E"
        TEXT_MAIN = "#1F2733"
        TEXT_SUB = "#64707F"
        GRAY_BTN = "#EDF1F6"
        self.colors = {
            "brand": BRAND, "brand_dark": BRAND_DARK, "text": TEXT_MAIN,
            "sub": TEXT_SUB, "gray_btn": GRAY_BTN, "gray_btn_active": "#DFE6EE",
            "white": "white", "green": "#1E8E4E", "amber": "#E8A23C", "danger": "#C0392B",
        }

        HEADER_H = S_px(54)
        header = tk.Frame(self.top, bg=BRAND_DARK, height=HEADER_H)
        header.pack(fill="x", side="top")
        header.pack_propagate(False)
        tk.Label(header, text="Z-View 服务管理", font=("Microsoft YaHei UI", 12, "bold"),
                 bg=BRAND_DARK, fg="white").place(x=S_px(20), y=S_px(8))
        tk.Label(header, text="平台前后端服务 · 状态监视与启停控制", font=("Microsoft YaHei UI", 9),
                 bg=BRAND_DARK, fg="#B9CBE0").place(x=S_px(20), y=S_px(30))

        # 全局操作工具栏
        toolbar = tk.Frame(self.top, bg="white")
        toolbar.pack(fill="x")
        tk.Button(toolbar, text="全部启动", font=("Microsoft YaHei UI", 9), bg=GRAY_BTN,
                  fg=TEXT_MAIN, relief="flat", cursor="hand2", padx=10,
                  command=self.start_all).pack(side="left", padx=(S_px(16), 6), pady=S_px(10))
        tk.Button(toolbar, text="全部停止", font=("Microsoft YaHei UI", 9), bg=GRAY_BTN,
                  fg=TEXT_MAIN, relief="flat", cursor="hand2", padx=10,
                  command=self.stop_all).pack(side="left", padx=6, pady=S_px(10))

        # 服务行容器
        self.list_frame = tk.Frame(self.top, bg="white")
        self.list_frame.pack(fill="x")

        # 操作日志
        self.log_text = tk.Text(self.top, height=6, font=("Consolas", 8), bg="#0E3358",
                                fg="#C7D6E8", relief="flat", state="disabled")
        self.log_text.pack(fill="x", padx=S_px(16), pady=(S_px(4), S_px(12)))

        self.build_rows()
        self.top.geometry(f"{W}x0")
        self.top.update_idletasks()
        self.top.geometry(f"{W}x{self.top.winfo_reqheight()}")
        self.top.deiconify()
        self.top.update_idletasks()
        try:
            self.top.eval("tk::PlaceWindow .center")
        except Exception:
            pass
        try:
            self.top.attributes("-topmost", True)
            self.top.after(1200, lambda: self.top.attributes("-topmost", False))
        except Exception:
            pass

        self.poll()

    def log(self, message: str) -> None:
        self.log_text.configure(state="normal")
        self.log_text.insert("end", time.strftime("[%H:%M:%S] ") + message + "\n")
        self.log_text.see("end")
        self.log_text.configure(state="disabled")

    def build_rows(self) -> None:
        for svc in SERVICES:
            frame = tk.Frame(self.list_frame, bg="#F4F7FA",
                             highlightbackground="#DCE4EC", highlightthickness=1)
            frame.pack(fill="x", padx=self.S_px(16), pady=self.S_px(4))
            state_lb = tk.Label(frame, text="…", font=("Microsoft YaHei UI", 9, "bold"),
                                bg="#F4F7FA", width=9, anchor="w")
            state_lb.pack(side="left", padx=(self.S_px(14), 6))
            name_lb = tk.Label(frame, text=svc["label"], font=("Microsoft YaHei UI", 10, "bold"),
                               bg="#F4F7FA", fg=self.colors["text"], width=20, anchor="w")
            name_lb.pack(side="left")
            port_lb = tk.Label(frame, text="端口 " + "/".join(str(p) for p in svc["ports"]),
                               font=("Microsoft YaHei UI", 8), bg="#F4F7FA",
                               fg=self.colors["sub"], width=12, anchor="w")
            port_lb.pack(side="left")
            btn_box = tk.Frame(frame, bg="#F4F7FA")
            btn_box.pack(side="right", padx=self.S_px(12))
            self.rows[svc["key"]] = {"frame": frame, "state": state_lb, "buttons": {}}
            btns = {}
            for action in ("启动", "重启", "停止"):
                btn = tk.Button(btn_box, text=action, font=("Microsoft YaHei UI", 8),
                                bg=self.colors["gray_btn"], fg=self.colors["text"],
                                relief="flat", cursor="hand2", width=6,
                                command=lambda s=svc, a=action: self.on_action(s, a))
                btn.pack(side="left", padx=2)
                btns[action] = btn
            self.rows[svc["key"]]["buttons"] = btns

    def on_action(self, svc: dict, action: str) -> None:
        label = svc["label"]
        try:
            if action == "启动":
                self.mgr.start(svc)
                self.log(f"{label} 启动指令已发出，等待端口就绪…")
                self._verify_async(svc, expect_running=True, action_label="启动")
            elif action == "停止":
                self.mgr.stop(svc)
                self.log(f"{label} 停止指令已发出，等待端口释放…")
                self._verify_async(svc, expect_running=False, action_label="停止")
            elif action == "重启":
                self.log(f"{label} 重启中…")

                def _restart():
                    try:
                        self.mgr.stop(svc)
                        time.sleep(1.0)
                        self.mgr.start(svc)
                        self._verify_async(svc, expect_running=True, action_label="重启")
                    except Exception as exc:
                        self.root.after(0, lambda: self.log(f"{label} 重启失败: {exc}"))

                threading.Thread(target=_restart, daemon=True).start()
            else:
                return
        except Exception as exc:
            # 兜底：此前回调抛异常会被 tkinter 静默吞掉，导致小窗口毫无反馈
            self.log(f"{label} {action}操作出错: {exc}")
        self.refresh_once()

    def _svc_alive(self, svc: dict) -> bool:
        """运行判定：TCP 服务看端口监听，UDP 服务看进程存在。"""
        if svc.get("proto") == "udp":
            return bool(find_pids(svc["match"]))
        return all(port_open(p) for p in svc["ports"])

    def _verify_async(self, svc: dict, expect_running: bool, action_label: str) -> None:
        """后台轮询真实端口/进程状态（最长 15s），把成功/失败结果回填日志窗口。"""
        label = svc["label"]
        ports_text = "/".join(str(p) for p in svc["ports"])

        def _worker():
            deadline = time.time() + 15
            ok = False
            while time.time() < deadline:
                alive = self._svc_alive(svc)
                if alive == expect_running:
                    ok = True
                    break
                time.sleep(0.8)
            if expect_running:
                msg = f"{label} {action_label}成功（端口 {ports_text} 已监听）" if ok else \
                      f"{label} {action_label}失败（端口 {ports_text} 未监听，请查看日志 {svc['log']}）"
            else:
                msg = f"{label} {action_label}成功（端口 {ports_text} 已释放）" if ok else \
                      f"{label} {action_label}失败（进程仍在运行）"
            self.root.after(0, lambda: self.log(msg))

        threading.Thread(target=_worker, daemon=True).start()

    def start_all(self) -> None:
        for svc in SERVICES:
            try:
                if self.mgr.state(svc) == "stopped":
                    self.mgr.start(svc)
                    self.log(f"{svc['label']} 启动指令已发出，等待端口就绪…")
                    self._verify_async(svc, expect_running=True, action_label="启动")
            except Exception as exc:
                self.log(f"{svc['label']} 启动出错: {exc}")
        self.refresh_once()

    def stop_all(self) -> None:
        for svc in SERVICES:
            try:
                self.mgr.stop(svc)
                self.log(f"{svc['label']} 停止指令已发出，等待端口释放…")
                self._verify_async(svc, expect_running=False, action_label="停止")
            except Exception as exc:
                self.log(f"{svc['label']} 停止出错: {exc}")
        self.refresh_once()


    def poll(self) -> None:
        try:
            for svc in SERVICES:
                row = self.rows.get(svc["key"])
                if not row:
                    continue
                state = self.mgr.state(svc)
                color = {"running": self.colors["green"], "starting": self.colors["amber"],
                         "stopped": self.colors["sub"]}.get(state, self.colors["sub"])
                text = {"running": "● 运行中", "starting": "◐ 启动中", "stopped": "○ 已停止"}.get(state, state)
                row["state"].configure(text=text, fg=color)
                btns = row["buttons"]
                if state == "stopped":
                    btns["启动"].configure(state="normal")
                    btns["重启"].configure(state="disabled")
                    btns["停止"].configure(state="disabled")
                else:
                    btns["启动"].configure(state="disabled")
                    btns["重启"].configure(state="normal")
                    btns["停止"].configure(state="normal")
        except Exception:
            pass
        self.top.after(2000, self.poll)


def main() -> None:
    handle = ctypes.windll.kernel32.CreateMutexW(None, False, MUTEX_NAME)
    if ctypes.windll.kernel32.GetLastError() == 183:
        return
    os.makedirs(LOG_DIR, exist_ok=True)
    app = App()
    try:
        app.root.mainloop()
    except Exception:
        pass


if __name__ == "__main__":
    main()
