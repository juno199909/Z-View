# -*- coding: utf-8 -*-
"""平台配置来源报告（#17 配置整合：最终值 + 来源可追踪）。

GET /api/v1/console/config-report —— 每个配置项给出
{value(敏感项脱敏), env(对应环境变量), source(默认值/环境变量/.env(路径))}，
并列出实际加载的 .env、密钥文件与 auth_state 文件状态。
"""
from __future__ import annotations

import os

from fastapi import APIRouter, Request

from auth_utils import TOKEN_SECRET, require_request_permission
from config_utils import describe_env_source, get_env, get_env_files_loaded
from zvplatform.settings import get_settings


router = APIRouter(tags=["config-report"])


def _mask(value) -> str:
    return "******" if value else ""


def _entry(value, env_name: str, mask: bool = False) -> dict:
    shown = _mask(value) if mask else value
    return {"value": shown, "env": env_name, "source": describe_env_source(env_name) if env_name else "固定"}


def build_platform_config_report() -> dict:
    s = get_settings()
    groups = {
        "platform": {
            "host": _entry(s.platform.host, "ZVIEW_PLATFORM_HOST"),
            "port": _entry(s.platform.port, "ZVIEW_PLATFORM_PORT"),
            "tls_port": _entry(s.platform.tls_port, "ZVIEW_AGENT_TLS_PORT"),
            "agent_tls_enabled": _entry(s.platform.agent_tls_enabled, "ZVIEW_AGENT_TLS_ENABLED"),
            "tls_certfile": _entry(s.platform.tls_certfile, "ZVIEW_TLS_CERTFILE"),
            "tls_keyfile": _entry(s.platform.tls_keyfile, "ZVIEW_TLS_KEYFILE"),
        },
        "agent": {
            "control_port": _entry(s.agent.control_port, "ZVIEW_AGENT_CONTROL_PORT"),
            "remote_port": _entry(s.agent.remote_port, "ZVIEW_AGENT_REMOTE_PORT"),
            "heartbeat_interval": _entry(s.agent.heartbeat_interval, "ZVIEW_AGENT_HEARTBEAT_INTERVAL"),
            "heartbeat_max_failures": _entry(s.agent.heartbeat_max_failures, "ZVIEW_AGENT_HEARTBEAT_MAX_FAILURES"),
        },
        "discovery": {
            "max_tasks": _entry(s.discovery.max_tasks, "ZVIEW_DISCOVERY_MAX_TASKS"),
            "max_targets": _entry(s.discovery.max_targets, "ZVIEW_DISCOVERY_MAX_TARGETS"),
            "ping_timeout": _entry(s.discovery.ping_timeout, "ZVIEW_DISCOVERY_PING_TIMEOUT"),
            "ping_concurrency": _entry(s.discovery.ping_concurrency, "ZVIEW_DISCOVERY_PING_CONCURRENCY"),
        },
        "alerts": {
            "online_seconds": _entry(s.alerts.online_seconds, "ZVIEW_ALERT_ONLINE_SECONDS"),
            "offline_seconds": _entry(s.alerts.offline_seconds, "ZVIEW_ALERT_OFFLINE_SECONDS"),
            "cpu_warning": _entry(s.alerts.cpu_warning, "ZVIEW_ALERT_CPU_WARNING"),
            "cpu_critical": _entry(s.alerts.cpu_critical, "ZVIEW_ALERT_CPU_CRITICAL"),
            "memory_warning": _entry(s.alerts.memory_warning, "ZVIEW_ALERT_MEMORY_WARNING"),
            "memory_critical": _entry(s.alerts.memory_critical, "ZVIEW_ALERT_MEMORY_CRITICAL"),
            "disk_warning": _entry(s.alerts.disk_warning, "ZVIEW_ALERT_DISK_WARNING"),
            "disk_critical": _entry(s.alerts.disk_critical, "ZVIEW_ALERT_DISK_CRITICAL"),
        },
        "database": {
            "host": _entry(get_env("ZVIEW_DB_HOST", "127.0.0.1"), "ZVIEW_DB_HOST"),
            "port": _entry(get_env("ZVIEW_DB_PORT", "3306"), "ZVIEW_DB_PORT"),
            "name": _entry(get_env("ZVIEW_DB_NAME", "cmdb"), "ZVIEW_DB_NAME"),
            "user": _entry(get_env("ZVIEW_DB_USER", "root"), "ZVIEW_DB_USER"),
            "password": _entry(_mask(get_env("ZVIEW_DB_PASSWORD", "")), "ZVIEW_DB_PASSWORD"),
        },
        "auth": {
            "admin_username": _entry(get_env("ZVIEW_ADMIN_USERNAME", "admin"), "ZVIEW_ADMIN_USERNAME"),
            "token_ttl_seconds": _entry(get_env("ZVIEW_AUTH_TTL_SECONDS", "43200"), "ZVIEW_AUTH_TTL_SECONDS"),
            "password_hash_iterations": _entry(get_env("ZVIEW_PASSWORD_HASH_ITERATIONS", "120000"),
                                               "ZVIEW_PASSWORD_HASH_ITERATIONS"),
        },
    }

    secret_path = get_env("ZVIEW_AUTH_SECRET_FILE") or os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "auth_secret.txt")
    agent_secret_path = get_env("ZVIEW_AGENT_TOKEN_FILE") or os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "agent_secret.txt")

    secrets_section = {
        "token_secret": {
            "set": bool(TOKEN_SECRET),
            "source": describe_env_source("ZVIEW_AUTH_SECRET") if describe_env_source("ZVIEW_AUTH_SECRET") != "未设置"
                      else (f"文件({secret_path})" if os.path.exists(secret_path) else "自动生成并落盘"),
            "value": _mask(TOKEN_SECRET),
        },
        "agent_token": {
            "set": bool(get_env("ZVIEW_AGENT_TOKEN")),
            "source": describe_env_source("ZVIEW_AGENT_TOKEN")
                      if describe_env_source("ZVIEW_AGENT_TOKEN") != "未设置"
                      else (f"文件({agent_secret_path})" if os.path.exists(agent_secret_path) else "未设置"),
            "value": _mask(get_env("ZVIEW_AGENT_TOKEN", "")),
        },
    }

    files_section = {
        "env_files_loaded": get_env_files_loaded(),
        "auth_secret_file": {"path": secret_path, "exists": os.path.exists(secret_path)},
        "agent_token_file": {"path": agent_secret_path, "exists": os.path.exists(agent_secret_path)},
        "auth_state_file": {
            "path": get_env("ZVIEW_AUTH_STATE_FILE") or os.path.join(
                os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "auth_state.json"),
            "exists": os.path.exists(get_env("ZVIEW_AUTH_STATE_FILE") or os.path.join(
                os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "auth_state.json")),
        },
    }

    # #17 二阶段：入口审计归类（平台侧 config.json 零消费，已从配置入口除名）
    entry_audit = {
        "platform_config_entries": [
            {"entry": ".env", "kind": "平台配置", "status": "在用（9 键全部被消费）"},
            {"entry": "环境变量", "kind": "平台配置", "status": "在用（覆盖 .env）"},
            {"entry": "auth_secret.txt", "kind": "密钥文件", "status": "在用（TOKEN_SECRET 持久化，自动生成）"},
            {"entry": "代码默认值", "kind": "平台配置", "status": "在用（集中定义于 zvplatform/settings.py）"},
        ],
        "reclassified_non_platform": [
            {"entry": "auth_state.json", "kind": "运行时状态", "note": "用户账号存储（非配置），迁移入库列 #10 专项"},
            {"entry": "agent_secret.txt", "kind": "密钥文件", "note": "Agent 通道令牌持久化（自动生成）"},
            {"entry": "config.json", "kind": "Agent 配置模板", "note": "平台侧零消费；Agent frozen 包内嵌 + GPO 分发模板 + 本机 dev 运行配置"},
            {"entry": "config.local.json", "kind": "Agent 配置", "note": "Agent 安装器写入（ProgramData），平台侧零消费"},
        ],
    }

    return {"groups": groups, "secrets": secrets_section, "files": files_section,
            "entry_audit": entry_audit}


@router.get("/api/v1/console/config-report")
def get_config_report(request: Request):
    """配置来源报告（admin）：每个配置项的最终值（敏感项脱敏）与来源链。"""
    require_request_permission(getattr(request.state, "auth_user", None), request.url.path, request.method)
    return build_platform_config_report()
