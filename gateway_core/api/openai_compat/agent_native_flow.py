from __future__ import annotations

import os
import re
from typing import Any

from gateway_core.agents.streaming.context import set_agent_stream_process_requested
from gateway_core.api.openai_compat.response_composer import ResponseComposer
from gateway_core.infra.postgres_dsn import postgres_dsn
from gateway_core.runtime import runtime_context as rt
from gateway_core.conversation.session_memory import remember_conversation_turn
from gateway_core.agents.school_sql.agent_stream import agent_native_enabled_for_token, stream_school_sql_agent_native
from gateway_core.agents.policy_only.agent_stream import policy_only_agent_enabled_for_token, stream_policy_only_agent_native


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
    enabled = header.strip().lower() in {"1", "true", "yes", "on"}
    set_agent_stream_process_requested(enabled)
    return enabled


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
    markdown_filter = _VisualMarkdownStreamFilter()
    composer = ResponseComposer(
        model_id=spec.model_id,
        completion_id=completion_id,
        now_ts=pipeline_ctx.now_ts_fn,
        include_role=False,
    )

    def _chunk(text: str) -> str:
        nonlocal first_token_ms
        text = markdown_filter.feed(text)
        if not text:
            return ""
        if first_token_ms is None:
            first_token_ms = response_tools.elapsed_ms()
        answer_chunks.append(text)
        chunks = composer.compose_text_delta(text, field="content")
        return chunks[0] if chunks else ""

    def _process_chunk(text: str) -> str:
        nonlocal first_token_ms
        if first_token_ms is None:
            first_token_ms = response_tools.elapsed_ms()
        process_chunks.append(text)
        mode = _env_value("SCHOOL_AGENT_NATIVE_PROCESS_DELTA_MODE", "TENANT_AGENT_NATIVE_PROCESS_DELTA_MODE", "reasoning_content").lower()
        if mode in {"content", "plain"}:
            answer_chunks.append(text)
            chunks = composer.compose_text_delta(text, field="content")
            return chunks[0] if chunks else ""
        chunks = composer.compose_text_delta(text, field="reasoning_content")
        return chunks[0] if chunks else ""

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
    yield _process_chunk("正在理解问题并准备检索学校数据...\n")
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
                    _merge_openwebui_sources(openwebui_sources, sources)
                    for chunk in composer.compose_sources_delta(sources):
                        yield chunk
                continue
            text = str(event.get("text") or "")
            if text:
                if str(event.get("type") or "") in {"process", "progress", "tool"}:
                    yield _process_chunk(text)
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
        for chunk in composer.compose_text_delta(flushed, field="content"):
            yield chunk

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
    markdown_filter = _VisualMarkdownStreamFilter()
    composer = ResponseComposer(
        model_id=spec.model_id,
        completion_id=completion_id,
        now_ts=pipeline_ctx.now_ts_fn,
        include_role=False,
    )

    def _chunk(text: str) -> str:
        nonlocal first_token_ms
        text = markdown_filter.feed(text)
        if not text:
            return ""
        if first_token_ms is None:
            first_token_ms = response_tools.elapsed_ms()
        answer_chunks.append(text)
        chunks = composer.compose_text_delta(text, field="content")
        return chunks[0] if chunks else ""

    def _process_chunk(text: str) -> str:
        nonlocal first_token_ms
        if first_token_ms is None:
            first_token_ms = response_tools.elapsed_ms()
        process_chunks.append(text)
        mode = _env_value("SCHOOL_AGENT_NATIVE_PROCESS_DELTA_MODE", "TENANT_AGENT_NATIVE_PROCESS_DELTA_MODE", "reasoning_content").lower()
        if mode in {"content", "plain"}:
            answer_chunks.append(text)
            chunks = composer.compose_text_delta(text, field="content")
            return chunks[0] if chunks else ""
        chunks = composer.compose_text_delta(text, field="reasoning_content")
        return chunks[0] if chunks else ""

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
    yield _process_chunk("正在理解问题并准备检索政策证据...\n")
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
                    _merge_openwebui_sources(openwebui_sources, sources)
                    for chunk in composer.compose_sources_delta(sources):
                        yield chunk
                continue
            text = str(event.get("text") or "")
            if text:
                if str(event.get("type") or "") in {"process", "progress", "tool"}:
                    yield _process_chunk(text)
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
        for chunk in composer.compose_text_delta(flushed, field="content"):
            yield chunk

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


def _merge_openwebui_sources(target: list[dict[str, Any]], incoming: list[dict[str, Any]]) -> None:
    seen = {_openwebui_source_url(item) for item in target}
    for item in incoming:
        if not isinstance(item, dict):
            continue
        url = _openwebui_source_url(item)
        if not url or url in seen:
            continue
        seen.add(url)
        target.append(item)


def _openwebui_source_url(source: dict[str, Any]) -> str:
    nested = source.get("source") if isinstance(source.get("source"), dict) else {}
    url = str(source.get("url") or nested.get("url") or "").strip()
    if url:
        return url
    metadata = source.get("metadata")
    if isinstance(metadata, list):
        for item in metadata:
            if isinstance(item, dict) and str(item.get("url") or "").strip():
                return str(item.get("url") or "").strip()
    return ""


class _VisualMarkdownStreamFilter:
    """Prevent model-written chart HTML links from being rendered as broken images."""

    def __init__(self) -> None:
        self._buffer = ""

    def feed(self, text: str) -> str:
        self._buffer += str(text or "")
        return self._drain(final=False)

    def flush(self) -> str:
        return self._drain(final=True)

    def _drain(self, *, final: bool) -> str:
        output: list[str] = []
        text = self._buffer
        while text:
            start = text.find("![")
            if start < 0:
                if final:
                    output.append(text)
                    text = ""
                else:
                    keep = 1 if text.endswith("!") else 0
                    if len(text) > keep:
                        output.append(text[:-keep] if keep else text)
                        text = text[-keep:] if keep else ""
                break
            output.append(text[:start])
            alt_end = text.find("]", start + 2)
            if alt_end < 0:
                if final:
                    output.append(text[start:])
                    text = ""
                else:
                    text = text[start:]
                break
            if alt_end + 1 >= len(text):
                if final:
                    output.append(text[start:])
                    text = ""
                else:
                    text = text[start:]
                break
            if text[alt_end + 1] != "(":
                output.append(text[start : start + 1])
                text = text[start + 1 :]
                continue
            close = text.find(")", alt_end + 2)
            if close < 0:
                if final:
                    output.append(text[start:])
                    text = ""
                else:
                    text = text[start:]
                break
            alt = text[start + 2 : alt_end].strip() or "图表"
            url = text[alt_end + 2 : close].strip()
            if _is_broken_chart_image_url(url):
                output.append(f"[查看图表：{alt}]({url})")
            else:
                output.append(text[start : close + 1])
            text = text[close + 1 :]
        self._buffer = text
        return "".join(output)


def _is_broken_chart_image_url(url: str) -> bool:
    value = str(url or "").strip().lower()
    if value.startswith("chart:"):
        return True
    if not value:
        return False
    path = value.split("?", 1)[0].split("#", 1)[0]
    return ("/chart/" in path and path.endswith((".html", ".json"))) or path.endswith(".html")
