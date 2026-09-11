# -*- coding: utf-8 -*-
"""Agent 通用任务执行框架（V1.8.3 通用任务通道，agent 侧）。

平台心跳响应可携带 jobs 列表（job_id/job_type/payload），本模块按
handler 注册表分发执行并返回结果（随下一次心跳上报 job_results）。

handler 注册约定：
    消费方（cmdb_agent_core / zvagent.heartbeat）在导入时注册各自 handler：
        zvagent.jobs.register_job_handler("command", execute_control_command)
        zvagent.jobs.register_job_handler("report", trigger_immediate_report)
    新任务类型无需改动分发循环。

安全边界：command 类型经 execute_control_command（raw 命令受
ZVIEW_AGENT_ALLOW_RAW_COMMAND 门控），与既有控制通道同一安全模型。
"""
from __future__ import annotations

from typing import Any, Callable

_JOB_HANDLERS: dict[str, Callable[[dict], Any]] = {}

# V1.9.x deferred 结果：长任务 handler 自行启动线程，完成后推送结果，
# 由心跳循环 take_deferred_results() 取走并随 job_results 上报。
_DEFERRED_RESULTS: list[dict] = []


def push_deferred_result(job_id: str, state: str, result: Any = None) -> None:
    """长任务完成后推送结果（state: succeeded/failed）。"""
    _DEFERRED_RESULTS.append({"job_id": str(job_id), "state": state, "result": result})


def take_deferred_results() -> list[dict]:
    """心跳循环取走已完成的 deferred 结果（取走即清空）。"""
    if not _DEFERRED_RESULTS:
        return []
    results = list(_DEFERRED_RESULTS)
    _DEFERRED_RESULTS.clear()
    return results


def register_job_handler(job_type: str, handler: Callable[[dict], Any]) -> None:
    """注册任务 handler（幂等，后注册覆盖）。"""
    _JOB_HANDLERS[str(job_type)] = handler


def job_handler(job_type: str) -> Callable:
    """装饰器形式注册。"""
    def deco(fn: Callable[[dict], Any]) -> Callable[[dict], Any]:
        register_job_handler(job_type, fn)
        return fn
    return deco


def registered_job_types() -> list[str]:
    return sorted(_JOB_HANDLERS)


def execute_pending_jobs(jobs: Any) -> list[dict]:
    """执行心跳响应下发的任务列表，返回结果（随下次心跳上报 job_results）。

    单个任务失败不影响其余任务；未知类型返回 failed（显式可见）。
    """
    if not isinstance(jobs, list) or not jobs:
        return []
    results: list[dict] = []
    for job in jobs:
        if not isinstance(job, dict):
            continue
        job_id = str(job.get("job_id") or "")
        job_type = str(job.get("job_type") or "")
        payload = job.get("payload")
        if not job_id or not job_type:
            results.append({"job_id": job_id, "state": "failed", "error": "missing job_id/job_type"})
            continue
        handler = _JOB_HANDLERS.get(job_type)
        safe_console_print(f"[V1910DBG] job {job_id} type {job_type}: handler={'FOUND' if handler else 'NOT FOUND'}")
        if handler is None:
            safe_console_print(f"[Jobs][DBG] job {job_id} type {job_type}: handler NOT registered "
                               f"(registered: {sorted(_JOB_HANDLERS)})")
            results.append({"job_id": job_id, "state": "failed",
                            "error": f"unknown job_type: {job_type}"})
            continue
        safe_console_print(f"[Jobs][DBG] job {job_id} type {job_type}: executing")
        try:
            # V1.9.x：handler 可返回 {"_async": True} 表示已自行启动异步执行，
            # 完成后通过 push_deferred_result 上报结果（长任务如补丁安装）
            exec_payload = {**(payload if isinstance(payload, dict) else {}), "job_id": job_id}
            result = handler(exec_payload)
            if isinstance(result, dict) and result.get("_async"):
                safe_console_print(f"[Jobs][DBG] job {job_id}: async started")
                results.append({"job_id": job_id, "state": "running"})
            else:
                safe_console_print(f"[Jobs][DBG] job {job_id}: succeeded")
                results.append({"job_id": job_id, "state": "succeeded", "result": result})
        except Exception as exc:
            safe_console_print(f"[Jobs][DBG] job {job_id}: failed {type(exc).__name__}: {exc}")
            results.append({"job_id": job_id, "state": "failed", "error": f"{type(exc).__name__}: {exc}"})
    return results
