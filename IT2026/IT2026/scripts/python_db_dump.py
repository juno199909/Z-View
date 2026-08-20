# -*- coding: utf-8 -*-
"""python_db_dump.py：纯 Python MySQL 全量导出（P4-05 fallback）。

用法：
  BACKUP_OUT=输出路径 python python_db_dump.py
"""
import os
import time

import mysql.connector

sys_out = os.environ.get("BACKUP_OUT", r"D:\IT2026\backups\cmdb-python-dump.sql")

# config_utils 在代码根
sys_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
import sys
sys.path.insert(0, sys_root)

from config_utils import get_db_config

db = get_db_config()
conn = mysql.connector.connect(**db, connection_timeout=10)
cur = conn.cursor()

cur.execute("SHOW TABLES")
tables = [row[0] for row in cur.fetchall()]
print(f"tables: {len(tables)}")

out = open(sys_out, "w", encoding="utf-8", newline="\n")
out.write("-- Z-View cmdb backup (python fallback)\n")
out.write(f"-- {time.strftime('%Y-%m-%d %H:%M:%S')}\n")
out.write("SET NAMES utf8mb4;\nSET FOREIGN_KEY_CHECKS=0;\n\n")

for table in tables:
    cur.execute(f"SHOW CREATE TABLE `{table}`")
    create_stmt = cur.fetchone()[1]
    out.write(f"-- Table: {table}\nDROP TABLE IF EXISTS `{table}`;\n{create_stmt};\n\n")
    cur.execute(f"SELECT * FROM `{table}`")
    rows = cur.fetchall()
    if rows:
        cols = [d[0] for d in cur.description]
        col_list = ",".join(f"`{c}`" for c in cols)
        out.write(f"INSERT INTO `{table}` ({col_list}) VALUES\n")
        value_lines = []
        for row in rows:
            vals = []
            for v in row:
                if v is None:
                    vals.append("NULL")
                elif isinstance(v, (int, float)):
                    vals.append(str(v))
                elif isinstance(v, bytes):
                    vals.append("_binary'%s'" % v.decode("utf-8", errors="replace").replace("'", "''"))
                else:
                    s = str(v).replace("\\", "\\\\").replace("'", "\\'").replace("\n", "\\n")
                    vals.append("'%s'" % s)
            value_lines.append("(" + ",".join(vals) + ")")
        out.write(",\n".join(value_lines) + ";\n\n")
    print(f"  {table}: {len(rows)} rows")

out.write("SET FOREIGN_KEY_CHECKS=1;\n")
out.close()
conn.close()
print("python dump:", sys_out, os.path.getsize(sys_out), "bytes")
