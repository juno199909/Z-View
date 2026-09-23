# -*- coding: utf-8 -*-
"""Agent 配置加载与合并（V1.8.1 迁入自 cmdb_agent_core.py）。

职责：默认配置、多候选配置文件解析（ProgramData 优先）、环境变量覆盖、
最终 CONFIG / SOFTWARE_CONFIG 单例。旧单体 cmdb_agent_core.py 从本模块导入。
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from config_utils import ensure_env_loaded, get_env
from console_utils import enable_utf8_stdio, safe_console_print

enable_utf8_stdio()
print = safe_console_print
ensure_env_loaded()

# =============================================================================
# 配置
# =============================================================================

_DEFAULT_CONFIG = {
    "server_url": get_env("ZVIEW_SERVER_URL", "http://127.0.0.1:8080") or "http://127.0.0.1:8080",
    "token": get_env("ZVIEW_AGENT_TOKEN", "") or "",
    "intervals": {
        "heartbeat": 30,
        "system_status": 30,
        "software": 30,
        "hardware": 86400,
    },
    "remote_desktop": {
        "require_consent": True,
        "consent_timeout_seconds": 30,
        "allow_if_no_user": False,
    },
    "control_port": int(get_env("ZVIEW_AGENT_CONTROL_PORT", "9001") or "9001"),
    "log_level": "INFO",
}

_DEFAULT_SOFTWARE_CONFIG = {
    "server_url": get_env("ZVIEW_SOFTWARE_SERVER_URL", "http://127.0.0.1:8081") or "http://127.0.0.1:8081",
    "token": get_env("ZVIEW_AGENT_TOKEN", "") or "",
    "intervals": {
        "policy_sync": 300,
        "task_poll": 30,
    },
    "download_dir": get_env("ZVIEW_DOWNLOAD_DIR", r"C:\CMDB-Agent\Downloads") or r"C:\CMDB-Agent\Downloads",
    "max_retries": 3,
}


def _load_config_from_file(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        # utf-8-sig 兼容带/不带 BOM 的文件（PowerShell 等工具重写配置会引入 BOM）
        with open(path, "r", encoding="utf-8-sig") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _merge_config(default: dict, override: dict) -> dict:
    result = dict(default)
    for key, value in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = _merge_config(result[key], value)
        else:
            result[key] = value
    return result


_CONFIG_CANDIDATES = (
    Path(__file__).resolve().parent.parent / "config.local.json",
    Path(__file__).resolve().parent.parent / "config.json",
)

if getattr(sys, "frozen", False):
    # 打包版配置解析优先级（V1.6.0 程序/数据分离）：
    # 1) ProgramData\CMDB-Agent\config\config.local.json —— 安装器写入，versions 切换不受影响
    # 2) exe 旁 config.local.json —— 旧版单文件布局兼容
    # 3) 构建时打入的 config.json
    _program_data_config = (
        Path(os.environ.get("ProgramData", ".")) / "CMDB-Agent" / "config" / "config.local.json"
    )
    _exe_dir = Path(sys.executable).resolve().parent
    _CONFIG_CANDIDATES = (
        _program_data_config,
        _exe_dir / "config.local.json",
    ) + _CONFIG_CANDIDATES


def _load_user_config() -> dict:
    for candidate in _CONFIG_CANDIDATES:
        data = _load_config_from_file(candidate)
        if data:
            return data
    return {}


_ACTIVE_CONFIG_FILE: str = ""


def _load_user_config() -> dict:
    global _ACTIVE_CONFIG_FILE
    for candidate in _CONFIG_CANDIDATES:
        data = _load_config_from_file(candidate)
        if data:
            _ACTIVE_CONFIG_FILE = str(candidate)
            return data
    return {}


def _config_source_labels(user_config: dict) -> dict:
    """key -> 来源标签（配置文件路径 / 环境变量 / 默认值），供配置报告使用。"""
    labels = {}
    active = _ACTIVE_CONFIG_FILE
    for key in user_config:
        labels[key] = f"配置文件({active})" if active else "配置文件"
    for env_key, attr in (("ZVIEW_AGENT_TOKEN", "token"), ("ZVIEW_SERVER_URL", "server_url"),
                          ("ZVIEW_AGENT_CONTROL_PORT", "control_port")):
        if get_env(env_key):
            labels[attr] = f"环境变量({env_key})"
    return labels


def build_config_report() -> dict:
    """最终生效配置（敏感项脱敏）+ 来源标签（#17 配置来源可追踪）。"""
    user_config = _load_user_config()
    labels = _config_source_labels(user_config)
    config, software_config = load_configs()

    def _mask(value):
        return "******" if value else ""

    effective = {k: (_mask(v) if k in ("token", "password") else v) for k, v in config.items()}
    effective_software = {k: (_mask(v) if k == "token" else v) for k, v in software_config.items()}
    sources = {"agent": {}, "software": {}}
    for key in effective:
        sources["agent"][key] = labels.get(key, "默认值")
    for key in effective_software:
        sources["software"][key] = labels.get(key, sources["agent"].get(key, "默认值"))
    return {
        "config_file_candidates": [str(p) for p in _CONFIG_CANDIDATES],
        "active_config_file": _ACTIVE_CONFIG_FILE or None,
        "effective": effective,
        "effective_software": effective_software,
        "sources": sources,
    }


def _apply_env_overrides(config: dict, software_config: dict) -> tuple[dict, dict]:
    merged_config = _merge_config(config, {})
    merged_software_config = _merge_config(software_config, {})

    agent_token = get_env("ZVIEW_AGENT_TOKEN")
    if agent_token:
        merged_config["token"] = agent_token
        merged_software_config["token"] = agent_token

    server_url = get_env("ZVIEW_SERVER_URL")
    if server_url:
        merged_config["server_url"] = server_url

    control_port = get_env("ZVIEW_AGENT_CONTROL_PORT")
    if control_port:
        try:
            merged_config["control_port"] = int(control_port)
        except ValueError:
            pass

    software_server_url = get_env("ZVIEW_SOFTWARE_SERVER_URL")
    if software_server_url:
        merged_software_config["server_url"] = software_server_url
    elif server_url:
        merged_software_config["server_url"] = server_url.replace(":8080", ":8081")

    download_dir = get_env("ZVIEW_DOWNLOAD_DIR")
    if download_dir:
        merged_software_config["download_dir"] = download_dir

    return merged_config, merged_software_config


def load_configs() -> tuple[dict, dict]:
    """按序组装最终配置：默认 → 用户配置文件（server_url 派生 software 端口）→ 环境变量。"""
    user_config = _load_user_config()
    config = _merge_config(_DEFAULT_CONFIG, user_config)
    software_config = _merge_config(_DEFAULT_SOFTWARE_CONFIG, user_config)

    # 统一 server_url 为顶层配置（原 core 行为：user 配置的 server_url 派生 software 端口）
    if "server_url" in user_config:
        software_config["server_url"] = user_config["server_url"].replace(":8080", ":8081")

    # P1-软件仓库修复：软件通道令牌继承顶层 Agent 令牌（config.local.json 的 token）。
    # 此前 SOFTWARE_CONFIG["token"] 仅来自 software 段/环境变量，默认为空，
    # 导致 Agent 软件任务轮询的 Authorization 为空、8081 端点级校验 401 且静默失败。
    if not software_config.get("token"):
        software_config["token"] = config.get("token") or ""

    return _apply_env_overrides(config, software_config)


CONFIG, SOFTWARE_CONFIG = load_configs()
