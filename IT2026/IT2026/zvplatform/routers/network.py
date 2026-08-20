# -*- coding: utf-8 -*-
"""终端网络状态 API（第一阶段：实时状态 + 历史趋势）。"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, HTTPException, Query

from mysql.connector import Error

from zvplatform.db import create_connection
from zvplatform.services.network_service import (
    get_network_history,
    get_network_live_state,
)


router = APIRouter(tags=["network"])

RANGE_SECONDS = {"5m": 300, "15m": 900, "1h": 3600, "6h": 21600, "24h": 86400}


@router.get("/api/v1/assets/{asset_id}/network")
def get_asset_network(asset_id: int):
    """终端实时网络状态（网卡信息 + 实时速率 + 网络质量）"""
    conn = create_connection()
    if not conn:
        raise HTTPException(status_code=500, detail="Database connection failed")
    try:
        state = get_network_live_state(conn, asset_id)
        if state is None:
            raise HTTPException(status_code=404, detail="Network data not found for this asset")
        return state
    except Error as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        conn.close()


@router.get("/api/v1/assets/{asset_id}/network/history")
def get_asset_network_history(
    asset_id: int,
    range: Optional[str] = Query(default="15m", description="5m/15m/1h/6h/24h"),
):
    """终端网络流量/质量历史趋势（1 分钟粒度）"""
    range_seconds = RANGE_SECONDS.get(str(range or "15m").lower())
    if range_seconds is None:
        raise HTTPException(status_code=422, detail="range must be one of: 5m, 15m, 1h, 6h, 24h")
    conn = create_connection()
    if not conn:
        raise HTTPException(status_code=500, detail="Database connection failed")
    try:
        return get_network_history(conn, asset_id, range_seconds)
    except Error as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        conn.close()
