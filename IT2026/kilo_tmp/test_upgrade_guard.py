# -*- coding: utf-8 -*-
"""升级台账/锁/退避逻辑单测（临时目录，不污染真实 runtime）"""
import sys, io, json, time, tempfile
from pathlib import Path
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
sys.path.insert(0, r'D:\IT2026\IT2026\IT2026\IT2026')
import os
os.chdir(r'D:\IT2026\IT2026\IT2026\IT2026')
import cmdb_agent_core as core

tmp = Path(tempfile.mkdtemp(prefix='zv-upgrade-test-'))
core._UPGRADE_LEDGER_PATH = tmp / 'upgrade-attempts.json'
core._UPGRADE_LOCK_PATH = tmp / 'upgrade.lock'

# 1. 台账：连续失败 → 第 5 次起退避，指数增长，封顶 24h
nxt = 0.0
for i in range(1, 8):
    nxt = core._record_upgrade_failure('1.8.3', f'fail {i}')
    if i < 5:
        assert nxt == 0.0, f'attempt {i} should not backoff, got {nxt}'
    else:
        assert nxt > time.time(), f'attempt {i} should backoff'
        entry = core._upgrade_ledger_read()['1.8.3']
        expect = min(1800 * (2 ** (i - 5)), 86400)
        assert entry['backoff_seconds'] == expect, f'attempt {i}: {entry["backoff_seconds"]} != {expect}'
print('ledger backoff schedule OK (30m -> 1h -> 2h ... cap 24h)')

# 2. 退避期内 remaining > 0
assert core._upgrade_backoff_remaining('1.8.3') > 0
# 无记录版本 remaining == 0
assert core._upgrade_backoff_remaining('9.9.9') == 0.0
print('backoff remaining OK')

# 3. COMMIT 清台账
core._clear_upgrade_failure('1.8.3')
assert core._upgrade_backoff_remaining('1.8.3') == 0.0
print('clear on commit OK')

# 4. 锁：获取 → 互斥 → 释放 → 可再获取
assert core._acquire_upgrade_lock('UPG-TEST-1') is True
assert core._acquire_upgrade_lock('UPG-TEST-2') is False
core._release_upgrade_lock()
assert core._acquire_upgrade_lock('UPG-TEST-3') is True
core._release_upgrade_lock()
print('lock mutex OK')

# 5. 陈旧锁接管（写入 31 分钟前的锁）
stale = {'pid': 1, 'upgrade_id': 'UPG-OLD', 'started_at': time.time() - 1810}
core._UPGRADE_LOCK_PATH.write_text(json.dumps(stale), encoding='utf-8')
assert core._acquire_upgrade_lock('UPG-NEW') is True
core._release_upgrade_lock()
print('stale lock takeover OK')

# 6. 新鲜锁不接管
fresh = {'pid': 1, 'upgrade_id': 'UPG-FRESH', 'started_at': time.time() - 60}
core._UPGRADE_LOCK_PATH.write_text(json.dumps(fresh), encoding='utf-8')
assert core._acquire_upgrade_lock('UPG-X') is False
core._release_upgrade_lock()
print('fresh lock respect OK')

import shutil
shutil.rmtree(tmp, ignore_errors=True)
print('ALL UPGRADE GUARD TESTS PASS')
