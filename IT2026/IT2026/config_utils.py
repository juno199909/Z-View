import os
import secrets
from functools import lru_cache
from typing import Dict, List, Optional, Sequence


DEFAULT_CORS_ORIGINS: Sequence[str] = (
    "http://localhost:5173",
    "http://127.0.0.1:5173",
    "http://localhost:4173",
    "http://127.0.0.1:4173",
)


def _strip_wrapping_quotes(value: str) -> str:
    trimmed = value.strip()
    if len(trimmed) >= 2 and trimmed[0] == trimmed[-1] and trimmed[0] in {"'", '"'}:
        return trimmed[1:-1]
    return trimmed


@lru_cache(maxsize=1)
def ensure_env_loaded() -> None:
    candidate_paths: List[tuple[str, bool]] = []
    env_file = os.getenv("ZVIEW_ENV_FILE")
    if env_file:
        candidate_paths.append((env_file, True))

    module_dir = os.path.dirname(__file__)
    candidate_paths.extend(
        [
            (os.path.join(os.getcwd(), ".env"), False),
            (os.path.join(module_dir, ".env"), False),
            (os.path.join(os.path.dirname(module_dir), ".env"), False),
        ]
    )

    seen = set()
    for raw_path, allow_override in candidate_paths:
        path = os.path.abspath(raw_path)
        if path in seen or not os.path.exists(path):
            continue
        seen.add(path)

        try:
            with open(path, "r", encoding="utf-8") as env_fp:
                for raw_line in env_fp:
                    line = raw_line.strip()
                    if not line or line.startswith("#") or "=" not in line:
                        continue
                    key, value = line.split("=", 1)
                    key = key.strip()
                    if key:
                        cleaned_value = _strip_wrapping_quotes(value)
                        if allow_override:
                            os.environ[key] = cleaned_value
                        else:
                            os.environ.setdefault(key, cleaned_value)
        except OSError:
            continue


def get_env(name: str, default: Optional[str] = None) -> Optional[str]:
    ensure_env_loaded()
    value = os.getenv(name)
    return value if value is not None else default


def _parse_csv_env(name: str, default_values: Sequence[str]) -> List[str]:
    raw_value = get_env(name)
    if raw_value is None:
        return [item for item in default_values if item]

    values = [item.strip() for item in raw_value.split(",")]
    return [item for item in values if item]


def get_db_config() -> Dict[str, object]:
    port_value = get_env("ZVIEW_DB_PORT", "3306") or "3306"
    try:
        port = int(port_value)
    except ValueError:
        port = 3306

    return {
        "host": get_env("ZVIEW_DB_HOST", "127.0.0.1") or "127.0.0.1",
        "user": get_env("ZVIEW_DB_USER", "root") or "root",
        "password": get_env("ZVIEW_DB_PASSWORD", "") or "",
        "database": get_env("ZVIEW_DB_NAME", "cmdb") or "cmdb",
        "port": port,
    }


def get_cors_middleware_options() -> Dict[str, object]:
    origins = _parse_csv_env("ZVIEW_CORS_ORIGINS", DEFAULT_CORS_ORIGINS)
    allow_all = "*" in origins
    return {
        "allow_origins": ["*"] if allow_all else origins,
        "allow_credentials": not allow_all,
        "allow_methods": ["*"],
        "allow_headers": ["*"],
    }


@lru_cache(maxsize=None)
def get_or_create_secret(
    env_name: str,
    file_env_name: str,
    default_file_name: str,
) -> str:
    configured = get_env(env_name)
    if configured:
        return configured

    secret_file = get_env(file_env_name)
    if not secret_file:
        secret_file = os.path.join(os.path.dirname(__file__), default_file_name)

    secret_path = os.path.abspath(secret_file)
    try:
        if os.path.exists(secret_path):
            with open(secret_path, "r", encoding="utf-8") as secret_fp:
                existing = secret_fp.read().strip()
            if existing:
                return existing
    except OSError:
        pass

    generated = secrets.token_urlsafe(48)
    try:
        os.makedirs(os.path.dirname(secret_path) or ".", exist_ok=True)
        temp_path = f"{secret_path}.tmp"
        with open(temp_path, "w", encoding="utf-8") as secret_fp:
            secret_fp.write(generated)
        os.replace(temp_path, secret_path)
    except OSError:
        return generated

    return generated
