# -*- coding: utf-8 -*-
"""软件安装记录查询路由（1.9.55 自 assets_api 迁入 #16 模块化，逻辑逐字保留）。

注意：软件管控/仓库代理（8081 通道）见 assets_api 挂载的 software 代理路由；
本模块仅覆盖平台库 asset_software 的查询端点。
"""
from typing import Optional

from fastapi import APIRouter, HTTPException, Query, Request
from mysql.connector import Error

from zvplatform.db import create_connection as get_db_connection


router = APIRouter(tags=["software-records"])


@router.get("/api/v1/software/all")
def get_all_software(asset_id: Optional[int] = Query(default=None)):
    """获取所有软件安装记录（详细清单）"""
    conn = get_db_connection()
    if not conn:
        raise HTTPException(status_code=500, detail="Database connection failed")

    try:
        cursor = conn.cursor(dictionary=True)

        base_sql = """
            SELECT
                s.id,
                s.software_name,
                s.version,
                s.vendor,
                s.install_date,
                s.size,
                s.size_mb,
                a.id as asset_id,
                a.hostname,
                a.ip_address
            FROM asset_software s
            LEFT JOIN assets a ON s.asset_id = a.id
            WHERE a.deleted_at IS NULL
        """
        if asset_id is not None:
            cursor.execute(base_sql + " AND s.asset_id = %s ORDER BY s.software_name", (asset_id,))
        else:
            cursor.execute(base_sql + " ORDER BY s.software_name, a.hostname")

        software_list = cursor.fetchall()

        return {"data": software_list}

    except Error as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        cursor.close()
        conn.close()






@router.get("/api/v1/software/stats")
def get_software_stats(limit: int = Query(default=10, ge=1, le=100)):
    """获取软件安装统计（Top N）"""
    conn = get_db_connection()
    if not conn:
        raise HTTPException(status_code=500, detail="Database connection failed")

    try:
        cursor = conn.cursor(dictionary=True)

        cursor.execute("""
            SELECT
                s.software_name,
                s.version,
                s.vendor,
                COUNT(DISTINCT s.asset_id) as install_count,
                GROUP_CONCAT(DISTINCT a.hostname ORDER BY a.hostname SEPARATOR ', ') as hostnames,
                GROUP_CONCAT(DISTINCT a.hostname ORDER BY a.hostname SEPARATOR ', ') as installed_assets
            FROM asset_software s
            JOIN assets a ON s.asset_id = a.id AND a.deleted_at IS NULL
            GROUP BY s.software_name, s.version, s.vendor
            ORDER BY install_count DESC, s.software_name
            LIMIT %s
        """, (limit,))

        stats = cursor.fetchall()

        return {"data": stats, "total": len(stats)}

    except Error as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        cursor.close()
        conn.close()


# ============================================================
# 后台守护线程启动入口（修复：历史版本丢失 def 行导致 worker 从未启动）

