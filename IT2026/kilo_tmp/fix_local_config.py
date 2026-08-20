# -*- coding: utf-8 -*-
"""给本机 ProgramData 配置补 token（迁移安装器丢 token 的临时修复）"""
import sys, io, json, os
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
sys.path.insert(0, r'D:\IT2026\IT2026\IT2026\IT2026')
os.chdir(r'D:\IT2026\IT2026\IT2026\IT2026')
from auth_utils import get_expected_agent_token

config_path = r'C:\ProgramData\CMDB-Agent\config\config.local.json'
cfg = json.load(open(config_path, encoding='utf-8')) if os.path.exists(config_path) else {}
token = get_expected_agent_token()
cfg['token'] = token
with io.open(config_path, 'w', encoding='utf-8') as f:
    f.write(json.dumps(cfg, ensure_ascii=False, indent=2))
print('config keys:', sorted(cfg.keys()), '| token len:', len(token))
