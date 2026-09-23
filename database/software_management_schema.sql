-- ============================================================
-- CMDB软件管理中心 - 数据库表结构
-- 版本: v1.0
-- 创建日期: 2026-06-12
-- ============================================================

USE cmdb;

-- ============================================================
-- 1. 软件包仓库表
-- ============================================================

-- 软件包主表
CREATE TABLE IF NOT EXISTS software_packages (
    id BIGINT PRIMARY KEY AUTO_INCREMENT,
    package_name VARCHAR(255) NOT NULL COMMENT '软件包名称',
    display_name VARCHAR(255) NOT NULL COMMENT '显示名称',
    version VARCHAR(100) NOT NULL COMMENT '版本号',
    category VARCHAR(50) NOT NULL COMMENT '分类：office/dev/security/other',
    vendor VARCHAR(255) COMMENT '厂商',
    description TEXT COMMENT '描述',
    file_name VARCHAR(255) NOT NULL COMMENT '文件名',
    file_size BIGINT NOT NULL COMMENT '文件大小（字节）',
    file_path VARCHAR(500) NOT NULL COMMENT '存储路径',
    file_hash VARCHAR(64) NOT NULL COMMENT 'SHA256哈希值',
    signature_hash VARCHAR(64) COMMENT '数字签名哈希',
    install_command TEXT COMMENT '安装命令（静默安装）',
    uninstall_command TEXT COMMENT '卸载命令（静默卸载）',
    install_args TEXT COMMENT '安装参数（JSON格式）',
    requires_reboot BOOLEAN DEFAULT FALSE COMMENT '是否需要重启',
    architecture ENUM('x86', 'x64', 'arm', 'all') DEFAULT 'all' COMMENT '架构',
    supported_os VARCHAR(255) COMMENT '支持的操作系统',
    min_os_version VARCHAR(50) COMMENT '最低操作系统版本',
    dependencies TEXT COMMENT '依赖软件（JSON数组）',
    upload_by VARCHAR(100) COMMENT '上传人',
    status ENUM('pending', 'verified', 'available', 'deprecated', 'deleted') DEFAULT 'pending' COMMENT '状态',
    download_count INT DEFAULT 0 COMMENT '下载次数',
    install_count INT DEFAULT 0 COMMENT '安装次数',
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    deleted_at DATETIME COMMENT '软删除时间',

    INDEX idx_name (package_name),
    INDEX idx_version (version),
    INDEX idx_category (category),
    INDEX idx_status (status),
    INDEX idx_created (created_at),
    UNIQUE KEY uk_name_version (package_name, version)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='软件包仓库表';

-- 软件包分片表（支持大文件分片上传）
CREATE TABLE IF NOT EXISTS software_package_chunks (
    id BIGINT PRIMARY KEY AUTO_INCREMENT,
    package_id BIGINT NOT NULL COMMENT '软件包ID',
    chunk_index INT NOT NULL COMMENT '分片索引',
    chunk_hash VARCHAR(64) NOT NULL COMMENT '分片哈希',
    chunk_size INT NOT NULL COMMENT '分片大小',
    chunk_path VARCHAR(500) NOT NULL COMMENT '分片存储路径',
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,

    INDEX idx_package (package_id),
    UNIQUE KEY uk_package_chunk (package_id, chunk_index),
    FOREIGN KEY (package_id) REFERENCES software_packages(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='软件包分片表';

-- ============================================================
-- 2. 软件策略表
-- ============================================================

-- 软件黑名单表
CREATE TABLE IF NOT EXISTS software_blacklist (
    id BIGINT PRIMARY KEY AUTO_INCREMENT,
    rule_name VARCHAR(255) NOT NULL COMMENT '规则名称',
    match_type ENUM('exact', 'regex', 'wildcard') DEFAULT 'exact' COMMENT '匹配类型',
    software_name VARCHAR(255) NOT NULL COMMENT '软件名称（支持通配符）',
    version_range VARCHAR(100) COMMENT '版本范围（如：>=1.0.0,<2.0.0）',
    vendor VARCHAR(255) COMMENT '厂商',
    action ENUM('alert', 'block', 'uninstall') DEFAULT 'alert' COMMENT '动作',
    reason TEXT COMMENT '禁止原因',
    severity ENUM('low', 'medium', 'high', 'critical') DEFAULT 'medium' COMMENT '严重级别',
    enabled BOOLEAN DEFAULT TRUE COMMENT '是否启用',
    apply_to_groups TEXT COMMENT '应用到的分组（JSON数组）',
    apply_to_assets TEXT COMMENT '应用到的资产（JSON数组）',
    created_by VARCHAR(100) COMMENT '创建人',
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,

    INDEX idx_software_name (software_name),
    INDEX idx_enabled (enabled),
    INDEX idx_action (action)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='软件黑名单表';

-- 软件白名单表
CREATE TABLE IF NOT EXISTS software_whitelist (
    id BIGINT PRIMARY KEY AUTO_INCREMENT,
    rule_name VARCHAR(255) NOT NULL COMMENT '规则名称',
    match_type ENUM('exact', 'regex', 'wildcard') DEFAULT 'exact' COMMENT '匹配类型',
    software_name VARCHAR(255) NOT NULL COMMENT '软件名称',
    version_range VARCHAR(100) COMMENT '版本范围',
    vendor VARCHAR(255) COMMENT '厂商',
    file_hash VARCHAR(64) COMMENT '文件哈希（更精确）',
    description TEXT COMMENT '描述',
    enabled BOOLEAN DEFAULT TRUE COMMENT '是否启用',
    apply_to_groups TEXT COMMENT '应用到的分组（JSON数组）',
    apply_to_assets TEXT COMMENT '应用到的资产（JSON数组）',
    created_by VARCHAR(100) COMMENT '创建人',
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,

    INDEX idx_software_name (software_name),
    INDEX idx_enabled (enabled),
    INDEX idx_hash (file_hash)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='软件白名单表';

-- 强制安装策略表
CREATE TABLE IF NOT EXISTS software_install_policies (
    id BIGINT PRIMARY KEY AUTO_INCREMENT,
    policy_name VARCHAR(255) NOT NULL COMMENT '策略名称',
    package_id BIGINT NOT NULL COMMENT '软件包ID',
    target_version VARCHAR(100) COMMENT '目标版本（null=最新版本）',
    enforce_type ENUM('mandatory', 'optional', 'recommended') DEFAULT 'mandatory' COMMENT '强制类型',
    install_deadline DATETIME COMMENT '安装截止日期',
    auto_upgrade BOOLEAN DEFAULT FALSE COMMENT '自动升级',
    check_interval INT DEFAULT 3600 COMMENT '检查间隔（秒）',
    enabled BOOLEAN DEFAULT TRUE COMMENT '是否启用',
    apply_to_groups TEXT COMMENT '应用到的分组（JSON数组）',
    apply_to_assets TEXT COMMENT '应用到的资产（JSON数组）',
    created_by VARCHAR(100) COMMENT '创建人',
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,

    INDEX idx_package (package_id),
    INDEX idx_enabled (enabled),
    FOREIGN KEY (package_id) REFERENCES software_packages(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='强制安装策略表';

-- ============================================================
-- 3. 任务管理表
-- ============================================================

-- 软件任务表
CREATE TABLE IF NOT EXISTS software_tasks (
    id BIGINT PRIMARY KEY AUTO_INCREMENT,
    task_name VARCHAR(255) NOT NULL COMMENT '任务名称',
    task_type ENUM('install', 'uninstall', 'upgrade', 'check') NOT NULL COMMENT '任务类型',
    package_id BIGINT COMMENT '软件包ID（卸载时可为空）',
    target_version VARCHAR(100) COMMENT '目标版本',
    software_name VARCHAR(255) COMMENT '软件名称（用于卸载）',
    schedule_type ENUM('immediate', 'scheduled', 'recurring') DEFAULT 'immediate' COMMENT '调度类型',
    scheduled_time DATETIME COMMENT '计划执行时间',
    recurring_rule VARCHAR(255) COMMENT '重复规则（cron表达式）',
    target_type ENUM('asset', 'group', 'all') NOT NULL COMMENT '目标类型',
    target_ids TEXT COMMENT '目标ID列表（JSON数组）',
    target_count INT DEFAULT 0 COMMENT '目标数量',
    priority ENUM('low', 'normal', 'high', 'urgent') DEFAULT 'normal' COMMENT '优先级',
    retry_count INT DEFAULT 3 COMMENT '重试次数',
    retry_interval INT DEFAULT 300 COMMENT '重试间隔（秒）',
    timeout INT DEFAULT 3600 COMMENT '超时时间（秒）',
    options TEXT COMMENT '任务选项（JSON格式）',
    status ENUM('pending', 'running', 'completed', 'failed', 'cancelled', 'paused') DEFAULT 'pending' COMMENT '状态',
    progress INT DEFAULT 0 COMMENT '进度（0-100）',
    success_count INT DEFAULT 0 COMMENT '成功数量',
    failed_count INT DEFAULT 0 COMMENT '失败数量',
    running_count INT DEFAULT 0 COMMENT '运行中数量',
    start_time DATETIME COMMENT '开始时间',
    end_time DATETIME COMMENT '结束时间',
    created_by VARCHAR(100) COMMENT '创建人',
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,

    INDEX idx_task_type (task_type),
    INDEX idx_status (status),
    INDEX idx_scheduled_time (scheduled_time),
    INDEX idx_created (created_at),
    INDEX idx_package (package_id),
    FOREIGN KEY (package_id) REFERENCES software_packages(id) ON DELETE SET NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='软件任务表';

-- 任务执行结果表
CREATE TABLE IF NOT EXISTS software_task_results (
    id BIGINT PRIMARY KEY AUTO_INCREMENT,
    task_id BIGINT NOT NULL COMMENT '任务ID',
    asset_id BIGINT NOT NULL COMMENT '资产ID',
    status ENUM('pending', 'downloading', 'installing', 'success', 'failed', 'timeout', 'cancelled') DEFAULT 'pending' COMMENT '状态',
    progress INT DEFAULT 0 COMMENT '进度（0-100）',
    download_progress INT DEFAULT 0 COMMENT '下载进度（0-100）',
    install_progress INT DEFAULT 0 COMMENT '安装进度（0-100）',
    download_speed BIGINT DEFAULT 0 COMMENT '下载速度（字节/秒）',
    downloaded_size BIGINT DEFAULT 0 COMMENT '已下载大小',
    retry_count INT DEFAULT 0 COMMENT '重试次数',
    error_code VARCHAR(50) COMMENT '错误代码',
    error_message TEXT COMMENT '错误信息',
    stdout_log TEXT COMMENT '标准输出日志',
    stderr_log TEXT COMMENT '标准错误日志',
    start_time DATETIME COMMENT '开始时间',
    end_time DATETIME COMMENT '结束时间',
    duration INT COMMENT '耗时（秒）',
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,

    INDEX idx_task (task_id),
    INDEX idx_asset (asset_id),
    INDEX idx_status (status),
    INDEX idx_created (created_at),
    FOREIGN KEY (task_id) REFERENCES software_tasks(id) ON DELETE CASCADE,
    FOREIGN KEY (asset_id) REFERENCES assets(id) ON DELETE CASCADE,
    UNIQUE KEY uk_task_asset (task_id, asset_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='任务执行结果表';

-- ============================================================
-- 4. 合规检查表
-- ============================================================

-- 软件合规检查规则表
CREATE TABLE IF NOT EXISTS software_compliance_checks (
    id BIGINT PRIMARY KEY AUTO_INCREMENT,
    check_name VARCHAR(255) NOT NULL COMMENT '检查名称',
    check_type ENUM('required', 'forbidden', 'version', 'license') NOT NULL COMMENT '检查类型',
    software_name VARCHAR(255) NOT NULL COMMENT '软件名称',
    required_version VARCHAR(100) COMMENT '要求版本',
    severity ENUM('low', 'medium', 'high', 'critical') DEFAULT 'medium' COMMENT '严重级别',
    enabled BOOLEAN DEFAULT TRUE COMMENT '是否启用',
    apply_to_groups TEXT COMMENT '应用到的分组（JSON数组）',
    created_by VARCHAR(100) COMMENT '创建人',
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,

    INDEX idx_check_type (check_type),
    INDEX idx_enabled (enabled),
    INDEX idx_software_name (software_name)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='软件合规检查表';

-- 合规检查结果表
CREATE TABLE IF NOT EXISTS software_compliance_results (
    id BIGINT PRIMARY KEY AUTO_INCREMENT,
    check_id BIGINT NOT NULL COMMENT '检查规则ID',
    asset_id BIGINT NOT NULL COMMENT '资产ID',
    is_compliant BOOLEAN DEFAULT FALSE COMMENT '是否合规',
    current_version VARCHAR(100) COMMENT '当前版本',
    expected_version VARCHAR(100) COMMENT '期望版本',
    details TEXT COMMENT '详情',
    checked_at DATETIME DEFAULT CURRENT_TIMESTAMP COMMENT '检查时间',

    INDEX idx_check (check_id),
    INDEX idx_asset (asset_id),
    INDEX idx_compliant (is_compliant),
    INDEX idx_checked_at (checked_at),
    FOREIGN KEY (check_id) REFERENCES software_compliance_checks(id) ON DELETE CASCADE,
    FOREIGN KEY (asset_id) REFERENCES assets(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='合规检查结果表';

-- ============================================================
-- 5. 审计日志表
-- ============================================================

-- 软件管理审计日志表
CREATE TABLE IF NOT EXISTS software_audit_logs (
    id BIGINT PRIMARY KEY AUTO_INCREMENT,
    operation_type ENUM('upload', 'delete', 'deploy', 'uninstall', 'policy_create', 'policy_update', 'policy_delete') NOT NULL COMMENT '操作类型',
    target_type VARCHAR(50) COMMENT '目标类型',
    target_id BIGINT COMMENT '目标ID',
    target_name VARCHAR(255) COMMENT '目标名称',
    operation_details TEXT COMMENT '操作详情（JSON格式）',
    operator VARCHAR(100) NOT NULL COMMENT '操作人',
    operator_ip VARCHAR(50) COMMENT '操作IP',
    result ENUM('success', 'failed') DEFAULT 'success' COMMENT '结果',
    error_message TEXT COMMENT '错误信息',
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,

    INDEX idx_operation_type (operation_type),
    INDEX idx_operator (operator),
    INDEX idx_created (created_at),
    INDEX idx_result (result)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='软件管理审计日志表';
