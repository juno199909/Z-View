# -*- coding: utf-8 -*-
"""终端分组路由（P1-01 从 assets_api 迁出）。"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException
from mysql.connector import Error

from zvplatform.db import create_connection


router = APIRouter(prefix="/api/v1/groups", tags=["groups"])


@router.get("")
def get_groups():
    """获取所有分组"""
    conn = create_connection()
    if not conn:
        raise HTTPException(status_code=500, detail="Database connection failed")

    try:
        cursor = conn.cursor(dictionary=True)

        cursor.execute("""
            SELECT g.id, g.name, g.description, g.created_at,
                   COUNT(a.id) as asset_count
            FROM asset_groups g
            LEFT JOIN assets a ON a.group_id = g.id AND a.deleted_at IS NULL
            GROUP BY g.id, g.name, g.description, g.created_at
            ORDER BY g.name
        """)

        groups = cursor.fetchall()

        # 格式化日期
        for group in groups:
            if group.get('created_at'):
                group['created_at'] = group['created_at'].strftime('%Y-%m-%d %H:%M:%S')

        return {"data": groups, "total": len(groups)}

    except Error as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        cursor.close()
        conn.close()


@router.post("")
def create_group(data: dict):
    """创建分组"""
    name = data.get('name')
    description = data.get('description', '')

    if not name:
        raise HTTPException(status_code=400, detail="Group name is required")

    conn = create_connection()
    if not conn:
        raise HTTPException(status_code=500, detail="Database connection failed")

    try:
        cursor = conn.cursor()

        # 检查名称是否重复
        cursor.execute("SELECT id FROM asset_groups WHERE name = %s", (name,))
        if cursor.fetchone():
            raise HTTPException(status_code=400, detail="Group name already exists")

        cursor.execute("""
            INSERT INTO asset_groups (name, description, created_at)
            VALUES (%s, %s, NOW())
        """, (name, description))

        conn.commit()
        group_id = cursor.lastrowid

        return {"message": "Group created successfully", "id": group_id}

    except HTTPException:
        raise
    except Error as e:
        conn.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        cursor.close()
        conn.close()


@router.put("/{group_id}")
def update_group(group_id: int, data: dict):
    """更新分组"""
    name = data.get('name')
    description = data.get('description')

    conn = create_connection()
    if not conn:
        raise HTTPException(status_code=500, detail="Database connection failed")

    try:
        cursor = conn.cursor()

        # 检查分组是否存在
        cursor.execute("SELECT id, name, description FROM asset_groups WHERE id = %s", (group_id,))
        row = cursor.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Group not found")

        current_name, current_desc = row[1], row[2]
        new_name = name if name is not None else current_name
        new_desc = description if description is not None else (current_desc or '')

        if not new_name:
            raise HTTPException(status_code=400, detail="Group name is required")

        # 检查名称是否与其他分组重复
        if new_name != current_name:
            cursor.execute("SELECT id FROM asset_groups WHERE name = %s AND id != %s", (new_name, group_id))
            if cursor.fetchone():
                raise HTTPException(status_code=400, detail="Group name already exists")

        cursor.execute("""
            UPDATE asset_groups
            SET name = %s, description = %s
            WHERE id = %s
        """, (new_name, new_desc, group_id))

        conn.commit()

        return {"message": "Group updated successfully"}

    except HTTPException:
        raise
    except Error as e:
        conn.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        cursor.close()
        conn.close()


@router.delete("/{group_id}")
def delete_group(group_id: int):
    """删除分组"""
    conn = create_connection()
    if not conn:
        raise HTTPException(status_code=500, detail="Database connection failed")

    try:
        cursor = conn.cursor()

        # 检查分组是否存在
        cursor.execute("SELECT id FROM asset_groups WHERE id = %s", (group_id,))
        if not cursor.fetchone():
            raise HTTPException(status_code=404, detail="Group not found")

        # 检查是否有资产使用该分组
        cursor.execute("SELECT COUNT(*) as count FROM assets WHERE group_id = %s AND deleted_at IS NULL", (group_id,))
        result = cursor.fetchone()
        if result and result[0] > 0:
            raise HTTPException(status_code=400, detail=f"Cannot delete group with {result[0]} assets")

        # 删除分组
        cursor.execute("DELETE FROM asset_groups WHERE id = %s", (group_id,))
        conn.commit()

        return {"message": "Group deleted successfully"}

    except HTTPException:
        raise
    except Error as e:
        conn.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        cursor.close()
        conn.close()
