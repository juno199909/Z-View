import base64
import hashlib
import hmac
import json
import os
import re
import secrets
import threading
import time
from fnmatch import fnmatch
from typing import Any, Dict, Iterable, Optional

from fastapi import HTTPException, Request

from config_utils import get_env, get_or_create_secret


DEFAULT_ADMIN_USERNAME = get_env("ZVIEW_ADMIN_USERNAME", "admin") or "admin"
DEFAULT_ADMIN_ROLE = get_env("ZVIEW_ADMIN_ROLE", "admin") or "admin"
CONFIGURED_ADMIN_PASSWORD = get_env("ZVIEW_ADMIN_PASSWORD")
TOKEN_SECRET = get_or_create_secret(
    "ZVIEW_AUTH_SECRET",
    "ZVIEW_AUTH_SECRET_FILE",
    "auth_secret.txt",
)
TOKEN_TTL_SECONDS = int(get_env("ZVIEW_AUTH_TTL_SECONDS", "43200") or "43200")
AUTH_STATE_FILE = get_env(
    "ZVIEW_AUTH_STATE_FILE",
    os.path.join(os.path.dirname(__file__), "auth_state.json"),
) or os.path.join(os.path.dirname(__file__), "auth_state.json")
PASSWORD_HASH_ITERATIONS = int(get_env("ZVIEW_PASSWORD_HASH_ITERATIONS", "120000") or "120000")
AUTH_STATE_LOCK = threading.Lock()
AGENT_TOKEN_ENV_NAME = "ZVIEW_AGENT_TOKEN"
AGENT_TOKEN_FILE_ENV_NAME = "ZVIEW_AGENT_TOKEN_FILE"
AGENT_TOKEN_FILE_NAME = "agent_secret.txt"
LEGACY_AGENT_TOKEN = "cmdb-agent-secret-2024"
WEAK_PASSWORD_PATTERNS = (
    "123456",
    "123123",
    "abc123",
    "admin123",
    "password",
    "qwerty",
    "000000",
)

ROLE_PERMISSIONS = {
    "admin": ("*",),
    "operator": (
        "auth:self",
        "assets:read",
        "assets:write",
        "groups:read",
        "groups:write",
        "software:read",
        "software:write",
        "alerts:read",
        "alerts:write",
        "logs:read",
        "automation:execute",
        "remote_desktop:control",
        "policies:read",
        "policies:write",
    ),
    "viewer": (
        "auth:self",
        "assets:read",
        "groups:read",
        "software:read",
        "alerts:read",
        "logs:read",
        "policies:read",
    ),
}

WRITE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}

DEFAULT_AUTH_EXEMPTIONS = (
    {"path": "/"},
    {"path": "/favicon.ico"},
    {"path": "/openapi.json"},
    {"prefix": "/docs"},
    {"prefix": "/redoc"},
)


def _b64url_encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("utf-8").rstrip("=")


def _b64url_decode(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode(f"{value}{padding}")


def _sign_payload(payload_segment: str) -> str:
    signature = hmac.new(
        TOKEN_SECRET.encode("utf-8"),
        payload_segment.encode("utf-8"),
        hashlib.sha256,
    ).digest()
    return _b64url_encode(signature)


def normalize_role(role: Optional[str]) -> str:
    """规范化角色名，非法配置按只读角色处理，避免误放权。"""
    normalized = str(role or "").strip().lower()
    if normalized in ROLE_PERMISSIONS:
        return normalized
    return "viewer"


def get_role_permissions(role: Optional[str]) -> list[str]:
    """返回角色权限列表，调用方只读使用，避免修改全局权限模板。"""
    return list(ROLE_PERMISSIONS.get(normalize_role(role), ROLE_PERMISSIONS["viewer"]))


def user_has_permission(user: Optional[Dict[str, Any]], permission: str) -> bool:
    """判断用户是否具备指定权限，管理员通配符保持向后兼容。"""
    if not permission:
        return True

    if not user:
        return False

    permissions = set(user.get("permissions") or get_role_permissions(user.get("role")))
    return "*" in permissions or permission in permissions


def resolve_required_permission(path: str, method: str) -> str:
    """根据请求路径推导权限点，保持中间件简单且便于后续扩展。"""
    normalized_path = str(path or "/")
    normalized_method = str(method or "GET").upper()
    is_write = normalized_method in WRITE_METHODS

    if normalized_path.startswith("/api/v1/auth/"):
        return "auth:self"
    if normalized_path.startswith("/api/v1/assets/") and normalized_path.endswith("/remote-control"):
        return "remote_desktop:control"
    if normalized_path.startswith("/api/v1/assets/") and normalized_path.endswith("/command"):
        return "automation:execute"
    if normalized_path.startswith("/api/v1/batch/execute"):
        return "automation:execute"
    if normalized_path.startswith("/api/v1/discovery/"):
        return "automation:execute" if is_write else "assets:read"
    if normalized_path.startswith("/api/v1/assets"):
        return "assets:write" if is_write else "assets:read"
    if normalized_path.startswith("/api/v1/groups"):
        return "groups:write" if is_write else "groups:read"
    if normalized_path.startswith("/api/v1/software"):
        return "software:write" if is_write else "software:read"
    if normalized_path.startswith("/api/v1/packages"):
        return "software:write" if is_write else "software:read"
    if normalized_path.startswith("/api/v1/policies"):
        return "policies:write" if is_write else "policies:read"
    if normalized_path.startswith("/api/v1/alerts"):
        return "alerts:write" if is_write else "alerts:read"
    if normalized_path.startswith("/api/v1/logs"):
        return "logs:write" if is_write else "logs:read"
    return "auth:self"


def require_request_permission(user: Optional[Dict[str, Any]], path: str, method: str) -> None:
    """统一请求权限校验，失败时返回 403 而不是继续执行业务逻辑。"""
    permission = resolve_required_permission(path, method)
    if not user_has_permission(user, permission):
        raise HTTPException(
            status_code=403,
            detail=f"Forbidden: missing permission {permission}",
        )


def _hash_password(password: str, salt: Optional[str] = None) -> str:
    normalized_password = str(password or "")
    salt = salt or secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256",
        normalized_password.encode("utf-8"),
        salt.encode("utf-8"),
        PASSWORD_HASH_ITERATIONS,
    )
    return f"pbkdf2_sha256${PASSWORD_HASH_ITERATIONS}${salt}${_b64url_encode(digest)}"


def _verify_password(password: str, stored_hash: str) -> bool:
    try:
        algorithm, iterations, salt, digest = str(stored_hash or "").split("$", 3)
        if algorithm != "pbkdf2_sha256":
            return False
        expected = hashlib.pbkdf2_hmac(
            "sha256",
            str(password or "").encode("utf-8"),
            salt.encode("utf-8"),
            int(iterations),
        )
        return hmac.compare_digest(_b64url_encode(expected), digest)
    except (TypeError, ValueError):
        return False


def _default_auth_state(password: Optional[str] = None) -> Dict[str, Any]:
    password = password or CONFIGURED_ADMIN_PASSWORD or secrets.token_urlsafe(18)
    credential_source = "env" if CONFIGURED_ADMIN_PASSWORD else "bootstrap"
    return {
        "username": DEFAULT_ADMIN_USERNAME,
        "role": normalize_role(DEFAULT_ADMIN_ROLE),
        "password_hash": _hash_password(password),
        "token_version": 1,
        "password_updated_at": None,
        "credential_source": credential_source,
    }


def _bootstrap_auth_state() -> Dict[str, Any]:
    bootstrap_password = CONFIGURED_ADMIN_PASSWORD or secrets.token_urlsafe(18)
    state = _default_auth_state(password=bootstrap_password)
    if not CONFIGURED_ADMIN_PASSWORD:
        print(
            f"[auth] Generated bootstrap admin password for '{state['username']}': "
            f"{bootstrap_password}. Please change it after first login."
        )
    try:
        return _save_auth_state(state)
    except OSError:
        return state


def _load_auth_state() -> Dict[str, Any]:
    if not os.path.exists(AUTH_STATE_FILE):
        return _bootstrap_auth_state()

    try:
        with open(AUTH_STATE_FILE, "r", encoding="utf-8") as fp:
            state = json.load(fp)
    except (OSError, ValueError, json.JSONDecodeError):
        return _bootstrap_auth_state()

    default_state = {
        "username": DEFAULT_ADMIN_USERNAME,
        "role": normalize_role(DEFAULT_ADMIN_ROLE),
        "password_hash": _hash_password(CONFIGURED_ADMIN_PASSWORD) if CONFIGURED_ADMIN_PASSWORD else "",
        "token_version": 1,
        "password_updated_at": None,
        "credential_source": "file",
    }
    merged = {
        "username": str(state.get("username") or default_state["username"]).strip() or default_state["username"],
        "role": normalize_role(state.get("role") or default_state["role"]),
        "password_hash": str(state.get("password_hash") or default_state["password_hash"]),
        "token_version": max(1, int(state.get("token_version") or default_state["token_version"])),
        "password_updated_at": state.get("password_updated_at"),
        "credential_source": str(state.get("credential_source") or "file"),
    }
    if not merged["password_hash"]:
        return _bootstrap_auth_state()
    return merged


def _save_auth_state(state: Dict[str, Any]) -> Dict[str, Any]:
    normalized = {
        "username": str(state.get("username") or DEFAULT_ADMIN_USERNAME).strip() or DEFAULT_ADMIN_USERNAME,
        "role": normalize_role(state.get("role") or DEFAULT_ADMIN_ROLE),
        "password_hash": str(state.get("password_hash") or ""),
        "token_version": max(1, int(state.get("token_version") or 1)),
        "password_updated_at": state.get("password_updated_at"),
        "credential_source": str(state.get("credential_source") or "file"),
    }

    os.makedirs(os.path.dirname(AUTH_STATE_FILE) or ".", exist_ok=True)
    temp_path = f"{AUTH_STATE_FILE}.tmp"
    with open(temp_path, "w", encoding="utf-8") as fp:
        json.dump(normalized, fp, ensure_ascii=False, indent=2)
    os.replace(temp_path, AUTH_STATE_FILE)
    return normalized


def get_auth_profile(username: Optional[str] = None) -> Dict[str, Any]:
    with AUTH_STATE_LOCK:
        state = _load_auth_state()

    requested_username = str(username or state["username"]).strip() or state["username"]
    if requested_username != state["username"]:
        return {}

    profile = {
        "username": state["username"],
        "role": normalize_role(state.get("role")),
        "token_version": state["token_version"],
        "password_updated_at": state.get("password_updated_at"),
        "credential_source": state.get("credential_source") or "file",
    }
    profile["permissions"] = get_role_permissions(profile["role"])
    profile["must_change_password"] = compute_must_change_password(profile)
    return profile


def compute_must_change_password(profile: Optional[Dict[str, Any]]) -> bool:
    profile = profile or {}
    credential_source = str(profile.get("credential_source") or "file").strip().lower()
    password_updated_at = profile.get("password_updated_at")
    return credential_source in {"default", "env", "bootstrap"} and not password_updated_at


def validate_password_strength(username: str, password: str) -> None:
    normalized_password = str(password or "")
    normalized_username = str(username or "").strip().lower()
    normalized_password_lower = normalized_password.lower()

    if len(normalized_password) < 8:
        raise ValueError("新密码长度不能少于 8 位")
    if normalized_username and normalized_username in normalized_password_lower:
        raise ValueError("新密码不能包含账号名")
    if not re.search(r"[A-Za-z]", normalized_password):
        raise ValueError("新密码至少包含 1 个字母")
    if not re.search(r"\d", normalized_password):
        raise ValueError("新密码至少包含 1 个数字")

    has_lower = bool(re.search(r"[a-z]", normalized_password))
    has_upper = bool(re.search(r"[A-Z]", normalized_password))
    has_special = bool(re.search(r"[^A-Za-z0-9]", normalized_password))
    if not ((has_lower and has_upper) or has_special):
        raise ValueError("新密码需同时包含大小写字母，或至少包含 1 个特殊字符")

    for weak_pattern in WEAK_PASSWORD_PATTERNS:
        if weak_pattern in normalized_password_lower:
            raise ValueError("新密码过于简单，请避免常见弱口令组合")


def authenticate_username_password(username: str, password: str) -> Optional[Dict[str, Any]]:
    normalized_username = str(username or "").strip()
    normalized_password = str(password or "")

    if not normalized_username or not normalized_password:
        return None

    with AUTH_STATE_LOCK:
        state = _load_auth_state()

    username_ok = hmac.compare_digest(normalized_username, state["username"])
    password_ok = _verify_password(normalized_password, state["password_hash"])

    if not username_ok or not password_ok:
        return None

    return {
        "username": state["username"],
        "role": normalize_role(state.get("role")),
        "permissions": get_role_permissions(state.get("role")),
        "token_version": state["token_version"],
        "password_updated_at": state.get("password_updated_at"),
        "credential_source": state.get("credential_source") or "file",
        "must_change_password": compute_must_change_password(state),
    }


def change_password(username: str, current_password: str, new_password: str) -> Dict[str, Any]:
    normalized_username = str(username or "").strip()
    normalized_new_password = str(new_password or "")
    validate_password_strength(normalized_username, normalized_new_password)

    with AUTH_STATE_LOCK:
        state = _load_auth_state()
        if not hmac.compare_digest(normalized_username, state["username"]):
            raise ValueError("用户不存在")
        if not _verify_password(current_password, state["password_hash"]):
            raise ValueError("当前密码不正确")
        if _verify_password(normalized_new_password, state["password_hash"]):
            raise ValueError("新密码不能与当前密码相同")

        state["password_hash"] = _hash_password(normalized_new_password)
        state["token_version"] = max(1, int(state.get("token_version") or 1)) + 1
        state["password_updated_at"] = int(time.time())
        state["credential_source"] = "file"
        saved_state = _save_auth_state(state)

    return {
        "username": saved_state["username"],
        "role": normalize_role(saved_state.get("role")),
        "permissions": get_role_permissions(saved_state.get("role")),
        "token_version": saved_state["token_version"],
        "password_updated_at": saved_state["password_updated_at"],
        "credential_source": saved_state.get("credential_source") or "file",
        "must_change_password": compute_must_change_password(saved_state),
    }


def issue_access_token(username: str, expires_in_seconds: Optional[int] = None) -> Dict[str, Any]:
    profile = get_auth_profile(username)
    issued_at = int(time.time())
    expires_at = issued_at + max(60, int(expires_in_seconds or TOKEN_TTL_SECONDS))
    payload = {
        "username": str(profile.get("username") or username),
        "role": normalize_role(profile.get("role")),
        "issued_at": issued_at,
        "expires_at": expires_at,
        "token_version": int(profile.get("token_version") or 1),
    }
    payload_segment = _b64url_encode(
        json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    )
    signature_segment = _sign_payload(payload_segment)
    return {
        "access_token": f"{payload_segment}.{signature_segment}",
        "token_type": "bearer",
        "issued_at": issued_at,
        "expires_at": expires_at,
        "username": payload["username"],
        "role": payload["role"],
        "permissions": get_role_permissions(payload["role"]),
        "password_updated_at": profile.get("password_updated_at"),
        "credential_source": profile.get("credential_source") or "file",
        "must_change_password": compute_must_change_password(profile),
    }


def verify_access_token(token: Optional[str]) -> Optional[Dict[str, Any]]:
    if not token or "." not in token:
        return None

    payload_segment, signature_segment = token.rsplit(".", 1)
    expected_signature = _sign_payload(payload_segment)
    if not hmac.compare_digest(signature_segment, expected_signature):
        return None

    try:
        payload = json.loads(_b64url_decode(payload_segment).decode("utf-8"))
    except (ValueError, json.JSONDecodeError, UnicodeDecodeError):
        return None

    username = str(payload.get("username") or "").strip()
    expires_at = payload.get("expires_at")
    issued_at = payload.get("issued_at")
    token_version = payload.get("token_version")

    if not username:
        return None
    if not isinstance(expires_at, int) or not isinstance(issued_at, int) or not isinstance(token_version, int):
        return None
    if expires_at <= int(time.time()):
        return None

    profile = get_auth_profile(username)
    if not profile:
        return None
    if int(profile.get("token_version") or 0) != token_version:
        return None

    role = normalize_role(profile.get("role") or payload.get("role"))
    return {
        "username": username,
        "role": role,
        "permissions": get_role_permissions(role),
        "issued_at": issued_at,
        "expires_at": expires_at,
        "token_version": token_version,
        "password_updated_at": profile.get("password_updated_at"),
        "credential_source": profile.get("credential_source") or "file",
        "must_change_password": compute_must_change_password(profile),
    }


def extract_bearer_token(source: Optional[Any]) -> Optional[str]:
    headers = getattr(source, "headers", None)
    if headers is None:
        return None
    authorization = headers.get("Authorization", "")
    if not authorization.startswith("Bearer "):
        return None
    token = authorization[7:].strip()
    return token or None


def extract_agent_token(source: Optional[Any], query_key: str = "agent_token") -> Optional[str]:
    token = extract_bearer_token(source)
    if token:
        return token

    query_params = getattr(source, "query_params", None)
    if query_params is None:
        return None

    fallback_token = str(query_params.get(query_key) or "").strip()
    return fallback_token or None


def _default_agent_token_path() -> str:
    return os.path.join(os.path.dirname(__file__), AGENT_TOKEN_FILE_NAME)


def _uses_managed_agent_token() -> bool:
    configured_token = str(get_env(AGENT_TOKEN_ENV_NAME, "") or "").strip()
    if configured_token:
        return True

    configured_token_file = str(get_env(AGENT_TOKEN_FILE_ENV_NAME, "") or "").strip()
    if configured_token_file:
        return True

    return os.path.exists(_default_agent_token_path())


def get_expected_agent_token() -> str:
    if _uses_managed_agent_token():
        return get_or_create_secret(
            AGENT_TOKEN_ENV_NAME,
            AGENT_TOKEN_FILE_ENV_NAME,
            AGENT_TOKEN_FILE_NAME,
        )
    return LEGACY_AGENT_TOKEN


def verify_agent_token(token: Optional[str]) -> Optional[Dict[str, Any]]:
    normalized_token = str(token or "").strip()
    if not normalized_token:
        return None

    expected_token = get_expected_agent_token()
    if not hmac.compare_digest(normalized_token, expected_token):
        return None

    using_legacy_token = expected_token == LEGACY_AGENT_TOKEN and not _uses_managed_agent_token()
    return {
        "auth_type": "agent",
        "token_source": "legacy_default" if using_legacy_token else "configured",
        "legacy_compat": using_legacy_token,
    }


def get_request_agent_auth(request: Optional[Any]) -> Optional[Dict[str, Any]]:
    request_state = getattr(request, "state", None)
    cached_auth = getattr(request_state, "agent_auth", None) if request_state is not None else None
    if cached_auth:
        return cached_auth

    token = extract_agent_token(request) if request is not None else None
    agent_auth = verify_agent_token(token)
    if request_state is not None and agent_auth:
        request_state.agent_auth = agent_auth
    return agent_auth


def require_agent_request(request: Optional[Any]) -> Dict[str, Any]:
    agent_auth = get_request_agent_auth(request)
    if not agent_auth:
        raise HTTPException(status_code=401, detail="Unauthorized")
    return agent_auth


def normalize_actor_name(value: Any, fallback: str = "system", max_length: int = 120) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        normalized = fallback
    return normalized[:max_length]


def get_request_username(request: Optional[Request], fallback: str = "system") -> str:
    auth_user = getattr(getattr(request, "state", None), "auth_user", {}) or {}
    return normalize_actor_name(auth_user.get("username"), fallback=fallback)


def is_exempt_path(
    path: str,
    method: str,
    exemptions: Optional[Iterable[Dict[str, Any]]] = None,
) -> bool:
    normalized_method = str(method or "").upper()
    if normalized_method == "OPTIONS":
        return True

    for rule in tuple(DEFAULT_AUTH_EXEMPTIONS) + tuple(exemptions or ()):
        allowed_methods = {str(item).upper() for item in rule.get("methods", []) if item}
        if allowed_methods and normalized_method not in allowed_methods:
            continue

        exact_path = rule.get("path")
        if exact_path is not None and path == exact_path:
            return True

        prefix = rule.get("prefix")
        if prefix and path.startswith(prefix):
            return True

        pattern = rule.get("pattern")
        if pattern and fnmatch(path, pattern):
            return True

    return False
