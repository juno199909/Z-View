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

from fastapi import Request


DEFAULT_ADMIN_USERNAME = os.getenv("ZVIEW_ADMIN_USERNAME", "admin")
DEFAULT_ADMIN_PASSWORD = os.getenv("ZVIEW_ADMIN_PASSWORD", "admin123")
TOKEN_SECRET = os.getenv(
    "ZVIEW_AUTH_SECRET",
    "zview-auth-secret-change-me",
)
TOKEN_TTL_SECONDS = int(os.getenv("ZVIEW_AUTH_TTL_SECONDS", "43200"))
AUTH_STATE_FILE = os.getenv(
    "ZVIEW_AUTH_STATE_FILE",
    os.path.join(os.path.dirname(__file__), "auth_state.json"),
)
PASSWORD_HASH_ITERATIONS = int(os.getenv("ZVIEW_PASSWORD_HASH_ITERATIONS", "120000"))
AUTH_STATE_LOCK = threading.Lock()
WEAK_PASSWORD_PATTERNS = (
    "123456",
    "123123",
    "abc123",
    "admin123",
    "password",
    "qwerty",
    "000000",
)

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


def _default_auth_state() -> Dict[str, Any]:
    return {
        "username": DEFAULT_ADMIN_USERNAME,
        "password_hash": _hash_password(DEFAULT_ADMIN_PASSWORD),
        "token_version": 1,
        "password_updated_at": None,
        "credential_source": "default",
    }


def _load_auth_state() -> Dict[str, Any]:
    if not os.path.exists(AUTH_STATE_FILE):
        return _default_auth_state()

    try:
        with open(AUTH_STATE_FILE, "r", encoding="utf-8") as fp:
            state = json.load(fp)
    except (OSError, ValueError, json.JSONDecodeError):
        return _default_auth_state()

    default_state = _default_auth_state()
    merged = {
        "username": str(state.get("username") or default_state["username"]).strip() or default_state["username"],
        "password_hash": str(state.get("password_hash") or default_state["password_hash"]),
        "token_version": max(1, int(state.get("token_version") or default_state["token_version"])),
        "password_updated_at": state.get("password_updated_at"),
        "credential_source": str(state.get("credential_source") or "file"),
    }
    return merged


def _save_auth_state(state: Dict[str, Any]) -> Dict[str, Any]:
    normalized = {
        "username": str(state.get("username") or DEFAULT_ADMIN_USERNAME).strip() or DEFAULT_ADMIN_USERNAME,
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
        "token_version": state["token_version"],
        "password_updated_at": state.get("password_updated_at"),
        "credential_source": state.get("credential_source") or "file",
    }
    profile["must_change_password"] = compute_must_change_password(profile)
    return profile


def compute_must_change_password(profile: Optional[Dict[str, Any]]) -> bool:
    profile = profile or {}
    credential_source = str(profile.get("credential_source") or "file").strip().lower()
    password_updated_at = profile.get("password_updated_at")
    return credential_source == "default" and not password_updated_at


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

    return {
        "username": username,
        "issued_at": issued_at,
        "expires_at": expires_at,
        "token_version": token_version,
        "password_updated_at": profile.get("password_updated_at"),
        "credential_source": profile.get("credential_source") or "file",
        "must_change_password": compute_must_change_password(profile),
    }


def extract_bearer_token(request: Request) -> Optional[str]:
    authorization = request.headers.get("Authorization", "")
    if not authorization.startswith("Bearer "):
        return None
    token = authorization[7:].strip()
    return token or None


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
