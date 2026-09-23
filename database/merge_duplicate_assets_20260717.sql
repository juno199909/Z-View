-- 存量重复资产合并脚本
-- 适用数据库: cmdb
-- 生成日期: 2026-07-17
-- 目标:
--   Brain: 29 -> 12
--   Juno: 18 -> 25, 20 -> 25
--   DESKTOP-HPD0GJ7: 24 -> 48
--
-- 执行前建议:
-- 1. 确认 8080 后端已运行包含“恢复已删除资产”逻辑的新版本
-- 2. 在低峰期执行
-- 3. 先在测试库或备份库演练
-- 4. 已按 2026-07-17 当前线上 cmdb 实际表结构复核：
--    - asset_software 包含 install_path / updated_at / size / license_status / size_mb
--    - software_inventory 当前无唯一键，直接 UPDATE asset_id 是安全的
--    - network_interfaces 唯一键为 (asset_id, if_index)
--    - software_task_results 唯一键为 (task_id, asset_id)

USE cmdb;

-- 1) 持久化备份表
-- 注意: MySQL 的 CREATE TABLE 会触发隐式提交，因此备份表创建放在事务外。
-- 注意: CREATE TABLE ... LIKE 在当前 MySQL 下不会复制外键关系，这里正是我们需要的“静态备份表”行为。
CREATE TABLE IF NOT EXISTS backup_20260717_assets LIKE assets;
INSERT IGNORE INTO backup_20260717_assets
SELECT * FROM assets WHERE id IN (12, 18, 20, 24, 25, 29, 48);

CREATE TABLE IF NOT EXISTS backup_20260717_agent_heartbeat LIKE agent_heartbeat;
INSERT IGNORE INTO backup_20260717_agent_heartbeat
SELECT * FROM agent_heartbeat WHERE asset_id IN (12, 18, 20, 24, 25, 29, 48);

CREATE TABLE IF NOT EXISTS backup_20260717_agent_tokens LIKE agent_tokens;
INSERT IGNORE INTO backup_20260717_agent_tokens
SELECT * FROM agent_tokens WHERE asset_id IN (12, 18, 20, 24, 25, 29, 48);

CREATE TABLE IF NOT EXISTS backup_20260717_alert_records LIKE alert_records;
INSERT IGNORE INTO backup_20260717_alert_records
SELECT * FROM alert_records WHERE asset_id IN (12, 18, 20, 24, 25, 29, 48);

CREATE TABLE IF NOT EXISTS backup_20260717_alerts LIKE alerts;
INSERT IGNORE INTO backup_20260717_alerts
SELECT * FROM alerts WHERE asset_id IN (12, 18, 20, 24, 25, 29, 48);

CREATE TABLE IF NOT EXISTS backup_20260717_asset_changes LIKE asset_changes;
INSERT IGNORE INTO backup_20260717_asset_changes
SELECT * FROM asset_changes WHERE asset_id IN (12, 18, 20, 24, 25, 29, 48);

CREATE TABLE IF NOT EXISTS backup_20260717_asset_software LIKE asset_software;
INSERT IGNORE INTO backup_20260717_asset_software
SELECT * FROM asset_software WHERE asset_id IN (12, 18, 20, 24, 25, 29, 48);

CREATE TABLE IF NOT EXISTS backup_20260717_batch_operation_results LIKE batch_operation_results;
INSERT IGNORE INTO backup_20260717_batch_operation_results
SELECT * FROM batch_operation_results WHERE asset_id IN (12, 18, 20, 24, 25, 29, 48);

CREATE TABLE IF NOT EXISTS backup_20260717_network_interfaces LIKE network_interfaces;
INSERT IGNORE INTO backup_20260717_network_interfaces
SELECT * FROM network_interfaces WHERE asset_id IN (12, 18, 20, 24, 25, 29, 48);

CREATE TABLE IF NOT EXISTS backup_20260717_raw_data LIKE raw_data;
INSERT IGNORE INTO backup_20260717_raw_data
SELECT * FROM raw_data WHERE asset_id IN (12, 18, 20, 24, 25, 29, 48);

CREATE TABLE IF NOT EXISTS backup_20260717_software_compliance_results LIKE software_compliance_results;
INSERT IGNORE INTO backup_20260717_software_compliance_results
SELECT * FROM software_compliance_results WHERE asset_id IN (12, 18, 20, 24, 25, 29, 48);

CREATE TABLE IF NOT EXISTS backup_20260717_software_inventory LIKE software_inventory;
INSERT IGNORE INTO backup_20260717_software_inventory
SELECT * FROM software_inventory WHERE asset_id IN (12, 18, 20, 24, 25, 29, 48);

CREATE TABLE IF NOT EXISTS backup_20260717_software_policy_logs LIKE software_policy_logs;
INSERT IGNORE INTO backup_20260717_software_policy_logs
SELECT * FROM software_policy_logs WHERE asset_id IN (12, 18, 20, 24, 25, 29, 48);

CREATE TABLE IF NOT EXISTS backup_20260717_software_task_results LIKE software_task_results;
INSERT IGNORE INTO backup_20260717_software_task_results
SELECT * FROM software_task_results WHERE asset_id IN (12, 18, 20, 24, 25, 29, 48);

CREATE TABLE IF NOT EXISTS backup_20260717_system_activity_logs LIKE system_activity_logs;
INSERT IGNORE INTO backup_20260717_system_activity_logs
SELECT * FROM system_activity_logs WHERE asset_id IN (12, 18, 20, 24, 25, 29, 48);

DROP PROCEDURE IF EXISTS merge_duplicate_assets_20260717;

DELIMITER $$

CREATE PROCEDURE merge_duplicate_assets_20260717()
BEGIN
    DECLARE v_count INT DEFAULT 0;

    DECLARE EXIT HANDLER FOR SQLEXCEPTION
    BEGIN
        ROLLBACK;
        RESIGNAL;
    END;

    -- 2) 前置校验
    SELECT COUNT(*)
      INTO v_count
      FROM assets
     WHERE id IN (12, 25, 48)
       AND deleted_at IS NULL;
    IF v_count <> 3 THEN
        SIGNAL SQLSTATE '45000'
            SET MESSAGE_TEXT = 'Precheck failed: keep assets (12,25,48) must exist and be active.';
    END IF;

    SELECT COUNT(*)
      INTO v_count
      FROM assets
     WHERE id IN (18, 20, 24, 29);
    IF v_count <> 4 THEN
        SIGNAL SQLSTATE '45000'
            SET MESSAGE_TEXT = 'Precheck failed: old asset ids (18,20,24,29) must all exist.';
    END IF;

    SELECT COUNT(*)
      INTO v_count
      FROM software_task_results old_rows
      JOIN software_task_results keep_rows
        ON keep_rows.task_id = old_rows.task_id
       AND (
            (old_rows.asset_id = 29 AND keep_rows.asset_id = 12) OR
            (old_rows.asset_id = 18 AND keep_rows.asset_id = 25) OR
            (old_rows.asset_id = 20 AND keep_rows.asset_id = 25) OR
            (old_rows.asset_id = 24 AND keep_rows.asset_id = 48)
       )
     WHERE old_rows.asset_id IN (18, 20, 24, 29);
    IF v_count <> 0 THEN
        SIGNAL SQLSTATE '45000'
            SET MESSAGE_TEXT = 'Precheck failed: software_task_results unique key conflict detected.';
    END IF;

    SELECT COUNT(*)
      INTO v_count
      FROM software_task_results a
      JOIN software_task_results b
        ON a.task_id = b.task_id
     WHERE a.asset_id = 18
       AND b.asset_id = 20;
    IF v_count <> 0 THEN
        SIGNAL SQLSTATE '45000'
            SET MESSAGE_TEXT = 'Precheck failed: software_task_results conflict between old Juno assets 18 and 20.';
    END IF;

    SELECT COUNT(*)
      INTO v_count
      FROM network_interfaces old_rows
      JOIN network_interfaces keep_rows
        ON keep_rows.if_index = old_rows.if_index
       AND (
            (old_rows.asset_id = 29 AND keep_rows.asset_id = 12) OR
            (old_rows.asset_id = 18 AND keep_rows.asset_id = 25) OR
            (old_rows.asset_id = 20 AND keep_rows.asset_id = 25) OR
            (old_rows.asset_id = 24 AND keep_rows.asset_id = 48)
       )
     WHERE old_rows.asset_id IN (18, 20, 24, 29);
    IF v_count <> 0 THEN
        SIGNAL SQLSTATE '45000'
            SET MESSAGE_TEXT = 'Precheck failed: network_interfaces unique key conflict detected.';
    END IF;

    SELECT COUNT(*)
      INTO v_count
      FROM network_interfaces a
      JOIN network_interfaces b
        ON a.if_index = b.if_index
     WHERE a.asset_id = 18
       AND b.asset_id = 20;
    IF v_count <> 0 THEN
        SIGNAL SQLSTATE '45000'
            SET MESSAGE_TEXT = 'Precheck failed: network_interfaces conflict between old Juno assets 18 and 20.';
    END IF;

    START TRANSACTION;

    -- 3) 锁定目标资产行
    SELECT id
      FROM assets
     WHERE id IN (12, 18, 20, 24, 25, 29, 48)
     FOR UPDATE;

    -- 4) 迁移普通历史/结果表
    UPDATE agent_heartbeat SET asset_id = 12 WHERE asset_id = 29;
    UPDATE agent_heartbeat SET asset_id = 25 WHERE asset_id IN (18, 20);
    UPDATE agent_heartbeat SET asset_id = 48 WHERE asset_id = 24;

    UPDATE agent_tokens SET asset_id = 12 WHERE asset_id = 29;
    UPDATE agent_tokens SET asset_id = 25 WHERE asset_id IN (18, 20);
    UPDATE agent_tokens SET asset_id = 48 WHERE asset_id = 24;

    UPDATE alert_records SET asset_id = 12 WHERE asset_id = 29;
    UPDATE alert_records SET asset_id = 25 WHERE asset_id IN (18, 20);
    UPDATE alert_records SET asset_id = 48 WHERE asset_id = 24;

    UPDATE alerts SET asset_id = 12 WHERE asset_id = 29;
    UPDATE alerts SET asset_id = 25 WHERE asset_id IN (18, 20);
    UPDATE alerts SET asset_id = 48 WHERE asset_id = 24;

    UPDATE asset_changes SET asset_id = 12 WHERE asset_id = 29;
    UPDATE asset_changes SET asset_id = 25 WHERE asset_id IN (18, 20);
    UPDATE asset_changes SET asset_id = 48 WHERE asset_id = 24;

    UPDATE batch_operation_results SET asset_id = 12 WHERE asset_id = 29;
    UPDATE batch_operation_results SET asset_id = 25 WHERE asset_id IN (18, 20);
    UPDATE batch_operation_results SET asset_id = 48 WHERE asset_id = 24;

    UPDATE raw_data SET asset_id = 12 WHERE asset_id = 29;
    UPDATE raw_data SET asset_id = 25 WHERE asset_id IN (18, 20);
    UPDATE raw_data SET asset_id = 48 WHERE asset_id = 24;

    UPDATE software_compliance_results SET asset_id = 12 WHERE asset_id = 29;
    UPDATE software_compliance_results SET asset_id = 25 WHERE asset_id IN (18, 20);
    UPDATE software_compliance_results SET asset_id = 48 WHERE asset_id = 24;

    UPDATE software_policy_logs SET asset_id = 12 WHERE asset_id = 29;
    UPDATE software_policy_logs SET asset_id = 25 WHERE asset_id IN (18, 20);
    UPDATE software_policy_logs SET asset_id = 48 WHERE asset_id = 24;

    UPDATE system_activity_logs SET asset_id = 12 WHERE asset_id = 29;
    UPDATE system_activity_logs SET asset_id = 25 WHERE asset_id IN (18, 20);
    UPDATE system_activity_logs SET asset_id = 48 WHERE asset_id = 24;

    -- 5) 迁移有唯一键约束的表
    UPDATE software_task_results SET asset_id = 12 WHERE asset_id = 29;
    UPDATE software_task_results SET asset_id = 25 WHERE asset_id IN (18, 20);
    UPDATE software_task_results SET asset_id = 48 WHERE asset_id = 24;

    UPDATE network_interfaces SET asset_id = 12 WHERE asset_id = 29;
    UPDATE network_interfaces SET asset_id = 25 WHERE asset_id IN (18, 20);
    UPDATE network_interfaces SET asset_id = 48 WHERE asset_id = 24;

    -- 6) 迁移软件类明细表
    -- asset_software 采用“补迁不重复的软件 + 删除旧记录”的方式，避免重复软件记录堆积。
    INSERT INTO asset_software (
        asset_id, software_name, version, vendor, category, install_date,
        install_path, created_at, updated_at, size, license_status, size_mb
    )
    SELECT
        12, src.software_name, src.version, src.vendor, src.category, src.install_date,
        src.install_path, src.created_at, src.updated_at, src.size, src.license_status, src.size_mb
    FROM asset_software src
    LEFT JOIN asset_software dst
      ON dst.asset_id = 12
     AND COALESCE(dst.software_name, '') = COALESCE(src.software_name, '')
     AND COALESCE(dst.version, '') = COALESCE(src.version, '')
     AND COALESCE(dst.vendor, '') = COALESCE(src.vendor, '')
    WHERE src.asset_id = 29
      AND dst.id IS NULL;
    DELETE FROM asset_software WHERE asset_id = 29;

    INSERT INTO asset_software (
        asset_id, software_name, version, vendor, category, install_date,
        install_path, created_at, updated_at, size, license_status, size_mb
    )
    SELECT
        25, src.software_name, src.version, src.vendor, src.category, src.install_date,
        src.install_path, src.created_at, src.updated_at, src.size, src.license_status, src.size_mb
    FROM asset_software src
    LEFT JOIN asset_software dst
      ON dst.asset_id = 25
     AND COALESCE(dst.software_name, '') = COALESCE(src.software_name, '')
     AND COALESCE(dst.version, '') = COALESCE(src.version, '')
     AND COALESCE(dst.vendor, '') = COALESCE(src.vendor, '')
    WHERE src.asset_id = 18
      AND dst.id IS NULL;
    DELETE FROM asset_software WHERE asset_id = 18;

    INSERT INTO asset_software (
        asset_id, software_name, version, vendor, category, install_date,
        install_path, created_at, updated_at, size, license_status, size_mb
    )
    SELECT
        25, src.software_name, src.version, src.vendor, src.category, src.install_date,
        src.install_path, src.created_at, src.updated_at, src.size, src.license_status, src.size_mb
    FROM asset_software src
    LEFT JOIN asset_software dst
      ON dst.asset_id = 25
     AND COALESCE(dst.software_name, '') = COALESCE(src.software_name, '')
     AND COALESCE(dst.version, '') = COALESCE(src.version, '')
     AND COALESCE(dst.vendor, '') = COALESCE(src.vendor, '')
    WHERE src.asset_id = 20
      AND dst.id IS NULL;
    DELETE FROM asset_software WHERE asset_id = 20;

    INSERT INTO asset_software (
        asset_id, software_name, version, vendor, category, install_date,
        install_path, created_at, updated_at, size, license_status, size_mb
    )
    SELECT
        48, src.software_name, src.version, src.vendor, src.category, src.install_date,
        src.install_path, src.created_at, src.updated_at, src.size, src.license_status, src.size_mb
    FROM asset_software src
    LEFT JOIN asset_software dst
      ON dst.asset_id = 48
     AND COALESCE(dst.software_name, '') = COALESCE(src.software_name, '')
     AND COALESCE(dst.version, '') = COALESCE(src.version, '')
     AND COALESCE(dst.vendor, '') = COALESCE(src.vendor, '')
    WHERE src.asset_id = 24
      AND dst.id IS NULL;
    DELETE FROM asset_software WHERE asset_id = 24;

    -- software_inventory 当前无相关记录，且线上表无唯一键冲突风险，直接迁移即可
    UPDATE software_inventory SET asset_id = 12 WHERE asset_id = 29;
    UPDATE software_inventory SET asset_id = 25 WHERE asset_id IN (18, 20);
    UPDATE software_inventory SET asset_id = 48 WHERE asset_id = 24;

    -- 7) 删除旧资产
    DELETE FROM assets WHERE id IN (18, 20, 24, 29);

    COMMIT;
END$$

DELIMITER ;

CALL merge_duplicate_assets_20260717();

-- 8) 执行后校验
SELECT id, hostname, ip_address, mac_address, deleted_at
FROM assets
WHERE id IN (12, 18, 20, 24, 25, 29, 48)
ORDER BY id;

SELECT hostname, COUNT(*) AS active_count,
       GROUP_CONCAT(CONCAT(id, ':', ip_address) ORDER BY id SEPARATOR ' | ') AS active_rows
FROM assets
WHERE deleted_at IS NULL
  AND hostname IN ('Brain', 'Juno', 'DESKTOP-HPD0GJ7')
GROUP BY hostname
ORDER BY hostname;

SELECT 'agent_heartbeat' AS table_name, asset_id, COUNT(*) AS row_count
FROM agent_heartbeat
WHERE asset_id IN (12, 18, 20, 24, 25, 29, 48)
GROUP BY asset_id
UNION ALL
SELECT 'alerts' AS table_name, asset_id, COUNT(*) AS row_count
FROM alerts
WHERE asset_id IN (12, 18, 20, 24, 25, 29, 48)
GROUP BY asset_id
UNION ALL
SELECT 'asset_changes' AS table_name, asset_id, COUNT(*) AS row_count
FROM asset_changes
WHERE asset_id IN (12, 18, 20, 24, 25, 29, 48)
GROUP BY asset_id
UNION ALL
SELECT 'asset_software' AS table_name, asset_id, COUNT(*) AS row_count
FROM asset_software
WHERE asset_id IN (12, 18, 20, 24, 25, 29, 48)
GROUP BY asset_id
UNION ALL
SELECT 'software_task_results' AS table_name, asset_id, COUNT(*) AS row_count
FROM software_task_results
WHERE asset_id IN (12, 18, 20, 24, 25, 29, 48)
GROUP BY asset_id
ORDER BY table_name, asset_id;

DROP PROCEDURE IF EXISTS merge_duplicate_assets_20260717;
