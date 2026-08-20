"""
软件管理API - 完整版本
合并了基础API和Agent通信API
"""

from fastapi import FastAPI, HTTPException, UploadFile, File, Form, Query, Response, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from typing import Any, Dict, List, Optional
from datetime import datetime
import mysql.connector
from mysql.connector import Error
import json
import hashlib
import os
import re
import shutil
import csv
import io

from auth_utils import (
    extract_bearer_token,
    get_request_username,
    is_exempt_path,
    require_agent_request,
    require_request_permission,
    verify_access_token,
)
from config_utils import get_cors_middleware_options, get_db_config, get_env

# 数据库配置
DB_CONFIG = get_db_config()

# 软件包存储路径
PACKAGE_STORAGE_PATH = get_env("ZVIEW_PACKAGE_STORAGE_PATH", "C:\\CMDB-Packages") or "C:\\CMDB-Packages"
if not os.path.exists(PACKAGE_STORAGE_PATH):
    os.makedirs(PACKAGE_STORAGE_PATH)

app = FastAPI(title="CMDB Software Management API", version="1.0.0")

# 配置CORS
app.add_middleware(
    CORSMiddleware,
    **get_cors_middleware_options(),
)

AUTH_EXEMPTIONS = (
    {"path": "/api/v1/software/agent/policies", "methods": ["POST"]},
    {"path": "/api/v1/software/agent/tasks/poll", "methods": ["POST"]},
    {
        "pattern": "/api/v1/software/agent/packages/*/download",
        "methods": ["GET"],
    },
    {
        "pattern": "/api/v1/software/agent/task-results/*",
        "methods": ["PUT"],
    },
    {
        "pattern": "/api/v1/software/task-results/*",
        "methods": ["PUT"],
    },
    {
        "pattern": "/api/v1/software/task-results/*/logs",
        "methods": ["POST"],
    },
    {"path": "/api/v1/packages", "methods": ["GET"]},
    {"path": "/api/v1/software/health", "methods": ["GET"]},
)


@app.middleware("http")
async def auth_middleware(request: Request, call_next):
    if is_exempt_path(request.url.path, request.method, AUTH_EXEMPTIONS):
        return await call_next(request)

    token = extract_bearer_token(request)
    auth_user = verify_access_token(token)
    if not auth_user:
        return JSONResponse(status_code=401, content={"detail": "Unauthorized"})

    try:
        require_request_permission(auth_user, request.url.path, request.method)
    except HTTPException as exc:
        return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})

    request.state.auth_user = auth_user
    return await call_next(request)

def get_db_connection():
    """获取数据库连接"""
    try:
        conn = mysql.connector.connect(**DB_CONFIG)
        cursor = conn.cursor()
        cursor.execute("SET time_zone = '+8:00'")
        cursor.close()
        return conn
    except Error as e:
        print(f"数据库连接失败: {e}")
        return None


def normalize_agent_asset_id(value: Any) -> int:
    try:
        asset_id = int(value)
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail="Invalid asset_id") from exc

    if asset_id <= 0:
        raise HTTPException(status_code=400, detail="Invalid asset_id")
    return asset_id


def get_agent_asset_info(cursor, asset_id: Any) -> Dict[str, Any]:
    normalized_asset_id = normalize_agent_asset_id(asset_id)
    cursor.execute(
        """
        SELECT id, group_id
        FROM assets
        WHERE id = %s AND deleted_at IS NULL
        LIMIT 1
        """,
        (normalized_asset_id,),
    )
    asset_info = cursor.fetchone()
    if not asset_info:
        raise HTTPException(status_code=404, detail="Asset not found")
    return asset_info


def ensure_agent_package_access(cursor, asset_id: int, package_id: int) -> None:
    cursor.execute(
        """
        SELECT r.id
        FROM software_task_results r
        INNER JOIN software_tasks t ON t.id = r.task_id
        WHERE r.asset_id = %s
          AND t.package_id = %s
          AND r.status IN ('pending', 'failed', 'downloading', 'installing')
          AND (t.scheduled_time IS NULL OR t.scheduled_time <= NOW())
          AND (
              r.status IN ('downloading', 'installing')
              OR r.retry_count < t.retry_count
          )
        ORDER BY r.id DESC
        LIMIT 1
        """,
        (asset_id, package_id),
    )
    if not cursor.fetchone():
        raise HTTPException(status_code=403, detail="Package download is not authorized for this asset")


def merge_task_log_content(existing_log: Optional[str], new_log: Optional[str], append: bool = True) -> Optional[str]:
    """合并任务日志内容，便于 Agent 分批上报日志。"""
    incoming_text = str(new_log or "").strip()
    if not incoming_text:
        return existing_log

    if not append:
        return incoming_text

    current_text = str(existing_log or "").rstrip()
    if not current_text:
        return incoming_text
    return f"{current_text}\n{incoming_text}"

# ============================================================
# Pydantic模型
# ============================================================

class SoftwareTask(BaseModel):
    task_name: str
    task_type: str
    package_id: Optional[int] = None
    software_name: Optional[str] = None
    target_type: str
    target_ids: List[int] = Field(default_factory=list)
    schedule_type: str = "immediate"
    scheduled_time: Optional[datetime] = None
    priority: str = "normal"


class SoftwarePackageUpdate(BaseModel):
    package_name: Optional[str] = None
    display_name: Optional[str] = None
    version: Optional[str] = None
    category: Optional[str] = None
    vendor: Optional[str] = None
    description: Optional[str] = None
    install_command: Optional[str] = None
    uninstall_command: Optional[str] = None
    requires_reboot: Optional[bool] = None
    architecture: Optional[str] = None
    status: Optional[str] = None

# ============================================================
# 软件包管理API
# ============================================================


def serialize_package_record(package: Dict[str, Any]) -> Dict[str, Any]:
    """统一格式化软件包记录，避免列表和详情返回字段不一致。"""
    normalized = dict(package)
    for date_field in ("created_at", "updated_at", "deleted_at"):
        if normalized.get(date_field):
            normalized[date_field] = normalized[date_field].strftime('%Y-%m-%d %H:%M:%S')
    if normalized.get("file_size") is not None:
        normalized["file_size_readable"] = format_file_size(normalized["file_size"])
    return normalized

@app.get("/api/v1/software/packages")
def get_packages(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=500),
    category: Optional[str] = None,
    status: Optional[str] = None,
    keyword: Optional[str] = None
):
    """获取软件包列表"""
    conn = get_db_connection()
    if not conn:
        raise HTTPException(status_code=500, detail="Database connection failed")

    try:
        cursor = conn.cursor(dictionary=True)

        where_clauses = ["deleted_at IS NULL"]
        params = []

        if category:
            where_clauses.append("category = %s")
            params.append(category)
        if status:
            where_clauses.append("status = %s")
            params.append(status)
        if keyword:
            where_clauses.append("(package_name LIKE %s OR display_name LIKE %s)")
            params.extend([f"%{keyword}%", f"%{keyword}%"])

        where_sql = " AND ".join(where_clauses)

        cursor.execute(f"SELECT COUNT(*) as total FROM software_packages WHERE {where_sql}", params)
        total = cursor.fetchone()['total']

        offset = (page - 1) * page_size
        cursor.execute(f"""
            SELECT id, package_name, display_name, version, category, vendor,
                   file_name, file_size, architecture, status, download_count, install_count,
                   upload_by, created_at
            FROM software_packages
            WHERE {where_sql}
            ORDER BY created_at DESC
            LIMIT %s OFFSET %s
        """, params + [page_size, offset])

        packages = cursor.fetchall()

        for index, pkg in enumerate(packages):
            packages[index] = serialize_package_record(pkg)

        return {"data": packages, "total": total, "page": page, "page_size": page_size}

    except Error as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        cursor.close()
        conn.close()


@app.get("/api/v1/packages")
def get_packages_legacy(
    request: Request,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=500),
    category: Optional[str] = None,
    status: Optional[str] = None,
    keyword: Optional[str] = None,
    name: Optional[str] = None
):
    """兼容旧版 Agent 的软件包查询接口"""
    effective_keyword = keyword or name
    response = get_packages(
        page=page,
        page_size=page_size,
        category=category,
        status=status,
        keyword=effective_keyword
    )

    base_url = str(request.base_url).rstrip("/")
    legacy_packages = []
    for package in response["data"]:
        legacy_package = dict(package)
        legacy_package["package_id"] = package["id"]
        legacy_package["name"] = package.get("display_name") or package.get("package_name")
        legacy_package["download_url"] = f"{base_url}/api/v1/software/agent/packages/{package['id']}/download"
        legacy_packages.append(legacy_package)

    response["data"] = legacy_packages
    return response

@app.post("/api/v1/software/packages/upload")
async def upload_package(
    request: Request,
    file: UploadFile = File(...),
    package_info: str = Form(...)
):
    """上传软件包"""
    conn = get_db_connection()
    if not conn:
        raise HTTPException(status_code=500, detail="Database connection failed")

    file_path = None
    try:
        info = json.loads(package_info)

        file_content = await file.read()
        file_hash = hashlib.sha256(file_content).hexdigest()
        file_size = len(file_content)

        cursor = conn.cursor(dictionary=True)
        cursor.execute("""
            SELECT id FROM software_packages
            WHERE package_name = %s AND version = %s AND deleted_at IS NULL
        """, (info['package_name'], info['version']))

        if cursor.fetchone():
            raise HTTPException(status_code=400, detail="此版本的软件包已存在")

        file_path = os.path.join(PACKAGE_STORAGE_PATH, f"{info['package_name']}_{info['version']}_{file.filename}")

        with open(file_path, 'wb') as f:
            f.write(file_content)

        uploader = get_request_username(request, fallback=info.get("upload_by") or "system")

        cursor.execute("""
            INSERT INTO software_packages (
                package_name, display_name, version, category, vendor,
                description, file_name, file_size, file_path, file_hash,
                install_command, uninstall_command, requires_reboot,
                architecture, status, upload_by
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """, (
            info['package_name'], info['display_name'], info['version'], info['category'],
            info.get('vendor'), info.get('description'), file.filename, file_size,
            file_path, file_hash, info.get('install_command'), info.get('uninstall_command'),
            info.get('requires_reboot', False), info.get('architecture', 'all'),
            'available', uploader
        ))

        package_id = cursor.lastrowid
        conn.commit()

        return {"message": "软件包上传成功", "package_id": package_id, "file_hash": file_hash}

    except json.JSONDecodeError:
        if file_path and os.path.exists(file_path):
            os.remove(file_path)
        raise HTTPException(status_code=400, detail="无效的软件包信息格式")
    except Error as e:
        if file_path and os.path.exists(file_path):
            os.remove(file_path)
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        cursor.close()
        conn.close()


@app.get("/api/v1/software/packages/categories")
def get_package_categories():
    """获取软件包分类汇总。"""
    conn = get_db_connection()
    if not conn:
        raise HTTPException(status_code=500, detail="Database connection failed")

    try:
        cursor = conn.cursor(dictionary=True)
        cursor.execute(
            """
            SELECT COALESCE(category, 'other') AS category, COUNT(*) AS package_count
            FROM software_packages
            WHERE deleted_at IS NULL
            GROUP BY COALESCE(category, 'other')
            ORDER BY package_count DESC, category ASC
            """
        )
        category_rows = cursor.fetchall()
        category_counts = {row["category"]: int(row["package_count"] or 0) for row in category_rows}

        default_categories = [
            {"value": "office", "label": "办公软件"},
            {"value": "dev", "label": "开发工具"},
            {"value": "security", "label": "安全软件"},
            {"value": "other", "label": "其他"},
        ]

        data = []
        seen_categories = set()
        for item in default_categories:
            category_value = item["value"]
            data.append({
                "value": category_value,
                "label": item["label"],
                "count": category_counts.get(category_value, 0),
            })
            seen_categories.add(category_value)

        for category_value, count in category_counts.items():
            if category_value in seen_categories:
                continue
            data.append({
                "value": category_value,
                "label": category_value,
                "count": count,
            })

        return {"data": data}

    except Error as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        cursor.close()
        conn.close()


@app.get("/api/v1/software/packages/stats")
def get_package_stats():
    """获取软件包统计信息。"""
    conn = get_db_connection()
    if not conn:
        raise HTTPException(status_code=500, detail="Database connection failed")

    try:
        cursor = conn.cursor(dictionary=True)
        cursor.execute(
            """
            SELECT
                COUNT(*) AS total,
                SUM(CASE WHEN status = 'available' THEN 1 ELSE 0 END) AS available_count,
                SUM(CASE WHEN status = 'deprecated' THEN 1 ELSE 0 END) AS deprecated_count,
                COALESCE(SUM(download_count), 0) AS total_download_count,
                COALESCE(SUM(install_count), 0) AS total_install_count,
                COALESCE(SUM(file_size), 0) AS total_file_size
            FROM software_packages
            WHERE deleted_at IS NULL
            """
        )
        stats = cursor.fetchone() or {}

        cursor.execute(
            """
            SELECT COALESCE(category, 'other') AS category, COUNT(*) AS package_count
            FROM software_packages
            WHERE deleted_at IS NULL
            GROUP BY COALESCE(category, 'other')
            """
        )
        category_rows = cursor.fetchall()

        return {
            "total": int(stats.get("total") or 0),
            "available_count": int(stats.get("available_count") or 0),
            "deprecated_count": int(stats.get("deprecated_count") or 0),
            "total_download_count": int(stats.get("total_download_count") or 0),
            "total_install_count": int(stats.get("total_install_count") or 0),
            "total_file_size": int(stats.get("total_file_size") or 0),
            "category_stats": [
                {
                    "category": row["category"],
                    "count": int(row["package_count"] or 0),
                }
                for row in category_rows
            ],
        }

    except Error as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        cursor.close()
        conn.close()


@app.get("/api/v1/software/packages/{package_id}")
def get_package_detail(package_id: int):
    """获取单个软件包详情。"""
    conn = get_db_connection()
    if not conn:
        raise HTTPException(status_code=500, detail="Database connection failed")

    try:
        cursor = conn.cursor(dictionary=True)
        cursor.execute(
            """
            SELECT id, package_name, display_name, version, category, vendor,
                   description, file_name, file_size, file_path, file_hash,
                   install_command, uninstall_command, requires_reboot,
                   architecture, status, download_count, install_count,
                   upload_by, created_at, updated_at, deleted_at
            FROM software_packages
            WHERE id = %s AND deleted_at IS NULL
            LIMIT 1
            """,
            (package_id,),
        )

        package = cursor.fetchone()
        if not package:
            raise HTTPException(status_code=404, detail="Package not found")

        return {"data": serialize_package_record(package)}

    except Error as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        cursor.close()
        conn.close()


@app.put("/api/v1/software/packages/{package_id}")
def update_package(package_id: int, payload: SoftwarePackageUpdate):
    """更新软件包信息。"""
    conn = get_db_connection()
    if not conn:
        raise HTTPException(status_code=500, detail="Database connection failed")

    try:
        update_data = payload.dict(exclude_unset=True)
        if not update_data:
            raise HTTPException(status_code=400, detail="No fields to update")

        if "status" in update_data and update_data["status"] not in {"available", "deprecated"}:
            raise HTTPException(status_code=400, detail="Invalid status")

        cursor = conn.cursor(dictionary=True)
        cursor.execute(
            """
            SELECT package_name, version
            FROM software_packages
            WHERE id = %s AND deleted_at IS NULL
            LIMIT 1
            """,
            (package_id,),
        )
        current_package = cursor.fetchone()
        if not current_package:
            raise HTTPException(status_code=404, detail="Package not found")

        next_package_name = update_data.get("package_name", current_package["package_name"])
        next_version = update_data.get("version", current_package["version"])
        cursor.execute(
            """
            SELECT id
            FROM software_packages
            WHERE package_name = %s AND version = %s AND deleted_at IS NULL AND id <> %s
            LIMIT 1
            """,
            (next_package_name, next_version, package_id),
        )
        if cursor.fetchone():
            raise HTTPException(status_code=400, detail="此版本的软件包已存在")

        update_fields = []
        params = []
        for field in [
            "package_name",
            "display_name",
            "version",
            "category",
            "vendor",
            "description",
            "install_command",
            "uninstall_command",
            "requires_reboot",
            "architecture",
            "status",
        ]:
            if field in update_data:
                update_fields.append(f"{field} = %s")
                params.append(update_data[field])

        if not update_fields:
            raise HTTPException(status_code=400, detail="No fields to update")

        update_fields.append("updated_at = NOW()")
        params.append(package_id)

        cursor.execute(
            f"""
            UPDATE software_packages
            SET {', '.join(update_fields)}
            WHERE id = %s AND deleted_at IS NULL
            """,
            params,
        )
        conn.commit()

        cursor.execute(
            """
            SELECT id, package_name, display_name, version, category, vendor,
                   description, file_name, file_size, file_path, file_hash,
                   install_command, uninstall_command, requires_reboot,
                   architecture, status, download_count, install_count,
                   upload_by, created_at, updated_at, deleted_at
            FROM software_packages
            WHERE id = %s
            LIMIT 1
            """,
            (package_id,),
        )
        updated_package = cursor.fetchone()
        return {"message": "Package updated successfully", "data": serialize_package_record(updated_package)}

    except Error as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        cursor.close()
        conn.close()


@app.get("/api/v1/software/packages/download/{package_id}")
def download_package(package_id: int, request: Request):
    """管理员下载软件包。"""
    conn = get_db_connection()
    if not conn:
        raise HTTPException(status_code=500, detail="Database connection failed")

    try:
        cursor = conn.cursor(dictionary=True)
        cursor.execute(
            """
            SELECT file_name, file_path, file_hash
            FROM software_packages
            WHERE id = %s AND status = 'available' AND deleted_at IS NULL
            LIMIT 1
            """,
            (package_id,),
        )
        package = cursor.fetchone()
        if not package or not os.path.exists(package["file_path"]):
            raise HTTPException(status_code=404, detail="Package not found")

        cursor.execute(
            "UPDATE software_packages SET download_count = download_count + 1 WHERE id = %s",
            (package_id,),
        )
        conn.commit()

        return FileResponse(
            path=package["file_path"],
            filename=package["file_name"],
            media_type="application/octet-stream",
            headers={"X-File-Hash": package["file_hash"]},
        )

    except Error as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        cursor.close()
        conn.close()


# ============================================================
# 任务管理API
# ============================================================

def resolve_task_target_asset_ids(cursor, target_type: str, target_ids: List[int]) -> List[int]:
    """Resolve task targets to concrete active asset IDs before creating task results."""
    normalized_type = str(target_type or "").strip().lower()
    normalized_ids = []
    seen = set()
    for value in target_ids or []:
        try:
            item_id = int(value)
        except (TypeError, ValueError):
            continue
        if item_id > 0 and item_id not in seen:
            seen.add(item_id)
            normalized_ids.append(item_id)

    if normalized_type == "asset":
        if not normalized_ids:
            raise HTTPException(status_code=400, detail="At least one asset target is required")
        placeholders = ", ".join(["%s"] * len(normalized_ids))
        cursor.execute(
            f"""
            SELECT id
            FROM assets
            WHERE deleted_at IS NULL
              AND id IN ({placeholders})
            ORDER BY id
            """,
            normalized_ids,
        )
    elif normalized_type == "group":
        if not normalized_ids:
            raise HTTPException(status_code=400, detail="At least one group target is required")
        placeholders = ", ".join(["%s"] * len(normalized_ids))
        cursor.execute(
            f"""
            SELECT id
            FROM assets
            WHERE deleted_at IS NULL
              AND group_id IN ({placeholders})
            ORDER BY id
            """,
            normalized_ids,
        )
    elif normalized_type == "all":
        cursor.execute(
            """
            SELECT id
            FROM assets
            WHERE deleted_at IS NULL
            ORDER BY id
            """
        )
    else:
        raise HTTPException(status_code=400, detail="Unsupported task target type")

    return [int(row[0]) for row in cursor.fetchall()]


@app.post("/api/v1/software/tasks")
def create_task(task: SoftwareTask, request: Request):
    """创建软件任务"""
    conn = get_db_connection()
    if not conn:
        raise HTTPException(status_code=500, detail="Database connection failed")

    try:
        cursor = conn.cursor()

        target_asset_ids = resolve_task_target_asset_ids(cursor, task.target_type, task.target_ids)
        if not target_asset_ids:
            raise HTTPException(status_code=400, detail="No active assets matched the selected targets")

        target_count = len(target_asset_ids)

        created_by = get_request_username(request)

        cursor.execute("""
            INSERT INTO software_tasks (
                task_name, task_type, package_id, software_name,
                schedule_type, scheduled_time, target_type, target_ids,
                target_count, priority, status, created_by
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """, (
            task.task_name, task.task_type, task.package_id, task.software_name,
            task.schedule_type, task.scheduled_time, task.target_type,
            json.dumps(task.target_ids), target_count, task.priority, 'pending', created_by
        ))

        task_id = cursor.lastrowid

        for asset_id in target_asset_ids:
            cursor.execute("""
                INSERT INTO software_task_results (task_id, asset_id, status)
                VALUES (%s, %s, 'pending')
            """, (task_id, asset_id))

        conn.commit()

        return {"message": "任务创建成功", "task_id": task_id, "target_count": target_count}

    except Error as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        cursor.close()
        conn.close()

@app.get("/api/v1/software/tasks")
def get_tasks(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=500),
    task_type: Optional[str] = None,
    software_name: Optional[str] = None,
    status: Optional[str] = None,
    keyword: Optional[str] = None
):
    """获取任务列表"""
    conn = get_db_connection()
    if not conn:
        raise HTTPException(status_code=500, detail="Database connection failed")

    try:
        cursor = conn.cursor(dictionary=True)
        where_clauses = ["1=1"]
        params: List[Any] = []

        if task_type:
            where_clauses.append("t.task_type = %s")
            params.append(task_type)
        if software_name:
            where_clauses.append("t.software_name = %s")
            params.append(software_name)
        if status:
            where_clauses.append("t.status = %s")
            params.append(status)
        if keyword:
            where_clauses.append("(t.task_name LIKE %s OR t.software_name LIKE %s OR p.display_name LIKE %s)")
            keyword_like = f"%{keyword}%"
            params.extend([keyword_like, keyword_like, keyword_like])

        where_sql = " AND ".join(where_clauses)

        cursor.execute(f"""
            SELECT COUNT(*) as total
            FROM software_tasks t
            LEFT JOIN software_packages p ON t.package_id = p.id
            WHERE {where_sql}
        """, params)
        total = cursor.fetchone()['total']

        offset = (page - 1) * page_size
        cursor.execute(f"""
            SELECT t.*, p.display_name as package_display_name
            FROM software_tasks t
            LEFT JOIN software_packages p ON t.package_id = p.id
            WHERE {where_sql}
            ORDER BY t.created_at DESC
            LIMIT %s OFFSET %s
        """, params + [page_size, offset])

        tasks = cursor.fetchall()

        for task in tasks:
            for date_field in ['created_at', 'updated_at', 'start_time', 'end_time', 'scheduled_time']:
                if task.get(date_field):
                    task[date_field] = task[date_field].strftime('%Y-%m-%d %H:%M:%S')

        return {"data": tasks, "total": total, "page": page, "page_size": page_size}

    except Error as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        cursor.close()
        conn.close()


@app.get("/api/v1/software/tasks/stats")
def get_task_stats(
    task_type: Optional[str] = None,
    software_name: Optional[str] = None,
    status: Optional[str] = None,
    keyword: Optional[str] = None
):
    """获取软件任务统计，统计口径基于全量任务而非当前页"""
    conn = get_db_connection()
    if not conn:
        raise HTTPException(status_code=500, detail="Database connection failed")

    try:
        cursor = conn.cursor(dictionary=True)
        where_clauses = ["1=1"]
        params: List[Any] = []

        if task_type:
            where_clauses.append("t.task_type = %s")
            params.append(task_type)
        if software_name:
            where_clauses.append("t.software_name = %s")
            params.append(software_name)
        if status:
            where_clauses.append("t.status = %s")
            params.append(status)
        if keyword:
            where_clauses.append("(t.task_name LIKE %s OR t.software_name LIKE %s OR p.display_name LIKE %s)")
            keyword_like = f"%{keyword}%"
            params.extend([keyword_like, keyword_like, keyword_like])

        where_sql = " AND ".join(where_clauses)
        cursor.execute(f"""
            SELECT
                COUNT(*) AS total,
                SUM(CASE WHEN t.status = 'pending' THEN 1 ELSE 0 END) AS pending,
                SUM(CASE WHEN t.status = 'running' THEN 1 ELSE 0 END) AS running,
                SUM(CASE WHEN t.status = 'completed' THEN 1 ELSE 0 END) AS completed,
                SUM(CASE WHEN t.status = 'failed' THEN 1 ELSE 0 END) AS failed
            FROM software_tasks t
            LEFT JOIN software_packages p ON t.package_id = p.id
            WHERE {where_sql}
        """, params)
        stats = cursor.fetchone() or {}

        return {
            "total": int(stats.get("total") or 0),
            "pending": int(stats.get("pending") or 0),
            "running": int(stats.get("running") or 0),
            "completed": int(stats.get("completed") or 0),
            "failed": int(stats.get("failed") or 0)
        }

    except Error as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        cursor.close()
        conn.close()

@app.get("/api/v1/software/tasks/{task_id}")
def get_task_detail(task_id: int):
    """获取任务详情"""
    conn = get_db_connection()
    if not conn:
        raise HTTPException(status_code=500, detail="Database connection failed")

    try:
        cursor = conn.cursor(dictionary=True)

        cursor.execute("""
            SELECT t.*, p.display_name as package_display_name
            FROM software_tasks t
            LEFT JOIN software_packages p ON t.package_id = p.id
            WHERE t.id = %s
        """, (task_id,))

        task = cursor.fetchone()
        if not task:
            raise HTTPException(status_code=404, detail="任务不存在")

        cursor.execute("""
            SELECT r.*, a.hostname, a.ip_address
            FROM software_task_results r
            LEFT JOIN assets a ON r.asset_id = a.id
            WHERE r.task_id = %s
            ORDER BY r.updated_at DESC
        """, (task_id,))

        results = cursor.fetchall()

        for item in [task] + results:
            for date_field in ['created_at', 'updated_at', 'start_time', 'end_time', 'scheduled_time']:
                if item.get(date_field):
                    item[date_field] = item[date_field].strftime('%Y-%m-%d %H:%M:%S')

        task['results'] = results
        return task

    except Error as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        cursor.close()
        conn.close()

@app.delete("/api/v1/software/tasks/{task_id}")
def delete_task(task_id: int):
    """删除任务"""
    conn = get_db_connection()
    if not conn:
        raise HTTPException(status_code=500, detail="Database connection failed")

    try:
        cursor = conn.cursor(dictionary=True)

        # 检查任务是否存在
        cursor.execute("SELECT id, status FROM software_tasks WHERE id = %s", (task_id,))
        task = cursor.fetchone()
        if not task:
            raise HTTPException(status_code=404, detail="任务不存在")

        # 检查任务是否正在运行
        if task['status'] == 'running':
            raise HTTPException(status_code=400, detail="无法删除正在运行的任务")

        # 删除任务结果
        cursor.execute("DELETE FROM software_task_results WHERE task_id = %s", (task_id,))

        # 删除任务
        cursor.execute("DELETE FROM software_tasks WHERE id = %s", (task_id,))

        conn.commit()

        return {"message": "任务删除成功"}

    except Error as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        cursor.close()
        conn.close()

@app.put("/api/v1/software/tasks/{task_id}/cancel")
def cancel_task(task_id: int):
    """取消软件任务"""
    conn = get_db_connection()
    if not conn:
        raise HTTPException(status_code=500, detail="Database connection failed")

    try:
        cursor = conn.cursor(dictionary=True)

        # 先检查任务状态，已结束的任务不允许重复取消
        cursor.execute("SELECT id, status FROM software_tasks WHERE id = %s", (task_id,))
        task = cursor.fetchone()
        if not task:
            raise HTTPException(status_code=404, detail="任务不存在")

        if task["status"] in {"completed", "failed", "cancelled"}:
            raise HTTPException(status_code=400, detail="任务已结束，无法取消")

        # 将所有未完成的执行结果统一标记为取消，避免 Agent 后续继续写回
        cursor.execute(
            """
            UPDATE software_task_results
            SET status = 'cancelled',
                end_time = NOW(),
                duration = CASE
                    WHEN start_time IS NULL THEN 0
                    ELSE TIMESTAMPDIFF(SECOND, start_time, NOW())
                END
            WHERE task_id = %s
              AND status IN ('pending', 'running', 'downloading', 'installing')
            """,
            (task_id,),
        )
        cancelled_results = cursor.rowcount

        # 重新汇总一次任务统计，保持列表页和详情页状态一致
        cursor.execute(
            """
            SELECT
                COUNT(*) AS total,
                SUM(CASE WHEN status = 'success' THEN 1 ELSE 0 END) AS success_count,
                SUM(CASE WHEN status = 'failed' THEN 1 ELSE 0 END) AS failed_count,
                SUM(CASE WHEN status IN ('downloading', 'installing') THEN 1 ELSE 0 END) AS running_count,
                AVG(progress) AS avg_progress
            FROM software_task_results
            WHERE task_id = %s
            """,
            (task_id,),
        )
        stats = cursor.fetchone() or {}

        success_count = int(stats.get("success_count") or 0)
        failed_count = int(stats.get("failed_count") or 0)
        running_count = int(stats.get("running_count") or 0)
        progress = int(stats.get("avg_progress") or 0)

        cursor.execute(
            """
            UPDATE software_tasks
            SET status = 'cancelled',
                progress = %s,
                success_count = %s,
                failed_count = %s,
                running_count = 0,
                end_time = NOW(),
                updated_at = NOW()
            WHERE id = %s
            """,
            (progress, success_count, failed_count, task_id),
        )
        conn.commit()

        print(
            f"[SoftwareAPI] 任务取消成功 task_id={task_id}, "
            f"cancelled_results={cancelled_results}, "
            f"success_count={success_count}, failed_count={failed_count}, "
            f"running_count={running_count}, progress={progress}"
        )
        return {
            "message": "任务取消成功",
            "task_id": task_id,
            "status": "cancelled",
            "cancelled_results": cancelled_results,
        }

    except HTTPException:
        conn.rollback()
        raise
    except Error as e:
        conn.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        cursor.close()
        conn.close()

@app.put("/api/v1/software/tasks/{task_id}/retry")
def retry_task(task_id: int):
    """重试软件任务"""
    conn = get_db_connection()
    if not conn:
        raise HTTPException(status_code=500, detail="Database connection failed")

    try:
        cursor = conn.cursor(dictionary=True)

        cursor.execute("SELECT id, status FROM software_tasks WHERE id = %s", (task_id,))
        task = cursor.fetchone()
        if not task:
            raise HTTPException(status_code=404, detail="任务不存在")

        if task["status"] == "running":
            raise HTTPException(status_code=400, detail="运行中的任务不能重试")
        if task["status"] == "completed":
            raise HTTPException(status_code=400, detail="已完成的任务不能重试")

        # 只重置失败、超时和已取消的结果，成功结果保持不变，避免重复覆盖已完成资产。
        cursor.execute(
            """
            UPDATE software_task_results
            SET status = 'pending',
                progress = 0,
                download_progress = 0,
                install_progress = 0,
                download_speed = 0,
                downloaded_size = 0,
                error_code = NULL,
                error_message = NULL,
                stdout_log = NULL,
                stderr_log = NULL,
                start_time = NULL,
                end_time = NULL,
                duration = NULL,
                retry_count = retry_count + 1
            WHERE task_id = %s
              AND status IN ('failed', 'timeout', 'cancelled')
            """,
            (task_id,),
        )
        reset_count = cursor.rowcount

        cursor.execute(
            """
            SELECT
                COUNT(*) AS total,
                SUM(CASE WHEN status = 'success' THEN 1 ELSE 0 END) AS success_count,
                SUM(CASE WHEN status = 'failed' THEN 1 ELSE 0 END) AS failed_count,
                SUM(CASE WHEN status IN ('downloading', 'installing') THEN 1 ELSE 0 END) AS running_count,
                AVG(progress) AS avg_progress
            FROM software_task_results
            WHERE task_id = %s
            """,
            (task_id,),
        )
        stats = cursor.fetchone() or {}

        if reset_count == 0:
            raise HTTPException(status_code=400, detail="任务没有可重试的结果")

        progress = int(stats.get("avg_progress") or 0)
        success_count = int(stats.get("success_count") or 0)
        failed_count = int(stats.get("failed_count") or 0)
        running_count = int(stats.get("running_count") or 0)

        cursor.execute(
            """
            UPDATE software_tasks
            SET status = 'pending',
                progress = %s,
                success_count = %s,
                failed_count = %s,
                running_count = %s,
                start_time = NULL,
                end_time = NULL,
                updated_at = NOW()
            WHERE id = %s
            """,
            (progress, success_count, failed_count, running_count, task_id),
        )
        conn.commit()

        return {
            "message": "任务重试成功",
            "task_id": task_id,
            "reset_results": reset_count,
        }

    except HTTPException:
        conn.rollback()
        raise
    except Error as e:
        conn.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        cursor.close()
        conn.close()

# ============================================================
# Agent通信API
# ============================================================

@app.post("/api/v1/software/agent/policies")
def get_agent_policies(data: dict, request: Request):
    """Agent获取策略"""
    require_agent_request(request)

    conn = get_db_connection()
    if not conn:
        raise HTTPException(status_code=500, detail="Database connection failed")

    try:
        cursor = conn.cursor(dictionary=True)
        asset_info = get_agent_asset_info(cursor, data.get('asset_id'))

        # 按资产/分组过滤黑名单
        cursor.execute("""
            SELECT
                id, rule_name, match_type, software_name, version_range, vendor,
                action, reason, severity, enabled, apply_to_groups, apply_to_assets,
                created_by, created_at, updated_at
            FROM software_blacklist
            WHERE enabled = TRUE
            ORDER BY created_at DESC, id DESC
        """)
        blacklist = [
            serialize_rule_targets(dict(rule))
            for rule in cursor.fetchall()
            if rule_applies_to_asset(rule, asset_info)
        ]

        # 按资产/分组过滤白名单
        cursor.execute("""
            SELECT
                id, rule_name, match_type, software_name, version_range, vendor,
                file_hash, description, enabled, apply_to_groups, apply_to_assets,
                created_by, created_at, updated_at
            FROM software_whitelist
            WHERE enabled = TRUE
            ORDER BY created_at DESC, id DESC
        """)
        whitelist = [
            serialize_rule_targets(dict(rule))
            for rule in cursor.fetchall()
            if rule_applies_to_asset(rule, asset_info)
        ]

        # 只下发可用的软件包对应的强制安装策略，避免 Agent 收到失效策略
        cursor.execute("""
            SELECT
                p.id, p.policy_name, p.package_id, p.target_version, p.enforce_type,
                p.install_deadline, p.auto_upgrade, p.check_interval, p.enabled,
                p.apply_to_groups, p.apply_to_assets, p.created_by, p.created_at,
                p.updated_at, pkg.package_name, pkg.display_name AS package_display_name,
                pkg.version AS package_version
            FROM software_install_policies p
            INNER JOIN software_packages pkg ON pkg.id = p.package_id
            WHERE p.enabled = TRUE
              AND pkg.status = 'available'
              AND pkg.deleted_at IS NULL
            ORDER BY p.created_at DESC, p.id DESC
        """)
        install_policies = [
            serialize_install_policy_row(dict(policy))
            for policy in cursor.fetchall()
            if rule_applies_to_asset(policy, asset_info)
        ]

        return {
            "blacklist": blacklist,
            "whitelist": whitelist,
            "install_policies": install_policies,
            "sync_time": datetime.now().timestamp()
        }

    except Error as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        cursor.close()
        conn.close()

@app.post("/api/v1/software/agent/tasks/poll")
def poll_tasks(data: dict, request: Request):
    """Agent轮询任务"""
    require_agent_request(request)

    conn = get_db_connection()
    if not conn:
        raise HTTPException(status_code=500, detail="Database connection failed")

    try:
        cursor = conn.cursor(dictionary=True)
        asset_id = get_agent_asset_info(cursor, data.get('asset_id'))['id']

        cursor.execute("""
            SELECT
                t.id as task_id, t.task_type, t.package_id, t.software_name,
                t.priority, t.timeout, t.retry_count, t.retry_interval,
                r.id as result_id, r.status as current_status,
                r.retry_count as current_retry_count
            FROM software_tasks t
            INNER JOIN software_task_results r ON t.id = r.task_id
            WHERE r.asset_id = %s
            AND t.status IN ('pending', 'running')
            AND r.status = 'pending'
            AND r.retry_count < t.retry_count
            AND (t.scheduled_time IS NULL OR t.scheduled_time <= NOW())
            ORDER BY t.priority DESC, t.created_at ASC
            LIMIT 5
        """, (asset_id,))

        tasks = cursor.fetchall()
        result_tasks = []

        for task in tasks:
            task_info = {
                'task_id': task['task_id'],
                'result_id': task['result_id'],
                'task_type': task['task_type'],
                'software_name': task['software_name'],
                'priority': task['priority'],
                'timeout': task['timeout']
            }

            if task['package_id'] and task['task_type'] in ['install', 'upgrade', 'uninstall']:
                cursor.execute("""
                    SELECT id, package_name, display_name, version, file_name,
                           file_size, file_hash, install_command, uninstall_command, requires_reboot
                    FROM software_packages
                    WHERE id = %s AND status = 'available'
                """, (task['package_id'],))

                package = cursor.fetchone()
                if package:
                    task_info['package_info'] = dict(package)
                    result_tasks.append(task_info)
            else:
                result_tasks.append(task_info)

        return {"tasks": result_tasks, "count": len(result_tasks)}

    except Error as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        cursor.close()
        conn.close()

@app.delete("/api/v1/software/packages/{package_id}")
def delete_package(package_id: int):
    """删除软件包"""
    conn = get_db_connection()
    if not conn:
        raise HTTPException(status_code=500, detail="Database connection failed")

    try:
        cursor = conn.cursor(dictionary=True)

        # 检查软件包是否存在
        cursor.execute("""
            SELECT id, file_path
            FROM software_packages
            WHERE id = %s AND deleted_at IS NULL
        """, (package_id,))

        package = cursor.fetchone()
        if not package:
            raise HTTPException(status_code=404, detail="Package not found")

        # 软删除：设置deleted_at
        cursor.execute("""
            UPDATE software_packages
            SET deleted_at = NOW()
            WHERE id = %s
        """, (package_id,))

        conn.commit()

        # 可选：删除物理文件
        # if package['file_path'] and os.path.exists(package['file_path']):
        #     try:
        #         os.remove(package['file_path'])
        #     except:
        #         pass

        return {"message": "Package deleted successfully"}

    except Error as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        cursor.close()
        conn.close()

@app.get("/api/v1/software/agent/packages/{package_id}/download")
def download_package(package_id: int, request: Request, asset_id: int = Query(..., ge=1)):
    """Agent下载软件包"""
    require_agent_request(request)

    conn = get_db_connection()
    if not conn:
        raise HTTPException(status_code=500, detail="Database connection failed")

    try:
        cursor = conn.cursor(dictionary=True)
        normalized_asset_id = get_agent_asset_info(cursor, asset_id)['id']
        ensure_agent_package_access(cursor, normalized_asset_id, package_id)

        cursor.execute("""
            SELECT file_name, file_path, file_hash
            FROM software_packages
            WHERE id = %s AND status = 'available' AND deleted_at IS NULL
        """, (package_id,))

        package = cursor.fetchone()
        if not package or not os.path.exists(package['file_path']):
            raise HTTPException(status_code=404, detail="Package not found")

        cursor.execute("UPDATE software_packages SET download_count = download_count + 1 WHERE id = %s", (package_id,))
        conn.commit()

        return FileResponse(
            path=package['file_path'],
            filename=package['file_name'],
            media_type='application/octet-stream',
            headers={'X-File-Hash': package['file_hash']}
        )

    except Error as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        cursor.close()
        conn.close()

@app.put("/api/v1/software/task-results/{result_id}")
@app.put("/api/v1/software/agent/task-results/{result_id}")
def update_task_result(result_id: int, data: dict, request: Request):
    """Agent更新任务执行结果"""
    require_agent_request(request)

    conn = get_db_connection()
    if not conn:
        raise HTTPException(status_code=500, detail="Database connection failed")

    try:
        cursor = conn.cursor(dictionary=True)
        asset_id = normalize_agent_asset_id(data.get('asset_id'))

        cursor.execute(
            """
            SELECT r.task_id, r.status, t.status AS task_status
            FROM software_task_results r
            INNER JOIN software_tasks t ON t.id = r.task_id
            WHERE r.id = %s AND r.asset_id = %s
            """,
            (result_id, asset_id),
        )
        result = cursor.fetchone()
        if not result:
            raise HTTPException(status_code=404, detail="Task result not found")

        # 取消或结束后的任务不再接受执行结果回写，避免状态被 Agent 反向覆盖
        if result["task_status"] in {"completed", "failed", "cancelled"}:
            raise HTTPException(status_code=409, detail="任务已结束，拒绝更新执行结果")

        task_id = result['task_id']
        update_fields = []
        params = []

        for field in ['status', 'progress', 'download_progress', 'install_progress',
                      'download_speed', 'downloaded_size', 'error_code', 'error_message',
                      'stdout_log', 'stderr_log']:
            if field in data:
                update_fields.append(f"{field} = %s")
                params.append(data[field])

        if data.get('status') in ['downloading', 'installing'] and result['status'] == 'pending':
            update_fields.append("start_time = NOW()")

        if data.get('status') in ['success', 'failed', 'timeout', 'cancelled']:
            update_fields.append("end_time = NOW()")
            update_fields.append("duration = TIMESTAMPDIFF(SECOND, start_time, NOW())")

        if update_fields:
            params.append(result_id)
            cursor.execute(f"UPDATE software_task_results SET {', '.join(update_fields)} WHERE id = %s", params)

        # 更新任务的汇总状态
        cursor.execute("""
            SELECT
                COUNT(*) as total,
                SUM(CASE WHEN status = 'success' THEN 1 ELSE 0 END) as success_count,
                SUM(CASE WHEN status = 'failed' THEN 1 ELSE 0 END) as failed_count,
                SUM(CASE WHEN status = 'pending' THEN 1 ELSE 0 END) as pending_count,
                SUM(CASE WHEN status IN ('downloading', 'installing') THEN 1 ELSE 0 END) as running_count,
                AVG(progress) as avg_progress
            FROM software_task_results
            WHERE task_id = %s
        """, (task_id,))

        stats = cursor.fetchone()

        # 确定任务整体状态
        if stats['success_count'] == stats['total']:
            task_status = 'completed'
        elif stats['running_count'] > 0:
            task_status = 'running'
        elif stats['pending_count'] > 0:
            task_status = 'pending'
        elif stats['failed_count'] > 0:
            task_status = 'failed'
        else:
            task_status = 'pending'

        # 更新任务表
        cursor.execute("""
            UPDATE software_tasks
            SET status = %s,
                progress = %s,
                success_count = %s,
                failed_count = %s,
                running_count = %s,
                start_time = IFNULL(start_time, (SELECT MIN(start_time) FROM software_task_results WHERE task_id = %s)),
                end_time = CASE WHEN %s IN ('completed', 'failed') THEN NOW() ELSE end_time END
            WHERE id = %s
        """, (
            task_status,
            int(stats['avg_progress'] or 0),
            stats['success_count'],
            stats['failed_count'],
            stats['running_count'],
            task_id,
            task_status,
            task_id
        ))

        conn.commit()

        return {"message": "Task result updated successfully"}

    except Error as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        cursor.close()
        conn.close()

@app.post("/api/v1/software/task-results/{result_id}/logs")
def upload_task_result_logs(result_id: int, data: dict, request: Request):
    """上传任务执行日志"""
    require_agent_request(request)

    conn = get_db_connection()
    if not conn:
        raise HTTPException(status_code=500, detail="Database connection failed")

    try:
        cursor = conn.cursor(dictionary=True)
        asset_id = normalize_agent_asset_id(data.get("asset_id"))

        cursor.execute(
            """
            SELECT r.id, r.stdout_log, r.stderr_log
            FROM software_task_results r
            WHERE r.id = %s AND r.asset_id = %s
            """,
            (result_id, asset_id),
        )
        result = cursor.fetchone()
        if not result:
            raise HTTPException(status_code=404, detail="Task result not found")

        append_mode = bool(data.get("append", True))
        log_type = str(data.get("log_type") or "").strip().lower()
        stdout_text = data.get("stdout_log")
        stderr_text = data.get("stderr_log")
        message_text = data.get("message")

        if stdout_text is None and stderr_text is None and message_text is not None:
            if log_type == "stderr":
                stderr_text = message_text
            else:
                stdout_text = message_text

        update_fields = []
        params = []

        if stdout_text is not None:
            merged_stdout = merge_task_log_content(result.get("stdout_log"), stdout_text, append=append_mode)
            update_fields.append("stdout_log = %s")
            params.append(merged_stdout)

        if stderr_text is not None:
            merged_stderr = merge_task_log_content(result.get("stderr_log"), stderr_text, append=append_mode)
            update_fields.append("stderr_log = %s")
            params.append(merged_stderr)

        if not update_fields:
            raise HTTPException(status_code=400, detail="没有可写入的日志内容")

        params.append(result_id)
        cursor.execute(f"UPDATE software_task_results SET {', '.join(update_fields)} WHERE id = %s", params)
        conn.commit()

        return {
            "message": "日志上传成功",
            "result_id": result_id,
            "updated_fields": [field.split(" = ")[0] for field in update_fields],
        }

    except HTTPException:
        conn.rollback()
        raise
    except Error as e:
        conn.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        cursor.close()
        conn.close()

# ============================================================
# 工具函数
# ============================================================

def format_file_size(size_bytes):
    """格式化文件大小"""
    for unit in ['B', 'KB', 'MB', 'GB']:
        if size_bytes < 1024.0:
            return f"{size_bytes:.2f} {unit}"
        size_bytes /= 1024.0
    return f"{size_bytes:.2f} TB"

@app.get("/api/v1/software/health")
def health_check():
    """健康检查"""
    conn = get_db_connection()
    if not conn:
        return {"status": "unhealthy", "database": "disconnected"}
    conn.close()
    return {"status": "healthy", "database": "connected", "storage_path": PACKAGE_STORAGE_PATH}



# ============================================================
# Pydantic模型
# ============================================================

class WhitelistRule(BaseModel):
    rule_name: str
    match_type: str = "exact"
    software_name: str
    version_range: Optional[str] = None
    vendor: Optional[str] = None
    file_hash: Optional[str] = None
    description: Optional[str] = None
    apply_to_groups: Optional[List[int]] = None
    apply_to_assets: Optional[List[int]] = None

class WhitelistRuleUpdate(BaseModel):
    rule_name: Optional[str] = None
    match_type: Optional[str] = None
    software_name: Optional[str] = None
    version_range: Optional[str] = None
    vendor: Optional[str] = None
    file_hash: Optional[str] = None
    description: Optional[str] = None
    enabled: Optional[bool] = None
    apply_to_groups: Optional[List[int]] = None
    apply_to_assets: Optional[List[int]] = None

class InstallPolicy(BaseModel):
    policy_name: str
    package_id: int
    target_version: Optional[str] = None
    enforce_type: str = "mandatory"
    install_deadline: Optional[datetime] = None
    auto_upgrade: bool = False
    check_interval: int = 3600
    apply_to_groups: Optional[List[int]] = None
    apply_to_assets: Optional[List[int]] = None

class InstallPolicyUpdate(BaseModel):
    policy_name: Optional[str] = None
    package_id: Optional[int] = None
    target_version: Optional[str] = None
    enforce_type: Optional[str] = None
    install_deadline: Optional[datetime] = None
    auto_upgrade: Optional[bool] = None
    check_interval: Optional[int] = None
    enabled: Optional[bool] = None
    apply_to_groups: Optional[List[int]] = None
    apply_to_assets: Optional[List[int]] = None

class ComplianceCheck(BaseModel):
    check_name: str
    check_type: str
    software_name: str
    required_version: Optional[str] = None
    severity: str = "medium"
    enabled: bool = True
    apply_to_groups: Optional[List[int]] = None

class ComplianceCheckUpdate(BaseModel):
    check_name: str
    check_type: str
    software_name: str
    required_version: Optional[str] = None
    severity: str = "medium"
    enabled: bool = True
    apply_to_groups: Optional[List[int]] = None

class ComplianceScanRequest(BaseModel):
    asset_ids: Optional[List[int]] = None
    check_ids: Optional[List[int]] = None
    task_name: Optional[str] = None
    created_by: str = "system"


SUPPORTED_COMPLIANCE_CHECK_TYPES = {"required", "forbidden", "version"}


def normalize_compliance_check_type(check_type: str) -> str:
    normalized = (check_type or "").strip().lower()
    if normalized not in SUPPORTED_COMPLIANCE_CHECK_TYPES:
        allowed_types = "、".join(sorted(SUPPORTED_COMPLIANCE_CHECK_TYPES))
        raise HTTPException(
            status_code=400,
            detail=f"不支持的合规检查类型: {check_type}，当前仅支持 {allowed_types}"
        )
    return normalized


def parse_json_int_list(value: Any) -> List[int]:
    """解析数据库中的 JSON 数组字段为整数列表"""
    if value is None:
        return []

    if isinstance(value, list):
        raw_items = value
    elif isinstance(value, str):
        text = value.strip()
        if not text:
            return []
        try:
            raw_items = json.loads(text)
        except json.JSONDecodeError:
            return []
    else:
        return []

    result: List[int] = []
    for item in raw_items:
        try:
            result.append(int(item))
        except (TypeError, ValueError):
            continue
    return result


def rule_applies_to_asset(rule: Dict[str, Any], asset_info: Dict[str, Any]) -> bool:
    """判断规则是否作用于指定资产。

    规则没有配置任何目标时，默认视为全局规则。
    """
    target_groups = parse_json_int_list(rule.get("apply_to_groups"))
    target_assets = parse_json_int_list(rule.get("apply_to_assets"))

    if not target_groups and not target_assets:
        return True

    asset_id = asset_info.get("id")
    group_id = asset_info.get("group_id")

    try:
        normalized_asset_id = int(asset_id) if asset_id is not None else None
    except (TypeError, ValueError):
        normalized_asset_id = None

    try:
        normalized_group_id = int(group_id) if group_id is not None else None
    except (TypeError, ValueError):
        normalized_group_id = None

    if normalized_asset_id is not None and normalized_asset_id in target_assets:
        return True

    if normalized_group_id is not None and normalized_group_id in target_groups:
        return True

    return False


def format_datetime_value(value: Any) -> Optional[str]:
    """统一格式化时间字段"""
    if not value:
        return None
    if isinstance(value, datetime):
        return value.strftime('%Y-%m-%d %H:%M:%S')
    return str(value)


def normalize_enabled_value(value: Any) -> bool:
    """兼容数据库返回的 enabled 字段类型"""
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return False


def serialize_rule_targets(row: Dict[str, Any]) -> Dict[str, Any]:
    """统一序列化规则目标字段。"""
    return {
        **row,
        "enabled": normalize_enabled_value(row.get("enabled")),
        "apply_to_groups": parse_json_int_list(row.get("apply_to_groups")),
        "apply_to_assets": parse_json_int_list(row.get("apply_to_assets")),
        "created_at": format_datetime_value(row.get("created_at")),
        "updated_at": format_datetime_value(row.get("updated_at")),
    }


def serialize_install_policy_row(row: Dict[str, Any]) -> Dict[str, Any]:
    """统一序列化安装策略。"""
    return {
        **row,
        "enabled": normalize_enabled_value(row.get("enabled")),
        "apply_to_groups": parse_json_int_list(row.get("apply_to_groups")),
        "apply_to_assets": parse_json_int_list(row.get("apply_to_assets")),
        "created_at": format_datetime_value(row.get("created_at")),
        "updated_at": format_datetime_value(row.get("updated_at")),
        "install_deadline": format_datetime_value(row.get("install_deadline")),
    }


def serialize_compliance_check_row(check: Dict[str, Any]) -> Dict[str, Any]:
    """统一序列化合规规则"""
    return {
        **check,
        "enabled": normalize_enabled_value(check.get("enabled")),
        "apply_to_groups": parse_json_int_list(check.get("apply_to_groups")),
        "created_at": format_datetime_value(check.get("created_at")),
        "updated_at": format_datetime_value(check.get("updated_at"))
    }


def serialize_compliance_result_row(result: Dict[str, Any]) -> Dict[str, Any]:
    """统一序列化合规结果"""
    raw_is_compliant = result.get("is_compliant")
    if raw_is_compliant is None:
        is_compliant = None
    else:
        is_compliant = bool(raw_is_compliant)

    check_type = (result.get("check_type") or "").lower()
    if check_type == "license" or is_compliant is None:
        compliance_status = "manual_review"
    else:
        compliance_status = "compliant" if is_compliant else "non_compliant"

    return {
        **result,
        "is_compliant": is_compliant,
        "compliance_status": compliance_status,
        "auto_check_supported": compliance_status != "manual_review",
        "can_auto_remediate": compliance_status == "non_compliant" and check_type in {"required", "version", "forbidden"},
        "checked_at": format_datetime_value(result.get("checked_at"))
    }


def build_compliance_results_where_clause(
    check_id: Optional[int] = None,
    is_compliant: Optional[bool] = None,
    severity: Optional[str] = None
) -> tuple[str, List[Any]]:
    """构建合规结果查询条件"""
    where_clauses: List[str] = []
    params: List[Any] = []

    if check_id:
        where_clauses.append("r.check_id = %s")
        params.append(check_id)

    if is_compliant is not None:
        where_clauses.append("r.is_compliant = %s")
        params.append(is_compliant)

    if severity:
        where_clauses.append("""
            EXISTS (
                SELECT 1
                FROM software_compliance_checks c_filter
                WHERE c_filter.id = r.check_id AND c_filter.severity = %s
            )
        """)
        params.append(severity)

    return (" AND ".join(where_clauses) if where_clauses else "1=1"), params


def build_compliance_checks_where_clause(
    check_id: Optional[int] = None,
    severity: Optional[str] = None
) -> tuple[str, List[Any]]:
    """构建合规规则查询条件"""
    where_clauses: List[str] = []
    params: List[Any] = []

    if check_id:
        where_clauses.append("id = %s")
        params.append(check_id)

    if severity:
        where_clauses.append("severity = %s")
        params.append(severity)

    return (" AND ".join(where_clauses) if where_clauses else "1=1"), params


def normalize_software_name(name: Optional[str]) -> str:
    """统一软件名称格式，降低大小写和符号差异影响"""
    if not name:
        return ""
    return re.sub(r"[\s\-_\.]+", "", str(name).strip().lower())


def software_name_matches(installed_name: Optional[str], expected_name: Optional[str]) -> bool:
    """判断资产上报的软件名是否匹配规则中的软件名"""
    left = normalize_software_name(installed_name)
    right = normalize_software_name(expected_name)
    if not left or not right:
        return False
    return left == right or left in right or right in left


def version_to_parts(version: Optional[str]) -> List[Any]:
    """将版本号拆成可比较的数字/文本片段"""
    if not version:
        return []

    parts: List[Any] = []
    for token in re.findall(r"\d+|[A-Za-z]+", str(version)):
        if token.isdigit():
            parts.append(int(token))
        else:
            parts.append(token.lower())
    return parts


def compare_versions(current_version: Optional[str], expected_version: Optional[str]) -> int:
    """比较版本号，返回 1/0/-1"""
    left = version_to_parts(current_version)
    right = version_to_parts(expected_version)

    max_length = max(len(left), len(right))
    for index in range(max_length):
        left_part = left[index] if index < len(left) else 0
        right_part = right[index] if index < len(right) else 0

        if left_part == right_part:
            continue

        if isinstance(left_part, int) and isinstance(right_part, int):
            return 1 if left_part > right_part else -1

        return 1 if str(left_part) > str(right_part) else -1

    return 0


def asset_matches_compliance_check(asset_info: Dict[str, Any], check_info: Dict[str, Any]) -> bool:
    """判断某条合规规则是否作用于指定资产"""
    target_groups = parse_json_int_list(check_info.get("apply_to_groups"))
    if not target_groups:
        return True
    group_id = asset_info.get("group_id")
    return group_id is not None and int(group_id) in target_groups


def pick_best_installed_software(installed_software: List[Dict[str, Any]], expected_name: str) -> Optional[Dict[str, Any]]:
    """从资产软件清单中挑出最匹配的软件记录，优先返回版本较高的项"""
    matched = [item for item in installed_software if software_name_matches(item.get("software_name"), expected_name)]
    if not matched:
        return None

    def sort_key(item: Dict[str, Any]) -> List[Any]:
        return version_to_parts(item.get("version"))

    matched.sort(key=sort_key, reverse=True)
    return matched[0]


def evaluate_compliance_result(
    check_info: Dict[str, Any],
    asset_info: Dict[str, Any],
    installed_software: List[Dict[str, Any]]
) -> Dict[str, Any]:
    """执行单资产单规则的合规判断"""
    software_name = check_info.get("software_name") or ""
    required_version = check_info.get("required_version")
    matched_software = pick_best_installed_software(installed_software, software_name)
    check_type = (check_info.get("check_type") or "").lower()

    current_version = matched_software.get("version") if matched_software else None
    asset_name = asset_info.get("hostname") or asset_info.get("ip_address") or f"资产 {asset_info.get('id')}"

    if check_type == "required":
        if matched_software:
            return {
                "is_compliant": True,
                "current_version": current_version,
                "expected_version": required_version,
                "details": f"{asset_name} 已安装 {software_name}"
            }
        return {
            "is_compliant": False,
            "current_version": None,
            "expected_version": required_version,
            "details": f"{asset_name} 未安装要求的软件 {software_name}"
        }

    if check_type == "forbidden":
        if matched_software:
            return {
                "is_compliant": False,
                "current_version": current_version,
                "expected_version": None,
                "details": f"{asset_name} 安装了禁止软件 {software_name}"
            }
        return {
            "is_compliant": True,
            "current_version": None,
            "expected_version": None,
            "details": f"{asset_name} 未发现禁止软件 {software_name}"
        }

    if check_type == "version":
        if not matched_software:
            return {
                "is_compliant": False,
                "current_version": None,
                "expected_version": required_version,
                "details": f"{asset_name} 未安装版本校验目标软件 {software_name}"
            }

        if not required_version:
            return {
                "is_compliant": True,
                "current_version": current_version,
                "expected_version": None,
                "details": f"{asset_name} 已安装 {software_name}，规则未设置目标版本"
            }

        compare_result = compare_versions(current_version, required_version)
        is_compliant = compare_result >= 0
        return {
            "is_compliant": is_compliant,
            "current_version": current_version,
            "expected_version": required_version,
            "details": (
                f"{asset_name} 的 {software_name} 当前版本 {current_version or '未知'} "
                f"{'满足' if is_compliant else '低于'}要求版本 {required_version}"
            )
        }

    if check_type == "license":
        return {
            "is_compliant": None,
            "current_version": current_version,
            "expected_version": required_version,
            "details": f"{asset_name} 当前未上报许可证信息，{software_name} 许可证规则仅支持手工核查",
            "compliance_status": "manual_review"
        }

    return {
        "is_compliant": False,
        "current_version": current_version,
        "expected_version": required_version,
        "details": f"未知的合规检查类型: {check_type}"
    }

# ============================================================
# 白名单管理API
# ============================================================

@app.get("/api/v1/software/whitelist")
def get_whitelist(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=500)
):
    """获取白名单列表"""
    conn = get_db_connection()
    if not conn:
        raise HTTPException(status_code=500, detail="Database connection failed")

    try:
        cursor = conn.cursor(dictionary=True)

        cursor.execute("SELECT COUNT(*) as total FROM software_whitelist")
        total = cursor.fetchone()['total']

        offset = (page - 1) * page_size
        cursor.execute("""
            SELECT id, rule_name, match_type, software_name, version_range,
                   vendor, file_hash, description, enabled,
                   apply_to_groups, apply_to_assets,
                   created_by, created_at, updated_at
            FROM software_whitelist
            ORDER BY created_at DESC
            LIMIT %s OFFSET %s
        """, (page_size, offset))

        rules = cursor.fetchall()
        return {
            "data": [serialize_rule_targets(rule) for rule in rules],
            "total": total,
            "page": page,
            "page_size": page_size
        }

    except Error as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        cursor.close()
        conn.close()

@app.post("/api/v1/software/whitelist")
def create_whitelist(rule: WhitelistRule, request: Request):
    """创建白名单规则"""
    conn = get_db_connection()
    if not conn:
        raise HTTPException(status_code=500, detail="Database connection failed")

    try:
        cursor = conn.cursor()

        apply_to_groups = json.dumps(rule.apply_to_groups) if rule.apply_to_groups else None
        apply_to_assets = json.dumps(rule.apply_to_assets) if rule.apply_to_assets else None

        created_by = get_request_username(request)

        cursor.execute("""
            INSERT INTO software_whitelist (
                rule_name, match_type, software_name, version_range, vendor,
                file_hash, description, apply_to_groups, apply_to_assets,
                created_by
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """, (
            rule.rule_name, rule.match_type, rule.software_name, rule.version_range,
            rule.vendor, rule.file_hash, rule.description,
            apply_to_groups, apply_to_assets, created_by
        ))

        rule_id = cursor.lastrowid
        conn.commit()

        return {"message": "白名单规则创建成功", "rule_id": rule_id}

    except Error as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        cursor.close()
        conn.close()

@app.put("/api/v1/software/whitelist/{rule_id}")
def update_whitelist(rule_id: int, rule: WhitelistRuleUpdate):
    """更新白名单规则"""
    conn = get_db_connection()
    if not conn:
        raise HTTPException(status_code=500, detail="Database connection failed")

    try:
        cursor = conn.cursor()

        cursor.execute("SELECT id FROM software_whitelist WHERE id = %s", (rule_id,))
        if not cursor.fetchone():
            raise HTTPException(status_code=404, detail="规则不存在")

        update_fields = []
        values = []
        field_map = {
            "rule_name": rule.rule_name,
            "match_type": rule.match_type,
            "software_name": rule.software_name,
            "version_range": rule.version_range,
            "vendor": rule.vendor,
            "file_hash": rule.file_hash,
            "description": rule.description,
            "enabled": rule.enabled,
        }
        for key, value in field_map.items():
            if value is not None:
                update_fields.append(f"{key} = %s")
                values.append(value)

        if rule.apply_to_groups is not None:
            update_fields.append("apply_to_groups = %s")
            values.append(json.dumps(rule.apply_to_groups))
        if rule.apply_to_assets is not None:
            update_fields.append("apply_to_assets = %s")
            values.append(json.dumps(rule.apply_to_assets))

        if not update_fields:
            raise HTTPException(status_code=400, detail="No valid fields to update")

        update_fields.append("updated_at = NOW()")
        values.append(rule_id)

        sql = f"UPDATE software_whitelist SET {', '.join(update_fields)} WHERE id = %s"
        cursor.execute(sql, values)

        conn.commit()

        return {"message": "白名单规则更新成功"}

    except Error as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        cursor.close()
        conn.close()

@app.delete("/api/v1/software/whitelist/{rule_id}")
def delete_whitelist(rule_id: int):
    """删除白名单规则"""
    conn = get_db_connection()
    if not conn:
        raise HTTPException(status_code=500, detail="Database connection failed")

    try:
        cursor = conn.cursor()

        cursor.execute("SELECT id FROM software_whitelist WHERE id = %s", (rule_id,))
        if not cursor.fetchone():
            raise HTTPException(status_code=404, detail="规则不存在")

        cursor.execute("DELETE FROM software_whitelist WHERE id = %s", (rule_id,))
        conn.commit()

        return {"message": "白名单规则删除成功"}

    except Error as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        cursor.close()
        conn.close()

# ============================================================
# 安装策略管理API
# ============================================================

@app.get("/api/v1/software/policies")
def get_install_policies(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=500)
):
    """获取安装策略列表"""
    conn = get_db_connection()
    if not conn:
        raise HTTPException(status_code=500, detail="Database connection failed")

    try:
        cursor = conn.cursor(dictionary=True)

        cursor.execute("SELECT COUNT(*) as total FROM software_install_policies")
        total = cursor.fetchone()['total']

        offset = (page - 1) * page_size
        cursor.execute("""
            SELECT p.*, pkg.display_name as package_name, pkg.version as package_version
            FROM software_install_policies p
            LEFT JOIN software_packages pkg ON p.package_id = pkg.id
            ORDER BY p.created_at DESC
            LIMIT %s OFFSET %s
        """, (page_size, offset))

        policies = cursor.fetchall()
        return {
            "data": [serialize_install_policy_row(policy) for policy in policies],
            "total": total,
            "page": page,
            "page_size": page_size
        }

    except Error as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        cursor.close()
        conn.close()

@app.post("/api/v1/software/policies")
def create_install_policy(policy: InstallPolicy, request: Request):
    """创建安装策略"""
    conn = get_db_connection()
    if not conn:
        raise HTTPException(status_code=500, detail="Database connection failed")

    try:
        cursor = conn.cursor()

        # 检查package_id是否存在
        cursor.execute("""
            SELECT id FROM software_packages
            WHERE id = %s AND status = 'available' AND deleted_at IS NULL
        """, (policy.package_id,))

        if not cursor.fetchone():
            raise HTTPException(status_code=404, detail="软件包不存在")

        apply_to_groups = json.dumps(policy.apply_to_groups) if policy.apply_to_groups else None
        apply_to_assets = json.dumps(policy.apply_to_assets) if policy.apply_to_assets else None

        created_by = get_request_username(request)

        cursor.execute("""
            INSERT INTO software_install_policies (
                policy_name, package_id, target_version, enforce_type,
                install_deadline, auto_upgrade, check_interval,
                apply_to_groups, apply_to_assets, created_by
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """, (
            policy.policy_name, policy.package_id, policy.target_version,
            policy.enforce_type, policy.install_deadline, policy.auto_upgrade,
            policy.check_interval, apply_to_groups, apply_to_assets, created_by
        ))

        policy_id = cursor.lastrowid
        conn.commit()

        return {"message": "安装策略创建成功", "policy_id": policy_id}

    except Error as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        cursor.close()
        conn.close()

@app.put("/api/v1/software/policies/{policy_id}")
def update_install_policy(policy_id: int, policy: InstallPolicyUpdate):
    """更新安装策略"""
    conn = get_db_connection()
    if not conn:
        raise HTTPException(status_code=500, detail="Database connection failed")

    try:
        cursor = conn.cursor()

        cursor.execute("SELECT id FROM software_install_policies WHERE id = %s", (policy_id,))
        if not cursor.fetchone():
            raise HTTPException(status_code=404, detail="策略不存在")

        update_fields = []
        values = []
        field_map = {
            "policy_name": policy.policy_name,
            "package_id": policy.package_id,
            "target_version": policy.target_version,
            "enforce_type": policy.enforce_type,
            "install_deadline": policy.install_deadline,
            "auto_upgrade": policy.auto_upgrade,
            "check_interval": policy.check_interval,
            "enabled": policy.enabled,
        }
        for key, value in field_map.items():
            if value is not None:
                update_fields.append(f"{key} = %s")
                values.append(value)

        if policy.apply_to_groups is not None:
            update_fields.append("apply_to_groups = %s")
            values.append(json.dumps(policy.apply_to_groups))
        if policy.apply_to_assets is not None:
            update_fields.append("apply_to_assets = %s")
            values.append(json.dumps(policy.apply_to_assets))

        if not update_fields:
            raise HTTPException(status_code=400, detail="No valid fields to update")

        update_fields.append("updated_at = NOW()")
        values.append(policy_id)

        sql = f"UPDATE software_install_policies SET {', '.join(update_fields)} WHERE id = %s"
        cursor.execute(sql, values)

        conn.commit()

        return {"message": "安装策略更新成功"}

    except Error as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        cursor.close()
        conn.close()

@app.delete("/api/v1/software/policies/{policy_id}")
def delete_install_policy(policy_id: int):
    """删除安装策略"""
    conn = get_db_connection()
    if not conn:
        raise HTTPException(status_code=500, detail="Database connection failed")

    try:
        cursor = conn.cursor()

        cursor.execute("SELECT id FROM software_install_policies WHERE id = %s", (policy_id,))
        if not cursor.fetchone():
            raise HTTPException(status_code=404, detail="策略不存在")

        cursor.execute("DELETE FROM software_install_policies WHERE id = %s", (policy_id,))
        conn.commit()

        return {"message": "安装策略删除成功"}

    except Error as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        cursor.close()
        conn.close()

# ============================================================
# 合规检查API
# ============================================================

@app.get("/api/v1/software/compliance/checks")
def get_compliance_checks(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=500)
):
    """获取合规检查规则列表"""
    conn = get_db_connection()
    if not conn:
        raise HTTPException(status_code=500, detail="Database connection failed")

    try:
        cursor = conn.cursor(dictionary=True)

        cursor.execute("SELECT COUNT(*) as total FROM software_compliance_checks")
        total = cursor.fetchone()['total']

        offset = (page - 1) * page_size
        cursor.execute("""
            SELECT id, check_name, check_type, software_name, required_version,
                   severity, enabled, apply_to_groups, created_by, created_at, updated_at
            FROM software_compliance_checks
            ORDER BY created_at DESC
            LIMIT %s OFFSET %s
        """, (page_size, offset))

        checks = cursor.fetchall()
        return {
            "data": [serialize_compliance_check_row(check) for check in checks],
            "total": total,
            "page": page,
            "page_size": page_size
        }

    except Error as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        cursor.close()
        conn.close()

@app.post("/api/v1/software/compliance/checks")
def create_compliance_check(check: ComplianceCheck, request: Request):
    """创建合规检查规则"""
    conn = get_db_connection()
    if not conn:
        raise HTTPException(status_code=500, detail="Database connection failed")

    try:
        cursor = conn.cursor()
        check_type = normalize_compliance_check_type(check.check_type)

        apply_to_groups = json.dumps(check.apply_to_groups) if check.apply_to_groups else None

        created_by = get_request_username(request)

        cursor.execute("""
            INSERT INTO software_compliance_checks (
                check_name, check_type, software_name, required_version,
                severity, enabled, apply_to_groups, created_by
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        """, (
            check.check_name, check_type, check.software_name,
            check.required_version, check.severity, check.enabled, apply_to_groups, created_by
        ))

        check_id = cursor.lastrowid
        conn.commit()

        return {"message": "合规检查规则创建成功", "check_id": check_id}

    except Error as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        cursor.close()
        conn.close()


@app.put("/api/v1/software/compliance/checks/{check_id}")
def update_compliance_check(check_id: int, check: ComplianceCheckUpdate):
    """更新合规检查规则"""
    conn = get_db_connection()
    if not conn:
        raise HTTPException(status_code=500, detail="Database connection failed")

    try:
        cursor = conn.cursor(dictionary=True)
        check_type = normalize_compliance_check_type(check.check_type)

        cursor.execute("SELECT id FROM software_compliance_checks WHERE id = %s", (check_id,))
        if not cursor.fetchone():
            raise HTTPException(status_code=404, detail="合规规则不存在")

        apply_to_groups = json.dumps(check.apply_to_groups) if check.apply_to_groups else None
        cursor.execute("""
            UPDATE software_compliance_checks
            SET check_name = %s,
                check_type = %s,
                software_name = %s,
                required_version = %s,
                severity = %s,
                enabled = %s,
                apply_to_groups = %s
            WHERE id = %s
        """, (
            check.check_name,
            check_type,
            check.software_name,
            check.required_version,
            check.severity,
            check.enabled,
            apply_to_groups,
            check_id
        ))

        conn.commit()
        return {"message": "合规检查规则更新成功", "check_id": check_id}

    except Error as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        cursor.close()
        conn.close()


@app.delete("/api/v1/software/compliance/checks/{check_id}")
def delete_compliance_check(check_id: int):
    """删除合规检查规则"""
    conn = get_db_connection()
    if not conn:
        raise HTTPException(status_code=500, detail="Database connection failed")

    try:
        cursor = conn.cursor(dictionary=True)
        cursor.execute("SELECT id, check_name FROM software_compliance_checks WHERE id = %s", (check_id,))
        check = cursor.fetchone()
        if not check:
            raise HTTPException(status_code=404, detail="合规规则不存在")

        cursor.execute("DELETE FROM software_compliance_results WHERE check_id = %s", (check_id,))
        deleted_results = cursor.rowcount
        cursor.execute("DELETE FROM software_compliance_checks WHERE id = %s", (check_id,))
        conn.commit()
        return {
            "message": "合规检查规则删除成功",
            "check_id": check_id,
            "check_name": check["check_name"],
            "deleted_results": deleted_results,
        }

    except Error as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        cursor.close()
        conn.close()

@app.get("/api/v1/software/compliance/results")
def get_compliance_results(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=500),
    check_id: Optional[int] = None,
    is_compliant: Optional[bool] = None,
    severity: Optional[str] = None
):
    """获取合规检查结果"""
    conn = get_db_connection()
    if not conn:
        raise HTTPException(status_code=500, detail="Database connection failed")

    try:
        cursor = conn.cursor(dictionary=True)
        where_sql, params = build_compliance_results_where_clause(
            check_id=check_id,
            is_compliant=is_compliant,
            severity=severity
        )

        cursor.execute(f"SELECT COUNT(*) as total FROM software_compliance_results r WHERE {where_sql}", params)
        total = cursor.fetchone()['total']

        offset = (page - 1) * page_size
        cursor.execute(f"""
            SELECT r.*, c.check_name, c.check_type, c.software_name as expected_software,
                   a.hostname, a.ip_address
            FROM software_compliance_results r
            LEFT JOIN software_compliance_checks c ON r.check_id = c.id
            LEFT JOIN assets a ON r.asset_id = a.id
            WHERE {where_sql}
            ORDER BY r.checked_at DESC
            LIMIT %s OFFSET %s
        """, params + [page_size, offset])

        results = cursor.fetchall()
        return {
            "data": [serialize_compliance_result_row(result) for result in results],
            "total": total,
            "page": page,
            "page_size": page_size
        }

    except Error as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        cursor.close()
        conn.close()


@app.get("/api/v1/software/compliance/stats")
def get_compliance_stats(
    check_id: Optional[int] = None,
    is_compliant: Optional[bool] = None,
    severity: Optional[str] = None
):
    """获取合规统计数据"""
    conn = get_db_connection()
    if not conn:
        raise HTTPException(status_code=500, detail="Database connection failed")

    try:
        cursor = conn.cursor(dictionary=True)
        where_sql, params = build_compliance_results_where_clause(
            check_id=check_id,
            is_compliant=is_compliant,
            severity=severity
        )
        checks_where_sql, checks_params = build_compliance_checks_where_clause(check_id=check_id, severity=severity)

        cursor.execute(f"""
            SELECT COUNT(*) AS total_checks,
                   SUM(CASE WHEN enabled = TRUE THEN 1 ELSE 0 END) AS enabled_checks
            FROM software_compliance_checks
            WHERE {checks_where_sql}
        """, checks_params)
        checks_overview = cursor.fetchone() or {}

        cursor.execute(f"""
            SELECT COUNT(*) AS total_results,
                   SUM(CASE WHEN r.is_compliant IS NOT NULL THEN 1 ELSE 0 END) AS evaluated_results,
                   SUM(CASE WHEN r.is_compliant = TRUE THEN 1 ELSE 0 END) AS compliant_count,
                   SUM(CASE WHEN r.is_compliant = FALSE THEN 1 ELSE 0 END) AS non_compliant_count,
                   SUM(CASE WHEN r.is_compliant IS NULL THEN 1 ELSE 0 END) AS manual_review_count
            FROM software_compliance_results r
            WHERE {where_sql}
        """, params)
        overview = cursor.fetchone() or {}

        total_results = int(overview.get("total_results") or 0)
        evaluated_results = int(overview.get("evaluated_results") or 0)
        compliant_count = int(overview.get("compliant_count") or 0)
        non_compliant_count = int(overview.get("non_compliant_count") or 0)
        manual_review_count = int(overview.get("manual_review_count") or 0)

        cursor.execute(f"""
            SELECT c.severity, COUNT(*) AS total
            FROM software_compliance_results r
            LEFT JOIN software_compliance_checks c ON r.check_id = c.id
            WHERE {where_sql}
            GROUP BY c.severity
            ORDER BY total DESC
        """, params)
        severity_distribution = cursor.fetchall()

        cursor.execute(f"""
            SELECT c.check_name, c.check_type, COUNT(*) AS total
            FROM software_compliance_results r
            LEFT JOIN software_compliance_checks c ON r.check_id = c.id
            WHERE {where_sql} AND r.is_compliant = FALSE
            GROUP BY r.check_id, c.check_name, c.check_type
            ORDER BY total DESC, c.check_name ASC
            LIMIT 10
        """, params)
        top_non_compliant_checks = cursor.fetchall()

        cursor.execute(f"""
            SELECT c.check_type, COUNT(*) AS total
            FROM software_compliance_results r
            LEFT JOIN software_compliance_checks c ON r.check_id = c.id
            WHERE {where_sql}
            GROUP BY c.check_type
            ORDER BY total DESC
        """, params)
        check_type_distribution = cursor.fetchall()

        return {
            "overview": {
                "total_checks": int(checks_overview.get("total_checks") or 0),
                "enabled_checks": int(checks_overview.get("enabled_checks") or 0),
                "total_results": total_results,
                "evaluated_results": evaluated_results,
                "compliant_count": compliant_count,
                "non_compliant_count": non_compliant_count,
                "manual_review_count": manual_review_count,
                "compliance_rate": round((compliant_count / evaluated_results) * 100, 2) if evaluated_results else 0
            },
            "severity_distribution": [
                {"severity": item.get("severity") or "unknown", "total": int(item.get("total") or 0)}
                for item in severity_distribution
            ],
            "top_non_compliant_checks": [
                {
                    "check_name": item.get("check_name") or "未命名规则",
                    "check_type": item.get("check_type") or "unknown",
                    "total": int(item.get("total") or 0)
                }
                for item in top_non_compliant_checks
            ],
            "check_type_distribution": [
                {"check_type": item.get("check_type") or "unknown", "total": int(item.get("total") or 0)}
                for item in check_type_distribution
            ]
        }

    except Error as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        cursor.close()
        conn.close()


@app.get("/api/v1/software/compliance/results/export")
def export_compliance_results(
    check_id: Optional[int] = None,
    is_compliant: Optional[bool] = None,
    severity: Optional[str] = None
):
    """导出合规结果 CSV"""
    conn = get_db_connection()
    if not conn:
        raise HTTPException(status_code=500, detail="Database connection failed")

    try:
        cursor = conn.cursor(dictionary=True)
        where_sql, params = build_compliance_results_where_clause(
            check_id=check_id,
            is_compliant=is_compliant,
            severity=severity
        )

        cursor.execute(f"""
            SELECT c.check_name, c.check_type, c.severity,
                   a.hostname, a.ip_address,
                   r.is_compliant, r.current_version, r.expected_version,
                   r.details, r.checked_at
            FROM software_compliance_results r
            LEFT JOIN software_compliance_checks c ON r.check_id = c.id
            LEFT JOIN assets a ON r.asset_id = a.id
            WHERE {where_sql}
            ORDER BY r.checked_at DESC
        """, params)
        rows = cursor.fetchall()

        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow([
            "规则名称",
            "检查类型",
            "风险级别",
            "终端名称",
            "IP地址",
            "是否合规",
            "当前版本",
            "要求版本",
            "说明",
            "扫描时间"
        ])

        for row in rows:
            check_type = (row.get("check_type") or "").lower()
            if check_type == "license" or row.get("is_compliant") is None:
                compliance_label = "手工核查"
            else:
                compliance_label = "合规" if row.get("is_compliant") else "不合规"
            writer.writerow([
                row.get("check_name") or "",
                row.get("check_type") or "",
                row.get("severity") or "",
                row.get("hostname") or "",
                row.get("ip_address") or "",
                compliance_label,
                row.get("current_version") or "",
                row.get("expected_version") or "",
                row.get("details") or "",
                format_datetime_value(row.get("checked_at")) or ""
            ])

        filename = f"software-compliance-results-{datetime.now().strftime('%Y%m%d-%H%M%S')}.csv"
        csv_content = output.getvalue()
        output.close()

        return Response(
            content='\ufeff' + csv_content,
            media_type="text/csv; charset=utf-8",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'}
        )

    except Error as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        cursor.close()
        conn.close()

@app.post("/api/v1/software/compliance/scan")
def trigger_compliance_scan(payload: ComplianceScanRequest, request: Request):
    """触发并立即执行服务端合规扫描"""
    conn = get_db_connection()
    if not conn:
        raise HTTPException(status_code=500, detail="Database connection failed")

    cursor = None
    try:
        cursor = conn.cursor(dictionary=True)

        asset_where_clauses = ["a.deleted_at IS NULL"]
        asset_params: List[Any] = []
        if payload.asset_ids:
            placeholders = ", ".join(["%s"] * len(payload.asset_ids))
            asset_where_clauses.append(f"a.id IN ({placeholders})")
            asset_params.extend(payload.asset_ids)

        cursor.execute(f"""
            SELECT a.id, a.hostname, a.ip_address, a.group_id
            FROM assets a
            WHERE {' AND '.join(asset_where_clauses)}
            ORDER BY a.id
        """, asset_params)
        assets = cursor.fetchall()
        if not assets:
            raise HTTPException(status_code=404, detail="未找到可扫描的资产")

        check_where_clauses = ["enabled = TRUE"]
        check_params: List[Any] = []
        if payload.check_ids:
            placeholders = ", ".join(["%s"] * len(payload.check_ids))
            check_where_clauses.append(f"id IN ({placeholders})")
            check_params.extend(payload.check_ids)

        cursor.execute(f"""
            SELECT id, check_name, check_type, software_name, required_version,
                   severity, enabled, apply_to_groups
            FROM software_compliance_checks
            WHERE {' AND '.join(check_where_clauses)}
            ORDER BY id
        """, check_params)
        checks = cursor.fetchall()
        if not checks:
            raise HTTPException(status_code=404, detail="未找到启用中的合规规则")

        target_assets = []
        for asset in assets:
            matched_checks = [check for check in checks if asset_matches_compliance_check(asset, check)]
            if matched_checks:
                asset["matched_checks"] = matched_checks
                target_assets.append(asset)

        if not target_assets:
            raise HTTPException(status_code=404, detail="所选资产与规则没有可执行的匹配关系")

        now = datetime.now()
        target_ids = [asset["id"] for asset in target_assets]
        created_by = get_request_username(request, fallback=payload.created_by or "system")
        task_name = payload.task_name or f"软件合规扫描 {now.strftime('%Y-%m-%d %H:%M:%S')}"
        target_type = "asset" if payload.asset_ids else "all"
        options = {
            "mode": "server-side-compliance-scan",
            "asset_ids": target_ids,
            "check_ids": payload.check_ids or [check["id"] for check in checks]
        }

        cursor.execute("""
            INSERT INTO software_tasks (
                task_name, task_type, software_name, target_type, target_ids,
                target_count, priority, options, status, progress,
                start_time, created_by
            ) VALUES (%s, 'check', %s, %s, %s, %s, %s, %s, 'running', %s, %s, %s)
        """, (
            task_name,
            "compliance-scan",
            target_type,
            json.dumps(target_ids),
            len(target_assets),
            "normal",
            json.dumps(options, ensure_ascii=False),
            0,
            now,
            created_by
        ))
        task_id = cursor.lastrowid

        result_id_by_asset: Dict[int, int] = {}
        for asset_id in target_ids:
            cursor.execute("""
                INSERT INTO software_task_results (
                    task_id, asset_id, status, progress, start_time
                ) VALUES (%s, %s, 'pending', 0, %s)
            """, (task_id, asset_id, now))
            result_id_by_asset[asset_id] = cursor.lastrowid

        conn.commit()

        total_checks = 0
        compliant_count = 0
        non_compliant_count = 0
        manual_review_count = 0
        success_count = 0
        failed_count = 0
        total_assets = len(target_assets)

        for index, asset in enumerate(target_assets, start=1):
            asset_id = asset["id"]
            result_id = result_id_by_asset[asset_id]

            try:
                cursor.execute("""
                    SELECT software_name, version, vendor, install_date
                    FROM asset_software
                    WHERE asset_id = %s
                """, (asset_id,))
                installed_software = cursor.fetchall()

                applicable_checks = asset["matched_checks"]
                asset_compliant_count = 0
                asset_non_compliant_count = 0
                asset_manual_review_count = 0
                for check in applicable_checks:
                    evaluation = evaluate_compliance_result(check, asset, installed_software)
                    cursor.execute("""
                        DELETE FROM software_compliance_results
                        WHERE check_id = %s AND asset_id = %s
                    """, (check["id"], asset_id))
                    cursor.execute("""
                        INSERT INTO software_compliance_results (
                            check_id, asset_id, is_compliant, current_version,
                            expected_version, details, checked_at
                        ) VALUES (%s, %s, %s, %s, %s, %s, NOW())
                    """, (
                        check["id"],
                        asset_id,
                        evaluation["is_compliant"],
                        evaluation["current_version"],
                        evaluation["expected_version"],
                        evaluation["details"]
                    ))
                    total_checks += 1
                    if evaluation["is_compliant"] is True:
                        compliant_count += 1
                        asset_compliant_count += 1
                    elif evaluation["is_compliant"] is False:
                        non_compliant_count += 1
                        asset_non_compliant_count += 1
                    else:
                        manual_review_count += 1
                        asset_manual_review_count += 1

                success_count += 1
                cursor.execute("""
                    UPDATE software_task_results
                    SET status = 'success',
                        progress = 100,
                        install_progress = 100,
                        stdout_log = %s,
                        end_time = NOW(),
                        duration = TIMESTAMPDIFF(SECOND, start_time, NOW())
                    WHERE id = %s
                """, (
                    f"已完成 {len(applicable_checks)} 条合规检查，合规 {asset_compliant_count}，不合规 {asset_non_compliant_count}，手工核查 {asset_manual_review_count}",
                    result_id
                ))
            except Exception as exc:
                failed_count += 1
                cursor.execute("""
                    UPDATE software_task_results
                    SET status = 'failed',
                        progress = 100,
                        error_message = %s,
                        end_time = NOW(),
                        duration = TIMESTAMPDIFF(SECOND, start_time, NOW())
                    WHERE id = %s
                """, (str(exc)[:1000], result_id))

            progress = int(index * 100 / total_assets)
            cursor.execute("""
                UPDATE software_tasks
                SET progress = %s,
                    success_count = %s,
                    failed_count = %s,
                    running_count = %s,
                    updated_at = NOW()
                WHERE id = %s
            """, (progress, success_count, failed_count, total_assets - index, task_id))
            conn.commit()

        final_status = "completed" if failed_count == 0 else ("failed" if success_count == 0 else "completed")
        cursor.execute("""
            UPDATE software_tasks
            SET status = %s,
                progress = 100,
                success_count = %s,
                failed_count = %s,
                running_count = 0,
                end_time = NOW(),
                updated_at = NOW(),
                options = %s
            WHERE id = %s
        """, (
            final_status,
            success_count,
            failed_count,
            json.dumps({
                **options,
                "summary": {
                    "asset_count": total_assets,
                    "check_count": total_checks,
                    "compliant_count": compliant_count,
                    "non_compliant_count": non_compliant_count,
                    "manual_review_count": manual_review_count
                }
            }, ensure_ascii=False),
            task_id
        ))
        conn.commit()

        return {
            "message": "合规扫描执行完成",
            "status": final_status,
            "task_id": task_id,
            "summary": {
                "asset_count": total_assets,
                "check_count": total_checks,
                "compliant_count": compliant_count,
                "non_compliant_count": non_compliant_count,
                "manual_review_count": manual_review_count,
                "success_count": success_count,
                "failed_count": failed_count
            }
        }

    except HTTPException:
        conn.rollback()
        raise
    except Error as e:
        conn.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    except Exception as e:
        conn.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        if cursor:
            cursor.close()
        conn.close()

# 注意：需要将这些API添加到 software_management_api_complete.py 中

if __name__ == "__main__":
    import uvicorn
    print("=" * 60)
    print("CMDB Software Management API Starting...")
    print("=" * 60)
    print(f"Package Storage: {PACKAGE_STORAGE_PATH}")
    print(f"API Service: http://0.0.0.0:8081")
    print(f"API Docs: http://localhost:8081/docs")
    print("=" * 60)
    uvicorn.run(app, host="0.0.0.0", port=8081, log_level="info")
