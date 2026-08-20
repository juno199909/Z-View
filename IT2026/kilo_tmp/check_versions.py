# -*- coding: utf-8 -*-
import sys, io, os
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
sys.path.insert(0, r'D:\IT2026\IT2026\IT2026\IT2026')
os.chdir(r'D:\IT2026\IT2026\IT2026\IT2026')
from zvplatform.db import create_connection
conn = create_connection()
cur = conn.cursor(dictionary=True)
cur.execute("SELECT id, hostname, agent_version, last_seen FROM assets WHERE deleted_at IS NULL AND id IN (28, 2213)")
for r in cur.fetchall():
    print(r['id'], r['hostname'], r['agent_version'], 'last_seen:', r['last_seen'])
cur.close()
conn.close()
