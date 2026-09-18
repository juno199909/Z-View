# -*- coding: utf-8 -*-
"""Asset repository (P1-01 migration from assets_api.py).

Pure data-access functions: no routing logic, no request handling.
Moved here to break the circular import between
zvplatform.routers.agent_heartbeat and assets_api.
"""
from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from zvplatform.db import format_datetime


def normalize_history_value(value: Any) -> Optional[str]:
    """Normalize a value for storage/comparison in asset_changes."""
    if value is None:
        return None
    if hasattr(value, "year") and hasattr(value, "month"):  # datetime-like
        return format_datetime(value)
    if isinstance(value, (dict, list)):
        try:
            return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
        except Exception:
            return str(value)
    if isinstance(value, bytes):
        value = value.decode("utf-8", errors="ignore")
    if isinstance(value, bool):
        return "1" if value else "0"
    text = str(value).strip()
    return text if text else None


def record_asset_change_entry(
    cursor,
    asset_id: int,
    field_name: str,
    old_value: Any,
    new_value: Any,
    *,
    change_type: str,
    source_type: str = "platform",
    operator_name: Optional[str] = None,
    details: Optional[Dict[str, Any]] = None,
) -> bool:
    """Insert a single asset_change row. Returns True if a row was inserted."""
    normalized_old = normalize_history_value(old_value)
    normalized_new = normalize_history_value(new_value)
    if normalized_old == normalized_new:
        return False

    details_json = None
    if details is not None:
        try:
            details_json = json.dumps(details, ensure_ascii=False, default=str)
        except Exception:
            details_json = json.dumps({"value": str(details)}, ensure_ascii=False)

    cursor.execute(
        """
        INSERT INTO asset_changes (
            asset_id, change_type, field_name, old_value, new_value,
            source_type, operator_name, details_json, created_at
        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, NOW())
        """,
        (
            asset_id,
            change_type,
            field_name,
            normalized_old,
            normalized_new,
            source_type,
            operator_name,
            details_json,
        ),
    )
    return True


def record_asset_changes(
    cursor,
    asset_id: int,
    before_row: Optional[Dict[str, Any]],
    after_row: Optional[Dict[str, Any]],
    *,
    field_names: List[str],
    change_type: str,
    source_type: str = "platform",
    operator_name: Optional[str] = None,
    details: Optional[Dict[str, Any]] = None,
) -> int:
    """Compare before_row and after_row on field_names, insert change rows.
    Returns the number of change entries inserted.
    """
    if not after_row:
        return 0

    affected = 0
    before_row = before_row or {}
    for field_name in field_names:
        if field_name not in after_row:
            continue
        if record_asset_change_entry(
            cursor,
            asset_id,
            field_name,
            before_row.get(field_name),
            after_row.get(field_name),
            change_type=change_type,
            source_type=source_type,
            operator_name=operator_name,
            details=details,
        ):
            affected += 1
    return affected


def fetch_asset_row(
    cursor,
    asset_id: int,
    *,
    include_deleted: bool = False,
) -> Optional[Dict[str, Any]]:
    """Fetch a single asset row by id.

    Uses SELECT * so callers get the full row; callers filter fields as needed.
    """
    where_clause = "id = %s"
    if not include_deleted:
        where_clause += " AND deleted_at IS NULL"
    cursor.execute(
        f"""
        SELECT *
        FROM assets
        WHERE {where_clause}
        LIMIT 1
        """,
        (asset_id,),
    )
    return cursor.fetchone()

from datetime import datetime
from typing import Any, Dict, List, Optional


def resolve_asset_online_status(
    asset: Dict[str, Any],
    *,
    online_seconds: int = 90,
    offline_seconds: int = 180,
) -> str:
    """Resolve online/offline status from last_seen timestamp.

    Uses online_seconds as the threshold. Falls back to the stored
    status field if last_seen is not a datetime.
    """
    last_seen = asset.get("last_seen")
    if isinstance(last_seen, datetime):
        age_seconds = (datetime.now() - last_seen).total_seconds()
        return "online" if age_seconds <= online_seconds else "offline"

    status = str(asset.get("status") or "").strip().lower()
    return status or "unknown"


def get_asset_agent_target(cursor, asset_id: int) -> Dict[str, Any]:
    """Fetch minimal asset info for Agent targeting (id, hostname, ip, status, ...)."""
    cursor.execute(
        """
        SELECT id, hostname, ip_address, status, agent_install_status, last_seen
        FROM assets
        WHERE id = %s AND deleted_at IS NULL
        LIMIT 1
        """,
        (asset_id,),
    )
    asset = cursor.fetchone()
    if not asset:
        raise ValueError(f"Asset {asset_id} not found")
    asset["resolved_status"] = resolve_asset_online_status(asset)
    return asset


def format_duration_text(total_seconds: Optional[int]) -> str:
    """Format seconds as 'Xh Ym Zs'."""
    try:
        seconds = max(0, int(total_seconds or 0))
    except (TypeError, ValueError):
        return "-"
    hours, remainder = divmod(seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    return f"{hours}h {minutes}m {seconds}s"


def estimate_online_seconds_from_heartbeats(
    heartbeat_rows: List[Dict[str, Any]],
    *,
    window_end: Optional[datetime] = None,
    max_gap_seconds: int = 180,
) -> int:
    """Estimate total online time from heartbeat samples.

    Gaps larger than max_gap_seconds are treated as offline periods.
    """
    heartbeat_times = [
        row.get("heartbeat_time")
        for row in heartbeat_rows
        if isinstance(row.get("heartbeat_time"), datetime)
    ]
    if not heartbeat_times:
        return 0

    sorted_times = sorted(heartbeat_times)
    online_seconds = 0
    for index, current_time in enumerate(sorted_times):
        next_time = (
            sorted_times[index + 1]
            if index + 1 < len(sorted_times)
            else (window_end or datetime.now())
        )
        delta_seconds = max(0, int((next_time - current_time).total_seconds()))
        online_seconds += min(delta_seconds, max_gap_seconds)
    return online_seconds


def get_asset_uptime_seconds(
    asset_row: Dict[str, Any],
    heartbeat_row: Optional[Dict[str, Any]] = None,
    *,
    online_seconds: int = 90,
) -> int:
    """Estimate uptime for an asset based on last_seen and current status."""
    if not asset_row:
        return 0

    last_seen = asset_row.get("last_seen")
    if not isinstance(last_seen, datetime):
        return 0

    resolved_status = resolve_asset_online_status(asset_row, online_seconds=online_seconds)
    if resolved_status != "online":
        return 0

    try:
        return max(0, int((datetime.now() - last_seen).total_seconds()))
    except Exception:
        return 0
