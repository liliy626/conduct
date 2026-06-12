from __future__ import annotations

from dataclasses import dataclass
from typing import Any, AsyncIterable

from gateway_core.api.openai_compat.agent_native_stream_filter import VisualMarkdownStreamFilter
from gateway_core.api.openai_compat.source_compat import merge_openwebui_sources
from gateway_core.conversation.session_memory import remember_conversation_turn
from gateway_core.infra.postgres_dsn import postgres_dsn
from gateway_core.runtime import runtime_context as rt


async def stream_school_sql_agent_native(**kwargs: Any):
    from gateway_core.agents.school_sql.agent_stream import stream_school_sql_agent_native as _stream

    async for event in _stream(**kwargs):
        yield event


async def stream_policy_only_agent_native(**kwargs: Any):
    from gateway_core.agents.policy_only.agent_stream import stream_policy_only_agent_native as _stream

    async for event in _stream(**kwargs):
        yield event


def resolve_agent_final_model(model: Any) -> Any:
    from gateway_core.api.openai_compat.agent_native_flow import resolve_agent_final_model as _resolve

    return _resolve(model)


def log_agent_native_sql(**kwargs: Any) -> None:
    from gateway_core.api.openai_compat.agent_native_flow import log_agent_native_sql as _log

    _log(**kwargs)


@dataclass(frozen=True)
class AgentNativeCollectedResult:
    answer: str
    process: str
    sources: list[dict[str, Any]]
    first_token_ms: float | None


async def collect_agent_native_events(
    events: AsyncIterable[dict[str, Any]],
    *,
    response_tools: Any,
) -> AgentNativeCollectedResult:
    first_token_ms: float | None = None
    answer_chunks: list[str] = []
    process_chunks: list[str] = []
    openwebui_sources: list[dict[str, Any]] = []
    markdown_filter = VisualMarkdownStreamFilter()

    async for event in events:
        event_type = str(event.get("type") or "")
        if event_type == "sources":
            sources = event.get("sources") if isinstance(event.get("sources"), list) else []
            if sources:
                merge_openwebui_sources(openwebui_sources, sources)
            continue

        text = str(event.get("text") or "")
        if not text:
            continue
        if event_type in {"process", "progress", "tool"}:
            process_chunks.append(text)
            continue

        text = markdown_filter.feed(text)
        if not text:
            continue
        if first_token_ms is None:
            first_token_ms = response_tools.elapsed_ms()
        answer_chunks.append(text)

    flushed = markdown_filter.flush()
    if flushed:
        if first_token_ms is None:
            first_token_ms = response_tools.elapsed_ms()
        answer_chunks.append(flushed)

    return AgentNativeCollectedResult(
        answer="".join(answer_chunks),
        process="".join(process_chunks),
        sources=openwebui_sources,
        first_token_ms=first_token_ms,
    )


async def run_agent_native_non_stream(
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
) -> dict[str, Any]:
    response_tools.log_monitor_event(
        {
            **monitor_base,
            "event": "chat_completion",
            "route_name": "school_agent_native",
            "cache_hit": False,
            "context_present": True,
            "context_source": "school_agent_native",
            "status": "ok",
            "response_mode": "agent_native_non_stream",
            "usage": rt._zero_usage(),
        }
    )
    try:
        collected = await collect_agent_native_events(
            stream_school_sql_agent_native(
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
            ),
            response_tools=response_tools,
        )
    except Exception as exc:
        rt.LOGGER.exception("school_agent_native_non_stream_error")
        text = response_tools.build_upstream_error_text(exc)
        response_tools.log_monitor_event(
            {
                **monitor_base,
                "event": "chat_completion",
                "route_name": "school_agent_native",
                "status": "error",
                "error_type": type(exc).__name__,
                "response_mode": "agent_native_non_stream",
                "usage": rt._current_trace_usage(),
                "answer_preview": monitor_answer_preview_fn(text),
            },
            first_token_ms=response_tools.elapsed_ms(),
            stream_done=True,
        )
        return runtime_response_fns.non_stream(spec.model_id, completion_id, text, rt._current_trace_usage())

    usage = rt._current_trace_usage()
    response_tools.log_monitor_event(
        {
            **monitor_base,
            "event": "chat_completion",
            "route_name": "school_agent_native",
            "cache_hit": False,
            "context_present": True,
            "context_source": "school_agent_native",
            "status": "ok",
            "response_mode": "agent_native_non_stream",
            "usage": usage,
            "answer_preview": monitor_answer_preview_fn(collected.answer),
            "process_preview": monitor_answer_preview_fn(collected.process),
        },
        first_token_ms=collected.first_token_ms,
        stream_done=True,
    )
    remember_conversation_turn(
        conversation_memory_key,
        question=effective_question,
        answer=collected.answer,
    )
    return runtime_response_fns.non_stream(
        spec.model_id,
        completion_id,
        collected.answer,
        usage,
        sources=collected.sources,
    )


async def run_policy_only_agent_native_non_stream(
    *,
    spec: Any,
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
) -> dict[str, Any]:
    try:
        collected = await collect_agent_native_events(
            stream_policy_only_agent_native(
                question=effective_question,
                token=token,
                model=model,
                policy_evidence_search_fn=policy_evidence_search_fn,
                openwebui_chat_id=openwebui_chat_id,
                conversation_context=conversation_context,
            ),
            response_tools=response_tools,
        )
    except Exception as exc:
        rt.LOGGER.exception("policy_only_agent_native_non_stream_error")
        text = response_tools.build_upstream_error_text(exc)
        response_tools.log_monitor_event(
            {
                **monitor_base,
                "event": "chat_completion",
                "route_name": "policy_only_agent_native",
                "status": "error",
                "error_type": type(exc).__name__,
                "response_mode": "policy_only_agent_native_non_stream",
                "usage": rt._current_trace_usage(),
                "answer_preview": monitor_answer_preview_fn(text),
            },
            first_token_ms=response_tools.elapsed_ms(),
            stream_done=True,
        )
        return runtime_response_fns.non_stream(spec.model_id, completion_id, text, rt._current_trace_usage())

    usage = rt._current_trace_usage()
    response_tools.log_monitor_event(
        {
            **monitor_base,
            "event": "chat_completion",
            "route_name": "policy_only_agent_native",
            "cache_hit": False,
            "context_present": True,
            "context_source": "policy_only_agent_native",
            "status": "ok",
            "response_mode": "policy_only_agent_native_non_stream",
            "usage": usage,
            "answer_preview": monitor_answer_preview_fn(collected.answer),
            "process_preview": monitor_answer_preview_fn(collected.process),
        },
        first_token_ms=collected.first_token_ms,
        stream_done=True,
    )
    remember_conversation_turn(
        conversation_memory_key,
        question=effective_question,
        answer=collected.answer,
    )
    return runtime_response_fns.non_stream(
        spec.model_id,
        completion_id,
        collected.answer,
        usage,
        sources=collected.sources,
    )
