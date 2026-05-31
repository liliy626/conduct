from __future__ import annotations

"""OpenAI-compatible chat entrypoint.

The online path is intentionally small:

1. policy API keys -> policy-only ReAct Agent tools
2. school API keys -> DDL/SQL ReAct Agent tools
3. everything else -> plain LLM chat

Old direct-answer, controlled-plan, data-context fallback, and feature routing
chains are intentionally not part of this orchestrator anymore.
"""

import asyncio
import hashlib
from io import StringIO
import json
import os
import re
import time
from typing import Any, Dict, List, Optional

from fastapi.responses import JSONResponse, StreamingResponse
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage

from gateway_core.agents.base_skill import RuntimeContext
from gateway_core.agents.school_sql.canonicalizer import normalize_sql_to_canonical
from gateway_core.agents.school_sql.query_normalizer import QueryNormalizer
from gateway_core.agents.school_sql.sql_utils import execute_query as _execute_query
from gateway_core.agents.universal_hub.graph_builder import compile_universal_hub_graph
from gateway_core.agents.universal_hub.models import SkillEvent
from gateway_core.agents.universal_hub.shadow.runtime_provider import build_gateway_readonly_runtime_provider
from gateway_core.api.openai_compat.adapter import UniversalHubStreamAdapter
from gateway_core.api.openai_compat import runtime_response_builders
from gateway_core.api.openai_compat.agent_native_flow import (
    agent_native_enabled_for_token,
    apply_agent_stream_process_header,
    policy_only_agent_enabled_for_token,
    resolve_agent_native_model,
    run_agent_native_stream,
    run_policy_only_agent_native_stream,
)
from gateway_core.api.openai_compat.chat_pipeline_parts import request_parts, response_parts
from gateway_core.api.openai_compat.pipeline_response_tools import build_pipeline_response_tools
from gateway_core.api.openai_compat.pipeline_setup_flow import prepare_pipeline_setup
from gateway_core.api.openai_compat.policy_evidence_search import build_policy_evidence_search
from gateway_core.prompts.prompt_registry import (
    assemble_llm_messages as _pr_assemble_llm_messages,
    build_answer_style_guard_prompt as _pr_build_answer_style_guard_prompt,
)
from gateway_core.runtime import runtime_context as rt
from gateway_core.runtime.request_handler import prepare_chat_session_context
from gateway_core.runtime.runtime_context import ChatCompletionRequest
from gateway_core.conversation.threading import extract_openwebui_chat_id
from gateway_core.conversation.session_memory import conversation_memory_key, get_conversation_context, sanitize_conversation_text


CANONICAL_PLAN_CACHE: dict[str, dict[str, Any]] = {
    "tenant:sch_zx_mlh:slot:teacher_leave_ranking": {
        "canonical_sqls": [
            """
            SELECT
              "请假人员"->>0 AS "教师姓名",
              COUNT(*) AS "请假次数",
              ROUND(SUM(COALESCE("请假时长", 0))::numeric, 1) AS "总请假时长_小时",
              ROUND(AVG(COALESCE("请假时长", 0))::numeric, 1) AS "平均每次时长_小时",
              MIN("请假开始时间") AS "最早请假时间",
              MAX("请假开始时间") AS "最近请假时间"
            FROM "{schema}"."教师销假_请假明细"
            WHERE "审批状态"::text ILIKE '%COMPLETED%'
              AND "请假人员" IS NOT NULL
            GROUP BY "请假人员"->>0
            ORDER BY "请假次数" DESC, "总请假时长_小时" DESC
            LIMIT 20
            """
        ],
        "required_outputs": ["data_evidence"],
        "tables_used": ["{schema}.教师销假_请假明细"],
        "title": "教师请假排行黄金执行计划",
    }
}


def _resolve_effective_stream_mode(
    *,
    requested_stream: Optional[bool],
    raw_last_question: str,
    last_question: str,
    accept_header: str,
    user_agent: str,
    is_meta_instruction_prompt,
) -> tuple[bool, bool]:
    return response_parts.resolve_effective_stream_mode(
        requested_stream=requested_stream,
        raw_last_question=raw_last_question,
        last_question=last_question,
        is_meta_instruction_prompt=is_meta_instruction_prompt,
    )


def _monitor_answer_preview(text: Any) -> str:
    return rt._clip_monitor_text(str(text or ""))


async def _collect_stream_text_and_usage(
    *,
    client: Any,
    lc_messages: List[BaseMessage],
    stringify_content,
    elapsed_ms,
) -> tuple[str, Dict[str, int], Optional[float], int]:
    return await response_parts.collect_stream_text_and_usage(
        client=client,
        lc_messages=lc_messages,
        stringify_content=stringify_content,
        elapsed_ms=elapsed_ms,
    )


async def run_chat_completions(
    request,
    req: ChatCompletionRequest,
    authorization: Optional[str] = None,
    x_school_scope: Optional[str] = None,
    x_user_id: Optional[str] = None,
    x_user_role: Optional[str] = None,
    x_user_permissions: Optional[str] = None,
):
    apply_agent_stream_process_header(request)
    setup = prepare_pipeline_setup(
        request=request,
        req=req,
        authorization=authorization,
        x_school_scope=x_school_scope,
        x_user_id=x_user_id,
        x_user_role=x_user_role,
        x_user_permissions=x_user_permissions,
        prepare_chat_session_context_fn=prepare_chat_session_context,
        current_user_id_fn=rt._current_user_id,
        current_viewer_role_fn=rt._current_viewer_role,
        gateway=rt.GATEWAY,
        to_lc_messages_fn=rt._cm_to_lc_messages,
        last_user_text_raw_fn=rt._cm_last_user_text_raw,
        last_user_text_fn=rt._cm_last_user_text,
        previous_user_text_fn=rt._cm_previous_user_text,
        stringify_content_fn=rt._cm_stringify_content,
        is_meta_instruction_prompt_fn=rt._ec_is_meta_instruction_prompt,
        resolve_effective_stream_mode_fn=_resolve_effective_stream_mode,
        set_route_name_fn=rt._set_route_name,
        set_trace_usage_fn=rt._set_trace_usage,
        zero_usage_fn=rt._zero_usage,
        collect_request_header_probe_fn=lambda: request_parts.collect_request_header_probe(
            request=request,
            clip_monitor_text=rt._clip_monitor_text,
        ),
        clip_monitor_text_fn=rt._clip_monitor_text,
        hash_token_for_monitor_fn=rt._hash_token_for_monitor,
    )

    runtime_response_fns = runtime_response_builders.build_runtime_response_fns(now_ts=rt._cm_now_ts)
    response_tools = build_pipeline_response_tools(
        request_started=setup.request_started,
        model_id=setup.spec.model_id,
        completion_id=setup.completion_id,
        stream=bool(req.stream),
        runtime_stream_chunk_fn=runtime_response_fns.stream_chunk,
        runtime_stream_end_fn=runtime_response_fns.stream_end,
        runtime_non_stream_response_fn=runtime_response_fns.non_stream,
        current_context_stage_trace_fn=rt._current_context_stage_trace,
        write_question_monitor_event_fn=rt._write_question_monitor_event,
    )

    policy_evidence_search_fn = build_policy_evidence_search(
        truthy_env_fn=rt._truthy_env,
        rag_embed_text_fn=rt._rag_embed_text,
        psycopg_module=rt.psycopg,
    )
    openwebui_chat_id = extract_openwebui_chat_id(req, request)
    conversation_memory_key_value = conversation_memory_key(
        chat_id=openwebui_chat_id,
        token=setup.token,
        school_scope=setup.x_school_scope or "",
    )
    conversation_context = _merge_conversation_contexts(
        get_conversation_context(conversation_memory_key_value),
        _conversation_context_from_lc_messages(setup.lc_messages),
    )

    if _should_fast_return_meta_json(setup.raw_last_question, setup.last_question):
        return await _fast_meta_json_response(setup, response_tools, runtime_response_fns)

    if _should_fast_return_empty_request(setup.effective_question, setup.raw_last_question, setup.last_question):
        return _fast_empty_response(setup, response_tools, runtime_response_fns)

    if _is_experimental_shadow_gating_passed(request=request, token=setup.token):
        return await _run_experimental_shadow_hub(
            request=request,
            req=req,
            setup=setup,
            response_tools=response_tools,
            runtime_response_fns=runtime_response_fns,
            openwebui_chat_id=openwebui_chat_id,
            policy_evidence_search_fn=policy_evidence_search_fn,
            conversation_context=conversation_context,
            route_name="experimental_shadow_hub",
        )

    if _is_universal_hub_ga_gating_passed(token=setup.token) and (
        _should_use_policy_only_agent(setup.token, setup.effective_question)
        or _should_use_school_agent(setup.token, setup.effective_question)
    ):
        return await _run_experimental_shadow_hub(
            request=request,
            req=req,
            setup=setup,
            response_tools=response_tools,
            runtime_response_fns=runtime_response_fns,
            openwebui_chat_id=openwebui_chat_id,
            policy_evidence_search_fn=policy_evidence_search_fn,
            conversation_context=conversation_context,
            route_name="universal_hub_ga",
        )

    if _should_use_policy_only_agent(setup.token, setup.effective_question):
        monitor_base = setup.pipeline_ctx.monitor_base(
            school_scope=setup.x_school_scope,
            question=setup.effective_question,
            last_question=setup.last_question,
            intent_route="policy_only_agent_native",
            route_name="policy_only_agent_native",
            context_source="policy_only_agent_native",
            context_present=True,
            cache_hit=False,
        )
        return StreamingResponse(
            run_policy_only_agent_native_stream(
                spec=setup.spec,
                pipeline_ctx=setup.pipeline_ctx,
                effective_question=setup.effective_question,
                token=setup.token,
                completion_id=setup.completion_id,
                openwebui_chat_id=openwebui_chat_id,
                monitor_base=monitor_base,
                response_tools=response_tools,
                runtime_response_fns=runtime_response_fns,
                model=resolve_agent_native_model(setup.client),
                policy_evidence_search_fn=policy_evidence_search_fn,
                conversation_context=conversation_context,
                conversation_memory_key=conversation_memory_key_value,
                monitor_answer_preview_fn=_monitor_answer_preview,
            ),
            media_type="text/event-stream",
        )

    if _should_use_school_agent(setup.token, setup.effective_question):
        monitor_base = setup.pipeline_ctx.monitor_base(
            school_scope=setup.x_school_scope,
            question=setup.effective_question,
            last_question=setup.last_question,
            intent_route="school_agent_native",
            route_name="school_agent_native",
            context_source="school_agent_native",
            context_present=True,
            cache_hit=False,
        )
        return StreamingResponse(
            run_agent_native_stream(
                spec=setup.spec,
                pipeline_ctx=setup.pipeline_ctx,
                effective_question=setup.effective_question,
                token=setup.token,
                school_scope=setup.x_school_scope,
                completion_id=setup.completion_id,
                openwebui_chat_id=openwebui_chat_id,
                monitor_base=monitor_base,
                response_tools=response_tools,
                runtime_response_fns=runtime_response_fns,
                model=resolve_agent_native_model(setup.client),
                policy_evidence_search_fn=policy_evidence_search_fn,
                conversation_context=conversation_context,
                conversation_memory_key=conversation_memory_key_value,
                monitor_answer_preview_fn=_monitor_answer_preview,
            ),
            media_type="text/event-stream",
        )

    return await _run_plain_chat(
        setup=setup,
        response_tools=response_tools,
        runtime_response_fns=runtime_response_fns,
    )


def _should_use_policy_only_agent(token: str, question: str) -> bool:
    return bool(str(question or "").strip()) and policy_only_agent_enabled_for_token(token)


def _should_use_school_agent(token: str, question: str) -> bool:
    return bool(str(question or "").strip()) and agent_native_enabled_for_token(token)


def _is_experimental_shadow_gating_passed(*, request: Any, token: str) -> bool:
    """Return true only for explicitly whitelisted Universal Hub shadow traffic."""

    if not rt._truthy_env("UNIVERSAL_HUB_SHADOW_GATEWAY_ENABLED", "0"):
        return False
    header_value = (
        request.headers.get("x-yili-experimental")
        or request.headers.get("x-yili-shadow")
        or request.headers.get("x-universal-hub-shadow")
        or ""
    )
    if str(header_value).strip().lower() not in {"1", "true", "yes", "on", "shadow"}:
        return False
    allowlist = _shadow_token_allowlist()
    if allowlist and str(token or "").strip() not in allowlist:
        return False
    return True


def _shadow_token_allowlist() -> set[str]:
    raw = os.getenv("UNIVERSAL_HUB_SHADOW_API_KEY_ALLOWLIST", "").strip()
    if not raw:
        return set()
    return {item.strip() for item in raw.replace(";", ",").split(",") if item.strip()}


def _is_universal_hub_ga_gating_passed(*, token: str) -> bool:
    if not rt._truthy_env("UNIVERSAL_HUB_GA_ENABLED", "0"):
        return False
    allowlist = _ga_token_allowlist()
    if allowlist and str(token or "").strip() not in allowlist:
        return False
    return True


def _ga_token_allowlist() -> set[str]:
    raw = os.getenv("UNIVERSAL_HUB_GA_API_KEY_ALLOWLIST", "").strip()
    if not raw:
        return set()
    return {item.strip() for item in raw.replace(";", ",").split(",") if item.strip()}


async def _run_experimental_shadow_hub(
    *,
    request: Any,
    req: ChatCompletionRequest,
    setup: Any,
    response_tools: Any,
    runtime_response_fns: Any,
    openwebui_chat_id: str | None,
    policy_evidence_search_fn: Any,
    conversation_context: str,
    route_name: str = "experimental_shadow_hub",
):
    required_outputs = _experimental_shadow_required_outputs(req, setup=setup)
    state = {
        "messages": _experimental_shadow_state_messages(setup.lc_messages),
        "session_context": _experimental_shadow_session_context(
            setup=setup,
            req=req,
            openwebui_chat_id=openwebui_chat_id,
        ),
        "required_outputs": required_outputs,
        "completed_outputs": [],
        "evidence_refs": [],
        "arti" + "fact" + "_refs": [],
        "visited_skills": [],
        "skill_call_count": 0,
        "max_skill_calls": _experimental_shadow_max_skill_calls(),
    }
    runtime_ctx = _experimental_shadow_runtime_ctx(
        request=request,
        setup=setup,
        openwebui_chat_id=openwebui_chat_id,
        policy_evidence_search_fn=policy_evidence_search_fn,
        conversation_context=conversation_context,
    )
    config = {"configurable": {"runtime_ctx": runtime_ctx}}

    monitor_base = setup.pipeline_ctx.monitor_base(
        school_scope=setup.x_school_scope,
        question=setup.effective_question,
        last_question=setup.last_question,
        intent_route=route_name,
        route_name=route_name,
        context_source=route_name,
        context_present=bool(required_outputs),
        cache_hit=False,
    )
    plan = _canonical_plan_for_question(
        setup.effective_question,
        route_name=route_name,
        session_context=state["session_context"],
    )
    if plan is not None:
        if setup.pipeline_ctx.stream:
            return StreamingResponse(
                _stream_canonical_plan_cache(
                    plan=plan,
                    user_query=setup.effective_question,
                    session_context=state["session_context"],
                    runtime_ctx=runtime_ctx,
                    setup=setup,
                    response_tools=response_tools,
                    runtime_response_fns=runtime_response_fns,
                    monitor_base={**monitor_base, "cache_hit": True},
                    route_name=route_name,
                ),
                media_type="text/event-stream",
            )
        try:
            result = await _execute_canonical_plan_cache(
                plan=plan,
                runtime_ctx=runtime_ctx,
                session_context=state["session_context"],
                user_query=setup.effective_question,
            )
            text = str(result.get("text") or "")
            sources = result.get("sources") if isinstance(result.get("sources"), list) else None
            response_tools.log_monitor_event(
                {
                    **monitor_base,
                    "cache_hit": True,
                    "event": "chat_completion",
                    "status": "ok",
                    "response_mode": f"{route_name}_plan_cache_non_stream",
                    "completed_outputs": plan.get("required_outputs", []),
                    "visited_skills": result.get("visited_skills", ["canonical_plan_cache"]),
                    "hub_error": "",
                    "usage": rt._zero_usage(),
                    "answer_preview": _monitor_answer_preview(text),
                },
                stream_done=True,
            )
            return JSONResponse(
                runtime_response_fns.non_stream(
                    setup.spec.model_id,
                    setup.completion_id,
                    text,
                    rt._zero_usage(),
                    sources=sources,
                )
            )
        except Exception as exc:
            text = response_tools.build_upstream_error_text(exc)
            response_tools.log_monitor_event(
                {
                    **monitor_base,
                    "cache_hit": True,
                    "event": "chat_completion",
                    "status": "error",
                    "response_mode": f"{route_name}_plan_cache_non_stream",
                    "error_type": type(exc).__name__,
                    "answer_preview": _monitor_answer_preview(text),
                },
                stream_done=True,
            )
            return JSONResponse(
                runtime_response_fns.non_stream(setup.spec.model_id, setup.completion_id, text, rt._zero_usage())
            )

    graph = compile_universal_hub_graph()

    if setup.pipeline_ctx.stream:
        return StreamingResponse(
            _stream_experimental_shadow_hub(
                graph=graph,
                state=state,
                config=config,
                setup=setup,
                response_tools=response_tools,
                runtime_response_fns=runtime_response_fns,
                monitor_base=monitor_base,
                route_name=route_name,
            ),
            media_type="text/event-stream",
        )

    try:
        final_state = await graph.ainvoke(state, config=config)
        text = _experimental_shadow_final_text(final_state)
        response_tools.log_monitor_event(
            {
                **monitor_base,
                "event": "chat_completion",
                "status": "ok",
                "response_mode": f"{route_name}_non_stream",
                "completed_outputs": final_state.get("completed_outputs", []),
                "visited_skills": final_state.get("visited_skills", []),
                "hub_error": final_state.get("hub_error", ""),
                "usage": rt._zero_usage(),
                "answer_preview": _monitor_answer_preview(text),
            },
            stream_done=True,
        )
        return JSONResponse(runtime_response_fns.non_stream(setup.spec.model_id, setup.completion_id, text, rt._zero_usage()))
    except Exception as exc:
        text = response_tools.build_upstream_error_text(exc)
        response_tools.log_monitor_event(
            {
                **monitor_base,
                "event": "chat_completion",
                "status": "error",
                "response_mode": f"{route_name}_non_stream",
                "error_type": type(exc).__name__,
                "answer_preview": _monitor_answer_preview(text),
            },
            stream_done=True,
        )
        return JSONResponse(runtime_response_fns.non_stream(setup.spec.model_id, setup.completion_id, text, rt._zero_usage()))


def _canonical_plan_cache_key(question: str, session_context: dict[str, Any] | None) -> str:
    context = session_context or {}
    tenant_id = str(context.get("school_id") or context.get("tenant_id") or "default").strip() or "default"
    return f"tenant:{tenant_id}:{QueryNormalizer.to_canonical_slot(question)}"


def _canonical_plan_for_question(
    question: str,
    *,
    route_name: str,
    session_context: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    if route_name != "universal_hub_ga":
        return None
    if not rt._truthy_env("UNIVERSAL_HUB_PLAN_CACHE_ENABLED", "1"):
        return None
    plan = CANONICAL_PLAN_CACHE.get(_canonical_plan_cache_key(question, session_context))
    return plan if isinstance(plan, dict) else None


async def _stream_canonical_plan_cache(
    *,
    plan: dict[str, Any],
    user_query: str,
    session_context: dict[str, Any],
    runtime_ctx: RuntimeContext,
    setup: Any,
    response_tools: Any,
    runtime_response_fns: Any,
    monitor_base: dict[str, Any],
    route_name: str,
):
    first_token_ms: float | None = None
    try:
        async for chunk in UniversalHubStreamAdapter.to_openai_sse(
            _single_skill_event(
                SkillEvent(
                    event_type="process",
                    data={"text": "- 命中高频问题黄金执行计划，已跳过 ReAct 随机规划。\n"},
                )
            ),
            model_id=setup.spec.model_id,
            completion_id=setup.completion_id,
            stream_tool_events=True,
            include_done=False,
        ):
            if first_token_ms is None:
                first_token_ms = response_tools.elapsed_ms()
            yield chunk
        result = await _execute_canonical_plan_cache(
            plan=plan,
            runtime_ctx=runtime_ctx,
            session_context=session_context,
            user_query=user_query,
        )
        text = str(result.get("text") or "")
        sources = result.get("sources") if isinstance(result.get("sources"), list) else []
        if text:
            async for chunk in UniversalHubStreamAdapter.to_openai_sse(
                _single_skill_event(SkillEvent(event_type="content", data={"text": text})),
                model_id=setup.spec.model_id,
                completion_id=setup.completion_id,
                stream_tool_events=False,
                include_done=False,
            ):
                if first_token_ms is None:
                    first_token_ms = response_tools.elapsed_ms()
                yield chunk
        if sources:
            async for chunk in UniversalHubStreamAdapter.to_openai_sse(
                _single_skill_event(SkillEvent(event_type="evidence", data={"sources": sources})),
                model_id=setup.spec.model_id,
                completion_id=setup.completion_id,
                stream_tool_events=False,
                include_done=False,
            ):
                if first_token_ms is None:
                    first_token_ms = response_tools.elapsed_ms()
                yield chunk
        response_tools.log_monitor_event(
            {
                **monitor_base,
                "event": "chat_completion",
                "status": "ok",
                "response_mode": f"{route_name}_plan_cache_stream",
                "completed_outputs": plan.get("required_outputs", []),
                "visited_skills": result.get("visited_skills", ["canonical_plan_cache"]),
                "hub_error": "",
                "usage": rt._zero_usage(),
                "answer_preview": _monitor_answer_preview(text),
            },
            first_token_ms=first_token_ms,
            stream_done=True,
        )
        yield runtime_response_fns.stream_end(setup.spec.model_id, setup.completion_id)
    except Exception as exc:
        text = response_tools.build_upstream_error_text(exc)
        yield runtime_response_fns.stream_chunk(setup.spec.model_id, setup.completion_id, text)
        response_tools.log_monitor_event(
            {
                **monitor_base,
                "event": "chat_completion",
                "status": "error",
                "response_mode": f"{route_name}_plan_cache_stream",
                "error_type": type(exc).__name__,
                "answer_preview": _monitor_answer_preview(text),
            },
            first_token_ms=first_token_ms,
            stream_done=True,
        )
        yield runtime_response_fns.stream_end(setup.spec.model_id, setup.completion_id)


async def _execute_canonical_plan_cache(
    *,
    plan: dict[str, Any],
    runtime_ctx: RuntimeContext,
    session_context: dict[str, Any],
    user_query: str,
) -> dict[str, Any]:
    schema_name = str(session_context.get("schema_name") or "").strip()
    if not schema_name:
        raise ValueError("schema_name is required for canonical plan cache")
    psycopg_module = runtime_ctx.get("psycopg_module")
    dsn = str(runtime_ctx.get("dsn") or "").strip()
    if psycopg_module is None or not dsn:
        raise RuntimeError("readonly database runtime is required for canonical plan cache")

    all_rows: list[dict[str, Any]] = []
    lineages: list[dict[str, Any]] = []
    for idx, sql_template in enumerate(plan.get("canonical_sqls") or [], start=1):
        sql = normalize_sql_to_canonical(_render_plan_sql(str(sql_template or ""), schema_name=schema_name))
        if not sql:
            continue
        rows = _execute_query(psycopg_module=psycopg_module, dsn=dsn, sql=sql, params=[])
        row_dicts = [dict(row) for row in rows]
        all_rows.extend(row_dicts)
        tables_used = [
            item.format(schema=schema_name)
            for item in (plan.get("tables_used") or [])
            if isinstance(item, str) and item.strip()
        ]
        lineages.append(
            _canonical_plan_lineage(
                index=idx,
                sql=sql,
                tables_used=tables_used,
                rows=row_dicts,
                user_query=user_query,
                session_context=session_context,
            )
        )

    return {
        "text": _canonical_plan_answer_text(user_query=user_query, rows=all_rows, title=str(plan.get("title") or "")),
        "sources": _canonical_plan_sources(lineages),
        "lineage_ledger": lineages,
        "visited_skills": ["canonical_plan_cache"],
    }


def _render_plan_sql(sql_template: str, *, schema_name: str) -> str:
    clean_schema = schema_name.replace('"', '""')
    return sql_template.format(schema=clean_schema)


def _canonical_plan_lineage(
    *,
    index: int,
    sql: str,
    tables_used: list[str],
    rows: list[dict[str, Any]],
    user_query: str,
    session_context: dict[str, Any],
) -> dict[str, Any]:
    sql_hash = hashlib.sha256(sql.encode("utf-8")).hexdigest()
    sample_payload = json.dumps(rows[:20], ensure_ascii=False, sort_keys=True, default=str)
    return {
        "evidence_ref_id": f"canonical_plan_{index}:{sql_hash[:12]}",
        "sql_hash": sql_hash,
        "tables_used": tables_used,
        "row_count": len(rows),
        "time_range": {},
        "query_purpose": str(user_query or "").strip(),
        "sample_row_fingerprint": hashlib.md5(sample_payload.encode("utf-8")).hexdigest(),
        "meta_context": {
            "tenant_id": str(session_context.get("school_id") or ""),
            "schema_name": str(session_context.get("schema_name") or ""),
            "plan_cache": True,
        },
    }


def _canonical_plan_sources(lineages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    sources: list[dict[str, Any]] = []
    for lineage in lineages:
        tables = [str(item) for item in lineage.get("tables_used") or [] if str(item)]
        title_table = tables[0] if tables else "canonical_sql_plan"
        sources.append(
            {
                "id": lineage.get("evidence_ref_id", ""),
                "title": f"SQL执行证据: {title_table}",
                "content": f"黄金执行计划返回 {lineage.get('row_count', 0)} 行；SQL Hash {lineage.get('sql_hash', '')[:12]}...",
                "metadata": {
                    "type": "sql_lineage",
                    "evidence_ref_id": lineage.get("evidence_ref_id", ""),
                    "sql_hash": lineage.get("sql_hash", ""),
                    "tables_used": tables,
                    "row_count": lineage.get("row_count", 0),
                    "sample_row_fingerprint": lineage.get("sample_row_fingerprint", ""),
                    "time_range": lineage.get("time_range", {}),
                    "meta_context": lineage.get("meta_context", {}),
                },
            }
        )
    return sources


def _canonical_plan_answer_text(*, user_query: str, rows: list[dict[str, Any]], title: str) -> str:
    if not rows:
        return "黄金执行计划已命中，但只读查询没有返回数据。"
    headers = list(rows[0].keys())
    lines = [f"## {title or '高频教务问题固定计划结果'}", "", f"问题：{user_query}", "", _markdown_table(headers, rows[:20])]
    return "\n".join(lines).strip()


def _markdown_table(headers: list[str], rows: list[dict[str, Any]]) -> str:
    safe_headers = [str(item) for item in headers]
    body = ["| " + " | ".join(safe_headers) + " |", "| " + " | ".join("---" for _ in safe_headers) + " |"]
    for row in rows:
        body.append("| " + " | ".join(_markdown_cell(row.get(header)) for header in safe_headers) + " |")
    return "\n".join(body)


def _markdown_cell(value: Any) -> str:
    text = str(value if value is not None else "")
    return text.replace("|", "\\|").replace("\n", " ")


async def _stream_experimental_shadow_hub(
    *,
    graph: Any,
    state: dict[str, Any],
    config: dict[str, Any],
    setup: Any,
    response_tools: Any,
    runtime_response_fns: Any,
    monitor_base: dict[str, Any],
    route_name: str = "experimental_shadow_hub",
):
    first_token_ms: float | None = None
    content_buffer = StringIO()
    content_buffer_chars = 0
    transactional_events: list[SkillEvent] = []
    final_state: dict[str, Any] | None = None
    content_emitted = False
    max_content_chars = _experimental_shadow_transactional_content_max_chars()
    try:
        async for event in graph.astream_events(state, config=config, version="v2"):
            if event.get("event") == "on_custom_event" and event.get("name") == "skill_stream_chunk":
                skill_event = _skill_event_from_custom_graph_event(event)
                if skill_event is None:
                    continue
                if skill_event.event_type in {"content", "artifact"}:
                    content_emitted = True
                if skill_event.event_type == "content":
                    text_delta = _skill_event_text(skill_event)
                    if text_delta and content_buffer_chars < max_content_chars:
                        remaining = max_content_chars - content_buffer_chars
                        content_buffer.write(text_delta[:remaining])
                        content_buffer_chars += min(len(text_delta), remaining)
                    continue
                if skill_event.event_type in {"artifact", "evidence", "evidence_completed"}:
                    transactional_events.append(skill_event)
                    continue
                async for chunk in UniversalHubStreamAdapter.to_openai_sse(
                    _single_skill_event(skill_event),
                    model_id=setup.spec.model_id,
                    completion_id=setup.completion_id,
                    stream_tool_events=True,
                    include_done=False,
                ):
                    if first_token_ms is None:
                        first_token_ms = response_tools.elapsed_ms()
                    yield chunk
            elif event.get("event") == "on_chain_end":
                output = (event.get("data") or {}).get("output")
                if isinstance(output, dict) and _looks_like_hub_final_state(output):
                    final_state = output

        final_state = final_state or {}
        text = _experimental_shadow_final_text(final_state)
        if text and not content_emitted:
            async for chunk in UniversalHubStreamAdapter.to_openai_sse(
                _single_skill_event(SkillEvent(event_type="content", data={"text": text})),
                model_id=setup.spec.model_id,
                completion_id=setup.completion_id,
                stream_tool_events=False,
                include_done=False,
            ):
                if first_token_ms is None:
                    first_token_ms = response_tools.elapsed_ms()
                yield chunk
        if content_emitted:
            buffered_text = content_buffer.getvalue()
            if buffered_text:
                async for chunk in UniversalHubStreamAdapter.to_openai_sse(
                    _single_skill_event(SkillEvent(event_type="content", data={"text": buffered_text})),
                    model_id=setup.spec.model_id,
                    completion_id=setup.completion_id,
                    stream_tool_events=False,
                    include_done=False,
                ):
                    if first_token_ms is None:
                        first_token_ms = response_tools.elapsed_ms()
                    yield chunk
        for event in transactional_events:
            async for chunk in UniversalHubStreamAdapter.to_openai_sse(
                _single_skill_event(event),
                model_id=setup.spec.model_id,
                completion_id=setup.completion_id,
                stream_tool_events=False,
                include_done=False,
            ):
                if first_token_ms is None:
                    first_token_ms = response_tools.elapsed_ms()
                yield chunk
        response_tools.log_monitor_event(
            {
                **monitor_base,
                "event": "chat_completion",
                "status": "ok",
                "response_mode": f"{route_name}_stream",
                "completed_outputs": final_state.get("completed_outputs", []),
                "visited_skills": final_state.get("visited_skills", []),
                "hub_error": final_state.get("hub_error", ""),
                "usage": rt._zero_usage(),
                "answer_preview": _monitor_answer_preview(text),
            },
            first_token_ms=first_token_ms,
            stream_done=True,
        )
        yield runtime_response_fns.stream_end(setup.spec.model_id, setup.completion_id)
    except Exception as exc:
        text = response_tools.build_upstream_error_text(exc)
        yield runtime_response_fns.stream_chunk(setup.spec.model_id, setup.completion_id, text)
        response_tools.log_monitor_event(
            {
                **monitor_base,
                "event": "chat_completion",
                "status": "error",
                "response_mode": f"{route_name}_stream",
                "error_type": type(exc).__name__,
                "answer_preview": _monitor_answer_preview(text),
            },
            first_token_ms=first_token_ms,
            stream_done=True,
        )
        yield runtime_response_fns.stream_end(setup.spec.model_id, setup.completion_id)


async def _single_skill_event(event: SkillEvent):
    yield event


def _skill_event_from_custom_graph_event(event: dict[str, Any]) -> SkillEvent | None:
    data = event.get("data") or {}
    if not isinstance(data, dict):
        return None
    event_type = data.get("event_type")
    payload = data.get("data")
    if not event_type:
        return None
    return SkillEvent(event_type=str(event_type), data=payload)


def _skill_event_text(event: SkillEvent) -> str:
    data = event.data
    if isinstance(data, str):
        return data
    if isinstance(data, dict):
        for key in ("text", "content", "message"):
            value = data.get(key)
            if value:
                return str(value)
    return ""


def _looks_like_hub_final_state(value: dict[str, Any]) -> bool:
    return any(key in value for key in ("messages", "completed_outputs", "visited_skills", "hub_error"))


def _experimental_shadow_state_messages(messages: list[BaseMessage]) -> list[BaseMessage]:
    max_messages = _experimental_shadow_max_history_messages()
    trimmed = _tail_with_system_prefix(list(messages or []), max_messages=max_messages)
    return [_truncate_message_content(message, max_chars=_experimental_shadow_max_message_chars()) for message in trimmed]


def _tail_with_system_prefix(messages: list[BaseMessage], *, max_messages: int) -> list[BaseMessage]:
    if max_messages <= 0 or len(messages) <= max_messages:
        return messages
    system_prefix: list[BaseMessage] = []
    for message in messages:
        if isinstance(message, SystemMessage):
            system_prefix.append(message)
            continue
        break
    if not system_prefix:
        return messages[-max_messages:]
    prefix = system_prefix[: max_messages - 1]
    tail_budget = max_messages - len(prefix)
    return [*prefix, *messages[len(system_prefix) :][-tail_budget:]]


def _truncate_message_content(message: BaseMessage, *, max_chars: int) -> BaseMessage:
    content = getattr(message, "content", None)
    if max_chars <= 0 or not isinstance(content, str) or len(content) <= max_chars:
        return message
    head_len = max_chars // 2
    tail_len = max_chars - head_len
    clipped = f"{content[:head_len]}\n...[history message truncated]...\n{content[-tail_len:]}"
    if hasattr(message, "model_copy"):
        return message.model_copy(update={"content": clipped})
    return message


def _experimental_shadow_max_history_messages() -> int:
    try:
        return max(4, min(int(os.getenv("UNIVERSAL_HUB_MAX_HISTORY_MESSAGES", "30") or "30"), 120))
    except Exception:
        return 30


def _experimental_shadow_max_message_chars() -> int:
    try:
        return max(1000, min(int(os.getenv("UNIVERSAL_HUB_MAX_MESSAGE_CHARS", "6000") or "6000"), 20000))
    except Exception:
        return 6000


def _experimental_shadow_transactional_content_max_chars() -> int:
    try:
        return max(1, min(int(os.getenv("UNIVERSAL_HUB_TRANSACTIONAL_CONTENT_MAX_CHARS", "120000") or "120000"), 500000))
    except Exception:
        return 120000


def _experimental_shadow_required_outputs(req: ChatCompletionRequest, *, setup: Any | None = None) -> list[str]:
    metadata = getattr(req, "metadata", None) or {}
    raw = metadata.get("shadow_required_outputs") or metadata.get("required_outputs")
    if raw is None:
        raw = os.getenv("UNIVERSAL_HUB_SHADOW_REQUIRED_OUTPUTS", "")
    if isinstance(raw, str):
        items = [item.strip() for item in raw.replace(";", ",").split(",")]
    elif isinstance(raw, list):
        items = [str(item or "").strip() for item in raw]
    else:
        items = []
    out: list[str] = []
    for item in items:
        if item and item not in out:
            out.append(item)
    if out:
        return out
    if setup is not None:
        if _should_use_policy_only_agent(setup.token, setup.effective_question):
            return ["policy_evidence"]
        if _should_use_school_agent(setup.token, setup.effective_question):
            return ["data_evidence"]
    return out


def _experimental_shadow_session_context(*, setup: Any, req: ChatCompletionRequest, openwebui_chat_id: str | None) -> dict[str, Any]:
    record = rt._current_gateway_api_key_record()
    school_id = str(getattr(record, "school_id", "") or setup.x_school_scope or "").strip()
    schema_name = str(getattr(record, "schema_name", "") or setup.x_school_scope or "").strip()
    thread_id = (
        openwebui_chat_id
        or getattr(req, "thread_id", None)
        or getattr(req, "conversation_id", None)
        or getattr(req, "chat_id", None)
        or setup.completion_id
    )
    return {
        "school_id": school_id,
        "schema_name": schema_name,
        "thread_id": str(thread_id or ""),
        "turn_id": 1,
        "is_experimental_shadow_gateway": True,
    }


def _experimental_shadow_runtime_ctx(
    *,
    request: Any,
    setup: Any,
    openwebui_chat_id: str | None,
    policy_evidence_search_fn: Any,
    conversation_context: str,
) -> RuntimeContext:
    runtime_ctx = RuntimeContext(
        request_handle=request,
        token=setup.token,
        school_scope=setup.x_school_scope,
        openwebui_chat_id=openwebui_chat_id or "",
        policy_evidence_search_fn=policy_evidence_search_fn,
        conversation_context=conversation_context,
    )
    record = rt._current_gateway_api_key_record()
    school_id = str(getattr(record, "school_id", "") or setup.x_school_scope or "shadow").strip()
    provider = build_gateway_readonly_runtime_provider(
        gateway=rt.GATEWAY,
        psycopg_factory=lambda: rt.psycopg,
        api_key_record_factory=lambda _school_id: record,
    )
    return provider.inject_live_dependencies(school_id, runtime_ctx)


def _experimental_shadow_max_skill_calls() -> int:
    try:
        return max(1, min(int(os.getenv("UNIVERSAL_HUB_SHADOW_MAX_SKILL_CALLS", "4") or "4"), 8))
    except Exception:
        return 4


def _experimental_shadow_final_text(final_state: dict[str, Any]) -> str:
    messages = list(final_state.get("messages", []) or [])
    for message in reversed(messages):
        if isinstance(message, AIMessage):
            text = str(message.content or "").strip()
            if text:
                return text
    error = str(final_state.get("hub_error") or "").strip()
    if error:
        return f"实验影子通道未完成：{error}"
    return "实验影子通道已完成，但没有产生可展示正文。"


def _should_fast_return_empty_request(effective_question: str, raw_last_question: str, last_question: str) -> bool:
    return not any(str(item or "").strip() for item in [effective_question, raw_last_question, last_question])


def _should_fast_return_meta_json(raw_last_question: str, last_question: str) -> bool:
    text = f"{raw_last_question or ''}\n{last_question or ''}".lower()
    return bool(
        rt._ec_is_meta_instruction_prompt(raw_last_question or "")
        or rt._ec_is_meta_instruction_prompt(last_question or "")
        or ("follow-up questions" in text and "json" in text)
        or ("suggest" in text and "follow" in text and "json" in text)
    )


def _fast_empty_response(setup: Any, response_tools: Any, runtime_response_fns: Any) -> JSONResponse:
    monitor_base = setup.pipeline_ctx.monitor_base(
        school_scope=setup.x_school_scope,
        question=setup.effective_question,
        last_question=setup.last_question,
        intent_route="empty_request",
        route_name="empty_request",
        context_source="empty_request",
        context_present=False,
        cache_hit=False,
    )
    response_tools.log_monitor_event(
        {
            **monitor_base,
            "event": "chat_completion",
            "route_name": "empty_request",
            "status": "ok",
            "response_mode": "fast_empty",
            "usage": rt._zero_usage(),
            "answer_preview": "",
        },
        stream_done=True,
    )
    return JSONResponse(runtime_response_fns.non_stream(setup.spec.model_id, setup.completion_id, "", rt._zero_usage()))


async def _fast_meta_json_response(setup: Any, response_tools: Any, runtime_response_fns: Any) -> JSONResponse:
    text = await _meta_followup_json(setup)
    monitor_base = setup.pipeline_ctx.monitor_base(
        school_scope=setup.x_school_scope,
        question=setup.effective_question,
        last_question=setup.last_question,
        intent_route="meta_json",
        route_name="meta_json",
        context_source="meta_json",
        context_present=False,
        cache_hit=False,
    )
    response_tools.log_monitor_event(
        {
            **monitor_base,
            "event": "chat_completion",
            "route_name": "meta_json",
            "status": "ok",
            "response_mode": "fast_meta_json",
            "usage": rt._zero_usage(),
            "answer_preview": text,
        },
        stream_done=True,
    )
    return JSONResponse(runtime_response_fns.non_stream(setup.spec.model_id, setup.completion_id, text, rt._zero_usage()))


async def _meta_followup_json(setup: Any) -> str:
    context_text = _meta_context_text(setup)
    suggestions = await _llm_followup_suggestions(setup, context_text)
    suggestions = _finalize_followup_suggestions(suggestions, context_text)
    return json.dumps({"follow_ups": suggestions[:3]}, ensure_ascii=False)


def _meta_context_text(setup: Any) -> str:
    parts: list[str] = []
    for message in getattr(setup, "lc_messages", []) or []:
        content = rt._cm_stringify_content(getattr(message, "content", ""))
        if content.strip():
            parts.append(content.strip())
    for item in [getattr(setup, "raw_last_question", ""), getattr(setup, "last_question", "")]:
        if str(item or "").strip():
            parts.append(str(item).strip())
    return "\n".join(parts)[-7000:]


async def _llm_followup_suggestions(setup: Any, context_text: str) -> list[str]:
    if not rt._truthy_env("GATEWAY_FOLLOWUP_LLM_ENABLED", "1"):
        return []
    prompt_context = _strip_followup_meta_prompt(context_text)
    if not prompt_context.strip():
        return []
    timeout = _followup_timeout_sec()
    model_id = os.getenv("GATEWAY_FOLLOWUP_MODEL", "").strip() or getattr(setup.spec, "model_id", "")
    model_started = time.perf_counter()
    try:
        spec = rt.GATEWAY.resolve_model(model_id)
        client = rt.GATEWAY.get_client(spec, 0.2, 256)
        messages = [
            SystemMessage(
                content=(
                    "你只负责根据最近一轮问答生成 3 个自然、具体、可继续追问的问题。"
                    "不要查数据，不要调用工具。不要建议重复已经完成的动作，例如已生成图表后不要再问能否生成同一张图。"
                    "优先生成能继续钻取事实、原因、对比、异常、明细或改进建议的问题。"
                    "输出严格 JSON：{\"follow_ups\":[\"...\",\"...\",\"...\"]}"
                )
            ),
            HumanMessage(content=f"最近对话内容：\n{_clip_context_text(prompt_context, 5000)}"),
        ]
        result = await asyncio.wait_for(client.ainvoke(messages), timeout=timeout)
        raw = rt._cm_stringify_content(getattr(result, "content", ""))
        return _parse_followup_suggestions(raw)
    except Exception:
        return []


def _followup_timeout_sec() -> float:
    try:
        return max(1.0, min(10.0, float(os.getenv("GATEWAY_FOLLOWUP_TIMEOUT_SEC", "5") or "5")))
    except Exception:
        return 5.0


def _strip_followup_meta_prompt(text: str) -> str:
    lines = []
    for line in str(text or "").splitlines():
        clean = line.strip()
        low = clean.lower()
        if not clean:
            continue
        if any(
            token in low
            for token in [
                "suggest 3-5 relevant follow-up",
                "response must be a json",
                "json format",
                "### task",
                "### guidelines",
                "### output",
            ]
        ):
            continue
        lines.append(clean)
    return "\n".join(lines)


def _parse_followup_suggestions(raw: str) -> list[str]:
    text = str(raw or "").strip()
    if not text:
        return []
    text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.I).strip()
    text = re.sub(r"\s*```$", "", text).strip()
    try:
        parsed = json.loads(text)
    except Exception:
        match = re.search(r"\{.*\}|\[.*\]", text, flags=re.S)
        if not match:
            return []
        try:
            parsed = json.loads(match.group(0))
        except Exception:
            return []
    if isinstance(parsed, dict):
        items = parsed.get("follow_ups") or parsed.get("followups") or parsed.get("questions")
    else:
        items = parsed
    if not isinstance(items, list):
        return []
    out: list[str] = []
    for item in items:
        clean = re.sub(r"\s+", " ", str(item or "").strip())
        if clean and clean not in out:
            out.append(clean)
    return out[:4]


def _finalize_followup_suggestions(suggestions: list[str], context_text: str) -> list[str]:
    lower = context_text.lower()
    fallback = [
        *_answer_aware_followup_suggestions(context_text),
        *_meta_followup_suggestions(context_text, lower),
    ]
    out: list[str] = []
    for item in [*suggestions, *fallback]:
        clean = re.sub(r"\s+", " ", str(item or "").strip())
        if not clean or clean in out:
            continue
        if _is_low_quality_followup(clean, context_text, lower):
            continue
        out.append(clean)
        if len(out) >= 3:
            break
    return out


def _answer_aware_followup_suggestions(context_text: str) -> list[str]:
    turn = _latest_chat_history_turn(context_text)
    user_question = turn.get("user", "")
    assistant_answer = turn.get("assistant", "") or _strip_followup_meta_prompt(context_text)
    text = assistant_answer.strip()
    if not text:
        return []

    classes = _extract_named_items(text, r"[一二三四五六七八九十]年级\s*\d+\s*班", limit=4)
    teachers = _extract_named_items(text, r"[\u4e00-\u9fa5]{2,4}老师", limit=4)
    dates = _extract_named_items(text, r"(?:\d{1,2}月\d{1,2}日|第\d+周|第[一二三四五六七八九十]+周|上周|本周|本月|上月|本学期|本学年)", limit=4)
    highlights = _extract_answer_highlights(text)
    out: list[str] = []

    if classes:
        out.append(f"{'、'.join(classes[:2])}的原始明细和主要原因分别是什么？")
    if teachers:
        out.append(f"{'、'.join(teachers[:2])}的具体明细、原因和后续建议能展开看吗？")
    if dates:
        out.append(f"{'、'.join(dates[:2])}这些时间点为什么比较突出，能查原始记录核实吗？")
    if any(token in text for token in ["未找到", "没有数据", "数据延迟", "缺失", "为空", "待核实"]):
        out.append("能换一个时间口径或关联数据表再核实一次吗？")
    if any(token in text for token in ["最高", "最多", "集中", "突出", "异常", "风险", "明显", "排名", "排行", "第一"]):
        out.append("这些最突出的对象背后的具体明细和原因是什么？")
    if any(token in text for token in ["建议", "关注", "整改", "改进", "优化"]):
        out.append("这些建议分别对应哪些原始数据和责任对象？")
    if any(token in text for token in ["相比", "对比", "趋势", "增长", "减少", "变化"]):
        out.append("变化最大的对象是谁，能按时间继续拆开看吗？")
    if highlights:
        out.append(f"答案里提到的“{highlights[0]}”能进一步展开到明细和依据吗？")

    if not out and user_question:
        out.append(f"能基于刚才的结论，继续展开“{_clip_context_text(user_question, 30)}”的明细依据吗？")
    return out


def _latest_chat_history_turn(context_text: str) -> dict[str, str]:
    text = str(context_text or "")
    if "<chat_history>" in text:
        text = text.split("<chat_history>", 1)[-1]
    if "</chat_history>" in text:
        text = text.split("</chat_history>", 1)[0]

    matches = list(re.finditer(r"(?m)^(USER|ASSISTANT):\s*", text))
    if not matches:
        return {}

    turns: list[tuple[str, str]] = []
    for idx, match in enumerate(matches):
        start = match.end()
        end = matches[idx + 1].start() if idx + 1 < len(matches) else len(text)
        turns.append((match.group(1), text[start:end].strip()))

    last_user = ""
    last_assistant = ""
    for role, content in turns:
        if role == "USER":
            last_user = content
            last_assistant = ""
        elif role == "ASSISTANT":
            last_assistant = content
    return {"user": last_user, "assistant": last_assistant}


def _extract_answer_highlights(text: str) -> list[str]:
    candidates: list[str] = []
    patterns = [
        r"\*\*([^*\n]{2,24})\*\*",
        r"“([^”\n]{2,24})”",
        r"‘([^’\n]{2,24})’",
    ]
    for pattern in patterns:
        for match in re.finditer(pattern, str(text or "")):
            item = re.sub(r"\s+", "", match.group(1).strip())
            if _is_useful_highlight(item) and item not in candidates:
                candidates.append(item)
            if len(candidates) >= 5:
                return candidates
    return candidates


def _is_useful_highlight(item: str) -> bool:
    if not item or len(item) < 2:
        return False
    generic = {"简短结论", "数据支撑", "建议关注", "总体结论", "数据解读", "引用来源"}
    if item in generic:
        return False
    if re.fullmatch(r"\d+(?:\.\d+)?%?", item):
        return False
    return True


def _is_low_quality_followup(question: str, context_text: str, lower_context: str) -> bool:
    q = question.strip()
    q_lower = q.lower()
    if len(q) < 6:
        return True
    if any(token in q_lower for token in ["question 1", "question 2", "question 3"]):
        return True
    if "图" in q and any(token in lower_context for token in ["已生成图表", "查看图表", "排行图", ".svg", ".html"]):
        if not any(token in q for token in ["原因", "变化", "趋势", "异常", "明细", "对比"]):
            return True
    if "导出" in q and not any(token in lower_context for token in ["明细", "完整名单", "记录"]):
        return True
    if "能不能把排行做成" in q or "生成同一" in q:
        return True
    return False


def _meta_followup_suggestions(text: str, lower: str) -> list[str]:
    if any(token in text for token in ["职称", "高级教师", "园丁奖", "荣誉", "申报"]):
        return [
            "申报这类职称还缺哪些材料？",
            "能结合校内成果记录判断一下优势和短板吗？",
            "相关政策依据和官方链接有哪些？",
        ]
    if any(token in text for token in ["积分", "成果", "教师发展", "指标", "同类"]):
        return [
            "主要积分来源分别是什么？",
            "和同学科、同职称教师相比处于什么位置？",
            "未来一年优先补哪些材料？",
        ]
    if any(token in text for token in ["请假", "病假", "晨午检", "流感", "健康"]):
        return [
            "请假主要集中在哪些年级或班级？",
            "病假占比和上周相比有没有变化？",
            "有没有需要重点关注的异常趋势？",
        ]
    if any(token in text for token in ["德育", "扣分", "行规", "纪律", "卫生"]):
        classes = _extract_named_items(text, r"[一二三四五六七八九十]年级\s*\d+\s*班", limit=3)
        if classes:
            class_text = "、".join(classes[:2])
            return [
                f"{class_text}具体是因为什么项目扣分最多？",
                "和上周或上月相比，扣分趋势有没有明显变化？",
                "能按扣分原因做一个占比分析吗？",
            ]
        return [
            "扣分主要集中在哪些班级？",
            "和上周相比有哪些变化？",
            "主要扣分原因分别是什么？",
        ]
    if any(token in text for token in ["报修", "维修", "设施", "资产", "灯"]):
        return [
            "哪些报修类型最集中？",
            "是否存在反复报修的地点或设备？",
            "本月和上月相比趋势如何？",
        ]
    if any(token in text for token in ["文印", "打印", "复印"]):
        return [
            "文印量最高的月份是哪几个月？",
            "按部门或年级看谁使用最多？",
            "有没有异常高峰或趋势变化？",
        ]
    if any(token in text for token in ["浦东", "教发院", "教研", "培训", "课题", "通知", "链接"]):
        return [
            "这些通知的官方链接分别是什么？",
            "按时间顺序帮我整理一下。",
            "哪些内容最值得学校教师关注？",
        ]
    if any(token in text for token in ["学校", "教师", "学生", "班级", "概况"]):
        return [
            "可以按年级或学科再拆开看吗？",
            "有没有异常值或需要关注的点？",
            "能生成一张图表展示分布吗？",
        ]
    if "follow-up" in lower or "suggest" in lower:
        return [
            "可以继续展开关键数据来源吗？",
            "有没有需要重点关注的异常点？",
            "能按时间或类别做一下对比吗？",
        ]
    return []


def _extract_named_items(text: str, pattern: str, *, limit: int = 5) -> list[str]:
    out: list[str] = []
    for match in re.finditer(pattern, str(text or "")):
        item = re.sub(r"\s+", "", match.group(0))
        if item and item not in out:
            out.append(item)
        if len(out) >= limit:
            break
    return out


def _conversation_context_from_lc_messages(messages: list[BaseMessage], *, max_messages: int = 10, max_chars: int = 6000) -> str:
    """Compact OpenWebUI's prior messages for Agent-native follow-up questions.

    LangGraph MemorySaver is still useful when OpenWebUI provides a stable
    chat_id, but OpenWebUI also sends prior messages in the request body. This
    explicit context keeps follow-up questions working even when chat_id/thread
    headers are missing or when async jobs replay a request without checkpointer
    history.
    """
    if len(messages) <= 1:
        return ""
    prior = messages[:-1]
    lines: list[str] = []
    for message in prior[-max_messages:]:
        text = sanitize_conversation_text(getattr(message, "content", ""))
        if not text:
            continue
        role = _message_role_label(message)
        lines.append(f"{role}: {_clip_context_text(text, 900)}")
    context = "\n".join(lines).strip()
    if len(context) > max_chars:
        context = context[-max_chars:].lstrip()
    return context


def _message_role_label(message: BaseMessage) -> str:
    if isinstance(message, HumanMessage):
        return "用户"
    if isinstance(message, AIMessage):
        return "助手"
    if isinstance(message, SystemMessage):
        return "系统"
    return "消息"


def _clip_context_text(text: str, limit: int) -> str:
    clean = " ".join(str(text or "").replace("\x00", " ").split())
    if len(clean) <= limit:
        return clean
    return clean[:limit].rstrip() + "...[截断]"


def _merge_conversation_contexts(*contexts: str, max_chars: int = 7000) -> str:
    seen: set[str] = set()
    lines: list[str] = []
    for context in contexts:
        for line in str(context or "").splitlines():
            clean = line.strip()
            if not clean or clean in seen:
                continue
            seen.add(clean)
            lines.append(clean)
    merged = "\n".join(lines).strip()
    if len(merged) > max_chars:
        merged = merged[-max_chars:].lstrip()
    return merged


async def _run_plain_chat(*, setup: Any, response_tools: Any, runtime_response_fns: Any):
    lc_messages = _pr_assemble_llm_messages(
        user_question=setup.effective_question,
        data_context_prompt="",
        system_prompt="",
        style_guard=_pr_build_answer_style_guard_prompt(wants_json=False, has_data_context=False),
        llm_messages=setup.lc_messages,
    )
    monitor_base = setup.pipeline_ctx.monitor_base(
        school_scope=setup.x_school_scope,
        question=setup.effective_question,
        last_question=setup.last_question,
        intent_route="plain_chat",
        route_name="plain_chat",
        context_source="plain_chat",
        context_present=False,
        cache_hit=False,
    )
    if setup.pipeline_ctx.stream:
        return StreamingResponse(
            _stream_plain_chat(
                setup=setup,
                lc_messages=lc_messages,
                monitor_base=monitor_base,
                response_tools=response_tools,
                runtime_response_fns=runtime_response_fns,
            ),
            media_type="text/event-stream",
        )

    model_started = time.perf_counter()
    try:
        text, usage, first_token_ms, model_call_ms = await _collect_stream_text_and_usage(
            client=setup.client,
            lc_messages=lc_messages,
            stringify_content=rt._cm_stringify_content,
            elapsed_ms=response_tools.elapsed_ms,
        )
        total_usage = rt._add_trace_usage(usage)
        response_tools.log_monitor_event(
            {
                **monitor_base,
                "event": "chat_completion",
                "route_name": "plain_chat",
                "status": "ok",
                "response_mode": "non_stream",
                "usage": total_usage,
                "model_call_ms": model_call_ms,
                "answer_preview": _monitor_answer_preview(text),
            },
            first_token_ms=first_token_ms,
            stream_done=True,
        )
        return JSONResponse(runtime_response_fns.non_stream(setup.spec.model_id, setup.completion_id, text, total_usage))
    except Exception as exc:
        text = response_tools.build_upstream_error_text(exc)
        response_tools.log_monitor_event(
            {
                **monitor_base,
                "event": "chat_completion",
                "route_name": "plain_chat",
                "status": "error",
                "error_type": type(exc).__name__,
                "model_call_ms": int((time.perf_counter() - model_started) * 1000),
                "answer_preview": _monitor_answer_preview(text),
            },
            stream_done=True,
        )
        return JSONResponse(runtime_response_fns.non_stream(setup.spec.model_id, setup.completion_id, text, rt._zero_usage()))


async def _stream_plain_chat(
    *,
    setup: Any,
    lc_messages: list[Any],
    monitor_base: dict[str, Any],
    response_tools: Any,
    runtime_response_fns: Any,
):
    first_token_ms: float | None = None
    chunks: list[str] = []
    usage: dict[str, int] = rt._zero_usage()
    model_started = time.perf_counter()
    try:
        async for chunk in setup.client.astream(lc_messages):
            chunk_usage = response_parts.extract_usage(chunk)
            if int(chunk_usage.get("total_tokens") or 0) > 0:
                usage = chunk_usage
            text = rt._cm_stringify_content(getattr(chunk, "content", ""))
            if not text:
                continue
            if first_token_ms is None:
                first_token_ms = response_tools.elapsed_ms()
            chunks.append(text)
            yield runtime_response_fns.stream_chunk(setup.spec.model_id, setup.completion_id, text)
    except Exception as exc:
        text = response_tools.build_upstream_error_text(exc)
        chunks.append(text)
        yield runtime_response_fns.stream_chunk(setup.spec.model_id, setup.completion_id, text)
        response_tools.log_monitor_event(
            {
                **monitor_base,
                "event": "chat_completion",
                "route_name": "plain_chat",
                "status": "error",
                "error_type": type(exc).__name__,
                "model_call_ms": int((time.perf_counter() - model_started) * 1000),
                "answer_preview": _monitor_answer_preview("".join(chunks)),
            },
            first_token_ms=first_token_ms,
            stream_done=True,
        )
        yield runtime_response_fns.stream_end(setup.spec.model_id, setup.completion_id)
        return

    total_usage = rt._add_trace_usage(usage)
    response_tools.log_monitor_event(
        {
            **monitor_base,
            "event": "chat_completion",
            "route_name": "plain_chat",
            "status": "ok",
            "response_mode": "stream",
            "usage": total_usage,
            "model_call_ms": int((time.perf_counter() - model_started) * 1000),
            "answer_preview": _monitor_answer_preview("".join(chunks)),
        },
        first_token_ms=first_token_ms,
        stream_done=True,
    )
    yield runtime_response_fns.stream_end(setup.spec.model_id, setup.completion_id)
