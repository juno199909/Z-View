# -*- coding: utf-8 -*-
"""平台数据模型（P1-01）。"""
from typing import Any, List, Optional, Dict

from datetime import datetime

from pydantic import BaseModel, Field


class SystemActivityLogCreate(BaseModel):
    source_type: str = "agent"
    module: str
    category: Optional[str] = None
    action: str
    level: str = "info"
    result: Optional[str] = None
    asset_id: Optional[int] = None
    hostname: Optional[str] = None
    ip_address: Optional[str] = None
    operator_name: Optional[str] = None
    session_id: Optional[str] = None
    title: Optional[str] = None
    message: str
    event_time: Optional[datetime] = None
    details: Optional[Any] = None
    stdout_log: Optional[str] = None
    stderr_log: Optional[str] = None



class BatchExecuteRequest(BaseModel):
    operation_type: str
    terminal_ids: List[int]
    parameters: Dict[str, Any] = Field(default_factory=dict)
    operator_name: Optional[str] = "console"


class DiscoveryPingRequest(BaseModel):
    ip_ranges: List[str] = Field(default_factory=list)
    concurrency: int = Field(default=100, ge=1, le=1000)
    timeout: int = Field(default=3000, ge=500, le=10000)


class DiscoveryImportRequest(BaseModel):
    ip_address: str
    hostname: Optional[str] = None
    mac_address: Optional[str] = None
    vendor: Optional[str] = None
    device_type: Optional[str] = None


class DiscoverySNMPTarget(BaseModel):
    ip: str
    community: str = "public"


class DiscoverySNMPRequest(BaseModel):
    targets: List[DiscoverySNMPTarget] = Field(default_factory=list)
    version: int = Field(default=2)
    timeout: int = Field(default=5, ge=1, le=30)



