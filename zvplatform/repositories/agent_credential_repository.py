# -*- coding: utf-8 -*-
"""Agent device credential repository (P1-01 migration from assets_api.py).

One-agent-one-secret device credentials. Moved here to break the circular
import between zvplatform.routers.agent_heartbeat and assets_api.
"""
from __future__ import annotations

import hashlib
import hmac
import secrets
from typing import Optional


def _hash_device_secret(asset_id: int, secret: str, token_secret: str) -> str:
    return hashlib.sha256(
        f"zv1:{asset_id}:{secret}:{token_secret}".encode("utf-8")
    ).hexdigest()


def ensure_agent_credentials_table(conn) -> None:
    """Create the agent_credentials table if it doesn't exist."""
    cursor = conn.cursor()
    try:
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS agent_credentials (
                asset_id BIGINT UNSIGNED PRIMARY KEY,
                secret_hash CHAR(64) NOT NULL,
                status ENUM('active','revoked') NOT NULL DEFAULT 'active',
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                last_used_at DATETIME NULL,
                CONSTRAINT fk_agent_credentials_asset
                    FOREIGN KEY (asset_id) REFERENCES assets(id) ON DELETE CASCADE
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """)
        conn.commit()
    finally:
        cursor.close()


def verify_agent_device_credential(
    conn,
    agent_id: int,
    secret: str,
    token_secret: str,
) -> bool:
    """Verify a device credential and update last_used_at on success."""
    if not isinstance(agent_id, int) or agent_id <= 0 or not secret:
        return False
    cursor = conn.cursor(dictionary=True)
    try:
        cursor.execute(
            "SELECT secret_hash, status, last_used_at FROM agent_credentials WHERE asset_id=%s",
            (agent_id,),
        )
        row = cursor.fetchone()
        if not row or row.get("status") != "active":
            return False
        expected = str(row.get("secret_hash") or "")
        if not hmac.compare_digest(
            _hash_device_secret(agent_id, str(secret), token_secret), expected
        ):
            return False
        cursor.execute(
            "UPDATE agent_credentials SET last_used_at=NOW() WHERE asset_id=%s",
            (agent_id,),
        )
        conn.commit()
        return True
    except Exception:
        return False
    finally:
        cursor.close()


def issue_agent_device_credential(
    cursor,
    asset_id: int,
    token_secret: str,
    *,
    allow_rotate: bool = False,
) -> Optional[dict]:
    """Issue a device credential for an asset.

    - No existing row or revoked -> issue new
    - Active credential exists -> by default do NOT re-issue (plaintext is irrecoverable)
    - allow_rotate=True -> rotate to a new secret (Agent supports persistent storage)
    """
    cursor.execute(
        "SELECT status FROM agent_credentials WHERE asset_id=%s FOR UPDATE",
        (asset_id,),
    )
    row = cursor.fetchone()
    if row and row.get("status") == "active" and not allow_rotate:
        return None
    device_secret = secrets.token_urlsafe(32)
    cursor.execute(
        """
        INSERT INTO agent_credentials (asset_id, secret_hash, status)
        VALUES (%s, %s, 'active')
        ON DUPLICATE KEY UPDATE secret_hash=VALUES(secret_hash), status='active', last_used_at=NULL
        """,
        (asset_id, _hash_device_secret(asset_id, device_secret, token_secret)),
    )
    return {"agent_id": asset_id, "device_secret": device_secret}


def agent_version_tuple(value) -> tuple:
    """Parse a version string like '1.9.51' into a comparable tuple (1, 9, 51)."""
    try:
        parts = str(value or "").strip().split(".")
        return tuple(int(p) for p in parts[:3]) if parts and parts[0] else (0, 0, 0)
    except (TypeError, ValueError):
        return (0, 0, 0)
