"""
软件策略管理API
包含黑名单、白名单、强制安装策略
"""

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from typing import Any, Dict, List, Optional
import mysql.connector
from mysql.connector import Error
import json
import re
from difflib import SequenceMatcher
from datetime import datetime
from auth_utils import (
    extract_bearer_token,
    get_request_username,
    is_exempt_path,
    require_request_permission,
    verify_access_token,
)
from console_utils import enable_utf8_stdio, safe_console_print
from config_utils import get_cors_middleware_options, get_db_config

enable_utf8_stdio()
print = safe_console_print

app = FastAPI(title="Software Policy Management API")

# 配置CORS
app.add_middleware(
    CORSMiddleware,
    **get_cors_middleware_options(),
)

AUTH_EXEMPTIONS = (
    {"pattern": "/api/v1/policies/check/*", "methods": ["GET"]},
)


@app.middleware("http")
async def auth_middleware(request: Request, call_next):
    if is_exempt_path(request.url.path, request.method, AUTH_EXEMPTIONS):
        return await call_next(request)

    token = extract_bearer_token(request)
    auth_user = verify_access_token(token)
    if not auth_user:
        return JSONResponse(status_code=401, content={"detail": "Unauthorized"})

    try:
        require_request_permission(auth_user, request.url.path, request.method)
    except HTTPException as exc:
        return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})

    request.state.auth_user = auth_user
    return await call_next(request)

# 数据库配置
DB_CONFIG = get_db_config()

POLICY_TARGET_ONLINE_SECONDS = 90
AGENT_INSTALL_STATUS_INSTALLED = "installed"


class PolicyLogCreate(BaseModel):
    policy_id: Optional[int] = 0
    asset_id: int
    software_name: Optional[str] = None
    action: Optional[str] = None
    result: Optional[str] = "success"
    message: Optional[str] = None

def get_db_connection():
    """获取数据库连接"""
    try:
        conn = mysql.connector.connect(**DB_CONFIG)
        cursor = conn.cursor()
        cursor.execute("SET time_zone = '+8:00'")
        cursor.close()
        return conn
    except Error as e:
        print(f"数据库连接错误: {e}")
        return None

def init_database():
    """初始化数据库表"""
    conn = get_db_connection()
    if not conn:
        return False

    try:
        cursor = conn.cursor()

        # 创建软件策略表
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS software_policies (
                id INT PRIMARY KEY AUTO_INCREMENT,
                policy_name VARCHAR(200) NOT NULL COMMENT '策略名称',
                policy_type ENUM('blacklist', 'whitelist', 'force_install') NOT NULL COMMENT '策略类型',
                description TEXT COMMENT '策略描述',
                enabled TINYINT(1) DEFAULT 1 COMMENT '是否启用',
                priority INT DEFAULT 0 COMMENT '优先级',
                target_type ENUM('all', 'group', 'asset') DEFAULT 'all' COMMENT '目标类型',
                target_ids JSON COMMENT '目标ID列表',
                created_by VARCHAR(50) DEFAULT 'system',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                INDEX idx_policy_type (policy_type),
                INDEX idx_enabled (enabled)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='软件策略表'
        """)

        # 创建策略规则表
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS software_policy_rules (
                id INT PRIMARY KEY AUTO_INCREMENT,
                policy_id INT NOT NULL COMMENT '策略ID',
                rule_type ENUM('software_name', 'package_id', 'vendor', 'category') DEFAULT 'software_name' COMMENT '规则类型',
                rule_value VARCHAR(500) NOT NULL COMMENT '规则值',
                match_type ENUM('exact', 'contains', 'regex') DEFAULT 'contains' COMMENT '匹配类型',
                action ENUM('allow', 'deny', 'force') DEFAULT 'deny' COMMENT '动作',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                INDEX idx_policy_id (policy_id)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='策略规则表'
        """)

        # 创建策略执行日志表
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS software_policy_logs (
                id INT PRIMARY KEY AUTO_INCREMENT,
                policy_id INT NOT NULL COMMENT '策略ID',
                asset_id INT NOT NULL COMMENT '资产ID',
                software_name VARCHAR(200) COMMENT '软件名称',
                action VARCHAR(50) COMMENT '执行动作',
                result VARCHAR(50) COMMENT '执行结果',
                message TEXT COMMENT '执行消息',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                INDEX idx_policy_id (policy_id),
                INDEX idx_asset_id (asset_id),
                INDEX idx_created_at (created_at)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='策略执行日志表'
        """)

        cursor.execute("""
            ALTER TABLE software_policy_logs
            MODIFY COLUMN result VARCHAR(50) NULL COMMENT '执行结果'
        """)

        conn.commit()
        print("[Policy] Policy tables initialized")
        return True

    except Error as e:
        print(f"[Policy] Table initialization failed: {e}")
        return False
    finally:
        cursor.close()
        conn.close()

# ============================================================
# Pydantic 模型
# ============================================================

class PolicyRule(BaseModel):
    rule_type: str = "software_name"  # software_name, package_id, vendor, category
    rule_value: str
    match_type: str = "contains"  # exact, contains, regex
    action: str = "deny"  # allow, deny, force

class SoftwarePolicy(BaseModel):
    policy_name: str
    policy_type: str  # blacklist, whitelist, force_install
    description: Optional[str] = None
    enabled: bool = True
    priority: int = 0
    target_type: str = "all"  # all, group, asset
    target_ids: List[int] = []
    rules: List[PolicyRule] = []

class PolicyUpdate(BaseModel):
    policy_name: Optional[str] = None
    policy_type: Optional[str] = None
    description: Optional[str] = None
    enabled: Optional[bool] = None
    priority: Optional[int] = None
    target_type: Optional[str] = None
    target_ids: Optional[List[int]] = None
    rules: Optional[List[PolicyRule]] = None


POLICY_TASK_CREATOR = "policy-engine"


def parse_target_ids(raw_value: Any) -> List[int]:
    if raw_value in (None, "", []):
        return []

    try:
        parsed = json.loads(raw_value) if isinstance(raw_value, str) else raw_value
    except (TypeError, ValueError, json.JSONDecodeError):
        return []

    if not isinstance(parsed, list):
        return []

    result: List[int] = []
    for item in parsed:
        try:
            result.append(int(item))
        except (TypeError, ValueError):
            continue
    return result


def priority_to_task_priority(priority: int) -> str:
    # 任务表当前优先级字段沿用字符串，统一转成可比较的零填充数字串。
    safe_priority = max(0, min(int(priority or 0), 999))
    return f"{safe_priority:03d}"


def build_policy_target_identity(row: Dict[str, Any]) -> str:
    mac_address = str(row.get("mac_address") or "").strip().lower()
    hostname = str(row.get("hostname") or "").strip().lower()
    ip_address = str(row.get("ip_address") or "").strip().lower()

    if mac_address:
        return f"mac:{mac_address}"
    if hostname and ip_address:
        return f"hostip:{hostname}|{ip_address}"
    if hostname:
        return f"host:{hostname}"
    if ip_address:
        return f"ip:{ip_address}"
    return f"id:{row.get('id')}"


def resolve_policy_target_assets(cursor, target_type: str, target_ids: List[int]) -> List[int]:
    where_clauses = ["deleted_at IS NULL"]
    params: List[Any] = []

    if target_type == "asset":
        if not target_ids:
            return []
        placeholders = ", ".join(["%s"] * len(target_ids))
        where_clauses.append(f"id IN ({placeholders})")
        params.extend(target_ids)
    elif target_type == "group":
        if not target_ids:
            return []
        placeholders = ", ".join(["%s"] * len(target_ids))
        where_clauses.append(f"group_id IN ({placeholders})")
        params.extend(target_ids)

        # 企业口径：组范围自动策略只下发给“当前有效在线且已安装 Agent”的终端，
        # 避免把历史离线资产、重复资产一并塞进待执行列表。
        where_clauses.append("agent_install_status = %s")
        where_clauses.append("last_seen IS NOT NULL")
        where_clauses.append("TIMESTAMPDIFF(SECOND, last_seen, NOW()) <= %s")
        params.extend([AGENT_INSTALL_STATUS_INSTALLED, POLICY_TARGET_ONLINE_SECONDS])
    else:
        # all 范围策略默认只命中当前有效在线终端，和终端列表在线口径保持一致。
        where_clauses.append("agent_install_status = %s")
        where_clauses.append("last_seen IS NOT NULL")
        where_clauses.append("TIMESTAMPDIFF(SECOND, last_seen, NOW()) <= %s")
        params.extend([AGENT_INSTALL_STATUS_INSTALLED, POLICY_TARGET_ONLINE_SECONDS])

    cursor.execute(
        f"""
        SELECT id, hostname, ip_address, mac_address, last_seen
        FROM assets
        WHERE {" AND ".join(where_clauses)}
        ORDER BY
            CASE WHEN last_seen IS NULL THEN 1 ELSE 0 END ASC,
            last_seen DESC,
            id DESC
        """,
        params,
    )
    rows = cursor.fetchall()

    if target_type == "asset":
        return sorted({int(row["id"]) for row in rows if row.get("id") is not None})

    unique_asset_ids: List[int] = []
    seen_identities = set()
    for row in rows:
        asset_id = row.get("id")
        if asset_id is None:
            continue

        identity = build_policy_target_identity(row)
        if identity in seen_identities:
            continue

        seen_identities.add(identity)
        unique_asset_ids.append(int(asset_id))

    return unique_asset_ids


def get_package_info(cursor, package_id: int) -> Optional[Dict[str, Any]]:
    cursor.execute(
        """
        SELECT id, package_name, display_name, version, status
        FROM software_packages
        WHERE id = %s AND deleted_at IS NULL
        """,
        (package_id,),
    )
    return cursor.fetchone()


def normalize_package_keyword(value: Optional[str]) -> str:
    return re.sub(r"[\s_\-\.]+", "", str(value or "").strip().lower())


def find_best_package_match(
    keyword: str,
    packages: List[Dict[str, Any]],
    match_type: str = "contains"
) -> Optional[Dict[str, Any]]:
    normalized_keyword = normalize_package_keyword(keyword)
    if not normalized_keyword:
        return None

    best_package = None
    best_score = 0.0

    for package in packages:
        candidates = [
            package.get("package_name"),
            package.get("display_name"),
        ]
        normalized_candidates = [normalize_package_keyword(item) for item in candidates if item]

        if not normalized_candidates:
            continue

        if any(candidate == normalized_keyword for candidate in normalized_candidates):
            return package

        if match_type != "exact" and any(
            normalized_keyword in candidate or candidate in normalized_keyword
            for candidate in normalized_candidates
        ):
            return package

        score = max(
            SequenceMatcher(None, normalized_keyword, candidate).ratio()
            for candidate in normalized_candidates
        )
        if score > best_score:
            best_score = score
            best_package = package

    if best_score >= 0.78:
        return best_package
    return None


def resolve_package_from_rule(cursor, rule: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    rule_type = rule.get("rule_type")

    if rule_type == "package_id":
        try:
            package_id = int(rule.get("rule_value"))
        except (TypeError, ValueError):
            return None
        return get_package_info(cursor, package_id)

    if rule_type != "software_name":
        return None

    keyword = str(rule.get("rule_value") or "").strip()
    if not keyword:
        return None

    match_type = rule.get("match_type") or "contains"
    package = None

    if match_type == "exact":
        cursor.execute(
            """
            SELECT id, package_name, display_name, version, status
            FROM software_packages
            WHERE deleted_at IS NULL
              AND status = 'available'
              AND (package_name = %s OR display_name = %s)
            ORDER BY created_at DESC, id DESC
            LIMIT 1
            """,
            (keyword, keyword),
        )
        package = cursor.fetchone()
    else:
        like_keyword = f"%{keyword}%"
        cursor.execute(
            """
            SELECT id, package_name, display_name, version, status
            FROM software_packages
            WHERE deleted_at IS NULL
              AND status = 'available'
              AND (package_name LIKE %s OR display_name LIKE %s)
            ORDER BY created_at DESC, id DESC
            LIMIT 1
            """,
            (like_keyword, like_keyword),
        )
        package = cursor.fetchone()

    if package:
        return package

    cursor.execute(
        """
        SELECT id, package_name, display_name, version, status
        FROM software_packages
        WHERE deleted_at IS NULL
          AND status = 'available'
        ORDER BY created_at DESC, id DESC
        """
    )
    return find_best_package_match(keyword, cursor.fetchall(), match_type)


def append_package_metadata(cursor, rules: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    for rule in rules:
        if rule.get("rule_type") != "package_id":
            continue

        try:
            package_id = int(rule.get("rule_value"))
        except (TypeError, ValueError):
            continue

        package = get_package_info(cursor, package_id)
        if package:
            rule["package_display_name"] = package.get("display_name")
            rule["package_version"] = package.get("version")
            rule["package_name"] = package.get("package_name")
    return rules


def serialize_policy_rule(rule: Any) -> Dict[str, Any]:
    if isinstance(rule, dict):
        return {
            "rule_type": str(rule.get("rule_type") or "software_name"),
            "rule_value": str(rule.get("rule_value") or "").strip(),
            "match_type": str(rule.get("match_type") or "contains"),
            "action": str(rule.get("action") or "deny"),
        }

    return {
        "rule_type": str(getattr(rule, "rule_type", "software_name") or "software_name"),
        "rule_value": str(getattr(rule, "rule_value", "") or "").strip(),
        "match_type": str(getattr(rule, "match_type", "contains") or "contains"),
        "action": str(getattr(rule, "action", "deny") or "deny"),
    }


def validate_force_install_rules(cursor, rules: List[Any]) -> List[Dict[str, Any]]:
    serialized_rules = [serialize_policy_rule(rule) for rule in rules or []]
    if not serialized_rules:
        raise HTTPException(status_code=400, detail="强制安装策略至少需要选择 1 个软件包")

    validated_rules: List[Dict[str, Any]] = []

    for index, rule in enumerate(serialized_rules, start=1):
        if rule.get("rule_type") != "package_id":
            raise HTTPException(
                status_code=400,
                detail=f"强制安装策略第 {index} 条规则必须从软件包仓库中选择软件",
            )

        raw_package_id = str(rule.get("rule_value") or "").strip()
        if not raw_package_id:
            raise HTTPException(
                status_code=400,
                detail=f"强制安装策略第 {index} 条规则缺少软件包标识",
            )

        try:
            package_id = int(raw_package_id)
        except (TypeError, ValueError) as exc:
            raise HTTPException(
                status_code=400,
                detail=f"强制安装策略第 {index} 条规则的软件包标识无效",
            ) from exc

        package = get_package_info(cursor, package_id)
        if not package:
            raise HTTPException(
                status_code=400,
                detail=f"强制安装策略第 {index} 条规则指定的软件包不存在",
            )

        if package.get("status") != "available":
            raise HTTPException(
                status_code=400,
                detail=f"强制安装策略第 {index} 条规则指定的软件包当前不可用",
            )

        validated_rules.append(
            {
                "rule_type": "package_id",
                "rule_value": str(package_id),
                "match_type": "exact",
                "action": "force",
            }
        )

    return validated_rules


def get_policy_rules(cursor, policy_id: int) -> List[Dict[str, Any]]:
    cursor.execute(
        """
        SELECT * FROM software_policy_rules
        WHERE policy_id = %s
        ORDER BY id ASC
        """,
        (policy_id,),
    )
    return cursor.fetchall()


def get_asset_context(cursor, asset_id: int) -> Optional[Dict[str, Any]]:
    cursor.execute(
        """
        SELECT id, hostname, ip_address, group_id, deleted_at
        FROM assets
        WHERE id = %s
        LIMIT 1
        """,
        (asset_id,),
    )
    return cursor.fetchone()


def policy_applies_to_asset(policy: Dict[str, Any], asset: Dict[str, Any]) -> bool:
    target_type = str(policy.get("target_type") or "all")
    target_ids = parse_target_ids(policy.get("target_ids"))

    if target_type == "all":
        return True

    if target_type == "asset":
        return int(asset.get("id") or 0) in set(target_ids)

    if target_type == "group":
        asset_group_id = asset.get("group_id")
        if asset_group_id is None:
            return False
        return int(asset_group_id) in set(target_ids)

    return False


def rule_value_matches_text(rule: Dict[str, Any], actual_value: Optional[str]) -> bool:
    actual = str(actual_value or "").strip()
    expected = str(rule.get("rule_value") or "").strip()
    match_type = str(rule.get("match_type") or "contains").lower()

    if not actual or not expected:
        return False

    actual_lower = actual.lower()
    expected_lower = expected.lower()

    if match_type == "exact":
        return actual_lower == expected_lower

    if match_type == "regex":
        try:
            return re.search(expected, actual, re.IGNORECASE) is not None
        except re.error:
            return False

    return expected_lower in actual_lower


def build_software_match_context(
    software_name: Optional[str] = None,
    vendor: Optional[str] = None,
    category: Optional[str] = None,
    package_id: Optional[str] = None,
) -> Dict[str, str]:
    return {
        "software_name": str(software_name or "").strip(),
        "vendor": str(vendor or "").strip(),
        "category": str(category or "").strip(),
        "package_id": str(package_id or "").strip(),
    }


def policy_rule_matches_software(rule: Dict[str, Any], software_context: Dict[str, str]) -> bool:
    rule_type = str(rule.get("rule_type") or "software_name").strip().lower()

    if rule_type in {"software_name", "vendor", "category"}:
        return rule_value_matches_text(rule, software_context.get(rule_type))

    if rule_type == "package_id":
        actual_package_id = str(software_context.get("package_id") or "").strip()
        expected_package_id = str(rule.get("rule_value") or "").strip()
        return bool(actual_package_id and expected_package_id and actual_package_id == expected_package_id)

    return False


def create_policy_log_entry(
    cursor,
    policy_id: int,
    software_name: Optional[str],
    action: str,
    result: str,
    message: str,
    asset_id: int = 0,
):
    cursor.execute(
        """
        INSERT INTO software_policy_logs (
            policy_id, asset_id, software_name, action, result, message
        ) VALUES (%s, %s, %s, %s, %s, %s)
        """,
        (policy_id, asset_id, software_name, action, result, message),
    )


def has_open_policy_install_task(cursor, policy_id: int, rule_id: int, package_id: int) -> bool:
    cursor.execute(
        """
        SELECT id
        FROM software_tasks
        WHERE task_type = 'install'
          AND package_id = %s
          AND created_by = %s
          AND status IN ('pending', 'running', 'partial')
          AND JSON_UNQUOTE(JSON_EXTRACT(options, '$.policy_id')) = %s
          AND JSON_UNQUOTE(JSON_EXTRACT(options, '$.policy_rule_id')) = %s
        LIMIT 1
        """,
        (package_id, POLICY_TASK_CREATOR, str(policy_id), str(rule_id)),
    )
    return cursor.fetchone() is not None


def enqueue_force_install_policy_tasks(cursor, policy_id: int) -> Dict[str, int]:
    cursor.execute("SELECT * FROM software_policies WHERE id = %s", (policy_id,))
    policy = cursor.fetchone()
    if not policy or policy.get("policy_type") != "force_install" or not policy.get("enabled"):
        return {"queued_tasks": 0, "skipped_rules": 0}

    target_assets = resolve_policy_target_assets(
        cursor,
        policy.get("target_type") or "all",
        parse_target_ids(policy.get("target_ids")),
    )
    if not target_assets:
        create_policy_log_entry(
            cursor,
            policy_id,
            None,
            "queue_install_task",
            "skipped",
            "未找到匹配的目标终端，未创建安装任务",
        )
        return {"queued_tasks": 0, "skipped_rules": 0}

    cursor.execute(
        """
        SELECT *
        FROM software_policy_rules
        WHERE policy_id = %s
        ORDER BY id ASC
        """,
        (policy_id,),
    )
    rules = cursor.fetchall()

    queued_tasks = 0
    skipped_rules = 0

    for rule in rules:
        package = resolve_package_from_rule(cursor, rule)
        if not package:
            skipped_rules += 1
            create_policy_log_entry(
                cursor,
                policy_id,
                str(rule.get("rule_value") or ""),
                "queue_install_task",
                "failed",
                "未能从策略规则解析到可用软件包",
            )
            continue

        package_id = int(package["id"])
        if package.get("status") != "available":
            skipped_rules += 1
            create_policy_log_entry(
                cursor,
                policy_id,
                str(package_id),
                "queue_install_task",
                "failed",
                "软件包不存在或当前不可用",
            )
            continue

        if has_open_policy_install_task(cursor, policy_id, rule["id"], package_id):
            skipped_rules += 1
            create_policy_log_entry(
                cursor,
                policy_id,
                package.get("display_name"),
                "queue_install_task",
                "skipped",
                "已有未完成的策略安装任务，跳过重复创建",
            )
            continue

        task_name = (
            f"[策略强装] {policy.get('policy_name')} - "
            f"{package.get('display_name')} {package.get('version')}"
        ).strip()
        task_options = {
            "source": "force_install_policy",
            "policy_id": policy_id,
            "policy_rule_id": rule["id"],
            "package_id": package_id,
        }
        target_ids_json = json.dumps(target_assets)

        cursor.execute(
            """
            INSERT INTO software_tasks (
                task_name, task_type, package_id, software_name,
                schedule_type, target_type, target_ids, target_count,
                priority, options, status, created_by
            ) VALUES (%s, 'install', %s, %s, 'immediate', 'asset', %s, %s, %s, %s, 'pending', %s)
            """,
            (
                task_name,
                package_id,
                package.get("display_name"),
                target_ids_json,
                len(target_assets),
                priority_to_task_priority(policy.get("priority") or 0),
                json.dumps(task_options, ensure_ascii=False),
                POLICY_TASK_CREATOR,
            ),
        )
        task_id = cursor.lastrowid

        for asset_id in target_assets:
            cursor.execute(
                """
                INSERT INTO software_task_results (task_id, asset_id, status)
                VALUES (%s, %s, 'pending')
                """,
                (task_id, asset_id),
            )

        create_policy_log_entry(
            cursor,
            policy_id,
            package.get("display_name"),
            "queue_install_task",
            "success",
            f"已创建安装任务 #{task_id}，目标终端 {len(target_assets)} 台",
        )
        queued_tasks += 1

    return {"queued_tasks": queued_tasks, "skipped_rules": skipped_rules}

# ============================================================
# API 端点
# ============================================================

@app.on_event("startup")
async def startup_event():
    """应用启动时初始化数据库"""
    init_database()

@app.get("/")
def root():
    return {"message": "Software Policy Management API", "version": "1.0.0"}

@app.post("/api/v1/policies")
def create_policy(policy: SoftwarePolicy, request: Request):
    """创建软件策略"""
    conn = get_db_connection()
    if not conn:
        raise HTTPException(status_code=500, detail="Database connection failed")

    try:
        cursor = conn.cursor(dictionary=True)
        rules_to_store = (
            validate_force_install_rules(cursor, policy.rules)
            if policy.policy_type == "force_install"
            else [serialize_policy_rule(rule) for rule in policy.rules]
        )

        # 插入策略
        created_by = get_request_username(request)

        cursor.execute("""
            INSERT INTO software_policies (
                policy_name, policy_type, description, enabled, priority,
                target_type, target_ids, created_by
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        """, (
            policy.policy_name, policy.policy_type, policy.description,
            policy.enabled, policy.priority, policy.target_type,
            json.dumps(policy.target_ids), created_by
        ))

        policy_id = cursor.lastrowid

        # 插入规则
        for rule in rules_to_store:
            cursor.execute("""
                INSERT INTO software_policy_rules (
                    policy_id, rule_type, rule_value, match_type, action
                ) VALUES (%s, %s, %s, %s, %s)
            """, (
                policy_id, rule["rule_type"], rule["rule_value"],
                rule["match_type"], rule["action"]
            ))

        queue_summary = {"queued_tasks": 0, "skipped_rules": 0}
        if policy.policy_type == "force_install" and policy.enabled:
            queue_summary = enqueue_force_install_policy_tasks(cursor, policy_id)

        conn.commit()

        return {
            "message": "策略创建成功",
            "policy_id": policy_id,
            **queue_summary,
        }

    except Error as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        cursor.close()
        conn.close()

@app.get("/api/v1/policies")
def get_policies(
    policy_type: Optional[str] = None,
    enabled: Optional[bool] = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100)
):
    """获取策略列表"""
    conn = get_db_connection()
    if not conn:
        raise HTTPException(status_code=500, detail="Database connection failed")

    try:
        cursor = conn.cursor(dictionary=True)

        # 构建查询条件
        where_clauses = []
        params = []

        if policy_type:
            where_clauses.append("policy_type = %s")
            params.append(policy_type)

        if enabled is not None:
            where_clauses.append("enabled = %s")
            params.append(enabled)

        where_sql = f"WHERE {' AND '.join(where_clauses)}" if where_clauses else ""

        # 获取总数
        cursor.execute(f"SELECT COUNT(*) as total FROM software_policies {where_sql}", params)
        total = cursor.fetchone()['total']

        # 获取策略列表
        offset = (page - 1) * page_size
        cursor.execute(f"""
            SELECT * FROM software_policies
            {where_sql}
            ORDER BY priority DESC, created_at DESC
            LIMIT %s OFFSET %s
        """, params + [page_size, offset])

        policies = cursor.fetchall()

        # 获取每个策略的规则
        for policy in policies:
            policy['rules'] = append_package_metadata(cursor, get_policy_rules(cursor, policy['id']))

            # 解析JSON字段
            if policy['target_ids']:
                policy['target_ids'] = json.loads(policy['target_ids'])

        return {
            "total": total,
            "page": page,
            "page_size": page_size,
            "data": policies
        }

    except Error as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        cursor.close()
        conn.close()

@app.get("/api/v1/policies/logs")
def get_policy_logs(
    policy_id: Optional[int] = None,
    asset_id: Optional[int] = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=100)
):
    """获取策略执行日志"""
    conn = get_db_connection()
    if not conn:
        raise HTTPException(status_code=500, detail="Database connection failed")

    try:
        cursor = conn.cursor(dictionary=True)

        where_clauses = []
        params = []

        if policy_id:
            where_clauses.append("policy_id = %s")
            params.append(policy_id)

        if asset_id:
            where_clauses.append("asset_id = %s")
            params.append(asset_id)

        where_sql = f"WHERE {' AND '.join(where_clauses)}" if where_clauses else ""

        # 获取总数
        cursor.execute(f"SELECT COUNT(*) as total FROM software_policy_logs {where_sql}", params)
        total = cursor.fetchone()['total']

        # 获取日志列表
        offset = (page - 1) * page_size
        cursor.execute(f"""
            SELECT * FROM software_policy_logs
            {where_sql}
            ORDER BY created_at DESC
            LIMIT %s OFFSET %s
        """, params + [page_size, offset])

        logs = cursor.fetchall()

        return {
            "total": total,
            "page": page,
            "page_size": page_size,
            "data": logs
        }

    except Error as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        cursor.close()
        conn.close()

@app.get("/api/v1/policies/{policy_id}")
def get_policy(policy_id: int):
    """获取策略详情"""
    conn = get_db_connection()
    if not conn:
        raise HTTPException(status_code=500, detail="Database connection failed")

    try:
        cursor = conn.cursor(dictionary=True)

        cursor.execute("SELECT * FROM software_policies WHERE id = %s", (policy_id,))
        policy = cursor.fetchone()

        if not policy:
            raise HTTPException(status_code=404, detail="策略不存在")

        # 获取规则
        policy['rules'] = append_package_metadata(cursor, get_policy_rules(cursor, policy_id))

        # 解析JSON字段
        if policy['target_ids']:
            policy['target_ids'] = json.loads(policy['target_ids'])

        return policy

    except Error as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        cursor.close()
        conn.close()

@app.put("/api/v1/policies/{policy_id}")
def update_policy(policy_id: int, policy: PolicyUpdate):
    """更新策略"""
    conn = get_db_connection()
    if not conn:
        raise HTTPException(status_code=500, detail="Database connection failed")

    try:
        cursor = conn.cursor(dictionary=True)

        cursor.execute("SELECT * FROM software_policies WHERE id = %s", (policy_id,))
        existing_policy = cursor.fetchone()
        if not existing_policy:
            raise HTTPException(status_code=404, detail="策略不存在")

        effective_policy_type = policy.policy_type if policy.policy_type is not None else existing_policy.get("policy_type")
        rules_to_store = None
        if policy.rules is not None:
            rules_to_store = (
                validate_force_install_rules(cursor, policy.rules)
                if effective_policy_type == "force_install"
                else [serialize_policy_rule(rule) for rule in policy.rules]
            )
        elif effective_policy_type == "force_install":
            rules_to_store = validate_force_install_rules(cursor, get_policy_rules(cursor, policy_id))

        # 构建更新语句
        update_fields = []
        params = []

        if policy.policy_name is not None:
            update_fields.append("policy_name = %s")
            params.append(policy.policy_name)

        if policy.policy_type is not None:
            update_fields.append("policy_type = %s")
            params.append(policy.policy_type)

        if policy.description is not None:
            update_fields.append("description = %s")
            params.append(policy.description)

        if policy.enabled is not None:
            update_fields.append("enabled = %s")
            params.append(policy.enabled)

        if policy.priority is not None:
            update_fields.append("priority = %s")
            params.append(policy.priority)

        if policy.target_type is not None:
            update_fields.append("target_type = %s")
            params.append(policy.target_type)

        if policy.target_ids is not None:
            update_fields.append("target_ids = %s")
            params.append(json.dumps(policy.target_ids))

        if not update_fields and policy.rules is None:
            raise HTTPException(status_code=400, detail="没有需要更新的字段")

        if update_fields:
            params.append(policy_id)
            cursor.execute(f"""
                UPDATE software_policies
                SET {', '.join(update_fields)}
                WHERE id = %s
            """, params)

        if policy.rules is not None:
            cursor.execute(
                "DELETE FROM software_policy_rules WHERE policy_id = %s",
                (policy_id,),
            )
            for rule in rules_to_store or []:
                cursor.execute("""
                    INSERT INTO software_policy_rules (
                        policy_id, rule_type, rule_value, match_type, action
                    ) VALUES (%s, %s, %s, %s, %s)
                """, (
                    policy_id, rule["rule_type"], rule["rule_value"],
                    rule["match_type"], rule["action"]
                ))

        effective_enabled = policy.enabled if policy.enabled is not None else bool(existing_policy.get("enabled"))

        queue_summary = {"queued_tasks": 0, "skipped_rules": 0}
        if effective_policy_type == "force_install" and effective_enabled:
            queue_summary = enqueue_force_install_policy_tasks(cursor, policy_id)

        conn.commit()

        return {
            "message": "策略更新成功",
            **queue_summary,
        }

    except Error as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        cursor.close()
        conn.close()


@app.post("/api/v1/policies/{policy_id}/execute")
def execute_policy(policy_id: int):
    """立即执行策略"""
    conn = get_db_connection()
    if not conn:
        raise HTTPException(status_code=500, detail="Database connection failed")

    try:
        cursor = conn.cursor(dictionary=True)

        cursor.execute("SELECT * FROM software_policies WHERE id = %s", (policy_id,))
        policy = cursor.fetchone()
        if not policy:
            raise HTTPException(status_code=404, detail="策略不存在")

        if not bool(policy.get("enabled")):
            raise HTTPException(status_code=400, detail="策略已禁用，无法立即执行")

        if policy.get("policy_type") == "force_install":
            validate_force_install_rules(cursor, get_policy_rules(cursor, policy_id))
            queue_summary = enqueue_force_install_policy_tasks(cursor, policy_id)
            conn.commit()
            return {
                "message": (
                    f"策略已立即执行，新增 {queue_summary['queued_tasks']} 个安装任务，"
                    f"跳过 {queue_summary['skipped_rules']} 条规则"
                ),
                **queue_summary,
            }

        create_policy_log_entry(
            cursor,
            policy_id,
            None,
            "execute_policy",
            "success",
            "策略为被动校验型，已记录立即执行操作，无需下发安装任务",
        )
        conn.commit()
        return {
            "message": "策略为被动校验型，无需下发安装任务，已记录本次立即执行操作",
            "queued_tasks": 0,
            "skipped_rules": 0,
        }

    except Error as e:
        conn.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        cursor.close()
        conn.close()

@app.delete("/api/v1/policies/{policy_id}")
def delete_policy(policy_id: int):
    """删除策略"""
    conn = get_db_connection()
    if not conn:
        raise HTTPException(status_code=500, detail="Database connection failed")

    try:
        cursor = conn.cursor(dictionary=True)
        cursor.execute("SELECT id, policy_name FROM software_policies WHERE id = %s", (policy_id,))
        policy = cursor.fetchone()
        if not policy:
            raise HTTPException(status_code=404, detail="策略不存在")

        cursor.execute("DELETE FROM software_policy_rules WHERE policy_id = %s", (policy_id,))
        deleted_rules = cursor.rowcount
        cursor.execute("DELETE FROM software_policy_logs WHERE policy_id = %s", (policy_id,))
        deleted_logs = cursor.rowcount
        cursor.execute("DELETE FROM software_policies WHERE id = %s", (policy_id,))
        conn.commit()

        return {
            "message": "策略删除成功",
            "policy_id": policy_id,
            "policy_name": policy["policy_name"],
            "deleted_rules": deleted_rules,
            "deleted_logs": deleted_logs,
        }

    except Error as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        cursor.close()
        conn.close()

@app.get("/api/v1/policies/check/{asset_id}")
def check_policies(
    asset_id: int,
    software_name: Optional[str] = Query(default=None),
    vendor: Optional[str] = Query(default=None),
    category: Optional[str] = Query(default=None),
    package_id: Optional[str] = Query(default=None),
):
    """检查软件是否符合策略（供Agent调用）"""
    conn = get_db_connection()
    if not conn:
        raise HTTPException(status_code=500, detail="Database connection failed")

    try:
        cursor = conn.cursor(dictionary=True)

        asset = get_asset_context(cursor, asset_id)
        if not asset or asset.get("deleted_at") is not None:
            raise HTTPException(status_code=404, detail="资产不存在")

        cursor.execute(
            """
            SELECT *
            FROM software_policies
            WHERE enabled = 1
            ORDER BY priority DESC, id DESC
            """
        )
        policies = cursor.fetchall()

        software_context = build_software_match_context(
            software_name=software_name,
            vendor=vendor,
            category=category,
            package_id=package_id,
        )

        result = {
            "allowed": True,
            "blocked_by": None,
            "force_install": []
        }
        force_install_seen = set()

        for policy in policies:
            if not policy_applies_to_asset(policy, asset):
                continue

            rules = get_policy_rules(cursor, policy["id"])

            # 检查黑名单
            if policy['policy_type'] == 'blacklist':
                for rule in rules:
                    if policy_rule_matches_software(rule, software_context):
                        result['allowed'] = False
                        result['blocked_by'] = policy['policy_name']
                        break
                if not result['allowed']:
                    break

            # 检查白名单
            elif policy['policy_type'] == 'whitelist':
                matched = any(policy_rule_matches_software(rule, software_context) for rule in rules)
                if not matched:
                    result['allowed'] = False
                    result['blocked_by'] = policy['policy_name']
                    break

            # 强制安装
            elif policy['policy_type'] == 'force_install':
                for rule in rules:
                    package = resolve_package_from_rule(cursor, rule)
                    if not package:
                        continue

                    package_name = (
                        package.get('display_name')
                        or package.get('package_name')
                        or str(rule.get('rule_value') or '').strip()
                    )
                    normalized_name = package_name.strip().lower()
                    if package_name and normalized_name not in force_install_seen:
                        force_install_seen.add(normalized_name)
                        result['force_install'].append(package_name)

        return result

    except Error as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        cursor.close()
        conn.close()


@app.post("/api/v1/policies/logs")
def create_policy_log(payload: PolicyLogCreate):
    """写入策略执行日志"""
    conn = get_db_connection()
    if not conn:
        raise HTTPException(status_code=500, detail="Database connection failed")

    try:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO software_policy_logs (
                policy_id, asset_id, software_name, action, result, message
            ) VALUES (%s, %s, %s, %s, %s, %s)
        """, (
            payload.policy_id or 0,
            payload.asset_id,
            payload.software_name,
            payload.action,
            payload.result,
            payload.message,
        ))
        conn.commit()
        return {"message": "Policy log created successfully", "id": cursor.lastrowid}
    except Error as e:
        conn.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        cursor.close()
        conn.close()

if __name__ == "__main__":
    import uvicorn
    print("[Policy] Service startup begin")
    print("[Policy] API docs: http://localhost:8082/docs")
    uvicorn.run(app, host="0.0.0.0", port=8082)
