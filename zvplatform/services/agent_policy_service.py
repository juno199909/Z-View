# -*- coding: utf-8 -*-
"""Agent 策略下发服务（P1-01 从 assets_api 迁出）。"""
from __future__ import annotations

import json

from fastapi import HTTPException, Request

from config_utils import get_env
from console_utils import safe_console_print
from auth_utils import get_request_username, require_agent_request
from zvplatform.db import create_connection


AGENT_POLICIES_DEFAULT = {
    "intervals": {
        "heartbeat": 30,
        "software": 120,
        "hardware": 86400,
    },
    "remote_desktop": {
        "require_consent": True,
        "consent_timeout_seconds": 90,
        "allow_if_no_user": False,
        "disable_uac_secure_desktop": True,
        "allow_shell": False,
        "shell_timeout_seconds": 60,
    },
}

AGENT_POLICY_INTERVAL_BOUNDS = {
    "heartbeat": (5, 3600),
    "software": (10, 86400),
    "hardware": (300, 604800),
}


def ensure_agent_policies_table(conn):
    cursor = conn.cursor(dictionary=True)
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS agent_policies (
            id INT PRIMARY KEY,
            policies_json LONGTEXT NOT NULL,
            updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """
    )
    cursor.execute("SELECT COUNT(*) AS total FROM agent_policies WHERE id = 1")
    if int((cursor.fetchone() or {}).get("total") or 0) == 0:
        cursor.execute(
            "INSERT INTO agent_policies (id, policies_json) VALUES (1, %s)",
            (json.dumps(AGENT_POLICIES_DEFAULT, ensure_ascii=False),),
        )
    conn.commit()


def load_agent_policies() -> dict:
    """读取当前策略；缺表/缺行/坏JSON时回退默认值。"""
    conn = create_connection()
    if not conn:
        return json.loads(json.dumps(AGENT_POLICIES_DEFAULT))
    try:
        ensure_agent_policies_table(conn)
        cursor = conn.cursor(dictionary=True)
        cursor.execute("SELECT policies_json FROM agent_policies WHERE id = 1")
        row = cursor.fetchone()
        merged = json.loads(json.dumps(AGENT_POLICIES_DEFAULT))
        if not row:
            return merged
        try:
            stored = json.loads(row.get("policies_json") or "{}")
        except (TypeError, ValueError):
            return merged
        if not isinstance(stored, dict):
            return merged
        for section, values in stored.items():
            if values is None:
                continue
            if isinstance(values, dict) and isinstance(merged.get(section), dict):
                for key, value in values.items():
                    if value is not None:
                        merged[section][key] = value
            else:
                merged[section] = values
        return merged
    except Exception:
        return json.loads(json.dumps(AGENT_POLICIES_DEFAULT))
    finally:
        try:
            conn.close()
        except Exception:
            pass


def load_agent_policies_with_meta() -> dict:
    policies = load_agent_policies()
    updated_at = None
    conn = create_connection()
    if conn:
        try:
            ensure_agent_policies_table(conn)
            cursor = conn.cursor(dictionary=True)
            cursor.execute("SELECT updated_at FROM agent_policies WHERE id = 1")
            row = cursor.fetchone()
            updated_at = str(row.get("updated_at")) if row and row.get("updated_at") else None
        except Exception:
            updated_at = None
        finally:
            try:
                conn.close()
            except Exception:
                pass
    return {"policies": policies, "updated_at": updated_at}

def normalize_agent_policy_config(payload: dict) -> tuple[dict, list]:
    """校验 agent 配置片段（intervals/remote_desktop），返回 (clean, errors)。

    clean 只含合法字段（分节），供统一策略引擎与控制台更新共用，
    保证两种来源的 agent 策略格式与校验完全一致。
    """
    errors = []
    clean: dict = {}

    intervals_in = (payload or {}).get("intervals")
    if intervals_in is not None:
        if not isinstance(intervals_in, dict):
            errors.append("intervals must be an object")
        else:
            clean_intervals: dict = {}
            for key, (low, high) in AGENT_POLICY_INTERVAL_BOUNDS.items():
                if key not in intervals_in or intervals_in.get(key) is None:
                    continue
                raw = intervals_in.get(key)
                if isinstance(raw, bool):
                    errors.append(f"intervals.{key} must be an integer")
                    continue
                try:
                    value = int(raw)
                except (TypeError, ValueError):
                    errors.append(f"intervals.{key} must be an integer")
                    continue
                if value < low or value > high:
                    errors.append(
                        f"intervals.{key} must be between {low} and {high} seconds"
                    )
                    continue
                clean_intervals[key] = value
            if clean_intervals:
                clean["intervals"] = clean_intervals

    remote_in = (payload or {}).get("remote_desktop")
    if remote_in is not None:
        if not isinstance(remote_in, dict):
            errors.append("remote_desktop must be an object")
        else:
            remote_clean: dict = {}
            for flag in ("require_consent", "allow_if_no_user", "disable_uac_secure_desktop", "allow_shell"):
                if flag not in remote_in or remote_in.get(flag) is None:
                    continue
                value = remote_in.get(flag)
                if isinstance(value, bool):
                    remote_clean[flag] = value
                elif str(value).strip().lower() in ("true", "1", "yes"):
                    remote_clean[flag] = True
                elif str(value).strip().lower() in ("false", "0", "no"):
                    remote_clean[flag] = False
                else:
                    errors.append(f"remote_desktop.{flag} must be a boolean")

            if "consent_timeout_seconds" in remote_in and remote_in.get("consent_timeout_seconds") is not None:
                raw = remote_in.get("consent_timeout_seconds")
                if isinstance(raw, bool):
                    errors.append("remote_desktop.consent_timeout_seconds must be an integer")
                else:
                    try:
                        value = int(raw)
                    except (TypeError, ValueError):
                        errors.append("remote_desktop.consent_timeout_seconds must be an integer")
                    else:
                        if 5 <= value <= 3600:
                            remote_clean["consent_timeout_seconds"] = value
                        else:
                            errors.append(
                                "remote_desktop.consent_timeout_seconds must be between 5 and 3600 seconds"
                            )

            if "shell_timeout_seconds" in remote_in and remote_in.get("shell_timeout_seconds") is not None:
                raw = remote_in.get("shell_timeout_seconds")
                if isinstance(raw, bool):
                    errors.append("remote_desktop.shell_timeout_seconds must be an integer")
                else:
                    try:
                        value = int(raw)
                    except (TypeError, ValueError):
                        errors.append("remote_desktop.shell_timeout_seconds must be an integer")
                    else:
                        if 5 <= value <= 600:
                            remote_clean["shell_timeout_seconds"] = value
                        else:
                            errors.append(
                                "remote_desktop.shell_timeout_seconds must be between 5 and 600 seconds"
                            )
            if remote_clean:
                clean["remote_desktop"] = remote_clean

    unknown_sections = set((payload or {}).keys()) - {"intervals", "remote_desktop"}
    if unknown_sections:
        errors.append(f"unknown policy sections: {', '.join(sorted(unknown_sections))}")
    return clean, errors


def normalize_agent_policies(payload: dict) -> tuple[dict, list]:
    """在当前策略基础上合并校验合法字段，返回 (新策略, 错误列表)。"""
    result = load_agent_policies()
    clean, errors = normalize_agent_policy_config(payload)
    for section, values in clean.items():
        if isinstance(values, dict) and isinstance(result.get(section), dict):
            result[section] = {**result[section], **values}
        else:
            result[section] = values
    return result, errors
