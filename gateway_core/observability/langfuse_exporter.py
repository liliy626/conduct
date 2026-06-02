from __future__ import annotations

import os
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Callable

from gateway_core.school.trace import SchoolTrace


_DISPLAY_NAMES = {
    "school_trace": "学校数据流观测",
    "school.resolve": "学校身份解析",
    "school_schema.index": "学校数据表索引",
    "agent_native.guard_router.llm": "智能体路由判断",
    "route_result": "路由结果",
    "domain_context": "业务上下文证据",
    "metadata_selection": "元数据选择",
    "agent_native.contract.plan": "输出契约规划",
    "query_plan": "查询计划",
    "agent_native.start": "智能体启动",
    "agent_native.langgraph.llm": "智能体模型流式输出",
    "agent_native.langgraph.tool.start": "工具调用开始",
    "agent_native.langgraph.tool.end": "工具调用完成",
    "ddl_react.tool.sql_experience_search": "历史 SQL 经验检索",
    "ddl_react.tool.sql_db_query": "SQL 查询执行",
    "sql_execution": "SQL 执行明细",
    "final_answer_context": "最终回答上下文",
    "context.build": "回答上下文组装",
    "chat_completion.final": "最终回答",
}


def langfuse_status(*, sdk_available: bool | None = None) -> dict[str, Any]:
    if sdk_available is None:
        sdk_available = _langfuse_sdk_available()
    base_url = _langfuse_base_url()
    return {
        "enabled": _truthy_env("LANGFUSE_ENABLED", default="0"),
        "configured": bool(os.getenv("LANGFUSE_PUBLIC_KEY") and os.getenv("LANGFUSE_SECRET_KEY")),
        "sdk_available": bool(sdk_available),
        "dashboard_url": base_url,
        "base_url": base_url,
    }


def export_school_trace_to_langfuse(
    trace: SchoolTrace | None,
    *,
    client_factory: Callable[[], Any] | None = None,
) -> bool:
    if trace is None or not _langfuse_export_enabled():
        return False
    try:
        client = client_factory() if client_factory is not None else _langfuse_client()
        if client is None:
            return False
        if _supports_timed_observations(client):
            return _export_school_trace_with_timing(trace, client=client)
        final_answer = _final_answer_text(trace)
        with client.start_as_current_observation(
            as_type="span",
            name=_display_name("school_trace"),
            input=_json_safe({"question": trace.question}),
            metadata=_json_safe({
                "original_name": "school_trace",
                "trace_id": trace.trace_id,
                "school_id": trace.school_id,
                "step_count": len(trace.steps),
            }),
        ) as root:
            root_output = _trace_output(trace, final_answer=final_answer)
            if final_answer:
                root_output["final_answer"] = final_answer
            root.update(output=_json_safe(root_output))
            if final_answer and not _has_final_answer_step(trace):
                with client.start_as_current_observation(
                    as_type="span",
                    name=_display_name("chat_completion.final"),
                    input=_json_safe({"question": trace.question}),
                    metadata=_json_safe({"original_name": "chat_completion.final"}),
                ) as final_span:
                    final_span.update(output=_json_safe({"answer": final_answer}))
            for step in trace.steps:
                with client.start_as_current_observation(
                    as_type="span",
                    name=_display_name(step.name),
                    input=_json_safe(step.input),
                    metadata=_json_safe({
                        "original_name": step.name,
                        "status": step.status,
                        "duration_ms": step.duration_ms,
                        "started_at": step.started_at,
                        "ended_at": step.ended_at,
                        **({"error": step.error} if step.error else {}),
                    }),
                ) as span:
                    span.update(output=_json_safe(step.output))
        flush = getattr(client, "flush", None)
        if callable(flush):
            flush()
        return True
    except Exception:
        return False


def _export_school_trace_with_timing(trace: SchoolTrace, *, client: Any) -> bool:
    from opentelemetry import trace as otel_trace_api

    final_answer = _final_answer_text(trace)
    root_start_ns = _unix_seconds_to_ns(trace.created_at)
    root_end_ns = _unix_seconds_to_ns(_trace_end_time(trace))
    root_otel_span = client._otel_tracer.start_span(  # noqa: SLF001 - Langfuse SDK has no public start_time parameter.
        name=_display_name("school_trace"),
        start_time=root_start_ns,
    )
    root = client._create_observation_from_otel_span(  # noqa: SLF001
        otel_span=root_otel_span,
        as_type="span",
        input=_json_safe({"question": trace.question}),
        metadata=_json_safe(
            {
                "original_name": "school_trace",
                "trace_id": trace.trace_id,
                "school_id": trace.school_id,
                "step_count": len(trace.steps),
            }
        ),
    )
    try:
        root_output = _trace_output(trace, final_answer=final_answer)
        if final_answer:
            root_output["final_answer"] = final_answer
        root.update(output=_json_safe(root_output))
        with otel_trace_api.use_span(root_otel_span, end_on_exit=False):
            if final_answer and not _has_final_answer_step(trace):
                _create_timed_observation(
                    client,
                    name=_display_name("chat_completion.final"),
                    start_time=trace.created_at,
                    end_time=_trace_end_time(trace),
                    input_payload={"question": trace.question},
                    output_payload={"answer": final_answer},
                    metadata={"original_name": "chat_completion.final"},
                )
            for step in trace.steps:
                _create_timed_observation(
                    client,
                    name=_display_name(step.name),
                    start_time=step.started_at or trace.created_at,
                    end_time=step.ended_at or step.started_at or trace.created_at,
                    input_payload=step.input,
                    output_payload=step.output,
                    metadata={
                        "original_name": step.name,
                        "status": step.status,
                        "duration_ms": step.duration_ms,
                        "started_at": step.started_at,
                        "ended_at": step.ended_at,
                        **({"error": step.error} if step.error else {}),
                    },
                )
    finally:
        root.end(end_time=root_end_ns)
    flush = getattr(client, "flush", None)
    if callable(flush):
        flush()
    return True


def _create_timed_observation(
    client: Any,
    *,
    name: str,
    start_time: float,
    end_time: float,
    input_payload: Any,
    output_payload: Any,
    metadata: dict[str, Any],
) -> None:
    child_otel_span = client._otel_tracer.start_span(  # noqa: SLF001
        name=name,
        start_time=_unix_seconds_to_ns(start_time),
    )
    child = client._create_observation_from_otel_span(  # noqa: SLF001
        otel_span=child_otel_span,
        as_type="span",
        input=_json_safe(input_payload),
        metadata=_json_safe(metadata),
    )
    child.update(output=_json_safe(output_payload))
    child.end(end_time=_unix_seconds_to_ns(end_time))


def _supports_timed_observations(client: Any) -> bool:
    return bool(getattr(client, "_otel_tracer", None) and getattr(client, "_create_observation_from_otel_span", None))


def _langfuse_export_enabled() -> bool:
    return (
        _truthy_env("LANGFUSE_ENABLED", default="0")
        and bool(os.getenv("LANGFUSE_PUBLIC_KEY"))
        and bool(os.getenv("LANGFUSE_SECRET_KEY"))
    )


def _langfuse_client() -> Any:
    try:
        from langfuse import get_client
    except Exception:
        return None
    if not os.getenv("LANGFUSE_HOST"):
        base_url = os.getenv("LANGFUSE_BASE_URL", "").strip()
        if base_url:
            os.environ["LANGFUSE_HOST"] = base_url
    try:
        return get_client()
    except Exception:
        return None


def _langfuse_sdk_available() -> bool:
    try:
        import langfuse  # noqa: F401
    except Exception:
        return False
    return True


def _langfuse_base_url() -> str:
    return (
        os.getenv("LANGFUSE_BASE_URL", "").strip()
        or os.getenv("LANGFUSE_HOST", "").strip()
        or "https://cloud.langfuse.com"
    )


def _trace_output(trace: SchoolTrace, *, final_answer: str = "") -> dict[str, Any]:
    has_error = any(step.status == "error" or step.error for step in trace.steps)
    output: dict[str, Any] = {"status": "error" if has_error else "ok", "step_count": len(trace.steps)}
    if has_error and final_answer:
        output["status"] = "recovered"
        output["answer_status"] = "generated_with_internal_error"
        output["internal_error_count"] = sum(1 for step in trace.steps if step.status == "error" or step.error)
    return output


def _final_answer_text(trace: SchoolTrace) -> str:
    for step in reversed(trace.steps):
        output = step.output if isinstance(step.output, dict) else {}
        for key in ("final_answer", "answer", "answer_preview", "latest_answer_context", "truth_data_markdown"):
            text = str(output.get(key) or "").strip()
            if text:
                return _truncate(text, 12000)
    return ""


def _has_final_answer_step(trace: SchoolTrace) -> bool:
    return any(step.name == "chat_completion.final" for step in trace.steps)


def _trace_end_time(trace: SchoolTrace) -> float:
    values = [float(step.ended_at or 0) for step in trace.steps if step.ended_at]
    return max(values) if values else float(trace.created_at or 0)


def _unix_seconds_to_ns(value: float) -> int:
    return int(float(value or 0) * 1_000_000_000)


def _json_safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_safe(item) for item in value]
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Decimal):
        try:
            if value == value.to_integral_value():
                return int(value)
            return float(value)
        except Exception:
            return str(value)
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def _display_name(name: str) -> str:
    return _DISPLAY_NAMES.get(str(name or ""), str(name or "") or "未命名步骤")


def _truncate(text: str, limit: int) -> str:
    clean = str(text or "")
    if len(clean) <= limit:
        return clean
    return clean[: max(0, limit - 20)] + "\n...<truncated>"


def _truthy_env(name: str, *, default: str = "0") -> bool:
    return str(os.getenv(name, default) or default).strip().lower() in {"1", "true", "yes", "on"}
