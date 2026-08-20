from __future__ import annotations

import json
import os
import queue
import threading
import time
from typing import Any, Callable

import pyautogui

from Capture.display_presence import DisplayPresenceProbe
from Capture.desktop_capture import DesktopFrameCapturer
from Common.logging_utils import make_component_logger
from Common.runtime_paths import get_high_integrity_helper_pipe_name
from IPC.named_pipe import NamedPipeCommandServer
from Input.input_controller import InputInjector
from agent_consent_ipc import (
    get_current_process_session_id,
    get_current_username,
    load_agent_config,
    load_tray_settings,
)
from desktop_context import InputDesktopController
from input_injector import MouseButton, MouseEvent, MouseEventType


pyautogui.FAILSAFE = False


class SecureDesktopInputBlocked(RuntimeError):
    """策略禁止在安全桌面（UAC 提示所在桌面）上注入输入时抛出。"""



class _DesktopBindingMismatch(RuntimeError):
    def __init__(
        self,
        worker_name: str,
        expected_target_signature: str,
        observed_target_signature: str,
        binding_state: dict[str, Any],
        message: str,
        *,
        mismatch_category: str = "binding_mismatch",
    ):
        super().__init__(message)
        self.worker_name = worker_name
        self.expected_target_signature = expected_target_signature
        self.observed_target_signature = observed_target_signature
        self.binding_state = dict(binding_state)
        self.mismatch_category = str(mismatch_category or "binding_mismatch")


class _DesktopBoundWorker:
    def __init__(
        self,
        *,
        name: str,
        binding_mode: str,
        desktop_controller: InputDesktopController,
        logger: Callable[[str], None],
        on_binding_applied: Callable[[dict[str, Any]], None] | None = None,
        on_worker_recycled: Callable[[dict[str, Any]], None] | None = None,
        secure_desktop_input_policy: Callable[[], bool] | None = None,
    ):
        self.name = name
        self.binding_mode = str(binding_mode or "input").strip().lower()
        self.desktop_controller = desktop_controller
        self.logger = logger
        self.on_binding_applied = on_binding_applied
        self.on_worker_recycled = on_worker_recycled
        # 安全桌面（UAC）输入策略：返回 False 时注入线程拒绝绑定/执行于安全桌面
        self.secure_desktop_input_policy = secure_desktop_input_policy
        self._tasks: queue.Queue | None = None
        self._worker_stop_event: threading.Event | None = None
        self._thread: threading.Thread | None = None
        self._call_lock = threading.Lock()
        self._state_lock = threading.Lock()
        self._last_binding_state: dict[str, Any] = {}
        self._last_desktop_signature = ""
        self._active_target_signature = ""
        self._active_target_kind = ""
        self._worker_generation = 0

    def start(self) -> None:
        with self._call_lock:
            if self._thread is not None and self._thread.is_alive():
                return
            target_state = self.desktop_controller.describe_current_state()
            resolved_binding_payload = self._resolve_binding_payload(target_state)
            target_signature = self._resolve_target_signature(target_state, resolved_binding_payload)
            self._ensure_worker_locked(
                target_signature=target_signature,
                target_state=target_state,
                reason="worker_start",
                force_recreate=True,
            )

    def stop(self, timeout: float = 2.0) -> None:
        with self._call_lock:
            self._stop_worker_locked(timeout=timeout)

    def recycle(
        self,
        reason: str,
        *,
        binding_payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        with self._call_lock:
            target_state = self.desktop_controller.describe_current_state()
            resolved_binding_payload = self._resolve_binding_payload(target_state, binding_payload)
            target_signature = self._resolve_target_signature(target_state, resolved_binding_payload)
            self._ensure_worker_locked(
                target_signature=target_signature,
                target_state=target_state,
                reason=reason,
                force_recreate=True,
            )
            return self.describe_state()

    def call(
        self,
        reason: str,
        operation: Callable[[dict[str, Any]], dict[str, Any]],
        timeout: float = 10.0,
        binding_payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        with self._call_lock:
            last_error: Exception | None = None
            for attempt in range(2):
                target_state = self.desktop_controller.describe_current_state()
                resolved_binding_payload = self._resolve_binding_payload(target_state, binding_payload)
                target_signature = self._resolve_target_signature(target_state, resolved_binding_payload)
                self._ensure_worker_locked(
                    target_signature=target_signature,
                    target_state=target_state,
                    reason=reason,
                    force_recreate=attempt > 0,
                )
                try:
                    return self._dispatch_locked(
                        reason=reason,
                        operation=operation,
                        timeout=timeout,
                        expected_target_signature=target_signature,
                        binding_payload=resolved_binding_payload,
                    )
                except _DesktopBindingMismatch as exc:
                    last_error = exc
                    self.logger(
                        f"{self.name} binding mismatch: reason={reason} attempt={attempt + 1} "
                        f"expected={target_signature or 'unknown'} "
                        f"observed={exc.observed_target_signature or 'unknown'} "
                        f"status={exc.binding_state.get('status', 'unknown')}"
                    )
                    time.sleep(0.05)
            if last_error is not None:
                raise last_error
            raise RuntimeError(f"{self.name} call failed without a reported error: reason={reason}")

    def describe_state(self) -> dict[str, Any]:
        with self._state_lock:
            state = dict(self._last_binding_state)
            state["worker_generation"] = self._worker_generation
            state["worker_target_signature"] = self._active_target_signature
            state["worker_target_kind"] = self._active_target_kind
            state["worker_alive"] = bool(self._thread is not None and self._thread.is_alive())
            return state

    def _resolve_target_signature(
        self,
        desktop_state: dict[str, Any],
        binding_payload: dict[str, Any] | None = None,
    ) -> str:
        if self.binding_mode == "capture":
            resolved_binding_payload = self._resolve_binding_payload(desktop_state, binding_payload)
            capture_target = resolved_binding_payload.get("capture_target") or {}
            return str(
                self.desktop_controller.build_capture_target_signature(
                    capture_target=capture_target,
                    state=desktop_state,
                )
            ).strip()

        return str(
            desktop_state.get("input_target_signature")
            or self.desktop_controller.build_input_target_signature(desktop_state)
        ).strip()

    def _resolve_binding_scope_signature(
        self,
        desktop_state: dict[str, Any],
        binding_payload: dict[str, Any] | None = None,
    ) -> str:
        capture_target = {}
        if self.binding_mode == "capture":
            capture_target = dict((binding_payload or {}).get("capture_target") or {})
            if not capture_target:
                capture_target = self._resolve_capture_target_payload(desktop_state, binding_payload)
        return str(
            self.desktop_controller.build_binding_scope_signature(
                binding_mode=self.binding_mode,
                capture_target=capture_target if self.binding_mode == "capture" else None,
                state=desktop_state,
            )
        ).strip()

    def _resolve_binding_payload(
        self,
        desktop_state: dict[str, Any],
        binding_payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        payload = dict(binding_payload or {})
        if self.binding_mode != "capture":
            payload["expected_binding_scope_signature"] = self._resolve_binding_scope_signature(
                desktop_state,
                payload,
            )
            return payload
        payload["capture_target"] = self._resolve_capture_target_payload(desktop_state, payload)
        payload["expected_binding_scope_signature"] = self._resolve_binding_scope_signature(
            desktop_state,
            payload,
        )
        return payload

    def _resolve_capture_target_payload(
        self,
        desktop_state: dict[str, Any],
        binding_payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        payload_target = dict((binding_payload or {}).get("capture_target") or {})
        bootstrap_target = self._build_bootstrap_capture_target(desktop_state)
        capture_target = self._merge_capture_targets(
            bootstrap_target,
            self._extract_capture_target_from_state(desktop_state),
        )
        capture_target = self._merge_capture_targets(capture_target, payload_target)
        if not self._capture_target_has_policy_metadata(capture_target):
            capture_target = self._merge_capture_targets(bootstrap_target, capture_target)
        return capture_target

    def _merge_capture_targets(
        self,
        base_target: dict[str, Any] | None,
        override_target: dict[str, Any] | None,
    ) -> dict[str, Any]:
        merged = dict(base_target or {})
        for key, value in dict(override_target or {}).items():
            if value is None:
                continue
            if isinstance(value, str) and not value.strip():
                continue
            if isinstance(value, (list, tuple, set, dict)) and not value:
                continue
            merged[key] = value
        return merged

    def _extract_capture_target_from_state(self, desktop_state: dict[str, Any]) -> dict[str, Any]:
        target_session_id = str(
            desktop_state.get("capture_target_session_id")
            or desktop_state.get("session_id")
            or ""
        ).strip()
        target_station = str(
            desktop_state.get("capture_target_station_name")
            or desktop_state.get("process_winsta")
            or ""
        ).strip()
        target_state = str(
            desktop_state.get("capture_target_state")
            or desktop_state.get("session_state")
            or desktop_state.get("status")
            or ""
        ).strip()
        if "capture_target_is_remote_session" in desktop_state:
            is_remote_session = bool(desktop_state.get("capture_target_is_remote_session"))
        else:
            is_remote_session = bool(desktop_state.get("is_remote_session"))
        if "capture_target_is_console_session" in desktop_state:
            is_console_session = bool(desktop_state.get("capture_target_is_console_session"))
        else:
            is_console_session = bool(
                target_session_id
                and str(target_session_id) == str(desktop_state.get("active_console_session_id") or "").strip()
            )
        return {
            "session_id": target_session_id,
            "station_name": target_station,
            "state": target_state,
            "is_remote_session": is_remote_session,
            "is_console_session": is_console_session,
            "preferred_capture_desktops": list(desktop_state.get("preferred_capture_desktops") or []),
            "preferred_capture_desktop_kind": str(
                desktop_state.get("preferred_capture_desktop_kind") or ""
            ).strip(),
            "authoritative_capture_binding": bool(desktop_state.get("authoritative_capture_binding")),
            "capture_binding_generation": int(desktop_state.get("capture_binding_generation") or 0),
            "capture_binding_policy_signature": str(
                desktop_state.get("capture_binding_policy_signature") or ""
            ).strip(),
            "allow_secure_desktop": bool(desktop_state.get("allow_secure_desktop")),
            "allow_screensaver_desktop": bool(desktop_state.get("allow_screensaver_desktop")),
            "desktop_transition_reason": str(desktop_state.get("desktop_transition_reason") or "").strip(),
        }

    def _capture_target_has_policy_metadata(self, capture_target: dict[str, Any] | None) -> bool:
        target = dict(capture_target or {})
        preferred_desktops = target.get("preferred_capture_desktops") or []
        preferred_kind = str(target.get("preferred_capture_desktop_kind") or "").strip()
        policy_signature = str(target.get("capture_binding_policy_signature") or "").strip()
        binding_generation = int(target.get("capture_binding_generation") or 0)
        authoritative_binding = bool(target.get("authoritative_capture_binding"))
        return bool(
            preferred_desktops
            or preferred_kind
            or policy_signature
            or binding_generation > 0
            or authoritative_binding
        )

    def _build_bootstrap_capture_target(self, desktop_state: dict[str, Any]) -> dict[str, Any]:
        preferred_desktops = list(desktop_state.get("preferred_capture_desktops") or [])
        preferred_kind = str(desktop_state.get("preferred_capture_desktop_kind") or "").strip()
        allow_secure_desktop = bool(desktop_state.get("allow_secure_desktop"))
        allow_screensaver_desktop = bool(desktop_state.get("allow_screensaver_desktop"))
        authoritative_binding = bool(
            desktop_state.get("authoritative_capture_binding")
            or preferred_desktops
            or preferred_kind
        )
        binding_generation = int(desktop_state.get("capture_binding_generation") or 0)
        if authoritative_binding and binding_generation <= 0:
            binding_generation = 1
        policy_signature = str(desktop_state.get("capture_binding_policy_signature") or "").strip()
        if authoritative_binding and not policy_signature:
            policy_signature = self._build_bootstrap_policy_signature(
                preferred_desktops=preferred_desktops,
                preferred_kind=preferred_kind,
                allow_secure_desktop=allow_secure_desktop,
                allow_screensaver_desktop=allow_screensaver_desktop,
            )
        target_session_id = str(
            desktop_state.get("capture_target_session_id")
            or desktop_state.get("session_id")
            or ""
        ).strip()
        return {
            "session_id": target_session_id,
            "station_name": str(
                desktop_state.get("capture_target_station_name")
                or desktop_state.get("process_winsta")
                or ""
            ).strip(),
            "state": str(
                desktop_state.get("capture_target_state")
                or desktop_state.get("session_state")
                or desktop_state.get("status")
                or ""
            ).strip(),
            "is_remote_session": bool(
                desktop_state.get("capture_target_is_remote_session", desktop_state.get("is_remote_session"))
            ),
            "is_console_session": bool(
                desktop_state.get(
                    "capture_target_is_console_session",
                    target_session_id
                    and str(target_session_id) == str(desktop_state.get("active_console_session_id") or "").strip(),
                )
            ),
            "preferred_capture_desktops": preferred_desktops,
            "preferred_capture_desktop_kind": preferred_kind,
            "authoritative_capture_binding": authoritative_binding,
            "capture_binding_generation": binding_generation,
            "capture_binding_policy_signature": policy_signature,
            "allow_secure_desktop": allow_secure_desktop,
            "allow_screensaver_desktop": allow_screensaver_desktop,
            "desktop_transition_reason": str(desktop_state.get("desktop_transition_reason") or "").strip(),
        }

    def _build_bootstrap_policy_signature(
        self,
        *,
        preferred_desktops: list[str],
        preferred_kind: str,
        allow_secure_desktop: bool,
        allow_screensaver_desktop: bool,
    ) -> str:
        normalized_preferred_desktops = [
            str(name).strip()
            for name in preferred_desktops
            if str(name).strip()
        ]
        return "|".join(
            [
                f"preferred={','.join(normalized_preferred_desktops) or 'none'}",
                f"kind={preferred_kind or 'unknown'}",
                f"allow_secure={1 if allow_secure_desktop else 0}",
                f"allow_screensaver={1 if allow_screensaver_desktop else 0}",
            ]
        )

    def _dispatch_locked(
        self,
        *,
        reason: str,
        operation: Callable[[dict[str, Any]], dict[str, Any]],
        timeout: float,
        expected_target_signature: str,
        binding_payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if self._tasks is None:
            raise RuntimeError(f"{self.name} worker queue is not initialized")

        result_queue: queue.Queue = queue.Queue(maxsize=1)
        self._tasks.put((reason, operation, result_queue, expected_target_signature, dict(binding_payload or {})))
        try:
            success, payload = result_queue.get(timeout=max(1.0, timeout))
        except queue.Empty as exc:
            raise TimeoutError(f"{self.name} operation timed out: reason={reason}") from exc
        if success:
            return payload
        raise payload

    def _stop_worker_locked(self, timeout: float = 2.0) -> None:
        previous_thread = self._thread
        previous_stop_event = self._worker_stop_event
        previous_tasks = self._tasks
        self._thread = None
        self._worker_stop_event = None
        self._tasks = None
        self._active_target_signature = ""
        self._active_target_kind = ""
        if previous_thread is not None:
            self._terminate_thread(previous_thread, previous_stop_event, previous_tasks, timeout=timeout)

    def _ensure_worker_locked(
        self,
        *,
        target_signature: str,
        target_state: dict[str, Any],
        reason: str,
        force_recreate: bool = False,
    ) -> None:
        thread_alive = self._thread is not None and self._thread.is_alive()
        if (
            not force_recreate
            and thread_alive
            and target_signature == self._active_target_signature
        ):
            return

        previous_thread = self._thread
        previous_stop_event = self._worker_stop_event
        previous_tasks = self._tasks
        previous_target_signature = self._active_target_signature
        previous_target_kind = self._active_target_kind

        self._thread = None
        self._worker_stop_event = None
        self._tasks = None

        if previous_thread is not None:
            self._terminate_thread(previous_thread, previous_stop_event, previous_tasks)

        self._worker_generation += 1
        self._worker_stop_event = threading.Event()
        self._tasks = queue.Queue()
        self._active_target_signature = target_signature
        self._active_target_kind = str(target_state.get("desktop_kind") or "unknown")
        self._thread = threading.Thread(
            target=self._run,
            args=(self._worker_stop_event, self._tasks),
            name=f"{self.name}-g{self._worker_generation}",
            daemon=True,
        )
        self._thread.start()

        recycle_info = {
            "reason": reason,
            "worker_name": self.name,
            "worker_generation": self._worker_generation,
            "previous_target_signature": previous_target_signature,
            "previous_target_kind": previous_target_kind,
            "next_target_signature": target_signature,
            "next_target_kind": self._active_target_kind,
            "thread_recreated": bool(previous_thread is not None),
        }
        if self.on_worker_recycled is not None:
            self.on_worker_recycled(dict(recycle_info))
        self.logger(
            f"{self.name} worker prepared: reason={reason} generation={self._worker_generation} "
            f"previous_target={previous_target_signature or 'none'} "
            f"next_target={target_signature or 'unknown'} kind={self._active_target_kind}"
        )

    def _terminate_thread(
        self,
        thread: threading.Thread | None,
        stop_event: threading.Event | None,
        tasks: queue.Queue | None,
        *,
        timeout: float = 2.0,
    ) -> None:
        if stop_event is not None:
            stop_event.set()
        if tasks is not None:
            try:
                tasks.put_nowait(None)
            except Exception:
                pass
        if thread is not None and thread.is_alive():
            thread.join(timeout=max(0.5, timeout))

    def _run(self, stop_event: threading.Event, tasks: queue.Queue) -> None:
        while not stop_event.is_set():
            task = tasks.get()
            if task is None:
                continue

            reason, operation, result_queue, expected_target_signature, binding_payload = task
            try:
                probe_state = self.desktop_controller.describe_current_state()
                resolved_binding_payload = self._resolve_binding_payload(probe_state, binding_payload)
                if self.binding_mode == "capture":
                    resolved_capture_target = resolved_binding_payload.get("capture_target")
                    binding_state = self.desktop_controller.ensure_current_thread_for_capture(
                        reason,
                        capture_target=resolved_capture_target,
                    )
                    resolved_binding_payload = self._resolve_binding_payload(binding_state, resolved_binding_payload)
                    resolved_capture_target = resolved_binding_payload.get("capture_target")
                    current_target_signature = self._resolve_target_signature(binding_state, resolved_binding_payload)
                    binding_state["capture_target_signature"] = current_target_signature
                    binding_state["capture_binding_identity"] = self.desktop_controller.build_capture_binding_identity(
                        resolved_capture_target,
                        state=binding_state,
                    )
                else:
                    binding_state = self.desktop_controller.ensure_current_thread_on_input_desktop(reason)
                    resolved_binding_payload = self._resolve_binding_payload(binding_state, resolved_binding_payload)
                    current_target_signature = self._resolve_target_signature(binding_state, resolved_binding_payload)
                    binding_state["input_target_signature"] = current_target_signature
                input_desktop_kind = str(
                    binding_state.get("input_desktop_kind")
                    or binding_state.get("desktop_kind")
                    or ""
                ).strip().lower()
                if self.binding_mode == "input" and input_desktop_kind.startswith("secure"):
                    allowed = True
                    if self.secure_desktop_input_policy is not None:
                        try:
                            allowed = bool(self.secure_desktop_input_policy())
                        except Exception:
                            allowed = True
                    if not allowed:
                        raise SecureDesktopInputBlocked(
                            f"{self.name} secure desktop input disabled by policy: "
                            f"desktop={binding_state.get('input_desktop') or 'unknown'}"
                        )
                    # 审计：每次对安全桌面（UAC）的授权注入都留痕
                    self.logger(
                        f"AUDIT {self.name} secure-desktop input authorized: reason={reason} "
                        f"desktop={binding_state.get('input_desktop') or 'unknown'}"
                    )
                    binding_state["secure_desktop_authorized"] = True
                self._record_binding_state(binding_state)
                current_signature = str(binding_state.get("desktop_signature") or "").strip()
                if current_signature and current_signature != self._last_desktop_signature:
                    previous_signature = self._last_desktop_signature or "uninitialized"
                    self.logger(
                        f"{self.name} desktop transition: previous={previous_signature} current={current_signature}"
                    )
                    self._last_desktop_signature = current_signature
                self._validate_binding_state(
                    binding_state=binding_state,
                    expected_target_signature=expected_target_signature,
                    observed_target_signature=current_target_signature,
                    expected_binding_scope_signature=str(
                        resolved_binding_payload.get("expected_binding_scope_signature") or ""
                    ).strip(),
                    reason=reason,
                )
                if self.on_binding_applied is not None:
                    self.on_binding_applied(dict(binding_state))
                result_queue.put((True, operation(dict(binding_state))))
            except Exception as exc:
                self.logger(f"{self.name} task failed: reason={reason} error={exc}")
                result_queue.put((False, exc))

    def _record_binding_state(self, binding_state: dict[str, Any]) -> None:
        with self._state_lock:
            self._last_binding_state = dict(binding_state)

    def _validate_binding_state(
        self,
        *,
        binding_state: dict[str, Any],
        expected_target_signature: str,
        observed_target_signature: str,
        expected_binding_scope_signature: str,
        reason: str,
    ) -> None:
        if expected_target_signature and observed_target_signature != expected_target_signature:
            raise _DesktopBindingMismatch(
                self.name,
                expected_target_signature,
                observed_target_signature,
                binding_state,
                (
                    f"{self.name} bound to unexpected desktop target for {reason}: "
                    f"expected={expected_target_signature} observed={observed_target_signature or 'unknown'}"
                ),
                mismatch_category="target_signature_mismatch",
            )

        observed_binding_scope_signature = str(
            binding_state.get("desktop_binding_scope_signature")
            or binding_state.get("desktop_handle_scope_signature")
            or ""
        ).strip()
        if (
            expected_binding_scope_signature
            and observed_binding_scope_signature
            and observed_binding_scope_signature != expected_binding_scope_signature
        ):
            raise _DesktopBindingMismatch(
                self.name,
                expected_target_signature,
                observed_target_signature,
                binding_state,
                (
                    f"{self.name} desktop context drift detected for {reason}: "
                    f"expected_scope={expected_binding_scope_signature} "
                    f"observed_scope={observed_binding_scope_signature}"
                ),
                mismatch_category="context_scope_mismatch",
            )

        if self.binding_mode == "capture":
            current_session_id = str(binding_state.get("session_id") or "").strip()
            target_session_id = str(binding_state.get("capture_target_session_id") or current_session_id).strip()
            if current_session_id and target_session_id and current_session_id != target_session_id:
                raise _DesktopBindingMismatch(
                    self.name,
                    expected_target_signature,
                    observed_target_signature,
                    binding_state,
                    (
                        f"{self.name} capture session mismatch for {reason}: "
                        f"current_session={current_session_id or 'unknown'} "
                        f"target_session={target_session_id or 'unknown'}"
                    ),
                    mismatch_category="session_mismatch",
                )

            if bool(binding_state.get("authoritative_capture_binding")):
                binding_generation = int(binding_state.get("capture_binding_generation") or 0)
                policy_signature = str(binding_state.get("capture_binding_policy_signature") or "").strip()
                if binding_generation <= 0 or not policy_signature:
                    raise _DesktopBindingMismatch(
                        self.name,
                        expected_target_signature,
                        observed_target_signature,
                        binding_state,
                        (
                            f"{self.name} authoritative capture policy missing for {reason}: "
                            f"generation={binding_generation} policy={policy_signature or 'none'}"
                        ),
                        mismatch_category="policy_metadata_missing",
                    )
                if not bool(binding_state.get("selected_desktop_allowed_by_policy")):
                    raise _DesktopBindingMismatch(
                        self.name,
                        expected_target_signature,
                        observed_target_signature,
                        binding_state,
                        (
                            f"{self.name} selected desktop is outside authoritative capture policy for {reason}: "
                            f"capture={binding_state.get('capture_desktop', 'unknown')} "
                            f"allowed={binding_state.get('preferred_capture_desktops', [])}"
                        ),
                        mismatch_category="policy_desktop_mismatch",
                    )
                if not bool(binding_state.get("selected_desktop_matches_preferred_kind")):
                    raise _DesktopBindingMismatch(
                        self.name,
                        expected_target_signature,
                        observed_target_signature,
                        binding_state,
                        (
                            f"{self.name} selected desktop kind mismatches authoritative policy for {reason}: "
                            f"capture_kind={binding_state.get('capture_desktop_kind', 'unknown')} "
                            f"preferred_kind={binding_state.get('preferred_capture_desktop_kind', 'unknown')}"
                        ),
                        mismatch_category="policy_kind_mismatch",
                    )

            if bool(binding_state.get("capture_thread_matches_target")):
                return
            raise _DesktopBindingMismatch(
                self.name,
                expected_target_signature,
                observed_target_signature,
                binding_state,
                (
                    f"{self.name} thread is not bound to the selected capture desktop for {reason}: "
                    f"status={binding_state.get('status', 'unknown')} "
                    f"thread={binding_state.get('thread_desktop', 'unknown')} "
                    f"capture={binding_state.get('capture_desktop', 'unknown')}"
                ),
                mismatch_category="thread_binding_mismatch",
            )

        if bool(binding_state.get("thread_matches_input")):
            return

        raise _DesktopBindingMismatch(
            self.name,
            expected_target_signature,
            observed_target_signature,
            binding_state,
            (
                f"{self.name} thread is not bound to the active input desktop for {reason}: "
                f"status={binding_state.get('status', 'unknown')} "
                f"thread={binding_state.get('thread_desktop', 'unknown')} "
                f"input={binding_state.get('input_desktop', 'unknown')}"
            ),
            mismatch_category="input_thread_binding_mismatch",
        )


class HighIntegritySessionHelperRuntime:
    MODIFIER_KEYS = {"ctrl", "shift", "alt"}
    MOUSE_BUTTONS = {
        "left": MouseButton.LEFT,
        "right": MouseButton.RIGHT,
        "middle": MouseButton.MIDDLE,
    }
    MOUSE_EVENT_TYPES = {
        "move": MouseEventType.MOVE,
        "down": MouseEventType.DOWN,
        "up": MouseEventType.UP,
        "click": MouseEventType.CLICK,
        "wheel": MouseEventType.WHEEL,
    }

    def __init__(self, session_id: int | None):
        self.session_id = session_id if session_id is not None else get_current_process_session_id()
        if self.session_id is None:
            raise RuntimeError("high-integrity helper requires a resolved session id")

        self.pipe_name = get_high_integrity_helper_pipe_name(self.session_id)
        self.logger = make_component_logger("HighIntegrityHelper")
        self.desktop_controller = InputDesktopController("HighIntegrityHelper")
        self.capture_desktop_controller = InputDesktopController("HighIntegrityHelperCapture")
        self.input_desktop_controller = InputDesktopController("HighIntegrityHelperInput")
        self.input_injector = InputInjector(manage_cursor_visibility=False, privileged_client=None)
        self.frame_capturer = DesktopFrameCapturer(
            backend_order=("dxgi", "wgc", "dwm", "mss", "gdi", "imagegrab", "pyautogui")
        )
        self.display_presence_probe = DisplayPresenceProbe()
        self._active_keys: set[str] = set()
        self._modifier_states = {key: False for key in self.MODIFIER_KEYS}
        self._keyboard_lock = threading.Lock()
        self._capture_lock = threading.Lock()
        self._capture_watchdog_lock = threading.Lock()
        self._stop_event = threading.Event()
        self._last_input_desktop_signature = ""
        self._capture_watchdog = {
            "empty_streak": 0,
            "unchanged_streak": 0,
            "binding_mismatch_streak": 0,
            "policy_mismatch_streak": 0,
            "reset_count": 0,
            "worker_recycle_count": 0,
            "last_recovery_action": "",
            "last_failure_reason": "",
            "last_backend": "",
            "last_signature": "",
            "last_capture_timestamp": 0.0,
            "last_binding_generation": 0,
            "last_policy_signature": "",
            "last_backend_generation": 0,
            "last_context_label": "",
            "last_blocker_reason": "",
        }
        self._capture_worker = _DesktopBoundWorker(
            name=f"cmdb-helper-capture-{self.session_id}",
            binding_mode="capture",
            desktop_controller=self.capture_desktop_controller,
            logger=self.logger,
            on_binding_applied=self._handle_capture_binding_applied,
            on_worker_recycled=self._handle_capture_worker_recycled,
        )
        self._input_worker = _DesktopBoundWorker(
            name=f"cmdb-helper-input-{self.session_id}",
            binding_mode="input",
            desktop_controller=self.input_desktop_controller,
            logger=self.logger,
            on_binding_applied=self._handle_input_binding_applied,
            secure_desktop_input_policy=self._secure_desktop_input_allowed,
        )
        self._secure_desktop_policy_cache = {"value": None, "expires": 0.0}
        self._pipe_server = NamedPipeCommandServer(
            self.pipe_name,
            self._handle_request,
            logger=self.logger,
            allow_all_users=True,
            # 安全审计 P0-5：会话范围 DACL + 客户端会话校验
            expected_session_id=self.session_id,
            enforce_session_scope=True,
        )

    def _secure_desktop_input_allowed(self) -> bool:
        """安全桌面（UAC）输入策略：config 默认值 + 托盘开关覆盖，带 2 秒缓存。"""
        now = time.monotonic()
        cache = getattr(self, "_secure_desktop_policy_cache", None)
        if cache is None:
            cache = self._secure_desktop_policy_cache = {"value": None, "expires": 0.0}
        if now < float(cache["expires"]) and cache["value"] is not None:
            return bool(cache["value"])

        allowed = True  # 与商业远控一致：会话已获同意的前提下默认允许操作 UAC
        try:
            remote_settings = (load_agent_config() or {}).get("remote_desktop") or {}
            allowed = bool(remote_settings.get("allow_secure_desktop_input", True))
        except Exception:
            pass
        try:
            tray_value = load_tray_settings().get("allow_secure_desktop_input")
            if tray_value is not None:
                allowed = bool(tray_value)
        except Exception:
            pass

        cache["value"] = bool(allowed)
        cache["expires"] = now + 2.0
        return bool(allowed)

    def run_forever(self) -> None:
        self._capture_worker.start()
        self._input_worker.start()
        self._pipe_server.start()
        self.logger(
            "helper runtime active: "
            f"session_id={self.session_id} pipe={self.pipe_name} username={get_current_username()}"
        )
        # 预热 H.264 编码器依赖：av/FFmpeg 首次导入约 1-2s，提前后台完成，
        # 避免远控会话首个关键帧卡顿（对桌面切换/助手重建场景尤其明显）
        import threading as _threading

        def _warm_h264():
            try:
                from Codec.h264_encoder import h264_available  # noqa: F401

                h264_available()
            except Exception:
                pass

        _threading.Thread(target=_warm_h264, daemon=True, name="h264-warmup").start()
        try:
            while not self._stop_event.wait(1.0):
                pass
        finally:
            self.stop()

    def stop(self) -> None:
        self._stop_event.set()
        try:
            self._release_input_state()
        except Exception:
            pass
        try:
            self.input_injector.stop()
        except Exception:
            pass
        try:
            self.frame_capturer.close()
        except Exception:
            pass
        self._capture_worker.stop()
        self._input_worker.stop()
        self._pipe_server.stop()

    def _enrich_desktop_state_with_display_presence(
        self,
        desktop_state: dict[str, Any] | None,
    ) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
        state = dict(desktop_state or {})
        inventory = self.display_presence_probe.get_display_inventory(force_refresh=False)
        assessment = self.display_presence_probe.assess(
            desktop_state=state,
            inventory=inventory,
        ).to_dict()
        state.update(
            {
                "display_substrate_class": str(assessment.get("substrate_class") or ""),
                "display_continuity_mode": str(assessment.get("continuity_mode") or ""),
                "display_persistent": bool(assessment.get("persistent", False)),
                "display_best_effort_only": bool(assessment.get("best_effort_only", False)),
                "display_surface_available": bool(
                    assessment.get("display_surface_available", False)
                ),
                "physical_display_attached": bool(
                    assessment.get("physical_display_attached", False)
                ),
                "virtual_display_attached": bool(
                    assessment.get("virtual_display_attached", False)
                ),
                "remote_display_surface": bool(
                    assessment.get("remote_display_surface", False)
                ),
                "secure_desktop_surface": bool(
                    assessment.get("secure_desktop_surface", False)
                ),
                "disconnected_surface": bool(
                    assessment.get("disconnected_surface", False)
                ),
                "render_monitor_count": int(assessment.get("render_monitor_count") or 0),
                "attached_display_count": int(assessment.get("attached_display_count") or 0),
                "requires_virtual_display_for_full_continuity": bool(
                    assessment.get("requires_virtual_display_for_full_continuity", False)
                ),
                "display_rank_hint": int(assessment.get("rank_hint") or 0),
                "display_notes": list(assessment.get("notes") or []),
                "display_inventory_remote_adapter_present": bool(
                    inventory.get("remote_adapter_present", False)
                ),
                "display_inventory_notes": list(inventory.get("notes") or []),
            }
        )
        return state, assessment, inventory

    def _handle_request(self, request: dict[str, Any]) -> dict[str, Any]:
        command = str(request.get("command") or "").strip().lower()
        payload = request.get("payload") or {}
        request_id = request.get("request_id")

        try:
            if command == "ping":
                response_payload = self._build_status_payload()
            elif command == "describe_desktop_context":
                response_payload = self._describe_desktop_context(payload)
            elif command == "capture_frame":
                response_payload = self._capture_frame(payload)
            elif command == "inject_mouse_event":
                response_payload = self._inject_mouse_event(payload)
            elif command == "inject_keyboard_event":
                response_payload = self._inject_keyboard_event(payload)
            elif command == "release_input_state":
                response_payload = self._release_input_state()
            else:
                raise ValueError(f"unsupported helper command: {command}")

            return {
                "ok": True,
                "payload": response_payload,
                "request_id": request_id,
            }
        except Exception as exc:
            self.logger(f"request failed: command={command} error={exc}")
            return {
                "ok": False,
                "error": str(exc),
                "request_id": request_id,
            }

    def _build_status_payload(self) -> dict[str, Any]:
        desktop_state, display_presence, display_inventory = self._enrich_desktop_state_with_display_presence(
            self.desktop_controller.describe_current_state()
        )
        desktop_binding_state = self.desktop_controller.describe_binding_state()
        desktop_transition_state = self.desktop_controller.describe_transition_state()
        username = get_current_username()
        return {
            "session_id": self.session_id,
            "pipe_name": self.pipe_name,
            "pid": os.getpid(),
            "username": username,
            "is_system": username.upper() == "SYSTEM",
            "desktop_context": {
                "input_desktop_available": bool(self.desktop_controller.has_input_desktop()),
                "state": desktop_state,
                "binding_state": desktop_binding_state,
                "transition_state": desktop_transition_state,
                "display_presence": display_presence,
                "display_inventory": {
                    "physical_display_attached": bool(
                        display_inventory.get("physical_display_attached", False)
                    ),
                    "virtual_display_attached": bool(
                        display_inventory.get("virtual_display_attached", False)
                    ),
                    "remote_adapter_present": bool(
                        display_inventory.get("remote_adapter_present", False)
                    ),
                    "attached_display_count": int(
                        display_inventory.get("attached_display_count") or 0
                    ),
                    "render_monitor_count": int(
                        display_inventory.get("render_monitor_count") or 0
                    ),
                    "notes": list(display_inventory.get("notes") or []),
                },
                "capture_worker_state": self._capture_worker.describe_state(),
                "input_worker_state": self._input_worker.describe_state(),
                "capture_watchdog": self._snapshot_capture_watchdog(),
                "backend_diagnostics": self.frame_capturer.describe_backend_state(),
            },
        }

    def _describe_desktop_context(self, payload: dict[str, Any]) -> dict[str, Any]:
        response = self._build_status_payload()
        response["reason"] = str(payload.get("reason") or "").strip()
        response["source"] = "high_integrity_helper"
        return response

    def _non_persistent_capture_blocker(
        self,
        display_presence: dict[str, Any],
        desktop_state: dict[str, Any],
    ) -> str:
        substrate_class = str(display_presence.get("substrate_class") or "").strip().lower()
        best_effort_only = bool(display_presence.get("best_effort_only", False))
        display_surface_available = bool(display_presence.get("display_surface_available", False))
        display_persistent = bool(display_presence.get("persistent", False))
        remote_display_surface = bool(display_presence.get("remote_display_surface", False))
        disconnected_surface = bool(display_presence.get("disconnected_surface", False))
        transient_substrates = {
            "remote_session_surface",
            "disconnected_surface",
            "console_headless_surface",
            "unknown_best_effort",
            "display_surface_unavailable",
        }
        if substrate_class in transient_substrates:
            return "non_persistent_capture_surface"
        if disconnected_surface:
            return "disconnected_capture_surface"
        if best_effort_only and not display_persistent:
            return "best_effort_capture_surface"
        if not display_surface_available and not display_persistent:
            return "display_surface_unavailable"
        if remote_display_surface and not display_persistent:
            return "remote_display_surface_without_persistent_substrate"
        if bool(desktop_state.get("display_best_effort_only")) and not bool(
            desktop_state.get("display_persistent")
        ):
            return "best_effort_capture_context"
        return ""

    def _encode_h264_packets(self, screenshot, force_keyframe: bool) -> list[dict[str, Any]]:
        """抓屏帧 → H.264 Annex-B 包（base64），持久编码器随分辨率重建。"""
        import base64 as _base64
        from Codec.h264_encoder import H264StreamEncoder

        width = int(getattr(screenshot, "width", 0) or 0)
        height = int(getattr(screenshot, "height", 0) or 0)
        enc = getattr(self, "_h264_encoder", None)
        if enc is None or enc.width != width or enc.height != height:
            try:
                if enc is not None:
                    enc.close()
            except Exception:
                pass
            enc = H264StreamEncoder(width, height, fps=30, crf=int(getattr(self, "_h264_crf", 21)))
            self._h264_encoder = enc
            self._h264_fail_count = 0
        pkts = enc.encode(screenshot, keyframe=force_keyframe)
        return [
            {"data": _base64.b64encode(p["data"]).decode("ascii"), "keyframe": bool(p["keyframe"])}
            for p in pkts
        ]

    def _capture_frame(self, payload: dict[str, Any]) -> dict[str, Any]:
        quality = max(25, min(95, int(payload.get("quality") or 75)))
        scale = float(payload.get("scale") or 1.0)
        scale = max(0.2, min(1.0, scale))
        previous_signature = payload.get("previous_signature")
        include_desktop_state = bool(payload.get("include_desktop_state", False))
        include_backend_diagnostics = bool(
            payload.get("include_backend_diagnostics", include_desktop_state)
        )
        
        def capture_operation(binding_state: dict[str, Any]) -> dict[str, Any]:
            op_started_at = time.perf_counter()
            captured_at = time.time()
            desktop_state, display_presence, _display_inventory = self._enrich_desktop_state_with_display_presence(
                binding_state
            )
            desktop_signature = str(desktop_state.get("desktop_signature") or "").strip()
            enrich_done_at = time.perf_counter()
            self.frame_capturer.prepare_for_desktop(
                desktop_signature,
                desktop_state=desktop_state,
                reason="capture_worker_desktop_transition",
            )
            prepare_done_at = time.perf_counter()
            backend_diagnostics = (
                self.frame_capturer.describe_backend_state()
                if include_backend_diagnostics
                else None
            )
            blocker = self._non_persistent_capture_blocker(display_presence, desktop_state)
            fallback_allowed = self.frame_capturer.fallback_capture_allowed()
            if blocker and not fallback_allowed:
                return {
                    "captured": False,
                    "empty": True,
                    "blocker": blocker,
                    "session_id": self.session_id,
                    "backend": str(self.frame_capturer.capture_backend or ""),
                    "captured_at": captured_at,
                    "display_presence": display_presence,
                    "desktop_context": desktop_state if include_desktop_state else None,
                    "backend_diagnostics": backend_diagnostics,
                }
            # blocker 存在但允许回退（RustDesk 式 GDI 降级）：继续尝试采集，
            # capture_raw 内部会用 mss/gdi 系后端读取桌面表面，出帧后标记 captured_fallback。
            screenshot = self.frame_capturer.capture_raw()
            grab_done_at = time.perf_counter()
            backend_diagnostics = (
                self.frame_capturer.describe_backend_state()
                if include_backend_diagnostics
                else None
            )
            if screenshot is None:
                return {
                    "captured": False,
                    "empty": True,
                    "blocker": blocker or None,
                    "session_id": self.session_id,
                    "backend": str(self.frame_capturer.capture_backend or ""),
                    "captured_at": captured_at,
                    "display_presence": display_presence,
                    "desktop_context": desktop_state if include_desktop_state else None,
                    "backend_diagnostics": backend_diagnostics,
                }

            try:
                signature = self.frame_capturer.build_signature(screenshot)
                response = {
                    "captured": True,
                    "unchanged": previous_signature is not None and signature == previous_signature,
                    "signature": signature,
                    "session_id": self.session_id,
                    "backend": str(self.frame_capturer.capture_backend or ""),
                    "captured_at": captured_at,
                    "desktop_signature": desktop_signature,
                    "display_presence": display_presence,
                    "backend_diagnostics": backend_diagnostics,
                }
                if blocker:
                    # 回退出帧成功：向平台透出兼容模式标记
                    response["blocker"] = blocker
                    response["fallback_capture"] = True
                if include_desktop_state:
                    response["desktop_context"] = desktop_state

                if not response["unchanged"]:
                    if (
                        payload.get("codec") == "h264"
                        and not getattr(self, "_h264_disabled", False)
                        and not getattr(self, "_h264_unavailable", False)
                    ):
                        # 助手侧直接 H.264 编码：抓屏后不再走 JPEG 编码，
                        # 管道只传小体积 H.264 包（服务端零转码直发观看端）
                        try:
                            # 动态分辨率：按引擎下发的 scale 缩放（弱机降采样保帧率）
                            h264_scale = 1.0
                            try:
                                h264_scale = float(payload.get("scale") or 1.0)
                            except Exception:
                                h264_scale = 1.0
                            if h264_scale < 0.99 and screenshot.width > 4:
                                try:
                                    from PIL import Image as _PILImage
                                    new_w = max(2, int(screenshot.width * h264_scale) // 2 * 2)
                                    new_h = max(2, int(screenshot.height * h264_scale) // 2 * 2)
                                    screenshot = screenshot.resize((new_w, new_h), _PILImage.BILINEAR)
                                except Exception:
                                    pass
                            self._h264_crf = int(payload.get("crf") or 21)
                            packets = self._encode_h264_packets(
                                screenshot, force_keyframe=bool(payload.get("force_keyframe"))
                            )
                            response["codec"] = "h264"
                            response["h264_packets"] = packets
                            response["h264_width"] = int(getattr(screenshot, "width", 0) or 0)
                            response["h264_height"] = int(getattr(screenshot, "height", 0) or 0)
                        except Exception as exc:
                            self._h264_fail_count = int(getattr(self, "_h264_fail_count", 0)) + 1
                            self.logger(
                                f"h264 helper encode failed streak={self._h264_fail_count} error={exc}"
                            )
                            if self._h264_fail_count >= 5:
                                self._h264_disabled = True
                            # 编码失败：回落 JPEG 路径
                            frame = self.frame_capturer.encode_frame(screenshot, quality=quality, scale=scale)
                            frame["signature"] = signature
                            response["frame"] = frame
                    else:
                        frame = self.frame_capturer.encode_frame(screenshot, quality=quality, scale=scale)
                        frame["signature"] = signature
                        response["frame"] = frame
                encode_done_at = time.perf_counter()
                self.logger(
                    "capture_frame timing: "
                    f"enrich={enrich_done_at - op_started_at:.3f}s "
                    f"prepare={prepare_done_at - enrich_done_at:.3f}s "
                    f"grab={grab_done_at - prepare_done_at:.3f}s "
                    f"encode+sig={encode_done_at - grab_done_at:.3f}s "
                    f"total={encode_done_at - op_started_at:.3f}s "
                    f"backend={self.frame_capturer.capture_backend} "
                    f"unchanged={response.get('unchanged')}"
                )
                return response
            finally:
                try:
                    screenshot.close()
                except Exception:
                    pass

        with self._capture_lock:
            last_error: Exception | None = None
            for attempt in range(2):
                try:
                    response = self._capture_worker.call(
                        "helper_capture_frame",
                        capture_operation,
                        timeout=12.0,
                        binding_payload=payload,
                    )
                except _DesktopBindingMismatch as exc:
                    last_error = exc
                    should_retry = self._handle_capture_binding_mismatch(exc, payload)
                    if should_retry and attempt == 0:
                        continue
                    raise

                should_retry = self._update_capture_watchdog_from_response(response, payload)
                if should_retry and attempt == 0:
                    continue
                return response

            if last_error is not None:
                raise last_error
            raise RuntimeError("helper capture failed without a reported error")

    def _inject_mouse_event(self, payload: dict[str, Any]) -> dict[str, Any]:
        event_payload = payload.get("event") or {}
        event_type = self.MOUSE_EVENT_TYPES.get(str(event_payload.get("type") or "").strip().lower())
        if event_type is None:
            raise ValueError("missing or invalid mouse event type")

        button = self.MOUSE_BUTTONS.get(
            str(event_payload.get("button") or "left").strip().lower(),
            MouseButton.LEFT,
        )
        event = MouseEvent(
            event_type,
            event_payload.get("x"),
            event_payload.get("y"),
            button=button,
            delta=int(event_payload.get("delta") or 0),
            normalized_x=event_payload.get("normalized_x"),
            normalized_y=event_payload.get("normalized_y"),
        )
        def mouse_operation(_binding_state: dict[str, Any]) -> dict[str, Any]:
            self.input_injector._execute_mouse_event(event)
            return {
                "injected": True,
                "session_id": self.session_id,
                "event": {
                    "type": event.type.value,
                    "button": event.button.value,
                    "x": event.x,
                    "y": event.y,
                    "normalized_x": event.normalized_x,
                    "normalized_y": event.normalized_y,
                    "delta": event.delta,
                },
            }

        result = self._input_worker.call("helper_mouse_injection", mouse_operation, timeout=8.0)

        self.logger(
            "mouse event injected via helper: "
            f"type={event.type.value} button={event.button.value} "
            f"target=({event.x},{event.y}) normalized=({event.normalized_x},{event.normalized_y}) "
            f"delta={event.delta}"
        )
        return result

    def _inject_keyboard_event(self, payload: dict[str, Any]) -> dict[str, Any]:
        action = str(payload.get("action") or "").strip().lower()
        key = str(payload.get("key") or "").strip().lower()
        text = str(payload.get("text") or "")
        modifiers = {
            "ctrl": bool(payload.get("ctrlKey", False)),
            "shift": bool(payload.get("shiftKey", False)),
            "alt": bool(payload.get("altKey", False)),
        }

        if not action:
            raise ValueError("missing keyboard action")

        def keyboard_operation(_binding_state: dict[str, Any]) -> dict[str, Any]:
            with self._keyboard_lock:
                if action == "keydown":
                    self._sync_modifier_states(modifiers)
                    if key in self.MODIFIER_KEYS:
                        if not self._modifier_states[key]:
                            pyautogui.keyDown(key)
                            self._modifier_states[key] = True
                    elif key and key not in self._active_keys:
                        pyautogui.keyDown(key)
                        self._active_keys.add(key)
                elif action == "keyup":
                    if key in self.MODIFIER_KEYS:
                        if self._modifier_states[key]:
                            pyautogui.keyUp(key)
                            self._modifier_states[key] = False
                    elif key and key in self._active_keys:
                        pyautogui.keyUp(key)
                        self._active_keys.discard(key)

                    self._sync_modifier_states(modifiers)
                elif action == "press":
                    active_modifiers = [name for name, enabled in modifiers.items() if enabled]
                    if key and active_modifiers and key not in self.MODIFIER_KEYS:
                        pyautogui.hotkey(*active_modifiers, key)
                    elif key:
                        pyautogui.press(key)
                elif action == "type":
                    if text:
                        try:
                            from Input.input_controller import send_unicode_text

                            # 逐键模拟会被终端输入法拦截成拼音组合，必须走 UNICODE 注入
                            if not send_unicode_text(text):
                                pyautogui.typewrite(text, interval=0.01)
                        except ImportError:
                            pyautogui.typewrite(text, interval=0.01)
                else:
                    raise ValueError(f"unsupported keyboard action: {action}")
            return {
                "injected": True,
                "session_id": self.session_id,
                "action": action,
                "key": key,
                "text_length": len(text),
                "modifiers": modifiers,
            }

        result = self._input_worker.call("helper_keyboard_injection", keyboard_operation, timeout=8.0)

        self.logger(
            "keyboard event injected via helper: "
            f"action={action} key={key or 'n/a'} modifiers={json.dumps(modifiers, ensure_ascii=False)} "
            f"text_length={len(text)}"
        )
        return result

    def _sync_modifier_states(self, target_modifiers: dict[str, bool]) -> None:
        for key, enabled in target_modifiers.items():
            current = self._modifier_states.get(key, False)
            if enabled and not current:
                pyautogui.keyDown(key)
                self._modifier_states[key] = True
            elif not enabled and current:
                pyautogui.keyUp(key)
                self._modifier_states[key] = False

    def _release_input_state(self) -> dict[str, Any]:
        def release_operation(_binding_state: dict[str, Any]) -> dict[str, Any]:
            with self._keyboard_lock:
                self.input_injector.release_mouse_buttons(list(self.input_injector.pressed_buttons))
                self._release_pressed_keys()
            return {
                "released": True,
                "session_id": self.session_id,
            }

        result = self._input_worker.call("helper_release_input_state", release_operation, timeout=8.0)
        self.logger("input state released via helper")
        return result

    def _release_pressed_keys(self) -> None:
        for key in list(self._active_keys):
            try:
                pyautogui.keyUp(key)
            except Exception:
                pass
        self._active_keys.clear()

        for key, enabled in list(self._modifier_states.items()):
            if not enabled:
                continue
            try:
                pyautogui.keyUp(key)
            except Exception:
                pass
            self._modifier_states[key] = False

    def _handle_capture_binding_applied(self, binding_state: dict[str, Any]) -> None:
        enriched_state, _display_presence, _display_inventory = self._enrich_desktop_state_with_display_presence(
            binding_state
        )
        desktop_signature = str(enriched_state.get("desktop_signature") or "").strip()
        self.frame_capturer.prepare_for_desktop(
            desktop_signature,
            desktop_state=enriched_state,
            reason="capture_worker_binding_applied",
        )
        with self._capture_watchdog_lock:
            self._capture_watchdog["last_binding_generation"] = int(
                enriched_state.get("capture_binding_generation") or 0
            )
            self._capture_watchdog["last_policy_signature"] = str(
                enriched_state.get("capture_binding_policy_signature") or ""
            )

    def _handle_capture_worker_recycled(self, recycle_info: dict[str, Any]) -> None:
        try:
            self.frame_capturer.close()
        except Exception:
            pass
        if bool(recycle_info.get("thread_recreated")):
            with self._capture_watchdog_lock:
                self._capture_watchdog["worker_recycle_count"] = int(
                    self._capture_watchdog.get("worker_recycle_count") or 0
                ) + 1
                self._capture_watchdog["last_recovery_action"] = str(
                    recycle_info.get("reason") or "worker_recycled"
                )
        self.logger(
            "capture worker recycled for desktop transition: "
            f"reason={recycle_info.get('reason', 'unknown')} "
            f"previous={recycle_info.get('previous_target_signature', 'none') or 'none'} "
            f"next={recycle_info.get('next_target_signature', 'unknown') or 'unknown'} "
            f"generation={recycle_info.get('worker_generation', 'unknown')}"
        )

    def _handle_input_binding_applied(self, binding_state: dict[str, Any]) -> None:
        desktop_signature = str(binding_state.get("desktop_signature") or "").strip()
        if not desktop_signature or desktop_signature == self._last_input_desktop_signature:
            return
        self._last_input_desktop_signature = desktop_signature
        self.logger(
            "input worker bound to desktop: "
            f"session={binding_state.get('session_id', 'unknown')} "
            f"desktop={binding_state.get('input_desktop', 'unknown')} "
            f"kind={binding_state.get('desktop_kind', 'unknown')} "
            f"signature={desktop_signature}"
        )

    def _snapshot_capture_watchdog(self) -> dict[str, Any]:
        with self._capture_watchdog_lock:
            return dict(self._capture_watchdog)

    def _handle_capture_binding_mismatch(
        self,
        exc: _DesktopBindingMismatch,
        payload: dict[str, Any],
    ) -> bool:
        mismatch_category = str(exc.mismatch_category or "binding_mismatch")
        with self._capture_watchdog_lock:
            self._capture_watchdog["binding_mismatch_streak"] = int(
                self._capture_watchdog.get("binding_mismatch_streak") or 0
            ) + 1
            if mismatch_category.startswith("policy_"):
                self._capture_watchdog["policy_mismatch_streak"] = int(
                    self._capture_watchdog.get("policy_mismatch_streak") or 0
                ) + 1
            else:
                self._capture_watchdog["policy_mismatch_streak"] = 0
            self._capture_watchdog["last_failure_reason"] = mismatch_category
            binding_mismatch_streak = int(self._capture_watchdog["binding_mismatch_streak"])
            policy_mismatch_streak = int(self._capture_watchdog["policy_mismatch_streak"])

        should_recycle = policy_mismatch_streak >= 1 or binding_mismatch_streak >= 2
        if should_recycle:
            self._recover_capture_runtime(
                reason=f"watchdog_{mismatch_category}",
                recycle_worker=True,
                payload=payload,
            )
        return should_recycle

    def _update_capture_watchdog_from_response(
        self,
        response: dict[str, Any],
        payload: dict[str, Any],
    ) -> bool:
        desktop_context = dict(response.get("desktop_context") or {})
        backend_diagnostics = dict(response.get("backend_diagnostics") or {})
        blocker = str(response.get("blocker") or "").strip()
        backend = str(
            response.get("backend")
            or backend_diagnostics.get("active_backend")
            or backend_diagnostics.get("current_backend")
            or self.frame_capturer.capture_backend
            or ""
        )
        captured_at = float(response.get("captured_at") or time.time())
        frame_signature = str(response.get("signature") or "").strip()
        captured = bool(response.get("captured"))
        empty = bool(response.get("empty"))
        unchanged = bool(response.get("unchanged"))
        backend_generation = int(backend_diagnostics.get("desktop_generation") or 0)
        context_label = str(backend_diagnostics.get("context_label") or "").strip()
        blocker_reason = str(backend_diagnostics.get("blocker_reason") or blocker or "").strip()
        backend_failure = self._extract_backend_failure_reason(
            backend_diagnostics,
            backend=backend,
        )
        recovery_hint = self._extract_backend_recovery_hint(
            backend_diagnostics,
            backend=backend,
        )

        with self._capture_watchdog_lock:
            previous_backend_generation = int(self._capture_watchdog.get("last_backend_generation") or 0)
            previous_context_label = str(self._capture_watchdog.get("last_context_label") or "")
            previous_blocker_reason = str(self._capture_watchdog.get("last_blocker_reason") or "")
            previous_recovery_action = str(self._capture_watchdog.get("last_recovery_action") or "")

        with self._capture_watchdog_lock:
            self._capture_watchdog["last_backend"] = backend
            self._capture_watchdog["last_capture_timestamp"] = captured_at
            self._capture_watchdog["last_backend_generation"] = backend_generation
            self._capture_watchdog["last_context_label"] = context_label
            self._capture_watchdog["last_blocker_reason"] = blocker_reason
            if desktop_context:
                self._capture_watchdog["last_binding_generation"] = int(
                    desktop_context.get("capture_binding_generation") or 0
                )
                self._capture_watchdog["last_policy_signature"] = str(
                    desktop_context.get("capture_binding_policy_signature") or ""
                )

            if captured:
                self._capture_watchdog["empty_streak"] = 0
                self._capture_watchdog["binding_mismatch_streak"] = 0
                self._capture_watchdog["policy_mismatch_streak"] = 0
                self._capture_watchdog["last_failure_reason"] = ""
                if unchanged:
                    self._capture_watchdog["unchanged_streak"] = int(
                        self._capture_watchdog.get("unchanged_streak") or 0
                    ) + 1
                else:
                    self._capture_watchdog["unchanged_streak"] = 0
                    self._capture_watchdog["last_signature"] = frame_signature
            elif empty:
                self._capture_watchdog["empty_streak"] = int(
                    self._capture_watchdog.get("empty_streak") or 0
                ) + 1
                self._capture_watchdog["last_failure_reason"] = (
                    blocker
                    or str(self.frame_capturer.last_failure_reason or "")
                    or "empty_frame"
                )
            else:
                self._capture_watchdog["last_failure_reason"] = (
                    blocker
                    or str(self.frame_capturer.last_failure_reason or "")
                    or "capture_failed"
                )

            empty_streak = int(self._capture_watchdog.get("empty_streak") or 0)
            unchanged_streak = int(self._capture_watchdog.get("unchanged_streak") or 0)

        recovery_plan = self._derive_capture_recovery_plan(
            captured=captured,
            empty=empty,
            backend=backend,
            backend_failure=backend_failure,
            blocker_reason=blocker_reason,
            recovery_hint=recovery_hint,
            backend_diagnostics=backend_diagnostics,
            previous_backend_generation=previous_backend_generation,
            previous_context_label=previous_context_label,
            previous_blocker_reason=previous_blocker_reason,
            previous_recovery_action=previous_recovery_action,
        )
        if recovery_plan is not None:
            if recovery_plan.get("recover"):
                self._recover_capture_runtime(
                    reason=str(recovery_plan.get("reason") or "capture_recovery"),
                    recycle_worker=bool(recovery_plan.get("recycle_worker", False)),
                    payload=payload,
                )
                return True
            return False

        if empty_streak >= 5:
            self._recover_capture_runtime(
                reason="watchdog_empty_frame_recycle",
                recycle_worker=True,
                payload=payload,
            )
            with self._capture_watchdog_lock:
                self._capture_watchdog["empty_streak"] = 0
            return True
        if empty_streak >= 3:
            self._recover_capture_runtime(
                reason="watchdog_empty_frame_reset",
                recycle_worker=False,
                payload=payload,
            )
            with self._capture_watchdog_lock:
                self._capture_watchdog["empty_streak"] = 0
            return True
        if unchanged_streak >= 10:
            self._recover_capture_runtime(
                reason="watchdog_unchanged_frame_recycle",
                recycle_worker=True,
                payload=payload,
            )
            with self._capture_watchdog_lock:
                self._capture_watchdog["unchanged_streak"] = 0
            return True
        if unchanged_streak >= 6:
            self._recover_capture_runtime(
                reason="watchdog_unchanged_frame_reset",
                recycle_worker=False,
                payload=payload,
            )
            with self._capture_watchdog_lock:
                self._capture_watchdog["unchanged_streak"] = 0
            return True
        return False

    def _extract_backend_failure_reason(
        self,
        backend_diagnostics: dict[str, Any],
        *,
        backend: str,
    ) -> str:
        normalized_backend = str(
            backend
            or backend_diagnostics.get("active_backend")
            or backend_diagnostics.get("current_backend")
            or ""
        ).strip().lower()
        for item in backend_diagnostics.get("backends") or []:
            item_backend = str((item or {}).get("backend") or "").strip().lower()
            if item_backend != normalized_backend:
                continue
            return str((item or {}).get("last_failure") or "").strip()
        last_capture_attempt = dict(backend_diagnostics.get("last_capture_attempt") or {})
        for item in reversed(last_capture_attempt.get("steps") or []):
            item_backend = str((item or {}).get("backend") or "").strip().lower()
            if normalized_backend and item_backend != normalized_backend:
                continue
            failure = str((item or {}).get("failure") or "").strip()
            if failure:
                return failure
        final_failure = str(last_capture_attempt.get("final_failure") or "").strip()
        if final_failure:
            return final_failure
        return str(backend_diagnostics.get("last_failure_reason") or "").strip()

    def _extract_backend_recovery_hint(
        self,
        backend_diagnostics: dict[str, Any],
        *,
        backend: str,
    ) -> dict[str, Any]:
        normalized_backend = str(
            backend
            or backend_diagnostics.get("active_backend")
            or backend_diagnostics.get("current_backend")
            or ""
        ).strip().lower()
        top_level_hint = dict(backend_diagnostics.get("recovery_hint") or {})
        top_level_backend = str(top_level_hint.get("backend") or "").strip().lower()
        if top_level_hint and (not normalized_backend or not top_level_backend or top_level_backend == normalized_backend):
            return top_level_hint
        for item in backend_diagnostics.get("backends") or []:
            item_backend = str((item or {}).get("backend") or "").strip().lower()
            if normalized_backend and item_backend != normalized_backend:
                continue
            recovery_hint = dict((item or {}).get("recovery_hint") or {})
            if recovery_hint:
                return recovery_hint
        last_capture_attempt = dict(backend_diagnostics.get("last_capture_attempt") or {})
        for item in reversed(last_capture_attempt.get("steps") or []):
            item_backend = str((item or {}).get("backend") or "").strip().lower()
            if normalized_backend and item_backend != normalized_backend:
                continue
            recovery_hint = dict((item or {}).get("recovery_hint") or {})
            if recovery_hint:
                return recovery_hint
        return dict(last_capture_attempt.get("recovery_hint") or {})

    def _derive_capture_recovery_plan(
        self,
        *,
        captured: bool,
        empty: bool,
        backend: str,
        backend_failure: str,
        blocker_reason: str,
        recovery_hint: dict[str, Any] | None,
        backend_diagnostics: dict[str, Any],
        previous_backend_generation: int,
        previous_context_label: str,
        previous_blocker_reason: str,
        previous_recovery_action: str,
    ) -> dict[str, Any] | None:
        if captured:
            return None

        normalized_failure = str(backend_failure or "").strip().lower()
        normalized_backend = str(backend or "").strip().lower()
        normalized_blocker = str(blocker_reason or "").strip().lower()
        context_label = str(backend_diagnostics.get("context_label") or "").strip().lower()
        backend_generation = int(backend_diagnostics.get("desktop_generation") or 0)
        last_prepare_changed = bool(backend_diagnostics.get("last_prepare_changed", False))
        context_change_reasons = [
            str(item).strip().lower()
            for item in (backend_diagnostics.get("last_context_change_reasons") or [])
            if str(item).strip()
        ]

        if normalized_blocker.startswith("blocked_non_persistent_capture_surface:"):
            return {
                "recover": False,
                "reason": normalized_blocker,
            }

        repeated_context = (
            backend_generation == int(previous_backend_generation or 0)
            and context_label == str(previous_context_label or "").strip().lower()
            and normalized_blocker == str(previous_blocker_reason or "").strip().lower()
        )
        explicit_action = str((recovery_hint or {}).get("action") or "").strip().lower()
        explicit_reason = str((recovery_hint or {}).get("reason") or "").strip().lower()

        if explicit_action == "retry_later":
            return {
                "recover": False,
                "reason": explicit_reason or explicit_action or normalized_blocker,
            }

        if explicit_action in {"reset_backend", "recycle_worker", "rebind_desktop"}:
            reason = f"watchdog_{explicit_reason or explicit_action}"
            if repeated_context and reason == str(previous_recovery_action or "").strip().lower():
                return None
            return {
                "recover": True,
                "recycle_worker": bool(
                    (recovery_hint or {}).get("recycle_worker")
                    or explicit_action in {"recycle_worker", "rebind_desktop"}
                ),
                "reason": reason,
            }

        if normalized_failure in {"session_changed", "invalid_desktop", "access_denied"}:
            reason = f"watchdog_{normalized_failure}"
            if repeated_context and reason == str(previous_recovery_action or "").strip().lower():
                return None
            return {
                "recover": True,
                "recycle_worker": True,
                "reason": reason,
            }

        if normalized_failure == "dxgi_access_lost":
            recycle_worker = bool(
                last_prepare_changed
                or backend_generation != int(previous_backend_generation or 0)
                or "desktop_signature_changed" in context_change_reasons
                or "backend_strategy_changed" in context_change_reasons
            )
            reason = "watchdog_dxgi_access_lost_recycle" if recycle_worker else "watchdog_dxgi_access_lost_reset"
            if repeated_context and reason == str(previous_recovery_action or "").strip().lower():
                return None
            return {
                "recover": True,
                "recycle_worker": recycle_worker,
                "reason": reason,
            }

        if empty and last_prepare_changed:
            reason = "watchdog_capture_context_transition"
            recycle_worker = bool(
                backend_generation != int(previous_backend_generation or 0)
                or "display_substrate_changed" in context_change_reasons
                or context_label != str(previous_context_label or "").strip().lower()
            )
            if repeated_context and reason == str(previous_recovery_action or "").strip().lower():
                return None
            return {
                "recover": True,
                "recycle_worker": recycle_worker,
                "reason": reason,
            }

        if normalized_backend == "dxgi" and normalized_failure in {"dxgi_timeout", "no_frame"} and last_prepare_changed:
            reason = "watchdog_dxgi_transition_retry"
            if repeated_context and reason == str(previous_recovery_action or "").strip().lower():
                return None
            return {
                "recover": True,
                "recycle_worker": False,
                "reason": reason,
            }

        return None

    def _recover_capture_runtime(
        self,
        *,
        reason: str,
        recycle_worker: bool,
        payload: dict[str, Any],
    ) -> None:
        previous_backend = str(self.frame_capturer.capture_backend or "")
        previous_failure = str(self.frame_capturer.last_failure_reason or "")
        try:
            self.frame_capturer.close()
        except Exception:
            pass

        with self._capture_watchdog_lock:
            self._capture_watchdog["reset_count"] = int(
                self._capture_watchdog.get("reset_count") or 0
            ) + 1
            self._capture_watchdog["last_recovery_action"] = str(reason or "capture_reset")

        if recycle_worker:
            self._capture_worker.recycle(reason, binding_payload=payload)

        self.logger(
            "capture watchdog recovery executed: "
            f"reason={reason or 'unknown'} recycle_worker={1 if recycle_worker else 0} "
            f"backend={previous_backend or 'unknown'} "
            f"failure={previous_failure or 'none'}"
        )
