from __future__ import annotations

import json
import re
import time
from typing import Any

from gateway_core.school.trace import SchoolTraceStep, include_sql, set_step_output, trace_step
from gateway_core.runtime.runtime_trace_context import _add_trace_usage
from gateway_core.infra.utils import safe_int as _safe_int
from gateway_core.infra.utils import truncate as _truncate_text


def record_langgraph_event_as_trace_step(
    trace: Any,
    event: dict[str, Any],
    *,
    prefix: str = "langgraph",
    trace_context: dict[str, Any] | None = None,
) -> None:
    """Record selected LangGraph runtime events as lightweight tenant trace steps.

    These steps are intended for user-visible process streaming. They expose
    observable actions (tool name, SQL preview, row count), not hidden model
    reasoning.
    """
    if not isinstance(event, dict):
        return
    kind = str(event.get("event") or "").strip()
    if kind in {
        "on_chat_model_start",
        "on_chat_model_stream",
        "on_chat_model_end",
        "on_chat_model_error",
        "on_llm_start",
        "on_llm_stream",
        "on_llm_end",
        "on_llm_error",
    }:
        _record_chat_model_event(trace, event, prefix=prefix, trace_context=trace_context)
        return
    if kind not in {"on_tool_start", "on_tool_end", "on_tool_error"}:
        return
    tool_name = str(event.get("name") or "").strip()
    data = event.get("data") if isinstance(event.get("data"), dict) else {}
    if kind == "on_tool_start":
        payload = _tool_start_payload(tool_name=tool_name, data=data)
        step_name = f"{prefix}.tool.start"
    elif kind == "on_tool_end":
        payload = _tool_end_payload(tool_name=tool_name, data=data)
        step_name = f"{prefix}.tool.end"
    else:
        payload = {"tool_name": tool_name, "error": _truncate_text(str(data.get("error") or ""), 800, strip=True, rstrip=True)}
        step_name = f"{prefix}.tool.error"
    with trace_step(trace, step_name, _with_trace_context({"event": kind, "tool_name": tool_name}, trace_context)) as step:
        set_step_output(step, _with_trace_context(payload, trace_context))


def _record_chat_model_event(
    trace: Any,
    event: dict[str, Any],
    *,
    prefix: str,
    trace_context: dict[str, Any] | None = None,
) -> None:
    if trace is None:
        return
    kind = str(event.get("event") or "").strip()
    run_id = str(event.get("run_id") or "").strip()
    if not run_id:
        return
    runs = _active_llm_runs(trace)
    now = time.perf_counter()
    if kind in {"on_chat_model_start", "on_llm_start"}:
        runs[run_id] = {
            "started_at": now,
            "started_wall_at": time.time(),
            "first_token_at": None,
            "stream_chunk_count": 0,
            "input": _with_trace_context(_chat_model_input_payload(event), trace_context),
            "trace_context": dict(trace_context or {}),
        }
        return
    state = runs.get(run_id)
    if kind in {"on_chat_model_stream", "on_llm_stream"}:
        if state is not None:
            state["stream_chunk_count"] = int(state.get("stream_chunk_count") or 0) + 1
            if state.get("first_token_at") is None:
                state["first_token_at"] = now
        return
    if kind in {"on_chat_model_end", "on_chat_model_error", "on_llm_end", "on_llm_error"}:
        state = runs.pop(run_id, None) or {
            "started_at": now,
            "first_token_at": None,
            "stream_chunk_count": 0,
            "input": _with_trace_context(_chat_model_input_payload(event), trace_context),
            "trace_context": dict(trace_context or {}),
        }
        duration_ms = int((now - float(state.get("started_at") or now)) * 1000)
        first_token_at = state.get("first_token_at")
        first_token_ms = int((float(first_token_at) - float(state.get("started_at") or now)) * 1000) if first_token_at else None
        data = event.get("data") if isinstance(event.get("data"), dict) else {}
        usage = _extract_usage(data.get("output"))
        if any(int(usage.get(key) or 0) > 0 for key in ("prompt_tokens", "completion_tokens", "total_tokens")):
            _add_trace_usage(usage)
        output = {
            "event": kind,
            "first_token_ms": first_token_ms,
            "stream_chunk_count": int(state.get("stream_chunk_count") or 0),
            "usage": usage,
        }
        step_trace_context = state.get("trace_context") if isinstance(state.get("trace_context"), dict) else trace_context
        error = _truncate_text(str(data.get("error") or ""), 800, strip=True, rstrip=True)
        step = SchoolTraceStep(
            name=f"{prefix}.llm",
            status="error" if kind.endswith("_error") else "ok",
            input=state.get("input") if isinstance(state.get("input"), dict) else {},
            output=_with_trace_context(output, step_trace_context),
            error=error,
            duration_ms=max(0, duration_ms),
            started_at=float(state.get("started_wall_at") or 0),
            ended_at=time.time(),
        )
        trace.steps.append(step)


def flush_active_langgraph_llm_runs(
    trace: Any,
    *,
    prefix: str = "langgraph",
    trace_context: dict[str, Any] | None = None,
) -> None:
    if trace is None:
        return
    runs = _active_llm_runs(trace)
    if not runs:
        return
    now = time.perf_counter()
    ended_wall_at = time.time()
    for run_id, state in list(runs.items()):
        started_at = float(state.get("started_at") or now)
        first_token_at = state.get("first_token_at")
        duration_ms = int((now - started_at) * 1000)
        first_token_ms = int((float(first_token_at) - started_at) * 1000) if first_token_at else None
        step_trace_context = state.get("trace_context") if isinstance(state.get("trace_context"), dict) else trace_context
        step = SchoolTraceStep(
            name=f"{prefix}.llm",
            status="ok",
            input=state.get("input") if isinstance(state.get("input"), dict) else {},
            output=_with_trace_context(
                {
                    "event": "flushed_without_end_event",
                    "run_id": run_id,
                    "first_token_ms": first_token_ms,
                    "stream_chunk_count": int(state.get("stream_chunk_count") or 0),
                    "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
                },
                step_trace_context,
            ),
            duration_ms=max(0, duration_ms),
            started_at=float(state.get("started_wall_at") or ended_wall_at),
            ended_at=ended_wall_at,
        )
        trace.steps.append(step)
        runs.pop(run_id, None)


def _with_trace_context(payload: dict[str, Any], trace_context: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(trace_context, dict) or not trace_context:
        return payload
    out = dict(payload)
    out.update(trace_context)
    return out


def _active_llm_runs(trace: Any) -> dict[str, dict[str, Any]]:
    runs = getattr(trace, "_active_llm_runs", None)
    if not isinstance(runs, dict):
        runs = {}
        setattr(trace, "_active_llm_runs", runs)
    return runs


def _chat_model_input_payload(event: dict[str, Any]) -> dict[str, Any]:
    metadata = event.get("metadata") if isinstance(event.get("metadata"), dict) else {}
    data = event.get("data") if isinstance(event.get("data"), dict) else {}
    raw_input = data.get("input")
    model_name = _first_text(
        metadata.get("ls_model_name"),
        metadata.get("model_name"),
        metadata.get("model"),
        event.get("name"),
    )
    return {
        "event": str(event.get("event") or "on_chat_model_start"),
        "run_id": str(event.get("run_id") or ""),
        "model_name": model_name,
        "provider": _first_text(metadata.get("ls_provider"), event.get("name")),
        "message_count": _message_count(raw_input),
    }


def _message_count(raw_input: Any) -> int:
    if isinstance(raw_input, dict):
        messages = raw_input.get("messages")
        if isinstance(messages, list):
            return len(messages)
        if isinstance(messages, tuple):
            return len(messages)
    if isinstance(raw_input, list):
        return len(raw_input)
    return 0


def _extract_usage(output: Any) -> dict[str, int]:
    metadata = {}
    if isinstance(output, dict):
        raw_meta = output.get("response_metadata") or output.get("usage_metadata") or output.get("metadata")
        metadata = raw_meta if isinstance(raw_meta, dict) else output
    else:
        raw_meta = getattr(output, "response_metadata", None) or getattr(output, "usage_metadata", None)
        metadata = raw_meta if isinstance(raw_meta, dict) else {}
    raw_usage = metadata.get("token_usage") or metadata.get("usage") or metadata
    if not isinstance(raw_usage, dict):
        raw_usage = {}
    prompt_tokens = _safe_int(raw_usage.get("prompt_tokens") or raw_usage.get("input_tokens"))
    completion_tokens = _safe_int(raw_usage.get("completion_tokens") or raw_usage.get("output_tokens"))
    total_tokens = _safe_int(raw_usage.get("total_tokens"))
    if total_tokens <= 0:
        total_tokens = prompt_tokens + completion_tokens
    return {
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_tokens": total_tokens,
    }


def _first_text(*values: Any) -> str:
    for value in values:
        text = str(value or "").strip()
        if text:
            return text
    return ""


def _tool_start_payload(*, tool_name: str, data: dict[str, Any]) -> dict[str, Any]:
    raw_input = data.get("input")
    sql = _extract_sql(raw_input)
    query = _extract_query(raw_input)
    input_preview = _jsonish(raw_input)
    if sql and not include_sql():
        input_preview = "<hidden>"
    return {
        "tool_name": tool_name,
        "query": _truncate_text(query, 500, strip=True, rstrip=True),
        "sql": _truncate_text(sql, 2000, strip=True, rstrip=True),
        "input_preview": _truncate_text(input_preview, 1200, strip=True, rstrip=True),
    }


def _tool_end_payload(*, tool_name: str, data: dict[str, Any]) -> dict[str, Any]:
    raw_output = data.get("output")
    content = getattr(raw_output, "content", raw_output)
    parsed = _loads_json(content)
    row_count = _first_present(
        parsed,
        "row_count",
        "doc_count",
        "experience_count",
        "suggestion_count",
        "table_count",
        "column_count",
    )
    output_preview = _jsonish(parsed or content)
    if not include_sql() and _contains_sql(output_preview):
        output_preview = "<hidden>"
    return {
        "tool_name": tool_name,
        "row_count": row_count,
        "table_refs": parsed.get("table_refs") if isinstance(parsed.get("table_refs"), list) else [],
        "referenced_views": parsed.get("referenced_views") if isinstance(parsed.get("referenced_views"), list) else [],
        "table_ref": str(parsed.get("table_ref") or ""),
        "allowed": parsed.get("allowed"),
        "requires_sample": parsed.get("requires_sample"),
        "error": _truncate_text(str(parsed.get("error") or ""), 800, strip=True, rstrip=True),
        "output_preview": _truncate_text(output_preview, 1200, strip=True, rstrip=True),
    }


def _extract_sql(value: Any) -> str:
    if isinstance(value, str) and _looks_like_sql(value):
        return value.strip()
    if isinstance(value, dict):
        for key in ("query", "sql", "raw_sql"):
            text = str(value.get(key) or "").strip()
            if _looks_like_sql(text):
                return text
    return ""


def _extract_query(value: Any) -> str:
    if isinstance(value, str) and not _looks_like_sql(value):
        return value.strip()
    if isinstance(value, dict):
        for key in ("query", "question", "input"):
            text = str(value.get(key) or "").strip()
            if text and not _looks_like_sql(text):
                return text
    return ""


def _looks_like_sql(text: str) -> bool:
    return bool(re.match(r"(?is)^\s*(select|with)\b", str(text or "")))


def _contains_sql(text: str) -> bool:
    return bool(re.search(r"(?is)\b(select|with)\b.+\bfrom\b", str(text or "")))


def _loads_json(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    text = str(value or "").strip()
    if not text:
        return {}
    try:
        parsed = json.loads(text)
    except Exception:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _first_present(payload: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in payload:
            return payload.get(key)
    return None


def _jsonish(value: Any) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, default=str)
    except Exception:
        return str(value or "")
