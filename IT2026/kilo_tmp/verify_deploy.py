# -*- coding: utf-8 -*-
import sys, io, os
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
sys.path.insert(0, r'D:\IT2026\IT2026\IT2026\IT2026')
os.chdir(r'D:\IT2026\IT2026\IT2026\IT2026')
import assets_api
script = assets_api._build_agent_deploy_ps_script('http://172.16.250.120:8080', 'TESTTOKEN')
checks = {
    'install cmd': '--install --quiet --server-url ' in script,
    'token var': 'agent_token=$Token' in script,
    'center var': '$Center = ' in script,
    'download uri': '/api/v1/agent/upgrade/download' in script,
}
print(checks)
assert all(checks.values())
from auth_utils import get_expected_agent_token
t = get_expected_agent_token()
print('agent token retrievable:', bool(t), 'len:', len(t or ''))
print('ALL OK')
