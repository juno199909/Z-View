-- 添加size_mb字段到asset_software表
ALTER TABLE asset_software ADD COLUMN size_mb DECIMAL(10,2) DEFAULT 0 COMMENT '软件大小(MB)';

-- 添加category字段到asset_software表
ALTER TABLE asset_software ADD COLUMN category VARCHAR(100) NULL COMMENT '软件分类' AFTER vendor;
