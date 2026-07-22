-- 软件策略表
CREATE TABLE IF NOT EXISTS software_policies (
    id INT PRIMARY KEY AUTO_INCREMENT,
    policy_name VARCHAR(200) NOT NULL COMMENT '策略名称',
    policy_type ENUM('blacklist', 'whitelist', 'force_install') NOT NULL COMMENT '策略类型：黑名单/白名单/强制安装',
    description TEXT COMMENT '策略描述',
    enabled TINYINT(1) DEFAULT 1 COMMENT '是否启用',
    priority INT DEFAULT 0 COMMENT '优先级，数字越大优先级越高',
    target_type ENUM('all', 'group', 'asset') DEFAULT 'all' COMMENT '目标类型',
    target_ids JSON COMMENT '目标ID列表',
    created_by VARCHAR(50) DEFAULT 'admin',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    INDEX idx_policy_type (policy_type),
    INDEX idx_enabled (enabled)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='软件策略表';

-- 策略规则表
CREATE TABLE IF NOT EXISTS software_policy_rules (
    id INT PRIMARY KEY AUTO_INCREMENT,
    policy_id INT NOT NULL COMMENT '策略ID',
    rule_type ENUM('software_name', 'package_id', 'vendor', 'category') DEFAULT 'software_name' COMMENT '规则类型',
    rule_value VARCHAR(500) NOT NULL COMMENT '规则值',
    match_type ENUM('exact', 'contains', 'regex') DEFAULT 'contains' COMMENT '匹配类型：精确/包含/正则',
    action ENUM('allow', 'deny', 'force') DEFAULT 'deny' COMMENT '动作：允许/拒绝/强制',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (policy_id) REFERENCES software_policies(id) ON DELETE CASCADE,
    INDEX idx_policy_id (policy_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='策略规则表';

-- 策略执行日志表
CREATE TABLE IF NOT EXISTS software_policy_logs (
    id INT PRIMARY KEY AUTO_INCREMENT,
    policy_id INT NOT NULL COMMENT '策略ID',
    asset_id INT NOT NULL COMMENT '资产ID',
    software_name VARCHAR(200) COMMENT '软件名称',
    action VARCHAR(50) COMMENT '执行动作',
    result ENUM('success', 'failed', 'blocked') COMMENT '执行结果',
    message TEXT COMMENT '执行消息',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_policy_id (policy_id),
    INDEX idx_asset_id (asset_id),
    INDEX idx_created_at (created_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='策略执行日志表';
