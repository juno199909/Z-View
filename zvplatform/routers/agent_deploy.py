# -*- coding: utf-8 -*-
"""终端部署三件套路由（P1-04，参照火绒企业版；1.9.55 自 assets_api 迁入 #16 模块化）。

1) 网页自助下载安装包（优先图形化 setup exe，回退 onedir zip）
2) 一键部署脚本生成（域开机脚本/三方桌管静默推送）
Agent 侧配套：Z-View.exe --install --quiet --server-url <center>
"""
from __future__ import annotations

from datetime import datetime
import os
from typing import Optional

from fastapi import APIRouter, HTTPException, Query, Request, Response
from fastapi.responses import FileResponse

from agent_upgrade_api import UPGRADE_DIR, get_latest_upgrade
from auth_utils import require_request_permission


router = APIRouter(tags=["agent-deploy"])


def _resolve_latest_agent_package() -> tuple[Optional[str], Optional[str]]:
    """最新版本目录里优先取图形化 setup exe，回退 onedir zip / 单文件 exe。"""
    import glob as _glob

    latest = get_latest_upgrade()
    version = str(latest.get("version") or "")
    if not version:
        return None, None
    version_dir = os.path.join(UPGRADE_DIR, version)
    # 网页自助部署优先给图形化安装器（双击下一步式 setup exe）；
    # 部署脚本/域推送仍走 zip（含 Z-View.exe + _internal）
    setups = sorted(
        _glob.glob(os.path.join(version_dir, "Z-View-Setup-*.exe")),
        reverse=True,
    )
    if setups:
        return version, setups[0]
    # 部署流程需要完整 onedir 包（zip 含 Z-View.exe + _internal），zip 优先
    for name in ("agent-onedir.zip", "Z-View.exe"):
        p = os.path.join(version_dir, name)
        if os.path.exists(p):
            return version, p
    return None, None


@router.get("/api/v1/console/agent-deploy/package")
def download_agent_deploy_package(request: Request):
    """网页自助部署：下载最新版 Agent 完整部署包（优先图形化 setup exe，admin）。

    包含 Z-View.exe + _internal\\（+ updater\\），终端解压后运行
    `Z-View.exe --install --quiet --server-url <中心地址>` 完成安装。
    """
    require_request_permission(getattr(request.state, "auth_user", None), request.url.path, request.method)
    version, package_path = _resolve_latest_agent_package()
    if not package_path:
        raise HTTPException(
            status_code=404,
            detail="No agent package available; upload one via /api/v1/agent/upgrade/upload first",
        )
    filename = os.path.basename(package_path)
    return FileResponse(
        package_path,
        media_type="application/octet-stream",
        filename=filename,
        headers={"X-Agent-Package-Filename": filename},
    )


def _build_agent_deploy_ps_script(center_url: str, deploy_token: str) -> str:
    return f"""# Z-View Agent 一键部署脚本（需管理员权限运行）
# 生成时间: {datetime.now():%Y-%m-%d %H:%M:%S}   中心: {center_url}
# 用途: 网页自助部署 / 域开机脚本 / 三方桌管静默推送（火绒企业版同款三件套）
# 注意: 脚本内嵌 Agent Token，仅限内部分发，勿公开传播
$ErrorActionPreference = 'Stop'
$Center = '{center_url}'
$Token  = '{deploy_token}'
$WorkDir = Join-Path $env:TEMP 'zview-agent-deploy'
New-Item -ItemType Directory -Force -Path $WorkDir | Out-Null

Write-Host '[1/3] downloading agent package (onedir zip)...'
Invoke-WebRequest -UseBasicParsing `
    -Uri "$Center/api/v1/agent/upgrade/download?agent_token=$Token" `
    -OutFile (Join-Path $WorkDir 'agent-onedir.zip')

Write-Host '[2/3] extracting...'
Expand-Archive -Force -Path (Join-Path $WorkDir 'agent-onedir.zip') -DestinationPath $WorkDir

Write-Host '[3/3] installing service...'
& (Join-Path $WorkDir 'Z-View.exe') --install --quiet --server-url $Center
if ($LASTEXITCODE -eq 0) {{
    Write-Host 'Z-View Agent deployed successfully.'
}} else {{
    Write-Host "deploy failed: exit=$LASTEXITCODE" -ForegroundColor Red
    exit 1
}}
"""


@router.get("/api/v1/console/agent-deploy/script")
def get_agent_deploy_script(
    request: Request,
    center: Optional[str] = Query(default=None, description="覆盖中心地址（终端访问用的 IP/域名），默认取当前访问地址"),
):
    """生成终端一键部署脚本（内嵌中心地址与下载 Token，admin 专用，勿外发）。"""
    require_request_permission(getattr(request.state, "auth_user", None), request.url.path, request.method)
    if center:
        base_url = center.rstrip("/")
    else:
        host = request.headers.get("host") or request.url.netloc
        scheme = request.headers.get("x-forwarded-proto") or request.url.scheme
        base_url = f"{scheme}://{host}"
    from auth_utils import get_expected_agent_token

    token = get_expected_agent_token()
    if not token:
        raise HTTPException(status_code=500, detail="agent token not configured on server")
    content = _build_agent_deploy_ps_script(base_url, token)
    return Response(
        content=content,
        media_type="text/plain; charset=utf-8",
        headers={"Content-Disposition": "attachment; filename=deploy-zview-agent.ps1"},
    )
