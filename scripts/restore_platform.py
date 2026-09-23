# -*- coding: utf-8 -*-
"""Z-View 平台恢复（P4-05）。

前置：
  1. 平台服务已停止（start_platform.ps1 -Action Stop）
  2. MySQL 可连接（cmdb 库存在或先创建）
  3. 备份目录含 cmdb-full.sql / auth_state.json / agent_upgrade/ / certs/
用法：
  python scripts/restore_platform.py --backup D:\\backups\\zview-backup-20260908-120000
"""
import argparse
import os
import shutil
import subprocess
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

APP_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, APP_ROOT)

from config_utils import get_db_config  # noqa: E402


def restore_mysql(backup_dir: str, db_config: dict):
    dump_file = os.path.join(backup_dir, "cmdb-full.sql")
    if not os.path.exists(dump_file):
        print("WARN: cmdb-full.sql not found - skipping MySQL restore")
        return
    mysql = shutil.which("mysql")
    if not mysql:
        for candidate in (
            r"C:\Program Files\MySQL\MySQL Server 8.0\bin\mysql.exe",
            r"C:\Program Files\MySQL\MySQL Server 8.4\bin\mysql.exe",
        ):
            if os.path.exists(candidate):
                mysql = candidate
                break
    if not mysql:
        print("WARN: mysql client not found - restore manually")
        return
    cmd = [
        mysql,
        f"--host={db_config['host']}",
        f"--port={db_config['port']}",
        f"--user={db_config['user']}",
        f"--password={db_config['password']}",
        "--default-character-set=utf8mb4",
    ]
    with open(dump_file, "r", encoding="utf-8") as fh:
        r = subprocess.run(cmd, stdin=fh, capture_output=True, text=True,
                           encoding="utf-8", errors="replace")
    print("MySQL restore:", "OK" if r.returncode == 0 else f"FAILED: {(r.stderr or '')[:300]}")


def copy_tree(src: str, dst: str, label: str):
    if not os.path.isdir(src):
        print(f"WARN: {label} not found in backup")
        return
    count = 0
    for dirpath, dirs, files in os.walk(src):
        rel = os.path.relpath(dirpath, src)
        dst_dir = os.path.join(dst, rel) if rel != "." else dst
        os.makedirs(dst_dir, exist_ok=True)
        for f in files:
            shutil.copy2(os.path.join(dirpath, f), os.path.join(dst_dir, f))
            count += 1
    print(f"{label}: {count} files restored")


def main():
    parser = argparse.ArgumentParser(description="Z-View platform restore")
    parser.add_argument("--backup", required=True, help="备份目录")
    args = parser.parse_args()

    backup_dir = args.backup
    if not os.path.isdir(backup_dir):
        print(f"备份目录不存在: {backup_dir}")
        sys.exit(1)

    db_config = get_db_config()

    print("=== Z-View 恢复流程 ===")
    restore_mysql(backup_dir, db_config)

    auth_src = os.path.join(backup_dir, "auth_state.json")
    if os.path.exists(auth_src):
        shutil.copy2(auth_src, os.path.join(APP_ROOT, "auth_state.json"))
        print("auth_state.json restored")

    upgrade_src = os.path.join(backup_dir, "agent_upgrade")
    if os.path.isdir(upgrade_src):
        copy_tree(upgrade_src, os.path.join(APP_ROOT, "agent_upgrade"), "agent_upgrade")

    certs_src = os.path.join(backup_dir, "certs")
    if os.path.isdir(certs_src):
        copy_tree(certs_src, os.path.join(APP_ROOT, "frontend", "certs"), "TLS certs")

    for f in ("config.json", ".env"):
        src_f = os.path.join(backup_dir, f)
        if os.path.exists(src_f):
            dst_f = os.path.join(APP_ROOT, f)
            if os.path.exists(dst_f):
                backup_cur = dst_f + ".pre-restore"
                shutil.copy2(dst_f, backup_cur)
                print(f"current {f} backed up to {backup_cur}")
            shutil.copy2(src_f, dst_f)
            print(f"config restored: {f}")

    print("\n=== 恢复完成 ===")
    print("后续步骤：")
    print("  1. start_platform.ps1 -Action Start 拉起平台")
    print("  2. 验证 /api/health 与登录")
    print("  3. 若 TLS 证书是新环境首次导入：证书安装到受信任的根证书颁发机构")


if __name__ == "__main__":
    main()
