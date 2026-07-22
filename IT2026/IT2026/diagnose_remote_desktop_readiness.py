from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from RemoteAgent.privileged_client import PrivilegedServiceClient  # noqa: E402


def _print_section(title: str) -> None:
    print(f"\n[{title}]")


def _emit_json(payload: dict) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))


def _emit_summary(payload: dict) -> None:
    readiness = payload.get("remote_desktop_readiness") or {}
    topology = payload.get("session_topology") or {}
    substrate = payload.get("display_substrate") or {}
    virtual_display = payload.get("virtual_display_status") or {}
    desktop_context_payload = payload.get("desktop_context") or {}
    service_desktop_context = desktop_context_payload.get("desktop_context") or {}
    helper_desktop_context = (
        ((desktop_context_payload.get("capture_helper_context") or {}).get("desktop_context"))
        or ((desktop_context_payload.get("input_helper_context") or {}).get("desktop_context"))
        or {}
    )
    service_binding = service_desktop_context.get("binding_state") or {}
    helper_binding = helper_desktop_context.get("binding_state") or {}
    helper_backend = helper_desktop_context.get("backend_diagnostics") or {}

    _print_section("Remote Desktop Readiness")
    print(f"commercial_continuity_ready: {bool(readiness.get('commercial_continuity_ready', False))}")
    print(f"continuity_grade: {readiness.get('continuity_grade') or 'unknown'}")
    print(
        "continuity_blocked_by_missing_substrate: "
        f"{bool(readiness.get('continuity_blocked_by_missing_substrate', False))}"
    )
    print(
        "preferred_capture_host_session_id: "
        f"{readiness.get('preferred_capture_host_session_id')}"
    )
    print(
        "active_capture_host_session_id: "
        f"{readiness.get('active_capture_host_session_id')}"
    )

    blockers = list(readiness.get("continuity_blockers") or [])
    requirements = list(readiness.get("continuity_requirements") or [])
    notes = list(readiness.get("notes") or [])

    print("continuity_blockers:")
    if blockers:
        for item in blockers:
            print(f"  - {item}")
    else:
        print("  - none")

    print("continuity_requirements:")
    if requirements:
        for item in requirements:
            print(f"  - {item}")
    else:
        print("  - none")

    print("key_notes:")
    if notes:
        for item in notes[:12]:
            print(f"  - {item}")
    else:
        print("  - none")

    _print_section("Display Substrate")
    print(f"provider_state: {substrate.get('provider_state') or 'unknown'}")
    print(f"persistent_available: {bool(substrate.get('persistent_available', False))}")
    print(
        "persistent_ready_for_unattended: "
        f"{bool(substrate.get('persistent_ready_for_unattended', False))}"
    )
    print(f"physical_display_attached: {bool(substrate.get('physical_display_attached', False))}")
    print(f"virtual_display_attached: {bool(substrate.get('virtual_display_attached', False))}")
    print(f"remote_adapter_present: {bool(substrate.get('remote_adapter_present', False))}")
    print(
        "virtual_display_provisioning_state: "
        f"{substrate.get('virtual_display_provisioning_state') or 'unknown'}"
    )

    _print_section("Virtual Display Provider")
    print(f"can_provision_virtual_display: {bool(virtual_display.get('can_provision_virtual_display', False))}")
    print(f"provisioning_state: {virtual_display.get('provisioning_state') or 'unknown'}")
    print(f"package_root: {virtual_display.get('package_root') or 'unknown'}")
    print(f"package_root_exists: {bool(virtual_display.get('package_root_exists', False))}")
    print(f"driver_manifest_loaded: {bool(virtual_display.get('driver_manifest_loaded', False))}")
    print(f"driver_package_complete: {bool(virtual_display.get('driver_package_complete', False))}")
    print(f"installed_device_present: {bool(virtual_display.get('installed_device_present', False))}")
    print(f"attached_virtual_display: {bool(virtual_display.get('attached_virtual_display', False))}")
    print(f"device_attached_to_desktop: {bool(virtual_display.get('device_attached_to_desktop', False))}")
    print(f"device_attached_confidence: {virtual_display.get('device_attached_confidence') or 'unknown'}")
    print(
        "display_inventory_virtual_adapter_count: "
        f"{virtual_display.get('display_inventory_virtual_adapter_count')}"
    )
    print(
        "display_inventory_virtual_attached_count: "
        f"{virtual_display.get('display_inventory_virtual_attached_count')}"
    )
    print(
        "display_inventory_attached_display_count: "
        f"{virtual_display.get('display_inventory_attached_display_count')}"
    )
    print(
        "display_inventory_render_monitor_count: "
        f"{virtual_display.get('display_inventory_render_monitor_count')}"
    )
    print(
        "display_inventory_remote_adapter_present: "
        f"{bool(virtual_display.get('display_inventory_remote_adapter_present', False))}"
    )

    _print_section("Session Topology")
    print(
        "console_session_id: "
        f"{((topology.get('console_session') or {}).get('session_id'))}"
    )
    print(
        "primary_remote_host_session_id: "
        f"{((topology.get('primary_remote_host_session') or {}).get('session_id'))}"
    )
    print(
        "preferred_capture_host_session_id: "
        f"{((topology.get('preferred_capture_host_session') or {}).get('session_id'))}"
    )
    print(
        "active_capture_host_session_id: "
        f"{((topology.get('active_capture_session') or {}).get('session_id'))}"
    )
    print(
        "capture_continuity_mode: "
        f"{((topology.get('capture_continuity') or {}).get('continuity_mode') or 'unknown')}"
    )

    _print_section("Desktop Context")
    print(
        "service_selected_desktop: "
        f"{(service_desktop_context.get('state') or {}).get('selected_desktop_name') or 'unknown'}"
    )
    print(
        "service_binding_generation: "
        f"{service_binding.get('desktop_context_generation')}"
    )
    print(
        "service_binding_reason: "
        f"{service_binding.get('desktop_binding_reason') or 'unknown'}"
    )
    print(
        "service_selected_kind: "
        f"{(service_desktop_context.get('state') or {}).get('selected_desktop_kind') or 'unknown'}"
    )
    print(
        "service_policy_kind: "
        f"{(service_desktop_context.get('state') or {}).get('preferred_capture_desktop_kind') or 'unknown'}"
    )
    print(
        "helper_selected_desktop: "
        f"{(helper_desktop_context.get('state') or {}).get('selected_desktop_name') or 'unknown'}"
    )
    print(
        "helper_binding_generation: "
        f"{helper_binding.get('desktop_context_generation')}"
    )
    print(
        "helper_binding_reason: "
        f"{helper_binding.get('desktop_binding_reason') or 'unknown'}"
    )
    print(
        "helper_rebind: "
        f"required={bool(helper_binding.get('desktop_rebind_required', False))} "
        f"attempted={bool(helper_binding.get('desktop_rebind_attempted', False))} "
        f"succeeded={bool(helper_binding.get('desktop_rebind_succeeded', False))}"
    )
    print(
        "helper_rebind_reason: "
        f"{helper_binding.get('desktop_rebind_reason') or 'none'}"
    )
    print(
        "helper_candidate: "
        f"{helper_binding.get('capture_selected_candidate_name') or 'unknown'} "
        f"[kind={helper_binding.get('capture_selected_candidate_kind') or 'unknown'} "
        f"source={helper_binding.get('capture_selected_candidate_source') or 'unknown'} "
        f"index={helper_binding.get('capture_selected_candidate_index')}]"
    )
    print(
        "helper_policy_match: "
        f"allowed={bool((helper_desktop_context.get('state') or {}).get('selected_desktop_allowed_by_policy', False))} "
        f"kind_match={bool((helper_desktop_context.get('state') or {}).get('selected_desktop_matches_preferred_kind', False))}"
    )
    print(
        "helper_handle_state: "
        f"cache_hit={bool(helper_binding.get('desktop_handle_cache_hit', False))} "
        f"reused={bool(helper_binding.get('desktop_handle_cache_reused', False))} "
        f"reopened={bool(helper_binding.get('desktop_handle_reopened', False))} "
        f"open_failed={bool(helper_binding.get('desktop_handle_open_failed', False))}"
    )
    print(
        "helper_backend: "
        f"{helper_backend.get('active_backend') or 'unknown'} "
        f"blocker={helper_backend.get('blocker_reason') or 'none'}"
    )
    candidate_trace = list(helper_binding.get("capture_candidate_trace") or [])
    print("helper_candidate_trace:")
    if candidate_trace:
        for item in candidate_trace[:8]:
            print(f"  - {item}")
    else:
        print("  - none")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Diagnose remote desktop continuity readiness via the privileged service pipe."
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit the full readiness payload as JSON.",
    )
    args = parser.parse_args()

    try:
        client = PrivilegedServiceClient()
        payload = {
            "remote_desktop_readiness": client.get_remote_desktop_readiness(),
            "display_substrate": client.get_display_substrate(),
            "virtual_display_status": client.get_virtual_display_status(force_refresh=True),
            "session_topology": client.get_session_topology(),
            "desktop_context": client.describe_desktop_context(
                reason="diagnose_remote_desktop_readiness",
                compact=not args.json,
            ),
        }
    except Exception as exc:
        print(
            json.dumps(
                {
                    "ok": False,
                    "error": str(exc),
                },
                ensure_ascii=False,
                indent=2,
            ),
            file=sys.stderr,
        )
        return 1

    if args.json:
        _emit_json(payload)
    else:
        _emit_summary(payload)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
