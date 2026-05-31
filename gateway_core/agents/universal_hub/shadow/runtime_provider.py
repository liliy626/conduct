from __future__ import annotations

import os
from collections.abc import Callable
from typing import Any

from gateway_core.agents.base_skill import RuntimeContext


ModelFactory = Callable[[str], Any]
PsycopgFactory = Callable[[], Any]
DsnResolver = Callable[[str], str]
ApiKeyRecordFactory = Callable[[str], Any]


class ReadOnlyRuntimeProvider:
    """Secure injector for offline live Skill replay dependencies.

    This class is intentionally conservative. It does not auto-discover
    production secrets, does not write to state, and refuses to run unless the
    caller explicitly enables live replay with ``SHADOW_LIVE_REPLAY_ENABLED=1``.
    """

    def __init__(
        self,
        *,
        model_factory: ModelFactory | None = None,
        final_model_factory: ModelFactory | None = None,
        psycopg_factory: PsycopgFactory | None = None,
        dsn_resolver: DsnResolver | None = None,
        api_key_record_factory: ApiKeyRecordFactory | None = None,
    ) -> None:
        self._model_factory = model_factory
        self._final_model_factory = final_model_factory
        self._psycopg_factory = psycopg_factory
        self._dsn_resolver = dsn_resolver
        self._api_key_record_factory = api_key_record_factory

    def inject_live_dependencies(self, school_id: str, current_ctx: RuntimeContext | dict[str, Any]) -> RuntimeContext:
        if not _truthy_env("SHADOW_LIVE_REPLAY_ENABLED", "0"):
            raise PermissionError("SHADOW_LIVE_REPLAY_ENABLED=1 is required for live replay dependency injection")

        clean_school_id = str(school_id or "").strip()
        if not clean_school_id:
            raise ValueError("school_id is required")

        ctx = current_ctx if isinstance(current_ctx, RuntimeContext) else RuntimeContext(current_ctx)
        model = self._build_model(clean_school_id)
        final_model = self._build_final_model(clean_school_id)
        psycopg_module = self._build_psycopg_module()
        dsn = self._resolve_dsn(clean_school_id)
        if not str(dsn or "").strip():
            raise RuntimeError("readonly DSN is required for live replay")
        if psycopg_module is None:
            raise RuntimeError("psycopg_module is required for live replay")

        ctx["model"] = model
        if final_model is not None:
            ctx["final_model"] = final_model
        ctx["psycopg_module"] = psycopg_module
        ctx["dsn"] = dsn
        api_key_record = self._build_api_key_record(clean_school_id)
        if api_key_record is not None:
            ctx["api_key_record"] = api_key_record
        ctx["is_live_sandbox"] = True
        return ctx

    def _build_model(self, school_id: str) -> Any:
        if self._model_factory is None:
            raise RuntimeError("model_factory is required for live replay")
        return self._model_factory(school_id)

    def _build_final_model(self, school_id: str) -> Any:
        if self._final_model_factory is None:
            return None
        return self._final_model_factory(school_id)

    def _build_psycopg_module(self) -> Any:
        if self._psycopg_factory is not None:
            return self._psycopg_factory()
        try:
            import psycopg

            return psycopg
        except Exception:
            return None

    def _resolve_dsn(self, school_id: str) -> str:
        if self._dsn_resolver is not None:
            return str(self._dsn_resolver(school_id) or "").strip()
        specific = os.getenv(f"SHADOW_LIVE_READONLY_DSN_{_env_suffix(school_id)}", "").strip()
        if specific:
            return specific
        return os.getenv("SHADOW_LIVE_READONLY_DSN", "").strip()

    def _build_api_key_record(self, school_id: str) -> Any:
        if self._api_key_record_factory is None:
            return None
        return self._api_key_record_factory(school_id)


def _truthy_env(name: str, default: str) -> bool:
    return os.getenv(name, default).strip().lower() in {"1", "true", "yes", "on"}


def _env_suffix(value: str) -> str:
    return "".join(ch if ch.isalnum() else "_" for ch in str(value or "").upper())


def build_gateway_readonly_runtime_provider(
    *,
    gateway: Any,
    psycopg_factory: PsycopgFactory | None = None,
    dsn_resolver: DsnResolver | None = None,
    api_key_record_factory: ApiKeyRecordFactory | None = None,
) -> ReadOnlyRuntimeProvider:
    """Build a live replay provider from the existing Gateway client factory."""

    def model_factory(_school_id: str) -> Any:
        model_id = os.getenv("SHADOW_LIVE_MODEL_ID", "").strip() or None
        spec = gateway.resolve_model(model_id)
        return gateway.get_client(spec, temperature=None, max_tokens=None)

    def final_model_factory(_school_id: str) -> Any:
        model_id = os.getenv("SHADOW_LIVE_FINAL_MODEL_ID", "").strip()
        if not model_id:
            return None
        spec = gateway.resolve_model(model_id)
        return gateway.get_client(spec, temperature=None, max_tokens=None)

    return ReadOnlyRuntimeProvider(
        model_factory=model_factory,
        final_model_factory=final_model_factory,
        psycopg_factory=psycopg_factory,
        dsn_resolver=dsn_resolver,
        api_key_record_factory=api_key_record_factory,
    )
