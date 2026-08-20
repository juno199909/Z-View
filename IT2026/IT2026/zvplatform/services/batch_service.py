# -*- coding: utf-8 -*-
"""批量操作服务层（P1-01 从 assets_api 迁出）。"""
from __future__ import annotations

import base64
import json
import os
from typing import Any, Dict, Optional
from urllib.parse import urlparse

import requests
from fastapi import HTTPException

from zvplatform.agent_client import AGENT_CONTROL_PORT, build_agent_auth_headers
from zvplatform.common import truncate_text
from zvplatform.db import format_datetime


def build_batch_parameters_text(operation_type: str, parameters: Dict[str, Any]) -> str:
    if operation_type == "command":
        return truncate_text(str(parameters.get("command") or ""), 300)
    if operation_type == "restart":
        delay = parameters.get("delay", 0)
        return f"delay={delay}s"
    if operation_type == "shutdown":
        delay = parameters.get("delay", 0)
        return f"delay={delay}s"
    if operation_type == "software":
        url = str(parameters.get("url") or "").strip()
        install_command = str(parameters.get("install_command") or "").strip()
        return truncate_text(f"url={url} | install={install_command}", 300)
    if operation_type == "script":
        return truncate_text(str(parameters.get("script") or ""), 300)
    return truncate_text(json.dumps(parameters, ensure_ascii=False), 300)


def build_batch_output(stdout_log: Optional[str], stderr_log: Optional[str], error_message: Optional[str]) -> str:
    parts = []
    if stdout_log:
        parts.append(str(stdout_log).strip())
    if stderr_log:
        parts.append(str(stderr_log).strip())
    if error_message:
        parts.append(str(error_message).strip())
    return "\n".join(part for part in parts if part)


def escape_powershell_single_quoted(value: str) -> str:
    return value.replace("'", "''")


def build_restart_command(parameters: Dict[str, Any]) -> str:
    try:
        delay = int(parameters.get("delay", 0) or 0)
    except (TypeError, ValueError):
        delay = 0
    delay = max(0, delay)
    return f"shutdown /r /t {delay} /f"


def build_shutdown_command(parameters: Dict[str, Any]) -> str:
    try:
        delay = int(parameters.get("delay", 0) or 0)
    except (TypeError, ValueError):
        delay = 0
    delay = max(0, delay)
    return f"shutdown /s /t {delay} /f"


def build_script_command(parameters: Dict[str, Any]) -> str:
    script = str(parameters.get("script") or "").strip()
    if not script:
        raise HTTPException(status_code=400, detail="Script content is required")
    encoded = base64.b64encode(script.encode("utf-16le")).decode("ascii")
    return f"powershell -NoProfile -ExecutionPolicy Bypass -EncodedCommand {encoded}"


def build_software_command(parameters: Dict[str, Any]) -> str:
    url = str(parameters.get("url") or "").strip()
    install_command = str(parameters.get("install_command") or "").strip()
    if not url:
        raise HTTPException(status_code=400, detail="Software URL is required")

    parsed = urlparse(url)
    file_name = os.path.basename(parsed.path) or "package.exe"
    safe_file_name = "".join(ch for ch in file_name if ch not in '<>:"/\\|?*').strip() or "package.exe"
    file_name_literal = escape_powershell_single_quoted(safe_file_name)
    url_literal = escape_powershell_single_quoted(url)

    default_install = """
if ($filePath.ToLower().EndsWith('.msi')) {
    $process = Start-Process -FilePath 'msiexec.exe' -ArgumentList @('/i', $filePath, '/qn', '/norestart') -Wait -PassThru
    exit $process.ExitCode
}
$process = Start-Process -FilePath $filePath -ArgumentList @('/quiet', '/norestart') -Wait -PassThru
exit $process.ExitCode
""".strip()

    custom_install = ""
    if install_command:
        custom_install = f"""
$installCommand = @'
{install_command}
'@
$installCommand = $installCommand.Replace('{{file}}', $filePath).Replace('{{filename}}', '{file_name_literal}').Replace('{{dir}}', $downloadDir)
cmd.exe /c $installCommand
exit $LASTEXITCODE
""".strip()

    ps_script = f"""
$ErrorActionPreference = 'Stop'
$downloadDir = Join-Path $env:TEMP 'CMDBBatch'
New-Item -ItemType Directory -Path $downloadDir -Force | Out-Null
$filePath = Join-Path $downloadDir '{file_name_literal}'
Invoke-WebRequest -Uri '{url_literal}' -OutFile $filePath -UseBasicParsing
Set-Location $downloadDir
{custom_install or default_install}
""".strip()

    encoded = base64.b64encode(ps_script.encode("utf-16le")).decode("ascii")
    return f"powershell -NoProfile -ExecutionPolicy Bypass -EncodedCommand {encoded}"


def build_batch_command(operation_type: str, parameters: Dict[str, Any]) -> str:
    if operation_type == "command":
        command = str(parameters.get("command") or "").strip()
        if not command:
            raise HTTPException(status_code=400, detail="Command is required")
        return command
    if operation_type == "restart":
        return build_restart_command(parameters)
    if operation_type == "shutdown":
        return build_shutdown_command(parameters)
    if operation_type == "software":
        return build_software_command(parameters)
    if operation_type == "script":
        return build_script_command(parameters)
    raise HTTPException(status_code=400, detail=f"Unsupported operation type: {operation_type}")


def get_batch_operation_timeout(operation_type: str) -> int:
    if operation_type == "restart":
        return 15
    if operation_type == "shutdown":
        return 15
    if operation_type == "software":
        return 180
    if operation_type == "script":
        return 120
    return 45


def build_batch_zview_cmd(operation_type: str, parameters: Dict[str, Any], command_text: str) -> Dict[str, Any]:
    """P0-10：平台侧构造结构化白名单命令，Agent 端按 op 分发，不再透传任意 shell。"""
    if operation_type == "restart":
        try:
            delay = int(parameters.get("delay", 0) or 0)
        except (TypeError, ValueError):
            delay = 0
        return {"op": "restart", "delay": max(0, min(delay, 3600))}
    if operation_type == "shutdown":
        try:
            delay = int(parameters.get("delay", 0) or 0)
        except (TypeError, ValueError):
            delay = 0
        return {"op": "shutdown", "delay": max(0, min(delay, 3600))}
    if operation_type in ("script", "software"):
        encoded = command_text.split("-EncodedCommand ", 1)[1].strip() if "-EncodedCommand " in command_text else ""
        return {"op": "script", "encoded": encoded,
                "timeout_seconds": get_batch_operation_timeout(operation_type)}
    # 自由命令（批量操作页手输）：raw 通道，Agent 侧逐条审计
    return {"op": "raw", "command": command_text,
            "timeout_seconds": get_batch_operation_timeout(operation_type)}


def execute_batch_command_on_agent(
    asset: Dict[str, Any],
    operation_type: str,
    parameters: Dict[str, Any],
    operator_name: str,
) -> Dict[str, Any]:
    asset_id = asset.get("id")
    hostname = asset.get("hostname")
    ip_address = asset.get("ip_address")
    status = asset.get("status")

    try:
        command_text = build_batch_command(operation_type, parameters)
    except HTTPException as exc:
        return {
            "asset_id": asset_id,
            "hostname": hostname,
            "ip_address": ip_address,
            "status": "failed",
            "command_text": None,
            "stdout_log": None,
            "stderr_log": None,
            "output_text": exc.detail,
            "returncode": None,
            "error_message": exc.detail,
        }

    if not ip_address:
        error_message = "Missing agent IP address"
        return {
            "asset_id": asset_id,
            "hostname": hostname,
            "ip_address": ip_address,
            "status": "failed",
            "command_text": command_text,
            "stdout_log": None,
            "stderr_log": None,
            "output_text": error_message,
            "returncode": None,
            "error_message": error_message,
        }

    if status != "online":
        error_message = "Target agent is offline"
        return {
            "asset_id": asset_id,
            "hostname": hostname,
            "ip_address": ip_address,
            "status": "failed",
            "command_text": command_text,
            "stdout_log": None,
            "stderr_log": None,
            "output_text": error_message,
            "returncode": None,
            "error_message": error_message,
        }

    timeout_seconds = get_batch_operation_timeout(operation_type)
    request_payload = {
        "zview_cmd": build_batch_zview_cmd(operation_type, parameters, command_text),
        "operator": operator_name,
    }

    try:
        response = requests.post(
            f"http://{ip_address}:{AGENT_CONTROL_PORT}/api/v1/command",
            json=request_payload,
            headers=build_agent_auth_headers(),
            timeout=timeout_seconds,
        )
        response.raise_for_status()
        body = response.json()
    except requests.RequestException as exc:
        error_message = f"Agent request failed: {exc}"
        return {
            "asset_id": asset_id,
            "hostname": hostname,
            "ip_address": ip_address,
            "status": "failed",
            "command_text": command_text,
            "stdout_log": None,
            "stderr_log": None,
            "output_text": error_message,
            "returncode": None,
            "error_message": error_message,
        }
    except ValueError:
        error_message = "Agent returned invalid JSON"
        return {
            "asset_id": asset_id,
            "hostname": hostname,
            "ip_address": ip_address,
            "status": "failed",
            "command_text": command_text,
            "stdout_log": None,
            "stderr_log": None,
            "output_text": error_message,
            "returncode": None,
            "error_message": error_message,
        }

    success = bool(body.get("success"))
    stdout_log = body.get("stdout")
    stderr_log = body.get("stderr")
    returncode = body.get("returncode")
    error_message = body.get("error")

    if not success and not error_message:
        error_message = "Agent reported execution failure"

    output_text = build_batch_output(stdout_log, stderr_log, error_message)

    return {
        "asset_id": asset_id,
        "hostname": hostname,
        "ip_address": ip_address,
        "status": "success" if success else "failed",
        "command_text": command_text,
        "stdout_log": stdout_log,
        "stderr_log": stderr_log,
        "output_text": output_text or ("Completed" if success else "Execution failed"),
        "returncode": returncode,
        "error_message": error_message,
    }


def normalize_batch_result_row(row: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "id": row.get("id"),
        "asset_id": row.get("asset_id"),
        "hostname": row.get("hostname"),
        "ip_address": row.get("ip_address"),
        "status": row.get("status"),
        "command_text": row.get("command_text"),
        "stdout_log": row.get("stdout_log"),
        "stderr_log": row.get("stderr_log"),
        "output": row.get("output_text") or build_batch_output(
            row.get("stdout_log"),
            row.get("stderr_log"),
            row.get("error_message"),
        ),
        "returncode": row.get("returncode"),
        "error_message": row.get("error_message"),
        "executed_at": format_datetime(row.get("executed_at")),
    }
