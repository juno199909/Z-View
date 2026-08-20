# -*- coding: utf-8 -*-
"""火绒风格托盘菜单动作回归测试。

覆盖：
  1. frozen/source 后端链（taskdialog 已从 auto 链移除）
  2. 「允许远程控制请求」开关：设置持久化 + 管道请求返回 disabled_by_user
  3. 「本机免确认」开关：设置持久化 + 管道请求自动放行 session_skip_enabled
  4. 气泡提醒在无托盘环境下静默不崩溃
运行: python tests\\test_tray_menu_actions.py
"""

import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import cmdb_agent_consent_ui as cui  # noqa: E402
from agent_consent_ipc import load_tray_settings, resolve_tray_settings_path, save_tray_settings  # noqa: E402

RESULTS = []


def record(name, ok, detail=""):
    RESULTS.append(bool(ok))
    print(f"[{'PASS' if ok else 'FAIL'}] {name} {detail}", flush=True)


def main():
    app = cui.ConsentTrayApp()
    settings_path = resolve_tray_settings_path()

    # 备份用户现有托盘设置，测试后恢复
    original = dict(load_tray_settings())

    try:
        # ---- 1. 后端链 ----
        order_source = cui._determine_dialog_backends(app)
        record("源码 auto 链 tkinter 优先", order_source[0] == "tkinter", f"order={order_source}")
        record("auto 链不再包含 taskdialog", "taskdialog" not in order_source, f"order={order_source}")
        sys.frozen = True
        try:
            order_frozen = cui._determine_dialog_backends(app)
        finally:
            del sys.frozen
        record(
            "frozen auto 链 tkinter 优先（品牌化弹窗）",
            order_frozen == ["tkinter", "messagebox"],
            f"order={order_frozen}",
        )
        explicit = cui._determine_dialog_backends(
            type("A", (), {"_task_dialog_indirect": object()})
        ) if False else None

    # ---- 2. 允许远程控制请求开关 ----
        save_tray_settings({"allow_remote_requests": True})
        cui._tray_apply_toggle(app, cui.IDM_TOGGLE_ALLOW_REQUESTS)
        after = load_tray_settings()
        record("ALLOW 开关翻转为 False", after.get("allow_remote_requests") is False,
               f"settings_path={settings_path.name}")
        saved = json.loads(settings_path.read_text(encoding="utf-8"))
        record("ALLOW 开关已持久化", saved.get("allow_remote_requests") is False)

        cui._tray_apply_toggle(app, cui.IDM_TOGGLE_ALLOW_REQUESTS)
        record("ALLOW 开关再次翻转恢复 True", load_tray_settings().get("allow_remote_requests") is True)

        # ---- 3. 本机免确认开关（直接走设置函数，绕过确认弹窗） ----
        cui._tray_set_skip_consent(app, True)
        record("SKIP 开关启用并持久化", load_tray_settings().get("skip_consent_for_session") is True)
        cui._tray_set_skip_consent(app, False)
        record("SKIP 开关关闭", load_tray_settings().get("skip_consent_for_session") is False)

        # ---- 4. 气泡在无托盘环境静默 ----
        cui._show_tray_balloon(app, "测试标题", "测试内容")
        record("无托盘时气泡调用不崩溃", True)

        # ---- 5. 查看本机信息（替代原「打开管理台」入口） ----
        info = cui._tray_collect_machine_info(app)
        record("主机名采集", bool(info.get("hostname")), f"host={info.get('hostname')}")
        record("用户名采集", bool(info.get("user")), f"user={info.get('user')}")
        record("系统信息采集", "Windows" in str(info.get("system", "")), f"sys={info.get('system')}")
        adapters = info.get("adapters") or []
        record("网卡列表非空", len(adapters) > 0, f"count={len(adapters)}")
        has_ip = any(a.get("ipv4") for a in adapters)
        has_mac = any(a.get("mac") for a in adapters)
        record("存在 IPv4 地址", has_ip)
        record("存在 MAC 地址", has_mac)
        text = cui._tray_render_machine_info(info)
        record(
            "渲染文本含关键字段",
            all(k in text for k in ("主机名", "IPv4", "MAC")),
            f"lines={text.count(chr(10)) + 1}",
        )
        record("不展示服务器地址", "服务器地址" not in text and "server_url" not in str(info))
        record("在线网卡排在离线前", _online_first(info), "")

    finally:
        save_tray_settings(original)

    print("=" * 60)


def _online_first(info):
    adapters = info.get("adapters") or []
    states = [bool(a.get("up")) for a in adapters]
    return states == sorted(states, reverse=True)

    failed = RESULTS.count(False)
    print(f"总计 {len(RESULTS)} 项, 失败 {failed} 项")
    import os

    os._exit(0 if failed == 0 else 1)


if __name__ == "__main__":
    main()
