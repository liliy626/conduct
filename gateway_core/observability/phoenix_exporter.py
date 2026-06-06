from __future__ import annotations

import json
import os
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Callable

from gateway_core.school.trace import SchoolTrace, SchoolTraceStep


DEFAULT_COLLECTOR_ENDPOINT = "http://127.0.0.1:16006/v1/traces"


def phoenix_status(*, exporter_available: bool | None = None) -> dict[str, Any]:
    if exporter_available is None:
        exporter_available = _phoenix_exporter_available()
    collector_endpoint = _collector_endpoint()
    return {
        "enabled": _truthy_env("PHOENIX_ENABLED", default="0"),
        "configured": bool(collector_endpoint),
        "exporter_available": bool(exporter_available),
        "collector_endpoint": collector_endpoint,
        "dashboard_url": _dashboard_url(collector_endpoint),
        "project_name": _project_name(),
    }


def export_school_trace_to_phoenix(
    trace: SchoolTrace | None,
    *,
    exporter_factory: Callable[[], Any] | None = None,
) -> bool:
    if trace is None or not _phoenix_export_enabled():
        return False
    try:
        # Phoenix 是可选观测侧车；导出失败不能影响主问答链路，但必须显式返回失败。
        from opentelemetry import trace as trace_api
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import SimpleSpanProcessor

        raw_exporter = exporter_factory() if exporter_factory is not None else _span_exporter()
        if raw_exporter is None:
            return False
        exporter = _RecordingSpanExporter(raw_exporter)
        provider = TracerProvider(
            resource=Resource.create(
                {
                    "service.name": "conduct-gateway",
                    "deployment.environment": os.getenv("GATEWAY_ENV", "local"),
                    "phoenix.project.name": _project_name(),
                }
            )
        )
        provider.add_span_processor(SimpleSpanProcessor(exporter))
        tracer = provider.get_tracer("conduct.workflow")
        root = tracer.start_span(
            "school_trace",
            start_time=_unix_seconds_to_ns(trace.created_at),
            attributes=_root_attributes(trace),
        )
        try:
            with trace_api.use_span(root, end_on_exit=False):
                for step in trace.steps:
                    _export_step_span(tracer, step, trace_created_at=trace.created_at)
                root.set_attributes(
                    _json_safe_attributes(
                        {
                            "gateway.output_json": _json_text(_trace_output(trace)),
                            "gateway.step_count": len(trace.steps),
                        }
                    )
                )
        finally:
            root.end(end_time=_unix_seconds_to_ns(_trace_end_time(trace)))
            exporter.force_flush()
            provider.force_flush()
            provider.shutdown()
        return exporter.ok
    except (ImportError, OSError, RuntimeError, TypeError, ValueError, AttributeError):
        return False


def _export_step_span(tracer: Any, step: SchoolTraceStep, *, trace_created_at: float) -> None:
    span = tracer.start_span(
        step.name,
        start_time=_unix_seconds_to_ns(step.started_at or trace_created_at),
        attributes=_step_attributes(step),
    )
    try:
        if step.status == "error" or step.error:
            from opentelemetry.trace import Status, StatusCode

            span.set_status(Status(StatusCode.ERROR, step.error or "step error"))
        if step.error:
            span.record_exception(Exception(step.error))
    finally:
        span.end(end_time=_unix_seconds_to_ns(step.ended_at or step.started_at or trace_created_at))


def _root_attributes(trace: SchoolTrace) -> dict[str, Any]:
    return _json_safe_attributes(
        {
            "openinference.span.kind": "CHAIN",
            "gateway.trace_id": trace.trace_id,
            "gateway.school_id": trace.school_id,
            "gateway.question": trace.question,
            "gateway.input_json": _json_text({"question": trace.question}),
            "gateway.step_count": len(trace.steps),
        }
    )


def _step_attributes(step: SchoolTraceStep) -> dict[str, Any]:
    return _json_safe_attributes(
        {
            "openinference.span.kind": _span_kind(step.name),
            "gateway.step.name": step.name,
            "gateway.step.status": step.status,
            "gateway.duration_ms": int(step.duration_ms or 0),
            "gateway.input_json": _json_text(step.input),
            "gateway.output_json": _json_text(step.output),
            **({"gateway.error": step.error} if step.error else {}),
        }
    )


def _span_kind(name: str) -> str:
    clean = str(name or "")
    if ".llm" in clean or "chat_completion" in clean or "final_fast" in clean:
        return "LLM"
    if ".tool." in clean or "sql_db_query" in clean or "ddl_react.tool" in clean:
        return "TOOL"
    return "CHAIN"


def _trace_output(trace: SchoolTrace) -> dict[str, Any]:
    has_error = any(step.status == "error" or step.error for step in trace.steps)
    return {
        "status": "error" if has_error else "ok",
        "step_count": len(trace.steps),
        "duration_ms": max(0, int(round((_trace_end_time(trace) - float(trace.created_at or 0)) * 1000))),
    }


def _trace_end_time(trace: SchoolTrace) -> float:
    return max([float(trace.created_at or 0), *[float(step.ended_at or 0) for step in trace.steps]])


def _span_exporter() -> Any:
    try:
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
    except ImportError:
        return None
    try:
        return OTLPSpanExporter(endpoint=_collector_endpoint(), timeout=_export_timeout_sec())
    except (OSError, RuntimeError, TypeError, ValueError):
        return None


def _phoenix_export_enabled() -> bool:
    return _truthy_env("PHOENIX_ENABLED", default="0") and bool(_collector_endpoint())


def _phoenix_exporter_available() -> bool:
    try:
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter  # noqa: F401
    except ImportError:
        return False
    return True


def _collector_endpoint() -> str:
    return os.getenv("PHOENIX_COLLECTOR_ENDPOINT", "").strip() or DEFAULT_COLLECTOR_ENDPOINT


def _dashboard_url(collector_endpoint: str) -> str:
    endpoint = str(collector_endpoint or DEFAULT_COLLECTOR_ENDPOINT).strip()
    return endpoint.removesuffix("/v1/traces")


def _project_name() -> str:
    return os.getenv("PHOENIX_PROJECT_NAME", "conduct-gateway").strip() or "conduct-gateway"


def _export_timeout_sec() -> float:
    try:
        return max(1.0, float(os.getenv("PHOENIX_EXPORT_TIMEOUT_SEC", "5")))
    except ValueError:
        return 5.0


class _RecordingSpanExporter:
    def __init__(self, delegate: Any) -> None:
        self.delegate = delegate
        self.ok = True

    def export(self, spans: Any) -> Any:
        try:
            result = self.delegate.export(spans)
        except (OSError, RuntimeError, TypeError, ValueError):
            self.ok = False
            raise
        if str(result).upper().endswith("FAILURE"):
            self.ok = False
        return result

    def shutdown(self) -> Any:
        shutdown = getattr(self.delegate, "shutdown", None)
        if callable(shutdown):
            return shutdown()
        return None

    def force_flush(self, timeout_millis: int = 30000) -> bool:
        force_flush = getattr(self.delegate, "force_flush", None)
        if callable(force_flush):
            flushed = bool(force_flush(timeout_millis=timeout_millis))
            if not flushed:
                self.ok = False
            return flushed
        return True


def _unix_seconds_to_ns(value: float) -> int:
    return int(float(value or 0) * 1_000_000_000)


def _json_text(value: Any) -> str:
    return json.dumps(_json_safe(value), ensure_ascii=False, default=str)


def _json_safe_attributes(attributes: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in _json_safe(attributes).items() if value is not None}


def _json_safe(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_safe(item) for item in value]
    return str(value)


def _truthy_env(name: str, default: str = "0") -> bool:
    return (os.getenv(name, default).strip().lower() or default) in {"1", "true", "yes", "on"}
