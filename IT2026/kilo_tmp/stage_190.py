# -*- coding: utf-8 -*-
"""Stage 1.9.0 修复版 zip + 更新 manifest"""
import sys, io, json, hashlib, shutil, os, time
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
src = r'D:\IT2026\IT2026\IT2026\IT2026\releases\Z-View-1.9.0-onedir.zip'
dst_dir = r'D:\IT2026\IT2026\IT2026\IT2026\agent_upgrade\1.9.0'
os.makedirs(dst_dir, exist_ok=True)
dst = os.path.join(dst_dir, 'agent-onedir.zip')
shutil.copyfile(src, dst)
sha = hashlib.sha256(); size = 0
with open(dst, 'rb') as f:
    while True:
        chunk = f.read(1024*512)
        if not chunk:
            break
        sha.update(chunk); size += len(chunk)
manifest = {'version': '1.9.0', 'sha256': sha.hexdigest(), 'size': size, 'package_type': 'onedir-zip',
            'filename': 'agent-onedir.zip', 'uploaded_at': time.strftime('%Y-%m-%d %H:%M:%S'),
            'uploaded_by': 'kilo-rollout-step2-fixed', 'path': dst}
with io.open(r'D:\IT2026\IT2026\IT2026\IT2026\agent_upgrade\manifest.json', 'w', encoding='utf-8') as f:
    f.write(json.dumps(manifest, ensure_ascii=False, indent=2))
print('staged 1.9.0 fixed zip, sha', sha.hexdigest()[:16], 'size', size)
