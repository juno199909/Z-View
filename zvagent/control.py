# -*- coding: utf-8 -*-
"""1.9.56 专项 2：自 cmdb_agent_core.py 逐字迁入（#16/#10 模块化，逻辑未改）。"""
import base64
import hmac
import json
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from config_utils import get_env
from console_utils import safe_console_print
from zvagent.heartbeat import trigger_immediate_report

from zvagent.config import CONFIG

def _truncate_control_output(value: str | None, limit: int = 20000) -> str:
    text = str(value or "")
    if len(text) <= limit:
        return text
    return text[:limit] + "\n...[truncated]"


# =============================================================================
# 高权限命令白名单（P0-10）
# 结构化协议：{"zview_cmd": {"op": "restart|shutdown|script|raw", ...}}
# - restart/shutdown：delay 强制钳位 0~3600
# - script：base64 编码 PowerShell，长度上限 128KB
# - raw：自由命令，默认允许（ZVIEW_AGENT_ALLOW_RAW_COMMAND=0 可关闭），逐条告警日志
# - 兼容：旧平台裸 {"command": "..."} 按 raw 处理（同 raw 开关）
# =============================================================================

_RAW_COMMAND_ENV_NAME = "ZVIEW_AGENT_ALLOW_RAW_COMMAND"
_SCRIPT_B64_MAX_LEN = 128 * 1024


def _raw_command_allowed() -> bool:
    return str(get_env(_RAW_COMMAND_ENV_NAME, "1") or "1").strip().lower() not in ("0", "false", "no", "off")


def _clamp_timeout(value: Any, default: int = 60) -> int:
    try:
        timeout = int(value)
    except (TypeError, ValueError):
        timeout = default
    return max(1, min(timeout, 300))


def _dispatch_zview_cmd(zv: dict, operator: str) -> dict:
    op = str(zv.get("op") or "").strip().lower()

    if op in ("restart", "shutdown"):
        try:
            delay = int(zv.get("delay", 0) or 0)
        except (TypeError, ValueError):
            delay = 0
        delay = max(0, min(delay, 3600))
        flag = "/r" if op == "restart" else "/s"
        command = f"shutdown {flag} /t {delay} /f"
        print(f"[Command] whitelist op={op} delay={delay} operator={operator}")
        return execute_control_command(command, 15)

    if op == "script":
        encoded = str(zv.get("encoded") or "").strip()
        if not encoded or len(encoded) > _SCRIPT_B64_MAX_LEN:
            return {"success": False, "error": "script encoded payload missing or too large",
                    "stdout": "", "stderr": "", "returncode": None}
        try:
            base64.b64decode(encoded, validate=True)
        except Exception:
            return {"success": False, "error": "script encoded payload is not valid base64",
                    "stdout": "", "stderr": "", "returncode": None}
        command = f"powershell -NoProfile -ExecutionPolicy Bypass -EncodedCommand {encoded}"
        print(f"[Command] whitelist op=script len={len(encoded)} operator={operator}")
        return execute_control_command(command, _clamp_timeout(zv.get("timeout_seconds", 120), 120))

    if op == "raw":
        command = str(zv.get("command") or "").strip()
        if not command:
            return {"success": False, "error": "Command is required",
                    "stdout": "", "stderr": "", "returncode": None}
        if not _raw_command_allowed():
            print(f"[Command] raw command DENIED by ZVIEW_AGENT_ALLOW_RAW_COMMAND=0 operator={operator}")
            return {"success": False, "error": "Raw command execution is disabled on this agent",
                    "stdout": "", "stderr": "", "returncode": None}
        print(f"[Command] raw command executed (audit): operator={operator} command={command[:200]}")
        return execute_control_command(command, _clamp_timeout(zv.get("timeout_seconds", 60), 60))

    return {"success": False, "error": f"unknown zview_cmd op: {op}",
            "stdout": "", "stderr": "", "returncode": None}


def handle_control_command_payload(payload: dict) -> dict:
    """9001 /api/v1/command 统一入口：结构化白名单优先，裸 command 兼容。"""
    operator = str(payload.get("operator") or payload.get("requester") or "platform")
    zv = payload.get("zview_cmd")
    if isinstance(zv, dict):
        return _dispatch_zview_cmd(zv, operator)
    # 兼容旧平台：裸 command 字段按 raw 处理（同一开关管控）
    legacy_command = str(payload.get("command") or "").strip()
    if legacy_command:
        return _dispatch_zview_cmd({"op": "raw", "command": legacy_command,
                                    "timeout_seconds": payload.get("timeout_seconds", 60)}, operator)
    return {"success": False, "error": "command payload required (zview_cmd or command)",
            "stdout": "", "stderr": "", "returncode": None}


def execute_control_command(command: str, timeout_seconds: int = 60) -> dict:
    normalized_command = str(command or "").strip()
    if not normalized_command:
        return {
            "success": False,
            "error": "Command is required",
            "stdout": "",
            "stderr": "",
            "returncode": None,
        }

    timeout_seconds = max(1, min(int(timeout_seconds or 60), 300))
    try:
        result = subprocess.run(
            normalized_command,
            shell=True,
            capture_output=True,
            text=True,
            errors="replace",
            timeout=timeout_seconds,
            check=False,
        )
        return {
            "success": result.returncode == 0,
            "stdout": _truncate_control_output(result.stdout),
            "stderr": _truncate_control_output(result.stderr),
            "returncode": result.returncode,
            "error": "" if result.returncode == 0 else f"Command exited with code {result.returncode}",
        }
    except subprocess.TimeoutExpired as exc:
        return {
            "success": False,
            "stdout": _truncate_control_output(exc.stdout),
            "stderr": _truncate_control_output(exc.stderr),
            "returncode": None,
            "error": f"Command timed out after {timeout_seconds} seconds",
        }
    except Exception as exc:
        return {
            "success": False,
            "stdout": "",
            "stderr": "",
            "returncode": None,
            "error": f"Command execution failed: {type(exc).__name__}: {exc}",
        }


# V1.8.3 通用任务通道：注册 command handler（与控制通道同一安全模型——
# raw 命令受 ZVIEW_AGENT_ALLOW_RAW_COMMAND 门控，zv 结构化命令白名单分发）
from zvagent import jobs as _agent_jobs  # noqa: E402


def _job_command_handler(payload: dict) -> dict:
    return execute_control_command(
        str(payload.get("command") or ""),
        _clamp_timeout(payload.get("timeout_seconds"), 60),
    )


_agent_jobs.register_job_handler("command", _job_command_handler)


class AgentControlRequestHandler(BaseHTTPRequestHandler):
    server_version = "ZViewAgentControl/1.0"
    max_request_body_bytes = 1024 * 1024

    def log_message(self, format, *args):
        print(f"[AgentControl] {format % args}")

    def _send_json(self, status_code: int, payload: dict):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status_code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(body)

    def _is_authorized(self) -> bool:
        expected_token = str(CONFIG.get("token") or "").strip()
        if not expected_token:
            return False
        authorization = str(self.headers.get("Authorization") or "").strip()
        provided_token = authorization[7:].strip() if authorization.lower().startswith("bearer ") else ""
        return bool(provided_token) and hmac.compare_digest(provided_token, expected_token)

    def _read_json_body(self) -> dict:
        raw_length = self.headers.get("Content-Length") or "0"
        try:
            content_length = int(raw_length)
        except ValueError as exc:
            raise ValueError("Invalid Content-Length") from exc
        if content_length < 0 or content_length > self.max_request_body_bytes:
            raise ValueError("Request body is too large")
        raw_body = self.rfile.read(content_length) if content_length else b"{}"
        if not raw_body:
            return {}
        value = json.loads(raw_body.decode("utf-8"))
        if not isinstance(value, dict):
            raise ValueError("JSON body must be an object")
        return value

    def do_POST(self):
        if not self._is_authorized():
            self._send_json(401, {"success": False, "error": "Unauthorized"})
            return

        try:
            payload = self._read_json_body()
        except (UnicodeDecodeError, ValueError, json.JSONDecodeError) as exc:
            self._send_json(400, {"success": False, "error": str(exc)})
            return

        if self.path == "/api/v1/command":
            result = handle_control_command_payload(payload)
            self._send_json(200 if result["success"] else 400, result)
            return

        if self.path == "/api/v1/security-command":
            try:
                from security_manager import execute_security_command
                command_type = payload.get("command_type") or ""
                params = payload.get("params") or {}
                result = execute_security_command(command_type, params)
                self._send_json(200 if result.get("success") else 400, result)
            except Exception as exc:
                self._send_json(500, {"success": False, "error": str(exc)})
            return

        if self.path == "/api/v1/trigger-report":
            result = trigger_immediate_report()
            self._send_json(200 if result["success"] else 502, result)
            return

        self._send_json(404, {"success": False, "error": "Unknown control endpoint"})


class AgentControlServer:
    def __init__(self, host: str | None = None, port: int | None = None):
        host = host or get_env("ZVIEW_BIND_HOST", "0.0.0.0") or "0.0.0.0"  # P0-2: 可配置监听地址
        self.host = host
        self.port = int(port or CONFIG.get("control_port") or 9001)
        self.server: ThreadingHTTPServer | None = None
        self.thread: threading.Thread | None = None

    def start(self):
        if self.thread and self.thread.is_alive():
            return
        self.server = ThreadingHTTPServer((self.host, self.port), AgentControlRequestHandler)
        self.server.daemon_threads = True
        self.thread = threading.Thread(target=self.server.serve_forever, name="agent-control", daemon=True)
        self.thread.start()
        print(f"[AgentControl] Listening on http://{self.host}:{self.port}")

    def stop(self):
        if self.server:
            self.server.shutdown()
            self.server.server_close()
            self.server = None
        if self.thread:
            self.thread.join(timeout=2)
            self.thread = None


class StarletteWebSocketAdapter:
    """Adapt the native websockets connection to the engine's Starlette API."""

    def __init__(self, connection):
        self.connection = connection
        self.remote_address = connection.remote_address

    async def receive_text(self) -> str:
        message = await self.connection.recv()
        if isinstance(message, bytes):
            return message.decode("utf-8")
        return str(message)

    async def send_json(self, payload: dict):
        await self.connection.send(json.dumps(payload, ensure_ascii=False))

    async def send_bytes(self, data: bytes):
        await self.connection.send(data)

    async def close(self, code: int = 1000, reason: str = ""):
        await self.connection.close(code=code, reason=reason)

