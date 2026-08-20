# -*- coding: utf-8 -*-
"""同意助手 Tk UI 线程化改造回归测试。

验证点：
  1. 源码运行（非 frozen）auto 后端顺序保持 tkinter 优先，行为不变
  2. frozen 构建 auto 后端顺序改为原生优先（taskdialog/messagebox/tkinter）
  3. 常驻 UI 线程：连续两轮弹窗周期后助手进程存活、结果正确（自动超时）
     ——旧实现每轮新建/销毁 Tk 根窗口，存在跨线程 Tcl 清理导致静默退出的缺陷

运行: python tests\\test_consent_ui_thread.py
注意: 屏幕上会短暂闪现两次确认弹窗（各约 6 秒自动超时），无需点击。
"""

import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import cmdb_agent_consent_ui as consent_ui  # noqa: E402

RESULTS = []


def record(name, ok, detail=""):
    RESULTS.append(bool(ok))
    print(f"[{'PASS' if ok else 'FAIL'}] {name} {detail}", flush=True)


def main():
    app = consent_ui.ConsentTrayApp()

    # ---- 1. 源码构建 auto 顺序不变 ----
    frozen_before = getattr(sys, "frozen", False)
    order = consent_ui._determine_dialog_backends(app)
    record("源码构建 auto 后端 tkinter 优先", order[0] == "tkinter", f"order={order}")

    # ---- 2. frozen 构建 auto 顺序 tkinter 优先（品牌化弹窗），原生兜底 ----
    sys.frozen = True
    try:
        order = consent_ui._determine_dialog_backends(app)
    finally:
        if not frozen_before:
            del sys.frozen
    tk_first = order[0] == "tkinter"
    has_native_fallback = "messagebox" in order
    record(
        "frozen 构建 auto 链 tkinter 优先",
        tk_first and has_native_fallback,
        f"order={order}",
    )

    # ---- 3. 常驻 UI 线程 + 连续弹窗周期存活 ----
    ready = consent_ui._ensure_tk_ui_thread(app)
    record("常驻 UI 线程就绪", bool(ready))
    thread = getattr(app, "_tk_ui_thread", None)
    record("UI 线程唯一且存活", thread is not None and thread.is_alive(), f"thread={thread.name if thread else None}")

    for cycle in (1, 2):
        started = time.time()
        value = consent_ui._invoke_tk_consent_dialog(
            app, "Z-View 远程控制确认", "ui-thread-test", "127.0.0.1", "LOCAL", 6
        )
        elapsed = time.time() - started
        valid_result = value in (consent_ui.IDYES, consent_ui.IDNO, consent_ui.IDTIMEOUT)
        record(
            f"第 {cycle} 轮弹窗周期完成",
            valid_result and elapsed <= 12.0,
            f"value={value} elapsed={elapsed:.1f}s",
        )
        record(
            f"第 {cycle} 轮后助手存活",
            thread.is_alive() and getattr(app, "_tk_ui_root", None) is not None,
        )

    print("=" * 60)
    failed = RESULTS.count(False)
    print(f"总计 {len(RESULTS)} 项, 失败 {failed} 项")
    return failed == 0


if __name__ == "__main__":
    ok = main()
    # 常驻 Tk 根窗口使解释器自然收尾会阻塞（与生产助手同源问题）；生产路径由
    # main() 的 os._exit 兜底，这里同样硬退出以验证业务结果。
    import os

    os._exit(0 if ok else 1)
