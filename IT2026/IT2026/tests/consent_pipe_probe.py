# -*- coding: utf-8 -*-
"""直连同意助手管道探针：验证 tkinter 弹窗路径是否可用。

运行后屏幕上应出现「Z-View 远程控制确认」弹窗；点击允许/拒绝或等超时，
脚本打印助手的原始响应。同时观察 agent-runtime.log 是否出现 dialog 日志。
"""

import json
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from multiprocessing.connection import Client  # noqa: E402

from agent_consent_ipc import build_consent_authkey, build_consent_pipe_name  # noqa: E402


def main():
    pipe_name = build_consent_pipe_name(2)
    timeout_seconds = int(sys.argv[1]) if len(sys.argv) > 1 else 45
    payload = {
        "type": "consent_request",
        "session_id": 2,
        "requester": "pipe-probe",
        "origin": "127.0.0.1",
        "target": "LOCAL-TEST",
        "timeout_seconds": timeout_seconds,
    }
    print(f"[probe] connecting {pipe_name} ...", flush=True)
    try:
        with Client(pipe_name, family="AF_PIPE", authkey=build_consent_authkey()) as conn:
            print("[probe] connected; sending request", flush=True)
            conn.send(payload)
            if conn.poll(timeout_seconds + 20):
                reply = conn.recv()
                print(f"[probe] reply: {json.dumps(reply, ensure_ascii=False)}", flush=True)
                return 0
            print("[probe] TIMEOUT waiting for helper reply (dialog may not have shown)", flush=True)
            return 1
    except Exception as exc:
        print(f"[probe] ERROR: {type(exc).__name__}: {exc}", flush=True)
        return 1


if __name__ == "__main__":
    time.sleep(0.2)
    sys.exit(main())
