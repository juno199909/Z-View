# -*- coding: utf-8 -*-
"""修复 cmdb_agent_consent_ui.py 中的双重编码乱码文案。"""

from pathlib import Path

TARGET = Path(__file__).resolve().parent.parent / "cmdb_agent_consent_ui.py"

# 行号(1-based) -> 完整新行内容
LINE_FIXES = {
    480: '    return str(username or os.environ.get("USERNAME") or "未知用户")',
    555: '        f"{requester} 正在请求远程控制这台终端。\\n\\n"',
    556: '        f"目标终端: {target}\\n"',
    557: '        f"来源地址: {origin}\\n"',
    558: '        f"当前登录用户: {_safe_dialog_username(self)}\\n\\n"',
    559: '        "是否允许本次远程控制？\\n"',
    560: '        f"{remaining_seconds} 秒内未处理将自动拒绝。"',
    644: '        TASKDIALOG_BUTTON(IDYES, "允许"),',
    645: '        TASKDIALOG_BUTTON(IDNO, "拒绝"),',
    697: '    config.pszMainInstruction = "Z-View 远程控制确认"',
    745: '    header = ttk.Label(container, text="Z-View 远程控制确认", font=("Microsoft YaHei UI", 12, "bold"))',
    771: '    allow_button = ttk.Button(button_row, text="允许", command=lambda: _finish(IDYES))',
    774: '    reject_button = ttk.Button(button_row, text="拒绝", command=lambda: _finish(IDNO))',
    830: '    origin = str(request.get("origin") or "未知来源")',
    831: '    target = str(request.get("target") or os.environ.get("COMPUTERNAME") or "当前终端")',
    833: '    title = "Z-View 远程控制确认"',
}


def main():
    text = TARGET.read_text(encoding="utf-8")
    lines = text.splitlines()
    for lineno, new_line in LINE_FIXES.items():
        old = lines[lineno - 1]
        if old == new_line:
            continue
        print(f"L{lineno}:")
        print(f"  - {old.strip()[:76]}")
        print(f"  + {new_line.strip()[:76]}")
        lines[lineno - 1] = new_line
    TARGET.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"\n已写回 {TARGET}")


if __name__ == "__main__":
    main()
