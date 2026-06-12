from __future__ import annotations

import json
import os
import re
from typing import Any

from gateway_core.agents.streaming.context import agent_stream_process_requested, set_agent_stream_process_requested
from gateway_core.infra.postgres_dsn import postgres_dsn
from gateway_core.runtime import runtime_context as rt
from gateway_core.conversation.session_memory import remember_conversation_turn
from gateway_core.api.openai_compat.agent_native_stream_filter import VisualMarkdownStreamFilter
from gateway_core.api.openai_compat.source_compat import merge_openwebui_sources


def agent_native_enabled_for_token(token: str) -> bool:
    from gateway_core.agents.school_sql.agent_stream import agent_native_enabled_for_token as _enabled

    return _enabled(token)


async def stream_school_sql_agent_native(**kwargs: Any):
    from gateway_core.agents.school_sql.agent_stream import stream_school_sql_agent_native as _stream

    async for event in _stream(**kwargs):
        yield event


def policy_only_agent_enabled_for_token(token: str) -> bool:
    from gateway_core.agents.policy_only.agent_stream import policy_only_agent_enabled_for_token as _enabled

    return _enabled(token)


async def stream_policy_only_agent_native(**kwargs: Any):
    from gateway_core.agents.policy_only.agent_stream import stream_policy_only_agent_native as _stream

    async for event in _stream(**kwargs):
        yield event


def _env_value(primary: str, legacy: str = "", default: str = "") -> str:
    value = os.getenv(primary, "").strip()
    if value:
        return value
    if legacy:
        value = os.getenv(legacy, "").strip()
        if value:
            return value
    return default


def apply_agent_stream_process_header(request: Any) -> bool:
    header = ""
    try:
        header = str(request.headers.get("X-Agent-Stream-Process") or "")
    except Exception:
        header = ""
    clean_header = header.strip().lower()
    if clean_header:
        enabled = clean_header in {"1", "true", "yes", "on"}
    else:
        enabled = _env_value(
            "SCHOOL_AGENT_STREAM_PROCESS_DEFAULT",
            "TENANT_AGENT_STREAM_PROCESS_DEFAULT",
            "1",
        ).lower() not in {"0", "false", "no", "off", "hidden", "disabled"}
    set_agent_stream_process_requested(enabled)
    return enabled


def _build_reasoning_stream_chunk(*, model_id: str, completion_id: str, created: int, text: str) -> str:
    payload = {
        "id": completion_id,
        "object": "chat.completion.chunk",
        "created": created,
        "model": model_id,
        "choices": [{"index": 0, "delta": {"reasoning_content": text}, "finish_reason": None}],
    }
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


def build_agent_process_stream_chunk(*, model_id: str, completion_id: str, created: int, text: str, content_chunk_fn) -> str:
    if not agent_stream_process_requested():
        return ""
    mode = _env_value(
        "SCHOOL_AGENT_NATIVE_PROCESS_DELTA_MODE",
        "TENANT_AGENT_NATIVE_PROCESS_DELTA_MODE",
        "reasoning_content",
    ).lower()
    if mode in {"off", "hidden", "none", "disabled", "0", "false"}:
        return ""
    if mode in {"content", "plain"}:
        return content_chunk_fn(text)
    return _build_reasoning_stream_chunk(
        model_id=model_id,
        completion_id=completion_id,
        created=created,
        text=text,
    )


def resolve_agent_native_model(default_model: Any) -> Any:
    model_id = _env_value("SCHOOL_AGENT_NATIVE_MODEL_ID", "TENANT_AGENT_NATIVE_MODEL_ID")
    if not model_id:
        return default_model
    try:
        spec = rt.GATEWAY.resolve_model(model_id)
        return rt.GATEWAY.get_client(spec, None, None)
    except Exception as exc:
        rt.LOGGER.warning("agent_native_model_fallback model_id=%s error=%s", model_id, exc)
        return default_model


def resolve_agent_final_model(default_model: Any | None = None) -> Any:
    model_id = _env_value("SCHOOL_AGENT_FINAL_MODEL_ID", "TENANT_AGENT_FINAL_MODEL_ID", "same")
    if not model_id:
        return default_model
    if model_id.strip().lower() in {"same", "current", "selected", "default"}:
        return default_model
    try:
        spec = rt.GATEWAY.resolve_model(model_id)
        return rt.GATEWAY.get_client(spec, None, None)
    except Exception as exc:
        rt.LOGGER.warning("agent_final_model_disabled model_id=%s error=%s", model_id, exc)
        return None


async def run_agent_native_stream(
    *,
    spec: Any,
    pipeline_ctx: Any,
    effective_question: str,
    token: str,
    school_scope: str | None,
    completion_id: str,
    openwebui_chat_id: str,
    monitor_base: dict[str, Any],
    response_tools: Any,
    runtime_response_fns: Any,
    model: Any,
    policy_evidence_search_fn: Any,
    conversation_context: str = "",
    conversation_memory_key: str = "",
    monitor_answer_preview_fn,
):
    first_token_ms: float | None = None
    answer_chunks: list[str] = []
    process_chunks: list[str] = []
    openwebui_sources: list[dict[str, Any]] = []
    markdown_filter = VisualMarkdownStreamFilter()

    def _chunk(text: str) -> str:
        nonlocal first_token_ms
        text = markdown_filter.feed(text)
        if not text:
            return ""
        if first_token_ms is None:
            first_token_ms = response_tools.elapsed_ms()
        answer_chunks.append(text)
        return runtime_response_fns.stream_chunk(spec.model_id, completion_id, text)

    def _process_chunk(text: str) -> str:
        nonlocal first_token_ms
        process_chunks.append(text)
        chunk = build_agent_process_stream_chunk(
            model_id=spec.model_id,
            completion_id=completion_id,
            created=pipeline_ctx.now_ts_fn(),
            text=text,
            content_chunk_fn=lambda value: runtime_response_fns.stream_chunk(spec.model_id, completion_id, value),
        )
        if not chunk:
            return ""
        if first_token_ms is None:
            first_token_ms = response_tools.elapsed_ms()
        mode = _env_value("SCHOOL_AGENT_NATIVE_PROCESS_DELTA_MODE", "TENANT_AGENT_NATIVE_PROCESS_DELTA_MODE", "").lower()
        if mode in {"content", "plain"}:
            answer_chunks.append(text)
        return chunk

    response_tools.log_monitor_event(
        {
            **monitor_base,
            "event": "chat_completion",
            "route_name": "school_agent_native",
            "cache_hit": False,
            "context_present": True,
            "context_source": "school_agent_native",
            "status": "ok",
            "response_mode": "agent_native_stream",
            "usage": rt._zero_usage(),
        }
    )
    try:
        async for event in stream_school_sql_agent_native(
            question=effective_question,
            token=token,
            school_scope=school_scope,
            dsn=postgres_dsn(),
            psycopg_module=rt.psycopg,
            model=model,
            final_model=resolve_agent_final_model(model),
            embedding_fn=rt._rag_embed_text,
            policy_evidence_search_fn=policy_evidence_search_fn,
            openwebui_chat_id=openwebui_chat_id,
            conversation_context=conversation_context,
            sql_logger=log_agent_native_sql,
        ):
            if str(event.get("type") or "") == "sources":
                sources = event.get("sources") if isinstance(event.get("sources"), list) else []
                if sources:
                    added_sources = merge_openwebui_sources(openwebui_sources, sources)
                    if added_sources:
                        yield runtime_response_fns.stream_chunk(spec.model_id, completion_id, "", sources=added_sources)
                continue
            text = str(event.get("text") or "")
            if text:
                if str(event.get("type") or "") in {"process", "progress", "tool"}:
                    chunk = _process_chunk(text)
                    if chunk:
                        yield chunk
                else:
                    yield _chunk(text)
    except Exception as exc:
        rt.LOGGER.exception("school_agent_native_stream_error")
        yield _chunk(response_tools.build_upstream_error_text(exc))
        response_tools.log_monitor_event(
            {
                **monitor_base,
                "event": "chat_completion",
                "route_name": "school_agent_native",
                "status": "error",
                "error_type": type(exc).__name__,
                "usage": rt._current_trace_usage(),
                "answer_preview": monitor_answer_preview_fn("".join(answer_chunks)),
                "process_preview": monitor_answer_preview_fn("".join(process_chunks)),
            },
            first_token_ms=first_token_ms,
            stream_done=True,
        )
        yield runtime_response_fns.stream_end(spec.model_id, completion_id, sources=openwebui_sources)
        return

    flushed = markdown_filter.flush()
    if flushed:
        if first_token_ms is None:
            first_token_ms = response_tools.elapsed_ms()
        answer_chunks.append(flushed)
        yield runtime_response_fns.stream_chunk(spec.model_id, completion_id, flushed)

    response_tools.log_monitor_event(
        {
            **monitor_base,
            "event": "chat_completion",
            "route_name": "school_agent_native",
            "cache_hit": False,
            "context_present": True,
            "context_source": "school_agent_native",
            "status": "ok",
            "response_mode": "agent_native_stream",
            "usage": rt._current_trace_usage(),
            "answer_preview": monitor_answer_preview_fn("".join(answer_chunks)),
            "process_preview": monitor_answer_preview_fn("".join(process_chunks)),
        },
        first_token_ms=first_token_ms,
        stream_done=True,
    )
    remember_conversation_turn(
        conversation_memory_key,
        question=effective_question,
        answer="".join(answer_chunks),
    )
    yield runtime_response_fns.stream_end(spec.model_id, completion_id, sources=openwebui_sources)


async def run_policy_only_agent_native_stream(
    *,
    spec: Any,
    pipeline_ctx: Any,
    effective_question: str,
    token: str,
    completion_id: str,
    openwebui_chat_id: str,
    monitor_base: dict[str, Any],
    response_tools: Any,
    runtime_response_fns: Any,
    model: Any,
    policy_evidence_search_fn: Any,
    conversation_context: str = "",
    conversation_memory_key: str = "",
    monitor_answer_preview_fn,
):
    first_token_ms: float | None = None
    answer_chunks: list[str] = []
    process_chunks: list[str] = []
    openwebui_sources: list[dict[str, Any]] = []
    markdown_filter = VisualMarkdownStreamFilter()

    def _chunk(text: str) -> str:
        nonlocal first_token_ms
        text = markdown_filter.feed(text)
        if not text:
            return ""
        if first_token_ms is None:
            first_token_ms = response_tools.elapsed_ms()
        answer_chunks.append(text)
        return runtime_response_fns.stream_chunk(spec.model_id, completion_id, text)

    def _process_chunk(text: str) -> str:
        nonlocal first_token_ms
        process_chunks.append(text)
        chunk = build_agent_process_stream_chunk(
            model_id=spec.model_id,
            completion_id=completion_id,
            created=pipeline_ctx.now_ts_fn(),
            text=text,
            content_chunk_fn=lambda value: runtime_response_fns.stream_chunk(spec.model_id, completion_id, value),
        )
        if not chunk:
            return ""
        if first_token_ms is None:
            first_token_ms = response_tools.elapsed_ms()
        mode = _env_value("SCHOOL_AGENT_NATIVE_PROCESS_DELTA_MODE", "TENANT_AGENT_NATIVE_PROCESS_DELTA_MODE", "").lower()
        if mode in {"content", "plain"}:
            answer_chunks.append(text)
        return chunk

    response_tools.log_monitor_event(
        {
            **monitor_base,
            "event": "chat_completion",
            "route_name": "policy_only_agent_native",
            "cache_hit": False,
            "context_present": True,
            "context_source": "policy_only_agent_native",
            "status": "ok",
            "response_mode": "policy_only_agent_native_stream",
            "usage": rt._zero_usage(),
        }
    )
    try:
        async for event in stream_policy_only_agent_native(
            question=effective_question,
            token=token,
            model=model,
            policy_evidence_search_fn=policy_evidence_search_fn,
            openwebui_chat_id=openwebui_chat_id,
            conversation_context=conversation_context,
        ):
            if str(event.get("type") or "") == "sources":
                sources = event.get("sources") if isinstance(event.get("sources"), list) else []
                if sources:
                    added_sources = merge_openwebui_sources(openwebui_sources, sources)
                    if added_sources:
                        yield runtime_response_fns.stream_chunk(spec.model_id, completion_id, "", sources=added_sources)
                continue
            text = str(event.get("text") or "")
            if text:
                if str(event.get("type") or "") in {"process", "progress", "tool"}:
                    chunk = _process_chunk(text)
                    if chunk:
                        yield chunk
                else:
                    yield _chunk(text)
    except Exception as exc:
        rt.LOGGER.exception("policy_only_agent_native_stream_error")
        yield _chunk(response_tools.build_upstream_error_text(exc))
        response_tools.log_monitor_event(
            {
                **monitor_base,
                "event": "chat_completion",
                "route_name": "policy_only_agent_native",
                "status": "error",
                "error_type": type(exc).__name__,
                "usage": rt._current_trace_usage(),
                "answer_preview": monitor_answer_preview_fn("".join(answer_chunks)),
                "process_preview": monitor_answer_preview_fn("".join(process_chunks)),
            },
            first_token_ms=first_token_ms,
            stream_done=True,
        )
        yield runtime_response_fns.stream_end(spec.model_id, completion_id, sources=openwebui_sources)
        return

    flushed = markdown_filter.flush()
    if flushed:
        if first_token_ms is None:
            first_token_ms = response_tools.elapsed_ms()
        answer_chunks.append(flushed)
        yield runtime_response_fns.stream_chunk(spec.model_id, completion_id, flushed)

    response_tools.log_monitor_event(
        {
            **monitor_base,
            "event": "chat_completion",
            "route_name": "policy_only_agent_native",
            "cache_hit": False,
            "context_present": True,
            "context_source": "policy_only_agent_native",
            "status": "ok",
            "response_mode": "policy_only_agent_native_stream",
            "usage": rt._current_trace_usage(),
            "answer_preview": monitor_answer_preview_fn("".join(answer_chunks)),
            "process_preview": monitor_answer_preview_fn("".join(process_chunks)),
        },
        first_token_ms=first_token_ms,
        stream_done=True,
    )
    remember_conversation_turn(
        conversation_memory_key,
        question=effective_question,
        answer="".join(answer_chunks),
    )
    yield runtime_response_fns.stream_end(spec.model_id, completion_id, sources=openwebui_sources)


def log_agent_native_sql(
    *,
    school_id: str = "",
    tenant_id: str = "",
    dataset_id: str,
    intent: str,
    sql: str,
    params: list[Any],
) -> None:
    clean_school_id = school_id or tenant_id
    rt.LOGGER.info(
        "agent_native_sql school=%s dataset=%s intent=%s sql=%s params=%s",
        clean_school_id,
        dataset_id,
        intent,
        sql,
        params,
    )
