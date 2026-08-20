-- ============================================================
-- Z-View 远程桌面会话表
-- 版本: v1.0 (第一阶段)
-- 不修改现有任何表，审计复用 system_activity_logs
-- ============================================================

USE cmdb;

CREATE TABLE IF NOT EXISTS remote_sessions (
    id BIGINT PRIMARY KEY AUTO_INCREMENT,
    session_token VARCHAR(64) NOT NULL COMMENT '短期会话令牌',
    asset_id BIGINT UNSIGNED NOT NULL COMMENT '目标终端ID',
    admin_user VARCHAR(100) NOT NULL COMMENT '操作管理员',
    agent_id VARCHAR(255) COMMENT 'Agent标识(hostname)',
    status ENUM('created','connecting','connected','disconnected','failed') NOT NULL DEFAULT 'created',
    client_ip VARCHAR(45) COMMENT '管理员客户端IP',
    disconnect_reason VARCHAR(255) COMMENT '断开原因',
    max_duration_sec INT DEFAULT 7200 COMMENT '最大会话时长(秒)',
    fps_limit INT DEFAULT 20 COMMENT 'FPS上限',
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    connected_at DATETIME COMMENT '连接成功时间',
    disconnected_at DATETIME COMMENT '断开时间',

    UNIQUE KEY uk_token (session_token),
    INDEX idx_asset (asset_id),
    INDEX idx_status (status),
    INDEX idx_admin (admin_user),
    FOREIGN KEY (asset_id) REFERENCES assets(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='远程桌面会话';