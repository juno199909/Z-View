"""
Z-View Agent 自动升级 API（R13）
- POST /api/v1/agent/upgrade/upload      admin 上传新版本 exe
- GET  /api/v1/agent/upgrade/status      admin 查看最新版本 + 各资产当前版本
- GET  /api/v1/agent/upgrade/download    agent_token 下载 exe
- DELETE /api/v1/agent/upgrade/{version} admin 删除某版本
存储: agent_upgrade/{version}/Z-View.exe + manifest.json（服务重启不丢）
心跳响应自动携带 upgrade 指令（版本不一致时），Agent 端自升级。
"""

import os
import json
import time
import hashlib
import shutil
from typing import Any, Dict, Optional

from fastapi import APIRouter, FastAPI, HTTPException, Request, UploadFile, File, Form
from fastapi.responses import FileResponse
import mysql.connector

from auth_utils import get_request_username, user_has_permission, require_agent_request
from console_utils import safe_console_print
from config_utils import get_db_config


router = APIRouter(prefix="/api/v1/agent/upgrade", tags=["agent-upgrade"])

# 升级包存储目录（代码根下，gitignore 防入库）
UPGRADE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "agent_upgrade")
MANIFEST_PATH = os.path.join(UPGRADE_DIR, "manifest.json")

# 内存态：最新升级信息 + 各资产上报的版本
LATEST_UPGRADE: Dict[str, Any] = {}          # {version, sha256, path, uploaded_at}
AGENT_REPORTED_VERSIONS: Dict[int, Dict[str, Any]] = {}  # {asset_id: {version, ts}}


def _ensure_dir():
    os.makedirs(UPGRADE_DIR, exist_ok=True)


def _load_manifest() -> Dict[str, Any]:
    try:
        with open(MANIFEST_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _save_manifest(manifest: Dict[str, Any]):
    _ensure_dir()
    with open(MANIFEST_PATH, "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)


def get_latest_upgrade() -> Dict[str, Any]:
    """供 heartbeat handler 调用：返回最新升级信息（无则空 dict）。"""
    if not LATEST_UPGRADE:
        m = _load_manifest()
        if m.get("version"):
            LATEST_UPGRADE.update(m)
    return dict(LATEST_UPGRADE)


def record_agent_version(asset_id: int, version: Optional[str]):
    """供 heartbeat handler 调用：记录资产上报的 Agent 版本。"""
    if asset_id and version:
        AGENT_REPORTED_VERSIONS[int(asset_id)] = {
            "version": str(version),
            "ts": time.time(),
        }


def _db():
    try:
        conn = mysql.connector.connect(**get_db_config())
        return conn
    except Exception as exc:
        safe_console_print(f"[AgentUpgrade] DB connect failed: {exc}")
        return None


def _require_admin(request: Request):
    # 复用中间件已校验，此处仅保留语义（admin 上传/删除）
    get_request_username(request, fallback="console")


@router.post("/upload")
async def upload_upgrade(
    request: Request,
    file: UploadFile = File(...),
    version: str = Form(...),
):
    """上传新版本 Agent exe（admin）。幂等：同版本覆盖。"""
    _require_admin(request)
    version = version.strip()
    if not version or not version.replace(".", "").replace("-", "").isalnum():
        raise HTTPException(status_code=422, detail="Invalid version format")
    if not (file.filename or "").lower().endswith(".exe"):
        raise HTTPException(status_code=422, detail="Only .exe files accepted")

    _ensure_dir()
    version_dir = os.path.join(UPGRADE_DIR, version)
    os.makedirs(version_dir, exist_ok=True)
    exe_path = os.path.join(version_dir, "Z-View.exe")

    sha = hashlib.sha256()
    size = 0
    with open(exe_path, "wb") as out:
        while chunk := await file.read(1024 * 512):
            out.write(chunk)
            sha.update(chunk)
            size += len(chunk)
    digest = sha.hexdigest()

    manifest = _load_manifest()
    manifest.update({
        "version": version,
        "sha256": digest,
        "size": size,
        "filename": "Z-View.exe",
        "uploaded_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "uploaded_by": get_request_username(request, fallback="console"),
    })
    _save_manifest(manifest)
    LATEST_UPGRADE.clear()
    LATEST_UPGRADE.update(manifest)

    safe_console_print(f"[AgentUpgrade] uploaded version={version} size={size} sha256={digest[:16]}...")
    return {"message": "Upgrade package uploaded", "version": version, "sha256": digest, "size": size}


@router.get("/status")
def upgrade_status():
    """最新版本 + 各资产当前 Agent 版本（admin 查看）。"""
    latest = get_latest_upgrade()
    conn = _db()
    assets = []
    if conn:
        try:
            cur = conn.cursor(dictionary=True)
            cur.execute(
                "SELECT id, hostname, ip_address FROM assets WHERE deleted_at IS NULL AND agent_install_status='installed'"
            )
            rows = cur.fetchall()
            for r in rows:
                # 优先读 DB（assets.agent_version，心跳维护，重启不丢），兜底内存
                info = AGENT_REPORTED_VERSIONS.get(int(r["id"]), {})
                ver = r.get("agent_version") or info.get("version")
                latest_v = latest.get("version")
                assets.append({
                    "asset_id": r["id"],
                    "hostname": r["hostname"],
                    "ip_address": r["ip_address"],
                    "current_version": ver,
                    "up_to_date": bool(ver and latest_v and ver == latest_v),
                    "last_report": info.get("ts"),
                })
            cur.close()
        except Exception as exc:
            safe_console_print(f"[AgentUpgrade] status db error: {exc}")
        finally:
            conn.close()
    return {"latest": latest, "assets": assets, "total": len(assets)}


@router.get("/download")
def download_upgrade(request: Request, version: str = ""):
    """Agent 下载新版本 exe（agent_token 鉴权：Authorization Bearer 或 ?agent_token=）。"""
    require_agent_request(request)
    latest = get_latest_upgrade()
    target_version = version or latest.get("version")
    if not target_version:
        raise HTTPException(status_code=404, detail="No upgrade package available")
    exe_path = os.path.join(UPGRADE_DIR, target_version, "Z-View.exe")
    if not os.path.exists(exe_path):
        raise HTTPException(status_code=404, detail="Upgrade package not found")
    return FileResponse(exe_path, media_type="application/octet-stream", filename="Z-View.exe")


@router.delete("/{version}")
def delete_upgrade(version: str, request: Request):
    """删除某版本的升级包（admin）。"""
    _require_admin(request)
    version_dir = os.path.join(UPGRADE_DIR, version)
    if not os.path.isdir(version_dir):
        raise HTTPException(status_code=404, detail="Version not found")
    shutil.rmtree(version_dir, ignore_errors=True)
    manifest = _load_manifest()
    if manifest.get("version") == version:
        manifest = {}
        _save_manifest(manifest)
        LATEST_UPGRADE.clear()
    return {"message": "Deleted", "version": version}


def mount_agent_upgrade_api(app: FastAPI):
    app.include_router(router)