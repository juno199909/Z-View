# -*- coding: utf-8 -*-
"""认证与用户管理路由（1.9.55 自 assets_api 迁入 #16 模块化，逻辑逐字保留）。

- POST /api/v1/auth/login（免鉴权豁免）、GET /api/v1/auth/me、POST /api/v1/auth/change-password
- 用户管理（admin）：/api/v1/auth/users CRUD + 重置密码；权限由中间件按 path 强制（auth:manage）
- 登录成功/失败均写审计（record_system_activity_log 由 assets_api 注入）
"""
from typing import List, Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from auth_utils import (
    authenticate_username_password,
    issue_access_token,
    get_auth_profile,
    change_password,
    get_request_username,
    list_users,
    create_user,
    update_user_role,
    set_user_scoped_groups,
    set_user_enabled,
    admin_reset_password,
    delete_user,
)
from console_utils import safe_console_print
from zvplatform.common import get_request_client_ip
from zvplatform.obs import format_log_line
from zvplatform.models import SystemActivityLogCreate

router = APIRouter()

_injected = {}


def bind_helpers(**kwargs):
    """assets_api 尾部注入共享助手（record_system_activity_log）。"""
    _injected.update(kwargs)
    globals().update(kwargs)


class LoginRequest(BaseModel):
    username: str
    password: str


class ChangePasswordRequest(BaseModel):
    current_password: str = Field(..., min_length=1)
    new_password: str = Field(..., min_length=6)





@router.post("/api/v1/auth/login")
def login(payload: LoginRequest, request: Request):
    auth_user = authenticate_username_password(payload.username, payload.password)
    if not auth_user:
        record_system_activity_log(SystemActivityLogCreate(
            source_type="platform",
            module="auth",
            category="authentication",
            action="login",
            level="warning",
            result="failed",
            ip_address=get_request_client_ip(request),
            operator_name=payload.username,
            title="平台登录失败",
            message=f"用户 {payload.username} 登录失败：用户名或密码错误",
            details={
                "username": payload.username,
                "reason": "invalid_credentials",
                "user_agent": request.headers.get("user-agent"),
            },
        ))
        raise HTTPException(status_code=401, detail="用户名或密码错误")

    token_payload = issue_access_token(auth_user["username"])
    safe_console_print(format_log_line("info", f"user {auth_user['username']} logged in"))
    record_system_activity_log(SystemActivityLogCreate(
        source_type="platform",
        module="auth",
        category="authentication",
        action="login",
        level="info",
        result="success",
        ip_address=get_request_client_ip(request),
        operator_name=auth_user["username"],
        title="平台登录成功",
        message=f"用户 {auth_user['username']} 登录平台成功",
        details={
            "username": auth_user["username"],
            "credential_source": auth_user.get("credential_source") or "file",
            "must_change_password": bool(auth_user.get("must_change_password")),
            "issued_at": token_payload.get("issued_at"),
            "expires_at": token_payload.get("expires_at"),
            "user_agent": request.headers.get("user-agent"),
        },
    ))
    return token_payload


@router.get("/api/v1/auth/me")
def get_current_user(request: Request):
    auth_user = getattr(request.state, "auth_user", None)
    if not auth_user:
        raise HTTPException(status_code=401, detail="Unauthorized")
    profile = get_auth_profile(auth_user.get("username"))
    return {
        **auth_user,
        "password_updated_at": profile.get("password_updated_at"),
        "credential_source": profile.get("credential_source") or "file",
        "must_change_password": bool(profile.get("must_change_password")),
    }


@router.post("/api/v1/auth/change-password")
def change_current_user_password(payload: ChangePasswordRequest, request: Request):
    auth_user = getattr(request.state, "auth_user", None)
    if not auth_user:
        raise HTTPException(status_code=401, detail="Unauthorized")

    try:
        result = change_password(
            auth_user.get("username", ""),
            payload.current_password,
            payload.new_password,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return {
        "message": "密码修改成功，请重新登录",
        "username": result.get("username"),
        "password_updated_at": result.get("password_updated_at"),
    }


# ============================================================
# 用户管理（仅 admin：/api/v1/auth/users → auth:manage 权限）
# ============================================================

class CreateUserRequest(BaseModel):
    username: str
    password: str
    role: str = "viewer"
    scoped_group_ids: Optional[List[int]] = None


class UpdateUserRequest(BaseModel):
    role: Optional[str] = None
    enabled: Optional[bool] = None
    scoped_group_ids: Optional[List[int]] = None


class ResetUserPasswordRequest(BaseModel):
    new_password: str


def _log_user_management(action: str, result: str, level: str, message: str,
                          target_user: str, operator: str, request: Request, details=None):
    record_system_activity_log(SystemActivityLogCreate(
        source_type="platform",
        module="auth",
        category="user_management",
        action=action,
        level=level,
        result=result,
        ip_address=get_request_client_ip(request),
        operator_name=operator,
        title=f"用户管理-{action}",
        message=message,
        details={"target_user": target_user, "operator": operator, **(details or {})},
    ))


@router.get("/api/v1/auth/users")
def list_platform_users(request: Request):
    auth_user = getattr(request.state, "auth_user", None)
    if not auth_user:
        raise HTTPException(status_code=401, detail="Unauthorized")
    return list_users()


@router.post("/api/v1/auth/users")
def create_platform_user(payload: CreateUserRequest, request: Request):
    auth_user = getattr(request.state, "auth_user", None)
    if not auth_user:
        raise HTTPException(status_code=401, detail="Unauthorized")
    operator = get_request_username(request)

    try:
        profile = create_user(payload.username, payload.password, payload.role, operator,
                              scoped_group_ids=payload.scoped_group_ids)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    _log_user_management(
        "create_user", "success", "info",
        f"管理员 {operator} 创建用户 {profile['username']}（角色 {profile['role']}）",
        profile["username"], operator, request,
        {"role": profile["role"], "scoped_group_ids": profile.get("scoped_group_ids") or []},
    )
    return {"message": "用户创建成功", "user": profile}


@router.put("/api/v1/auth/users/{username}")
def update_platform_user(username: str, payload: UpdateUserRequest, request: Request):
    auth_user = getattr(request.state, "auth_user", None)
    if not auth_user:
        raise HTTPException(status_code=401, detail="Unauthorized")
    operator = get_request_username(request)

    try:
        if payload.scoped_group_ids is not None:
            profile = set_user_scoped_groups(username, payload.scoped_group_ids, operator)
            _log_user_management(
                "update_scope", "success", "info",
                f"管理员 {operator} 更新用户 {username} 的资产分组范围 "
                f"（{len(payload.scoped_group_ids)} 个分组，空=不限制）",
                username, operator, request,
                {"scoped_group_ids": payload.scoped_group_ids},
            )
        if payload.role is not None:
            profile = update_user_role(username, payload.role, operator)
            _log_user_management(
                "update_role", "success", "info",
                f"管理员 {operator} 将用户 {username} 角色变更为 {profile['role']}",
                username, operator, request, {"role": profile["role"]},
            )
        if payload.enabled is not None:
            profile = set_user_enabled(username, payload.enabled, operator)
            action = "enable_user" if payload.enabled else "disable_user"
            _log_user_management(
                action, "success", "info",
                f"管理员 {operator} {'启用' if payload.enabled else '停用'}用户 {username}",
                username, operator, request, {"enabled": payload.enabled},
            )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    return {"message": "用户更新成功", "user": profile}


@router.put("/api/v1/auth/users/{username}/reset-password")
def reset_platform_user_password(username: str, payload: ResetUserPasswordRequest, request: Request):
    auth_user = getattr(request.state, "auth_user", None)
    if not auth_user:
        raise HTTPException(status_code=401, detail="Unauthorized")
    operator = get_request_username(request)

    try:
        profile = admin_reset_password(username, payload.new_password, operator)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    _log_user_management(
        "reset_password", "success", "warning",
        f"管理员 {operator} 重置了用户 {username} 的登录密码",
        username, operator, request,
    )
    return {"message": "密码已重置，该用户原登录令牌已全部失效", "user": profile}


@router.delete("/api/v1/auth/users/{username}")
def delete_platform_user(username: str, request: Request):
    auth_user = getattr(request.state, "auth_user", None)
    if not auth_user:
        raise HTTPException(status_code=401, detail="Unauthorized")
    operator = get_request_username(request)

    try:
        result = delete_user(username, operator)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    _log_user_management(
        "delete_user", "success", "warning",
        f"管理员 {operator} 删除了用户 {username}",
        username, operator, request,
    )
    return {"message": "用户已删除", **result}

