# -*- coding: utf-8 -*-
"""Agent 认证与平台通道（V1.8.1 迁入自 cmdb_agent_core.py）。

职责：一机一密设备凭据（缓存/持久化/清除）、请求头组装（zv1 或全局 token）、
ca-bundle 解析、平台基地址 TLS 自动协商（8443 探测 + 24h 缓存）。
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path
from typing import Optional
from urllib.parse import urljoin, urlparse

import requests

from config_utils import get_env
from console_utils import safe_console_print
from zvagent.config import CONFIG, SOFTWARE_CONFIG

print = safe_console_print


# 一机一密（P0-01）：设备凭据缓存（agent_id + device_secret）
_AGENT_CREDENTIALS_CACHE_PATH = (
    Path(os.environ.get("ProgramData", ".")) / "CMDB-Agent" / "runtime" / "agent-credentials.json"
)


def _load_device_credentials() -> dict:
    """加载设备凭据：config.json 字段优先，其次 runtime 缓存文件"""
    creds = {
        "agent_id": CONFIG.get("agent_id"),
        "device_secret": CONFIG.get("device_secret"),
    }
    if creds.get("agent_id") and creds.get("device_secret"):
        return creds
    try:
        if _AGENT_CREDENTIALS_CACHE_PATH.exists():
            data = json.loads(_AGENT_CREDENTIALS_CACHE_PATH.read_text(encoding="utf-8"))
            creds["agent_id"] = creds.get("agent_id") or data.get("agent_id")
            creds["device_secret"] = creds.get("device_secret") or data.get("device_secret")
    except Exception:
        pass
    return creds


def _save_device_credentials(agent_id: int, device_secret: str) -> None:
    """持久化设备凭据到 runtime 目录（仅服务进程本地）"""
    try:
        _AGENT_CREDENTIALS_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        _AGENT_CREDENTIALS_CACHE_PATH.write_text(
            json.dumps({"agent_id": agent_id, "device_secret": device_secret}, ensure_ascii=False),
            encoding="utf-8",
        )
    except Exception as exc:
        print(f"[Auth] 设备凭据保存失败: {exc}")


def _device_credentials() -> dict:
    if getattr(_device_credentials, "_cache", None) is None:
        _device_credentials._cache = _load_device_credentials()  # type: ignore[attr-defined]
    return _device_credentials._cache  # type: ignore[attr-defined]



def _agent_headers() -> dict:
    # 一机一密：已注册设备凭据则用 zv1 token，否则退回全局 token
    creds = _device_credentials()
    agent_id = creds.get("agent_id")
    device_secret = creds.get("device_secret")
    if agent_id and device_secret:
        authorization = f"Bearer zv1:{int(agent_id)}:{device_secret}"
    else:
        authorization = f"Bearer {CONFIG.get('token', '')}"
    return {
        "Content-Type": "application/json",
        "Authorization": authorization,
    }


# =============================================================================
# P0-02：Agent → 平台 TLS（自动协商 + CA 校验）
# 平台 8443 端口提供 TLS 监听（证书 SAN 含平台 IP）；Agent 先试 https://host:8443，
# 成功后缓存 24h；失败回落 http://host:8080（10 分钟内不重试）。
# CA 校验：runtime/ca-bundle.pem（心跳响应下发）或安装目录 ca-bundle.pem。
# =============================================================================

_AGENT_ERROR_LOG_PATH = (
    Path(os.environ.get("ProgramData", ".")) / "CMDB-Agent" / "logs" / "agent-error.log"
)
_AGENT_TLS_CACHE_PATH = (
    Path(os.environ.get("ProgramData", ".")) / "CMDB-Agent" / "runtime" / "agent-tls.json"
)


def _ca_bundle_path() -> Optional[str]:
    candidates = []
    env_bundle = get_env("ZVIEW_AGENT_CA_BUNDLE")
    if env_bundle:
        candidates.append(env_bundle)
    try:
        if getattr(sys, "frozen", False):
            candidates.append(str(Path(sys.executable).parent / "ca-bundle.pem"))
    except Exception:
        pass
    candidates.append(str(_AGENT_TLS_CACHE_PATH.parent / "ca-bundle.pem"))
    for candidate in candidates:
        if candidate and os.path.exists(candidate):
            return candidate
    return None


def _log_agent_error(message: str) -> None:
    """P1-03/P0-06：Agent 内部失败详情落盘（stdout 在服务模式不可见）。"""
    try:
        _AGENT_ERROR_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        import datetime as _dt
        with open(_AGENT_ERROR_LOG_PATH, "a", encoding="utf-8") as fh:
            fh.write(f"[{_dt.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {message}\n")
    except Exception:
        pass


def clear_device_credentials() -> None:
    """清除本地设备凭据缓存（zv1 401 时回退全局 token）。"""
    try:
        if _AGENT_TLS_CACHE_PATH.parent.exists():
            cred = _AGENT_TLS_CACHE_PATH.parent / "agent-credentials.json"
            if cred.exists():
                cred.unlink()
    except Exception:
        pass
    try:
        _device_credentials._cache = None  # type: ignore[attr-defined]
    except Exception:
        pass


def _agent_requests_verify():
    bundle = _ca_bundle_path()
    if bundle:
        return bundle
    return False  # 证书未分发时退化为不校验（明文风险与旧版一致）


def _platform_base() -> str:
    """平台基地址：TLS 自动协商。"""
    base = str(CONFIG.get("server_url") or "").rstrip("/")
    if not base.startswith("http://"):
        return base
    parsed = urlparse(base)
    tls_base = f"https://{parsed.hostname}:8443"

    cached = {}
    try:
        if _AGENT_TLS_CACHE_PATH.exists():
            cached = json.loads(_AGENT_TLS_CACHE_PATH.read_text(encoding="utf-8"))
    except Exception:
        cached = {}
    now = time.time()
    if cached.get("tls_ok") and now - float(cached.get("ts", 0)) < 86400:
        return tls_base
    if cached.get("ts") and not cached.get("tls_ok") and now - float(cached.get("ts", 0)) < 600:
        return base

    verify = _agent_requests_verify()
    if not verify:
        print("[TLS] 警告：ca-bundle 未分发，TLS 探测将以不校验模式进行")
    try:
        requests.get(urljoin(tls_base, "/"), headers=_agent_headers(), timeout=5, verify=verify)
        ok = True
    except Exception:
        ok = False
    try:
        _AGENT_TLS_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        _AGENT_TLS_CACHE_PATH.write_text(json.dumps({"tls_ok": ok, "ts": now}), encoding="utf-8")
    except Exception:
        pass
    if ok:
        print(f"[TLS] 平台 TLS 通道已启用: {tls_base} (verify={'ca-bundle' if verify else 'OFF'})")
        return tls_base
    print("[TLS] 平台 TLS 探测失败，继续使用 http")
    return base


def _software_headers() -> dict:
    return {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {SOFTWARE_CONFIG.get('token', '')}",
    }


