# -*- coding: utf-8 -*-
"""Go Agent 冒烟测试：本地 mock 心跳服务，验证 HTTP 协议、凭据落盘、循环运行。不碰生产资产。"""
import json
import os
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

AGENT_EXE = r"D:\IT2026\IT2026\IT2026\IT2026\agent-go\Z-View-Agent.exe"
PORT = 18080
hits = []

class Mock(BaseHTTPRequestHandler):
    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(length) or b"{}")
        hits.append({"path": self.path, "auth": self.headers.get("Authorization"), "hostname": body.get("hostname"), "version": body.get("agent_version")})
        resp = {
            "status": "success",
            "asset_id": 99999,
            "message": "mock ok",
            "policies": {"mock": True},
            "agent_credential": {"asset_id": 99999, "secret": "mock-secret-123"},
        }
        data = json.dumps(resp).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *a):
        pass

server = HTTPServer(("127.0.0.1", PORT), Mock)
threading.Thread(target=server.serve_forever, daemon=True).start()

proc = subprocess.Popen(
    [AGENT_EXE, "-server", f"http://127.0.0.1:{PORT}", "-token", "TESTTOKEN", "-interval", "2"],
    stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
    creationflags=subprocess.CREATE_NO_WINDOW,
)
time.sleep(6)
proc.terminate()
try:
    out, _ = proc.communicate(timeout=5)
except Exception:
    proc.kill()
    out, _ = proc.communicate()
server.shutdown()

print("=== agent stdout ===")
print(out)
print("=== mock hits ===")
print(json.dumps(hits, ensure_ascii=False, indent=1))
cred_path = os.path.join(os.path.dirname(AGENT_EXE), "zv-credential.json")
print("credential file exists:", os.path.exists(cred_path))
if os.path.exists(cred_path):
    print(json.dumps(json.load(open(cred_path)), indent=1))
ok = len(hits) >= 2 and all(h["auth"] == "Bearer TESTTOKEN" for h in hits) and os.path.exists(cred_path)
print("SMOKE TEST:", "PASS" if ok else "FAIL")
