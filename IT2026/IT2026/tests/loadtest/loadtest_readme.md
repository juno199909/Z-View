# P3-05 Agent 心跳压测

## 运行

```bash
cd IT2026/IT2026/IT2026/tests/loadtest
python loadtest_hb.py --agents 10 --count 3   # 先小规模验证
python loadtest_hb.py --agents 100 --count 5  # 100 Agent 基准
python loadtest_hb.py --agents 500 --count 3  # 500 Agent 压测
```

前提：平台 8080 在线；token 从代码根 config.json 自动读取。

## 安全注意

- 压测会创建 `loadtest-*` 资产并写入 agent_heartbeat 表
- 结束后清理：

```sql
DELETE FROM agent_heartbeat WHERE asset_id IN (SELECT id FROM assets WHERE hostname LIKE 'loadtest-%');
DELETE FROM alerts WHERE asset_id IN (SELECT id FROM assets WHERE hostname LIKE 'loadtest-%');
DELETE FROM assets WHERE hostname LIKE 'loadtest-%';
```

## 解读

- QPS：平台单机心跳吞吐能力（对照 84 台 × 30s 间隔 = 实际需求仅 ~3 QPS）
- P95 延迟 > 1s 或错误率 > 1%：需检查 DB 连接池/索引
- 100 Agent 通过后再做 500/1000（重点观察 assets 表 UPDATE 锁与 agent_heartbeat 插入）
