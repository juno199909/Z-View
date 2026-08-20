-- ============================================================
-- Agent 设备凭据（一机一密，P0-01）
-- 由 assets_api 启动时幂等创建，此文件用于全新部署参考
-- ============================================================

USE cmdb;

CREATE TABLE IF NOT EXISTS agent_credentials (
    asset_id BIGINT UNSIGNED PRIMARY KEY COMMENT '资产ID（即 agent_id）',
    secret_hash CHAR(64) NOT NULL COMMENT 'SHA256(zv1:{asset_id}:{secret}:{pepper})',
    status ENUM('active','revoked') NOT NULL DEFAULT 'active' COMMENT '凭据状态',
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP COMMENT '签发时间',
    last_used_at DATETIME NULL COMMENT '最近使用时间',
    CONSTRAINT fk_agent_credentials_asset
        FOREIGN KEY (asset_id) REFERENCES assets(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='Agent设备凭据';
