# -*- coding: utf-8 -*-
"""数据库连接与时间格式化（P1-06 Repository 基座）。"""
from __future__ import annotations

import mysql.connector
from mysql.connector import Error

from config_utils import get_db_config
from console_utils import safe_console_print


def create_connection():
    """创建数据库连接（会话时区北京）。连接失败返回 None。"""
    try:
        conn = mysql.connector.connect(**get_db_config())
        cursor = conn.cursor()
        cursor.execute("SET time_zone = '+8:00'")
        cursor.close()
        return conn
    except Error as e:
        safe_console_print(f"[DB] Connection failed: {e}")
        return None


def format_datetime(value):
    """统一格式化时间对象为 'YYYY-MM-DD HH:MM:SS' 字符串。"""
    import datetime

    if not value:
        return None
    if isinstance(value, datetime.datetime):
        return value.strftime("%Y-%m-%d %H:%M:%S")
    if isinstance(value, datetime.date):
        return value.strftime("%Y-%m-%d")
    return str(value)


def table_exists(conn, table_name: str) -> bool:
    """检查指定表是否存在（P1-01 从 assets_api 迁出）。"""
    from config_utils import get_db_config
    cursor = conn.cursor()
    try:
        cursor.execute("""
            SELECT COUNT(*)
            FROM information_schema.tables
            WHERE table_schema = %s AND table_name = %s
        """, (get_db_config()["database"], table_name))
        row = cursor.fetchone()
        return bool(row and row[0])
    finally:
        cursor.close()
