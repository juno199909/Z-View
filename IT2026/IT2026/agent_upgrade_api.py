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
import re
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

# P1 升级器专项：熔断器管理 API（升级失败 3 次熔断 30min，管理端可查询/解除）
# 熔断器本体在 zvplatform/routers/agent_heartbeat.py（函数级延迟导入防循环）


@router.get("/breakers")
def list_upgrade_breakers():
    """升级熔断器状态列表（管理端排障/处置用，admin 权限）。"""
    from zvplatform.routers.agent_heartbeat import _UPGRADE_FAILURE_BREAKER

    import time as _time

    now = _time.time()
    breakers = []
    for asset_id, b in sorted(_UPGRADE_FAILURE_BREAKER.items()):
        until = float(b.get("until") or 0)
        breakers.append({
            "asset_id": asset_id,
            "to_version": b.get("to_version"),
            "count": b.get("count"),
            "open": bool(until and now < until),
            "cooldown_until": until,
        })
    return {"breakers": breakers}


@router.delete("/breakers/{asset_id}")
def clear_upgrade_breaker(asset_id: int):
    """解除指定资产的升级熔断（管理端处置用，admin 权限）。"""
    from zvplatform.routers.agent_heartbeat import _UPGRADE_FAILURE_BREAKER

    removed = _UPGRADE_FAILURE_BREAKER.pop(asset_id, None)
    if removed is None:
        raise HTTPException(status_code=404, detail="breaker not found")
    safe_console_print(
        f"[Upgrade] breaker cleared for asset {asset_id} "
        f"(was {removed.get('to_version')}, count {removed.get('count')})"
    )
    return {"message": "breaker cleared", "asset_id": asset_id}


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


_MANIFEST_MTIME: float = 0.0


def get_latest_upgrade() -> Dict[str, Any]:
    """供 heartbeat handler 调用：返回最新升级信息（无则空 dict）。

    manifest.json 按 mtime 变化自动重载——带外发布（脚本直写 manifest）无需重启后端。
    """
    global _MANIFEST_MTIME
    try:
        mtime = os.path.getmtime(MANIFEST_PATH)
    except OSError:
        mtime = 0.0
    if not LATEST_UPGRADE or mtime != _MANIFEST_MTIME:
        m = _load_manifest()
        if m.get("version"):
            LATEST_UPGRADE.clear()
            LATEST_UPGRADE.update(m)
            _MANIFEST_MTIME = mtime
    return dict(LATEST_UPGRADE)


def record_agent_version(asset_id: int, version: Optional[str]):
    """供 heartbeat handler 调用：记录资产上报的 Agent 版本。"""
    if asset_id and version:
        AGENT_REPORTED_VERSIONS[int(asset_id)] = {
            "version": str(version),
            "ts": time.time(),
        }


# V1.7.0：升级事务历史（desired/reported/upgrade_state 协议的服务端持久层）
_UPGRADE_HISTORY_TABLE = "agent_upgrade_history"


def ensure_agent_upgrade_history(conn) -> None:
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS agent_upgrade_history (
            id BIGINT AUTO_INCREMENT PRIMARY KEY,
            asset_id INT NOT NULL,
            upgrade_id VARCHAR(80) NOT NULL,
            from_version VARCHAR(32) DEFAULT NULL,
            to_version VARCHAR(32) DEFAULT NULL,
            state VARCHAR(24) NOT NULL,
            detail TEXT,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
            UNIQUE KEY uk_asset_upgrade (asset_id, upgrade_id),
            KEY idx_state (state)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """)
    conn.commit()
    cur.close()


def record_agent_upgrade_state(conn, asset_id: int, upgrade_id: str, state: str,
                               from_version: Optional[str] = None,
                               to_version: Optional[str] = None,
                               detail: Optional[dict] = None) -> None:
    """按 (asset_id, upgrade_id) 幂等 upsert 升级状态。"""
    try:
        ensure_agent_upgrade_history(conn)
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO agent_upgrade_history "
            "(asset_id, upgrade_id, from_version, to_version, state, detail) "
            "VALUES (%s, %s, %s, %s, %s, %s) "
            "ON DUPLICATE KEY UPDATE state=VALUES(state), from_version=VALUES(from_version), "
            "to_version=VALUES(to_version), detail=VALUES(detail)",
            (asset_id, upgrade_id, from_version, to_version, state,
             json.dumps(detail, ensure_ascii=False) if detail else None),
        )
        conn.commit()
        cur.close()
    except Exception as exc:
        safe_console_print(f"[AgentUpgrade] history record failed: {exc}")


def close_reached_upgrade(conn, asset_id: int, version: str) -> int:
    """V1.7.0 收敛：Agent 版本已达 desired → 未完结事务标 COMMITTED。

    updater 旧版不上报终态，版本到位即最强 COMMIT 证据
    （避免事务停留在 DISPATCHED/RUNNING）。返回受影响行数。
    """
    try:
        ensure_agent_upgrade_history(conn)
        cursor = conn.cursor()
        try:
            cursor.execute(
                "UPDATE agent_upgrade_history SET state = 'COMMITTED' "
                "WHERE asset_id = %s AND to_version = %s AND state IN ('DISPATCHED', 'RUNNING')",
                (asset_id, version),
            )
            conn.commit()
            return cursor.rowcount
        finally:
            cursor.close()
    except Exception as exc:
        safe_console_print(f"[AgentUpgrade] close reached upgrade failed: {exc}")
        return 0


def get_latest_upgrade_states(conn) -> dict:
    """asset_id -> 该资产最近一条升级历史（升级状态页用）。"""
    try:
        cur = conn.cursor(dictionary=True)
        cur.execute("""
            SELECT h.asset_id, h.upgrade_id, h.from_version, h.to_version, h.state, h.updated_at
            FROM agent_upgrade_history h
            JOIN (SELECT asset_id, MAX(id) AS mi FROM agent_upgrade_history GROUP BY asset_id) m
              ON m.asset_id = h.asset_id AND m.mi = h.id
        """)
        rows = cur.fetchall()
        cur.close()
        return {int(r["asset_id"]): r for r in rows}
    except Exception:
        return {}


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
    allow_downgrade: bool = Form(False),
):
    """上传新版本 Agent 包（admin）。幂等：同版本覆盖。支持 .exe（单文件）与 .zip（onedir 目录包）。"""
    _require_admin(request)
    version = version.strip()
    if not version or not version.replace(".", "").replace("-", "").isalnum():
        raise HTTPException(status_code=422, detail="Invalid version format")

    filename_lower = (file.filename or "").lower()
    if filename_lower.endswith(".exe"):
        package_type = "exe"
        stored_name = "Z-View.exe"
    elif filename_lower.endswith(".zip"):
        # V1.6.0：onedir 目录包（Z-View.exe + _internal + updater），由 ZViewUpdater 安装
        package_type = "onedir-zip"
        stored_name = "agent-onedir.zip"
    else:
        raise HTTPException(status_code=422, detail="Only .exe or .zip files accepted")

    # 版本回退保护（2026-09-09 事故）：manifest 被旧版本覆盖会让所有终端
    # 收到"降级指令"并无限重试。默认拒绝 <= 当前 manifest 的上传。
    current_manifest = _load_manifest()
    if current_manifest.get("version") and not allow_downgrade:

        def _ver_tuple(v: str) -> tuple:
            parts = re.findall(r"\d+", str(v))
            return tuple(int(p) for p in parts[:3]) if parts else (0,)

        if _ver_tuple(version) <= _ver_tuple(str(current_manifest["version"])):
            raise HTTPException(
                status_code=409,
                detail=(
                    f"Refusing downgrade upload: {version} <= current "
                    f"{current_manifest['version']}; pass allow_downgrade=true to force"
                ),
            )

    _ensure_dir()
    version_dir = os.path.join(UPGRADE_DIR, version)
    os.makedirs(version_dir, exist_ok=True)
    exe_path = os.path.join(version_dir, stored_name)

    sha = hashlib.sha256()
    size = 0
    with open(exe_path, "wb") as out:
        while chunk := await file.read(1024 * 512):
            out.write(chunk)
            sha.update(chunk)
            size += len(chunk)
    digest = sha.hexdigest()

    # P1-04 部署流程修复：onedir-zip 包解压 Z-View.exe 到版本目录，
    # 供网页自助部署（/api/v1/console/agent-deploy/package）下载——
    # 此前版本目录只有 zip 无解压 exe，部署端 404 "No agent package available"
    if package_type == "onedir-zip":
        import zipfile as _zipfile

        try:
            deploy_exe = os.path.join(version_dir, "Z-View.exe")
            with _zipfile.ZipFile(exe_path, "r") as zf:
                with zf.open("Z-View.exe") as src_f, open(deploy_exe, "wb") as dst_f:
                    while chunk := src_f.read(1024 * 512):
                        dst_f.write(chunk)
            safe_console_print(f"[AgentUpgrade] deploy exe extracted: {deploy_exe}")
        except Exception as exc:
            safe_console_print(f"[AgentUpgrade] deploy exe extraction failed: {exc}")

    manifest = _load_manifest()
    manifest.update({
        "version": version,
        "sha256": digest,
        "size": size,
        "package_type": package_type,
        "filename": stored_name,
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
    """最新版本 + 各资产当前 Agent 版本 + 最近升级事务状态（admin 查看）。"""
    latest = get_latest_upgrade()
    conn = _db()
    assets = []
    upgrade_states = {}
    if conn:
        try:
            ensure_agent_upgrade_history(conn)
            upgrade_states = get_latest_upgrade_states(conn)
        except Exception:
            upgrade_states = {}
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
                    "last_upgrade": upgrade_states.get(int(r["id"])),
                })
            cur.close()
        except Exception as exc:
            safe_console_print(f"[AgentUpgrade] status db error: {exc}")
        finally:
            conn.close()
    return {"latest": latest, "assets": assets, "total": len(assets)}


@router.get("/download")
def download_upgrade(request: Request, version: str = ""):
    """Agent 下载升级包（agent_token 鉴权：Authorization Bearer 或 ?agent_token=）。
    按该版本目录实际存在的包文件响应（Z-View.exe 或 agent-onedir.zip）。"""
    require_agent_request(request)
    latest = get_latest_upgrade()
    target_version = version or latest.get("version")
    if not target_version:
        raise HTTPException(status_code=404, detail="No upgrade package available")
    version_dir = os.path.join(UPGRADE_DIR, target_version)
    candidates = []
    manifest_name = ""
    if latest.get("version") == target_version:
        manifest_name = str(latest.get("filename") or "")
        if manifest_name:
            candidates.append(manifest_name)
    candidates += ["Z-View.exe", "agent-onedir.zip"]
    for name in candidates:
        exe_path = os.path.join(version_dir, name)
        if os.path.exists(exe_path):
            media_type = "application/zip" if name.endswith(".zip") else "application/octet-stream"
            return FileResponse(exe_path, media_type=media_type, filename=name)
    raise HTTPException(status_code=404, detail="Upgrade package not found")


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