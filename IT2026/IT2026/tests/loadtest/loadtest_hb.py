# -*- coding: utf-8 -*-
"""P3-05：Agent 心跳压测工具（asyncio 并发模拟）。"""
import argparse
import asyncio
import json
import statistics
import time
import urllib.request
import uuid


def post_heartbeat(base_url: str, token: str, hostname: str, timeout: float):
    payload = {
        "hostname": hostname,
        "ip_address": f"10.{uuid.uuid4().int % 255}.{uuid.uuid4().int % 255}.1",
        "mac_address": f"LT-{uuid.uuid4().hex[:12]}",
        "status": "online",
        "agent_version": "loadtest",
        "agent_install_status": "installed",
    }
    req = urllib.request.Request(
        f"{base_url}/api/v1/agent/heartbeat",
        data=json.dumps(payload).encode(), method="POST",
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {token}"},
    )
    start = time.perf_counter()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            r.read()
            return time.perf_counter() - start, r.status, None
    except Exception as exc:
        return time.perf_counter() - start, None, f"{type(exc).__name__}: {exc}"


async def worker(worker_id: int, base_url: str, token: str, count: int,
                 timeout: float, results: list, errors: list):
    loop = asyncio.get_event_loop()
    for i in range(count):
        hostname = f"loadtest-{worker_id:03d}-{i:04d}"
        latency, status, err = await loop.run_in_executor(
            None, post_heartbeat, base_url, token, hostname, timeout,
        )
        if err:
            errors.append((hostname, err))
        else:
            results.append(latency)


async def main():
    parser = argparse.ArgumentParser(description="Z-View Agent heartbeat load test")
    parser.add_argument("--agents", type=int, default=100, help="并发虚拟 Agent 数")
    parser.add_argument("--count", type=int, default=5, help="每个 Agent 心跳次数")
    parser.add_argument("--duration", type=int, default=60, help="总时长上限（秒，超时熔断）")
    parser.add_argument("--base-url", default="http://127.0.0.1:8080")
    parser.add_argument("--timeout", type=float, default=15.0)
    args = parser.parse_args()

    token = open(r"D:\IT2026\IT2026\IT2026\IT2026\config.json",
                 encoding="utf-8").read().split('"token": "')[1].split('"')[0]

    total = args.agents * args.count
    started = time.perf_counter()
    print(f"启动压测: agents={args.agents} x count={args.count} = {total} 心跳")

    results, errors = [], []
    tasks = [
        asyncio.create_task(worker(w, args.base_url, token, args.count, args.timeout, results, errors))
        for w in range(args.agents)
    ]
    done, pending = await asyncio.wait(tasks, timeout=args.duration)
    for t in pending:
        t.cancel()
    elapsed = time.perf_counter() - started

    if results:
        results.sort()
        p50 = results[int(len(results) * 0.5)]
        p95 = results[int(len(results) * 0.95)]
        p99 = results[min(len(results) - 1, int(len(results) * 0.99))]
        print(f"\n===== 压测报告 =====")
        print(f"实际耗时: {elapsed:.1f}s | 完成: {len(results)} | 错误: {len(errors)}")
        print(f"QPS: {len(results) / max(elapsed, 0.001):.1f}")
        print(f"延迟 P50={p50*1000:.0f}ms  P95={p95*1000:.0f}ms  P99={p99*1000:.0f}ms  max={results[-1]*1000:.0f}ms")
    else:
        print("无成功请求")
    if errors:
        print(f"错误样例: {errors[:3]}")
    print("提示: 压测写入 loadtest-* 资产，清理 SQL: "
          "DELETE FROM agent_heartbeat WHERE asset_id IN (SELECT id FROM assets WHERE hostname LIKE 'loadtest-%'); "
          "DELETE FROM assets WHERE hostname LIKE 'loadtest-%';")


if __name__ == "__main__":
    asyncio.run(main())
