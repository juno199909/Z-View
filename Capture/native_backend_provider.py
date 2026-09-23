from __future__ import annotations

import importlib
from dataclasses import dataclass
from typing import Any, Protocol


class CaptureBackendProviderProtocol(Protocol):
    backend_name: str

    def get_support_status(self, capturer=None) -> tuple[bool, str]: ...

    def prepare(self, capturer=None) -> None: ...

    def grab(self, capturer=None): ...

    def reset(self, capturer=None, reason: str = "") -> None: ...

    def close(self, capturer=None) -> None: ...

    def describe_state(self) -> dict[str, Any]: ...


@dataclass(slots=True)
class NativeBackendProviderHandle:
    backend_name: str
    provider: CaptureBackendProviderProtocol | None = None
    provider_module: str = ""
    load_error: str = ""
    load_attempted: bool = False

    def ensure_loaded(self) -> CaptureBackendProviderProtocol | None:
        if self.load_attempted:
            return self.provider
        self.load_attempted = True
        package_name = __name__.rsplit(".", 1)[0]
        last_error = ""
        for module_name in _build_module_candidates(package_name, self.backend_name):
            try:
                module = importlib.import_module(module_name)
            except ModuleNotFoundError as exc:
                if exc.name == module_name:
                    last_error = "provider_module_not_found"
                    continue
                last_error = f"provider_import_error:{exc}"
                continue
            except Exception as exc:
                last_error = f"provider_import_error:{exc}"
                continue
            try:
                provider = _instantiate_provider(module, self.backend_name)
            except Exception as exc:
                last_error = f"provider_init_error:{exc}"
                continue
            if provider is None:
                last_error = "provider_factory_missing"
                continue
            self.provider = provider
            self.provider_module = module_name
            self.load_error = ""
            return self.provider
        self.load_error = last_error or "provider_unavailable"
        return None

    def get_support_status(self, capturer=None) -> tuple[bool, str]:
        provider = self.ensure_loaded()
        if provider is None:
            return False, self.load_error or "provider_unavailable"
        try:
            supported, reason = provider.get_support_status(capturer)
        except Exception as exc:
            return False, f"provider_support_probe_failed:{exc}"
        return bool(supported), str(reason or "")

    def prepare(self, capturer=None) -> None:
        provider = self.ensure_loaded()
        if provider is None:
            raise RuntimeError(self.load_error or "provider_unavailable")
        provider.prepare(capturer)

    def grab(self, capturer=None):
        provider = self.ensure_loaded()
        if provider is None:
            raise RuntimeError(self.load_error or "provider_unavailable")
        return provider.grab(capturer)

    def reset(self, capturer=None, reason: str = "") -> None:
        provider = self.ensure_loaded()
        if provider is None:
            return
        provider.reset(capturer, reason=reason)

    def close(self, capturer=None) -> None:
        provider = self.provider
        if provider is None:
            return
        provider.close(capturer)

    def describe_state(self) -> dict[str, Any]:
        provider = self.ensure_loaded()
        state = {
            "backend": self.backend_name,
            "provider_loaded": provider is not None,
            "provider_module": self.provider_module,
            "load_error": self.load_error,
            "load_attempted": self.load_attempted,
        }
        if provider is None:
            return state
        describe = getattr(provider, "describe_state", None)
        if callable(describe):
            try:
                provider_state = describe()
            except Exception as exc:
                provider_state = {"describe_error": str(exc)}
            if isinstance(provider_state, dict):
                state["provider_state"] = provider_state
            else:
                state["provider_state"] = {"value": provider_state}
        return state


def create_capture_backend_provider(
    backend_name: str,
    capturer=None,
) -> NativeBackendProviderHandle:
    handle = NativeBackendProviderHandle(backend_name=str(backend_name or "").strip().lower())
    if capturer is not None:
        handle.ensure_loaded()
    return handle


def _build_module_candidates(package_name: str, backend_name: str) -> tuple[str, ...]:
    normalized_backend_name = str(backend_name or "").strip().lower()
    if not normalized_backend_name:
        return ()
    return (
        f"{package_name}.providers.{normalized_backend_name}_provider",
        f"{package_name}.{normalized_backend_name}_provider",
        f"{normalized_backend_name}_provider",
    )


def _instantiate_provider(module, backend_name: str):
    factory = getattr(module, "create_capture_backend_provider", None)
    if callable(factory):
        return factory(backend_name=backend_name)
    factory = getattr(module, "create_provider", None)
    if callable(factory):
        return factory(backend_name=backend_name)
    provider_cls = getattr(module, "Provider", None)
    if provider_cls is not None:
        return provider_cls(backend_name=backend_name)
    return None
