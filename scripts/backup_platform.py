# -*- coding: utf-8 -*-
"""Z-View 平台备份（P4-05）。

备份内容：
  1. MySQL cmdb 库（mysqldump 完整备份）
  2. 认证状态（auth_state.json）
  3. Agent 升级包清单（agent_upgrade/manifest.json + 各版本）
  4. TLS 证书（frontend/certs/）
  5. 配置（config.json / config_utils 所需 env）
用法：
  python scripts/backup_platform.py --output D:\\backups\\zview-20260908
注意：需要 mysqldump 在 PATH 或 DB_TOOLS_DIR；建议每日计划任务执行。
"""
import argparse
import os
import shutil
import subprocess
import sys
import time

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

APP_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, APP_ROOT)

from config_utils import get_db_config  # noqa: E402


def backup_mysql(output_dir: str, db_config: dict) -> str | None:
    """mysqldump 完整备份 cmdb 库"""
    mysqldump = shutil.which("mysqldump")
    if not mysqldump:
        # 常见安装位置
        for candidate in (
            r"C:\Program Files\MySQL\MySQL Server 8.0\bin\mysqldump.exe",
            r"C:\Program Files\MySQL\MySQL Server 8.4\bin\mysqldump.exe",
        ):
            if os.path.exists(candidate):
                mysqldump = candidate
                break
    if not mysqldump:
        print("WARN: mysqldump not found - skipping MySQL backup")
        return None

    dump_file = os.path.join(output_dir, "cmdb-full.sql")
    cmd = [
        mysqldump,
        f"--host={db_config['host']}",
        f"--port={db_config['port']}",
        f"--user={db_config['user']}",
        f"--password={db_config['password']}",
        "--single-transaction",
        "--routines",
        "--triggers",
        "--databases", db_config["database"],
        "--result-file=" + dump_file,
    ]
    r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if r.returncode != 0:
        print("mysqldump FAILED:", (r.stderr or "")[:300])
        return None
    size = os.path.getsize(dump_file)
    print(f"MySQL dump: {dump_file} ({size} bytes)")
    return dump_file


def copy_tree(src: str, dst: str, label: str, max_bytes: int = 2 * 1024 * 1024 * 1024) -> int:
    """复制目录（跳过大文件可选）"""
    if not os.path.isdir(src):
        print(f"WARN: {label} not found: {src}")
        return 0
    count = 0
    total = 0
    for dirpath, dirs, files in os.walk(src):
        rel = os.path.relpath(dirpath, src)
        dst_dir = os.path.join(dst, rel) if rel != "." else dst
        os.makedirs(dst_dir, exist_ok=True)
        for f in files:
            src_file = os.path.join(dirpath, f)
            try:
                if os.path.getsize(src_file) > max_bytes:
                    print(f"SKIP large: {f}")
                    continue
                shutil.copy2(src_file, os.path.join(dst_dir, f))
                count += 1
                total += os.path.getsize(src_file)
            except (PermissionError, OSError) as e:
                print(f"SKIP locked: {f} ({e})")
    print(f"{label}: {count} files ({total} bytes)")
    return count


def main():
    parser = argparse.ArgumentParser(description="Z-View platform backup")
    parser.add_argument("--output", required=True, help="备份输出目录")
    args = parser.parse_args()

    timestamp = time.strftime("%Y%m%d-%H%M%S")
    output_dir = os.path.join(args.output, f"zview-backup-{timestamp}")
    os.makedirs(output_dir, exist_ok=True)

    db_config = get_db_config()
    started = time.time()

    # 1) MySQL
    backup_mysql(output_dir, db_config)

    # 2) 认证状态
    auth_state = os.path.join(APP_ROOT, "auth_state.json")
    if os.path.exists(auth_state):
        shutil.copy2(auth_state, os.path.join(output_dir, "auth_state.json"))
        print("auth_state.json copied")

    # 3) Agent 升级包（manifest + 各版本 exe，跳过超 200MB 单文件由 copy_tree 处理）
    upgrade_dir = os.path.join(APP_ROOT, "agent_upgrade")
    if os.path.isdir(upgrade_dir):
        copy_tree(upgrade_dir, os.path.join(output_dir, "agent_upgrade"), "agent_upgrade")

    # 4) TLS 证书
    certs_dir = os.path.join(APP_ROOT, "frontend", "certs")
    if os.path.isdir(certs_dir):
        copy_tree(certs_dir, os.path.join(output_dir, "certs"), "TLS certs")

    # 5) 配置
    for f in ("config.json", ".env", "requirements.txt"):
        src_f = os.path.join(APP_ROOT, f)
        if os.path.exists(src_f):
            shutil.copy2(src_f, os.path.join(output_dir, f))
            print(f"config copied: {f}")

    # 清单
    manifest = {
        "backup_time": timestamp,
        "output_dir": output_dir,
        "contents": os.listdir(output_dir),
        "platform": "Z-View v3.1",
    }
    import json
    json.dump(manifest, open(os.path.join(output_dir, "backup-manifest.json"), "w",
                             encoding="utf-8"), ensure_ascii=False, indent=2)

    print(f"\n备份完成: {output_dir} (耗时 {time.time()-started:.0f}s)")
    print("恢复流程见 scripts/restore_platform.py")


if __name__ == "__main__":
    import shutil  # noqa
    main()
