-- ============================================================
-- Z-View 终端安全管理 - 数据库表结构
-- 版本: v1.0
-- 创建日期: 2026-08-31
-- 说明: 独立于现有 alerts 表，不修改任何现有表
-- ============================================================

USE cmdb;

-- ============================================================
-- 1. 安全策略中心
-- ============================================================

-- 安全策略主表（多策略/版本/优先级）
CREATE TABLE IF NOT EXISTS security_policies (
    id BIGINT PRIMARY KEY AUTO_INCREMENT,
    policy_name VARCHAR(255) NOT NULL COMMENT '策略名称',
    policy_type ENUM('firewall','usb','app_control','file_protect','behavior') NOT NULL COMMENT '策略类型',
    description TEXT COMMENT '描述',
    enabled BOOLEAN DEFAULT TRUE COMMENT '是否启用',
    priority INT DEFAULT 0 COMMENT '优先级（数值越大越高）',
    version INT DEFAULT 1 COMMENT '当前版本',
    config_json LONGTEXT NOT NULL COMMENT '策略规则配置JSON',
    created_by VARCHAR(100) COMMENT '创建人',
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,

    INDEX idx_type_enabled (policy_type, enabled),
    INDEX idx_priority (priority),
    INDEX idx_name (policy_name)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='安全策略主表';

-- 策略版本历史（回滚用）
CREATE TABLE IF NOT EXISTS security_policy_versions (
    id BIGINT PRIMARY KEY AUTO_INCREMENT,
    policy_id BIGINT NOT NULL COMMENT '策略ID',
    version INT NOT NULL COMMENT '版本号',
    config_json LONGTEXT NOT NULL COMMENT '该版本配置',
    changed_by VARCHAR(100) COMMENT '变更人',
    change_note VARCHAR(500) COMMENT '变更说明',
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,

    INDEX idx_policy (policy_id),
    UNIQUE KEY uk_policy_version (policy_id, version),
    FOREIGN KEY (policy_id) REFERENCES security_policies(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='安全策略版本历史';

-- 策略绑定（全局/组/终端三级）
CREATE TABLE IF NOT EXISTS security_policy_bindings (
    id BIGINT PRIMARY KEY AUTO_INCREMENT,
    policy_id BIGINT NOT NULL COMMENT '策略ID',
    scope_type ENUM('global','group','asset') NOT NULL COMMENT '绑定范围',
    scope_id BIGINT NULL COMMENT '范围ID（group_id/asset_id，global为NULL）',
    enabled BOOLEAN DEFAULT TRUE COMMENT '是否启用',
    created_by VARCHAR(100) COMMENT '创建人',
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,

    INDEX idx_policy (policy_id),
    INDEX idx_scope (scope_type, scope_id),
    FOREIGN KEY (policy_id) REFERENCES security_policies(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='安全策略绑定';

-- 策略执行结果
CREATE TABLE IF NOT EXISTS security_policy_exec_results (
    id BIGINT PRIMARY KEY AUTO_INCREMENT,
    policy_id BIGINT NOT NULL COMMENT '策略ID',
    asset_id BIGINT UNSIGNED NOT NULL COMMENT '资产ID',
    scope_type VARCHAR(20) COMMENT '下发范围',
    status ENUM('pending','success','failed','partial') NOT NULL DEFAULT 'pending',
    applied_rules INT DEFAULT 0 COMMENT '已应用规则数',
    failed_rules INT DEFAULT 0 COMMENT '失败规则数',
    error_detail TEXT COMMENT '错误详情',
    executed_at DATETIME COMMENT '执行时间',
    reported_at DATETIME COMMENT '上报时间',
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,

    INDEX idx_policy_asset (policy_id, asset_id),
    INDEX idx_status (status),
    INDEX idx_asset (asset_id),
    FOREIGN KEY (policy_id) REFERENCES security_policies(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='安全策略执行结果';

-- ============================================================
-- 2. 安全事件中心
-- ============================================================

CREATE TABLE IF NOT EXISTS security_events (
    id BIGINT PRIMARY KEY AUTO_INCREMENT,
    asset_id BIGINT UNSIGNED NOT NULL COMMENT '终端ID',
    event_type ENUM('virus','suspicious_process','usb','firewall','app_control',
                    'file_anomaly','registry_anomaly','network_anomaly','policy_exec') NOT NULL COMMENT '事件类型',
    severity ENUM('info','low','medium','high','critical') NOT NULL COMMENT '风险等级',
    title VARCHAR(255) COMMENT '事件标题',
    description TEXT COMMENT '事件描述',
    process_name VARCHAR(255) COMMENT '进程名',
    process_pid INT COMMENT '进程PID',
    file_path VARCHAR(500) COMMENT '文件路径',
    remote_ip VARCHAR(45) COMMENT '远程IP',
    remote_port INT COMMENT '远程端口',
    details_json LONGTEXT COMMENT '附加详情JSON',
    status ENUM('open','processing','resolved','ignored') DEFAULT 'open' COMMENT '处置状态',
    handler VARCHAR(100) COMMENT '处置人',
    handle_note TEXT COMMENT '处置备注',
    handled_at DATETIME COMMENT '处置时间',
    occurred_at DATETIME NOT NULL COMMENT '发生时间',
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,

    INDEX idx_asset_time (asset_id, occurred_at),
    INDEX idx_type_severity (event_type, severity),
    INDEX idx_status (status),
    INDEX idx_occurred (occurred_at),
    FOREIGN KEY (asset_id) REFERENCES assets(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='安全事件中心';

-- ============================================================
-- 3. USB 管控
-- ============================================================

-- USB 设备台账
CREATE TABLE IF NOT EXISTS usb_devices (
    id BIGINT PRIMARY KEY AUTO_INCREMENT,
    asset_id BIGINT UNSIGNED NOT NULL COMMENT '终端ID',
    device_id VARCHAR(255) COMMENT '设备实例ID',
    vid_pid VARCHAR(20) COMMENT 'VID/PID',
    serial_number VARCHAR(255) COMMENT '序列号',
    device_class VARCHAR(50) COMMENT '设备类(USBStorage/HID/Net/MTP)',
    friendly_name VARCHAR(255) COMMENT '友好名称',
    manufacturer VARCHAR(255) COMMENT '厂商',
    first_seen DATETIME COMMENT '首次发现',
    last_seen DATETIME COMMENT '最后发现',
    status ENUM('allowed','blocked','unknown') DEFAULT 'unknown' COMMENT '状态',

    INDEX idx_asset (asset_id),
    INDEX idx_vidpid (vid_pid),
    INDEX idx_device_id (device_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='USB设备台账';

-- USB 插拔日志
CREATE TABLE IF NOT EXISTS usb_events (
    id BIGINT PRIMARY KEY AUTO_INCREMENT,
    asset_id BIGINT UNSIGNED NOT NULL COMMENT '终端ID',
    device_id VARCHAR(255) COMMENT '设备实例ID',
    vid_pid VARCHAR(20) COMMENT 'VID/PID',
    event_type ENUM('insert','remove','blocked','allowed') NOT NULL COMMENT '事件类型',
    device_class VARCHAR(50) COMMENT '设备类',
    friendly_name VARCHAR(255) COMMENT '友好名称',
    details_json TEXT COMMENT '附加详情',
    occurred_at DATETIME NOT NULL COMMENT '发生时间',
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,

    INDEX idx_asset_time (asset_id, occurred_at),
    INDEX idx_event_type (event_type),
    INDEX idx_device_id (device_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='USB插拔日志';

-- ============================================================
-- 4. 程序管控
-- ============================================================

-- 进程启动日志
CREATE TABLE IF NOT EXISTS process_launch_logs (
    id BIGINT PRIMARY KEY AUTO_INCREMENT,
    asset_id BIGINT UNSIGNED NOT NULL COMMENT '终端ID',
    process_name VARCHAR(255) COMMENT '进程名',
    pid INT COMMENT '进程PID',
    ppid INT COMMENT '父进程PID',
    path VARCHAR(500) COMMENT '可执行文件路径',
    cmd_line TEXT COMMENT '命令行',
    user VARCHAR(100) COMMENT '运行用户',
    md5 VARCHAR(32) COMMENT '文件MD5',
    matched_policy VARCHAR(255) COMMENT '命中策略名',
    action ENUM('allowed','blocked','alerted') DEFAULT 'allowed' COMMENT '处置动作',
    launched_at DATETIME NOT NULL COMMENT '启动时间',
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,

    INDEX idx_asset_time (asset_id, launched_at),
    INDEX idx_process_name (process_name),
    INDEX idx_action (action)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='进程启动日志';

-- ============================================================
-- 5. 文件保护
-- ============================================================

-- 文件保护目录基线
CREATE TABLE IF NOT EXISTS file_protect_baselines (
    id BIGINT PRIMARY KEY AUTO_INCREMENT,
    asset_id BIGINT UNSIGNED NOT NULL COMMENT '终端ID',
    dir_path VARCHAR(500) NOT NULL COMMENT '保护目录',
    file_path VARCHAR(500) NOT NULL COMMENT '文件相对路径',
    file_size BIGINT COMMENT '文件大小',
    md5 VARCHAR(32) COMMENT '文件MD5',
    baseline_at DATETIME COMMENT '基线建立时间',
    INDEX idx_asset_dir (asset_id, dir_path),
    INDEX idx_file_path (file_path)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='文件保护基线';

-- 文件异常事件
CREATE TABLE IF NOT EXISTS file_anomaly_events (
    id BIGINT PRIMARY KEY AUTO_INCREMENT,
    asset_id BIGINT UNSIGNED NOT NULL COMMENT '终端ID',
    dir_path VARCHAR(500) COMMENT '保护目录',
    file_path VARCHAR(500) NOT NULL COMMENT '异常文件路径',
    anomaly_type ENUM('modified','created','deleted','renamed','mass_change') NOT NULL COMMENT '异常类型',
    old_md5 VARCHAR(32) COMMENT '原MD5',
    new_md5 VARCHAR(32) COMMENT '新MD5',
    process_name VARCHAR(255) COMMENT '触发进程',
    details_json TEXT COMMENT '附加详情',
    occurred_at DATETIME NOT NULL COMMENT '发生时间',
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_asset_time (asset_id, occurred_at),
    INDEX idx_dir (dir_path),
    INDEX idx_anomaly_type (anomaly_type)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='文件异常事件';