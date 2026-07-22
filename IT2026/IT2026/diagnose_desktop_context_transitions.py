from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from RemoteAgent.privileged_client import PrivilegedServiceClient  # noqa: E402


def _normalize_bool(value) -> str:
    return "1" if bool(value) else "0"


def _stringify(value) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _extract_desktop_state(payload: dict, key: str) -> tuple[dict, dict]:
    context = dict(payload.get(key) or {})
    return (
        dict(context.get("state") or {}),
        dict(context.get("binding_state") or {}),
    )


def _format_desktop_label(state: dict, binding: dict) -> str:
    selected_name = _stringify(
        binding.get("capture_selected_candidate_name")
        or state.get("selected_desktop_name")
        or state.get("capture_desktop")
        or state.get("thread_desktop")
        or "unknown"
    )
    selected_kind = _stringify(
        binding.get("capture_selected_candidate_kind")
        or state.get("selected_desktop_kind")
        or state.get("capture_desktop_kind")
        or state.get("thread_desktop_kind")
        or "unknown"
    )
    selected_source = _stringify(
        binding.get("capture_selected_candidate_source")
        or binding.get("last_binding_selected_source")
        or state.get("selected_desktop_source")
        or state.get("capture_desktop_source")
        or "unknown"
    )
    status = _stringify(binding.get("status") or state.get("status") or "unknown")
    return f"{selected_name}[{selected_kind}|{selected_source}|{status}]"


def _evaluate_sample(payload: dict) -> list[str]:
    issues: list[str] = []
    service_state, service_binding = _extract_desktop_state(payload.get("desktop_context") or {}, "desktop_context")
    capture_helper_state, capture_helper_binding = _extract_desktop_state(payload, "capture_helper_context")

    if _stringify(service_state.get("input_desktop")).lower() == "unavailable":
        issues.append("service_input_unavailable")
    if bool(service_binding.get("desktop_handle_open_failed")):
        issues.append("service_handle_open_failed")
    if bool(service_binding.get("window_station_changed")) and not bool(
        service_binding.get("desktop_inventory_invalidated_cache_count")
    ):
        issues.append("service_winsta_changed_without_cache_invalidation")

    if capture_helper_state:
        if not bool(capture_helper_state.get("selected_desktop_allowed_by_policy", True)):
            issues.append("helper_desktop_policy_mismatch")
        if not bool(capture_helper_state.get("selected_desktop_matches_preferred_kind", True)):
            issues.append("helper_desktop_kind_mismatch")
    if capture_helper_binding:
        if bool(capture_helper_binding.get("desktop_handle_open_failed")):
            issues.append("helper_handle_open_failed")
        if bool(capture_helper_binding.get("desktop_handle_scope_drift")):
            issues.append("helper_handle_scope_drift")
    return issues


def _emit_sample_line(index: int, payload: dict, issues: list[str]) -> None:
    service_context = dict(payload.get("desktop_context") or {})
    service_state, service_binding = _extract_desktop_state(service_context, "desktop_context")
    input_helper_state, input_helper_binding = _extract_desktop_state(payload, "input_helper_context")
    capture_helper_state, capture_helper_binding = _extract_desktop_state(payload, "capture_helper_context")

    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    console_session = _stringify((payload.get("console_session") or {}).get("session_id") or "")
    preferred_capture_host = _stringify((payload.get("preferred_capture_host_session") or {}).get("session_id") or "")
    active_capture_host = _stringify((payload.get("active_capture_session") or {}).get("session_id") or "")
    helper_capture_host = _stringify((payload.get("capture_helper_host_session") or {}).get("session_id") or "")
    target_session = _stringify((payload.get("capture_target_session") or {}).get("session_id") or "")
    issue_text = ",".join(issues) if issues else "none"

    print(
        "desktop_context_sample "
        f"index={index} "
        f"time=\"{ts}\" "
        f"console={console_session or 'unknown'} "
        f"preferred_capture={preferred_capture_host or 'unknown'} "
        f"active_capture={active_capture_host or 'unknown'} "
        f"capture_helper_host={helper_capture_host or 'unknown'} "
        f"target_session={target_session or 'unknown'} "
        f"service={_format_desktop_label(service_state, service_binding)} "
        f"input_helper={_format_desktop_label(input_helper_state, input_helper_binding)} "
        f"capture_helper={_format_desktop_label(capture_helper_state, capture_helper_binding)} "
        f"service_input={_stringify(service_state.get('input_desktop') or 'unknown')} "
        f"service_input_src={_stringify(service_state.get('input_desktop_source') or 'unknown')} "
        f"service_input_inferred={_normalize_bool(service_state.get('input_desktop_inferred'))} "
        f"inventory_changed={_normalize_bool(service_state.get('desktop_inventory_changed'))} "
        f"winsta_changed={_normalize_bool(service_state.get('window_station_changed'))} "
        f"helper_rebind={_normalize_bool(capture_helper_binding.get('desktop_rebind_succeeded'))} "
        f"issues={issue_text}"
    )


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Watch Desktop Context state across RDP/console/lock transitions."
    )
    parser.add_argument("--samples", type=int, default=30, help="Number of samples to collect.")
    parser.add_argument("--interval", type=float, default=1.0, help="Seconds between samples.")
    parser.add_argument("--session-id", type=int, default=None, help="Optional target session id.")
    parser.add_argument("--reason", default="desktop_context_transition_probe", help="Probe reason tag.")
    parser.add_argument(
        "--jsonl",
        default="",
        help="Optional file path to append full JSON samples as JSONL.",
    )
    parser.add_argument(
        "--compact",
        action="store_true",
        help="Request compact service payloads.",
    )
    args = parser.parse_args()

    samples = max(1, min(int(args.samples), 3600))
    interval = max(0.2, min(float(args.interval), 30.0))
    jsonl_path = Path(args.jsonl).expanduser() if args.jsonl else None

    client = PrivilegedServiceClient()
    issue_count = 0

    for index in range(1, samples + 1):
        payload = client.describe_desktop_context(
            reason=str(args.reason or "desktop_context_transition_probe"),
            session_id=args.session_id,
            compact=bool(args.compact),
        )
        issues = _evaluate_sample(payload)
        issue_count += len(issues)
        _emit_sample_line(index, payload, issues)

        if jsonl_path is not None:
            record = {
                "sample_index": index,
                "captured_at": datetime.now().isoformat(timespec="seconds"),
                "issues": issues,
                "payload": payload,
            }
            with jsonl_path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(record, ensure_ascii=False) + "\n")

        if index < samples:
            time.sleep(interval)

    print(
        "desktop_context_summary "
        f"samples={samples} "
        f"issue_count={issue_count} "
        f"result={'pass' if issue_count == 0 else 'needs_review'}"
    )
    return 0 if issue_count == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
