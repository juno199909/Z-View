# -*- coding: utf-8 -*-
import sys, io, json, os
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
sys.path.insert(0, r'D:\IT2026\IT2026\IT2026\IT2026')
os.chdir(r'D:\IT2026\IT2026\IT2026\IT2026')
from zvplatform.db import create_connection
conn = create_connection()
cur = conn.cursor(dictionary=True)
cur.execute("SELECT id, hostname, ip_address, agent_version, disk_info, last_seen FROM assets WHERE deleted_at IS NULL AND id IN (28, 2213)")
for r in cur.fetchall():
    print('==', r['id'], r['hostname'], r['ip_address'], 'agent_version:', r.get('agent_version'), 'last_seen:', r.get('last_seen'))
    try:
        disks = json.loads(r.get('disk_info') or '[]')
        for d in disks:
            print('   disk:', d)
    except Exception as e:
        print('   disk_info parse err:', e)
cur.close()
conn.close()
