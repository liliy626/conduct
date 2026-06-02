from __future__ import annotations

import json
import os
import re
import asyncio
from datetime import datetime
from typing import Any, AsyncIterator, Callable

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.tools import StructuredTool
from langgraph.checkpoint.memory import MemorySaver
from langgraph.prebuilt import create_react_agent

from gateway_core.api.openai_compat.chat_pipeline_parts.request_parts import build_upstream_error_text
from gateway_core.api.openai_compat.citation_formatter import openwebui_sources_from_citation_sources
from gateway_core.agents.streaming.langgraph_event_stream import record_langgraph_event_as_trace_step
from gateway_core.agents.contracts import ContractPlanner, build_tool_contract
from gateway_core.tools.langchain_tools import build_langchain_agent_tools
from gateway_core.conversation.threading import build_langgraph_thread_id
from gateway_core.agents.school_sql.agent_model import agent_model_for_tool_loop
from gateway_core.schema_context.ddl_embedding import ddl_embed_text
from gateway_core.agents.school_sql.sql_tools import DDLReactTools
from gateway_core.infra.api_keys import current_api_key_record, is_school_record
from gateway_core.runtime.runtime_trace_context import _add_trace_usage
from gateway_core.school.schema_indexer import build_school_schema_index
from gateway_core.school.trace import finish_trace, new_tenant_trace, set_step_output, trace_preview, trace_step


_AGENT_NATIVE_MEMORY = MemorySaver()


async def stream_school_sql_agent_native(
    *,
    question: str,
    token: str | None,
    school_scope: str | None,
    dsn: str,
    psycopg_module: Any,
    model: Any,
    final_model: Any = None,
    embedding_fn: Callable[[str], list[float] | None] | None,
    policy_evidence_search_fn: Callable[[str], list[dict[str, Any]]] | None = None,
    openwebui_chat_id: str = "",
    conversation_context: str = "",
    sql_logger: Callable[..., None] | None = None,
    disabled_tool_names: tuple[str, ...] = (),
) -> AsyncIterator[dict[str, Any]]:
    """Run the school-schema DDL ReAct Agent as the primary streaming answerer.

    This intentionally mirrors the old yili-ai-backend shape: retrieve history
    and DDL context, pass an enhanced user message into a LangGraph ReAct Agent,
    stream observable tool calls, and let that same Agent produce the final
    natural-language answer.
    """
    trace = new_tenant_trace(question)
    final_answer_chunks: list[str] = []
    final_answer_source = "direct_agent_content"

    def _content_event(text: str) -> dict[str, str]:
        final_answer_chunks.append(str(text or ""))
        return {"type": "content", "text": str(text or "")}

    def _record_final_answer() -> None:
        answer = _sanitize_final_answer("".join(final_answer_chunks))
        if not answer:
            return
        with trace_step(trace, "chat_completion.final", {"question": question}) as step:
            set_step_output(step, {"final_answer": answer, "answer_source": final_answer_source})

    try:
        with trace_step(
            trace,
            "school.resolve",
            {
                "token_present": bool(str(token or "").strip()),
                "school_scope": school_scope or "",
                "api_key_db_required": True,
                "agent_native": True,
            },
        ) as step:
            record = _require_school_api_key_record()
            school_id = record.school_id or record.schema_name
            if trace is not None:
                trace.school_id = school_id
            set_step_output(
                step,
                {
                    "school_id": school_id,
                    "schema_name": record.schema_name,
                    "display_name": record.display_name,
                    "key_type": record.key_type,
                },
            )

        is_chat_route = await _is_guard_router_chat(trace=trace, model=model, question=question)
        with trace_step(trace, "route_result", {"question": question}) as step:
            set_step_output(
                step,
                {
                    "decision": "chat" if is_chat_route else "data",
                    "route": "guard_router" if is_chat_route else "school_sql_agent",
                    "input": {"question": question, "conversation_context_chars": len(str(conversation_context or ""))},
                    "output": {"uses_database_agent": not is_chat_route},
                    "error": None,
                },
            )
        if is_chat_route:
            final_answer_source = "guard_router"
            async for chunk in _stream_guard_router_chat(
                trace=trace,
                model=model,
                question=question,
                conversation_context=conversation_context,
            ):
                yield _content_event(chunk)
            _record_final_answer()
            finish_trace(trace)
            return

        schema_index = _build_agent_schema_index(
            trace=trace,
            school_id=school_id,
            dsn=dsn,
            psycopg_module=psycopg_module,
        )
        with trace_step(trace, "metadata_selection", {"question": question, "schema_name": schema_index.source_schema}) as step:
            set_step_output(
                step,
                {
                    "input": {"question": question, "schema_name": schema_index.source_schema},
                    "decision": {
                        "source": _schema_index_source(schema_index),
                        "dataset_count": len(schema_index.datasets),
                        "selection_scope": "catalog_loaded_not_question_filtered",
                    },
                    "output": _index_trace_payload(
                        schema_index,
                        source=_schema_index_source(schema_index),
                        include_datasets=False,
                    ),
                    "error": None,
                },
            )

        tools = DDLReactTools(
            question=question,
            school_id=school_id,
            package_index=schema_index,
            dsn=dsn,
            psycopg_module=psycopg_module,
            embedding_fn=_ddl_embedding_fn(embedding_fn),
            trace=trace,
            sql_logger=sql_logger,
        )
        sql_experience = tools.sql_experience_search(question)
        ddl_context = _schema_catalog_context(schema_index, question=question)
        with trace_step(trace, "domain_context", {"question": question, "school_id": school_id}) as step:
            set_step_output(
                step,
                {
                    "input": {"question": question, "school_id": school_id, "schema_name": schema_index.source_schema},
                    "decision": {
                        "sql_experience_chars": len(str(sql_experience or "")),
                        "ddl_context_chars": len(str(ddl_context or "")),
                    },
                    "output": {
                        "sql_experience_preview": trace_preview(sql_experience),
                        "ddl_context_preview": trace_preview(ddl_context),
                    },
                    "error": None,
                },
            )
        final_handoff_enabled = _final_handoff_enabled(final_model)
        direct_snapshot_mode = _is_direct_snapshot_request(question)
        contract_plan = _plan_tool_contract(
            trace=trace,
            model=agent_model_for_tool_loop(model),
            question=question,
            conversation_context=conversation_context,
        )
        tool_contract = build_tool_contract(question, plan=contract_plan)
        tool_list = [
            *tools.as_langchain_tools(),
            *build_langchain_agent_tools(
                school_id=school_id,
                trace=trace,
                policy_evidence_search_fn=policy_evidence_search_fn,
                question=question,
                tool_contract=tool_contract,
            ),
        ]
        disabled_tools = {str(name or "").strip() for name in disabled_tool_names if str(name or "").strip()}
        if disabled_tools:
            tool_list = [
                tool for tool in tool_list if str(getattr(tool, "name", "") or "").strip() not in disabled_tools
            ]
        if final_handoff_enabled:
            tool_list.append(
                _final_answer_handoff_tool(
                    trace=trace,
                    final_model=final_model,
                    question=question,
                    source_views_fn=lambda: list(tools.source_views),
                    tool_contract=tool_contract,
                )
            )

        memory_enabled = bool(str(openwebui_chat_id or "").strip())
        thread_id = build_langgraph_thread_id(
            chat_id=openwebui_chat_id,
            school_id=school_id,
            gateway_token_hash=_hash_token(token),
        )
        agent = create_react_agent(
            agent_model_for_tool_loop(model),
            tool_list,
            prompt=_agent_native_prompt(
                schema_index,
                final_handoff_enabled=final_handoff_enabled,
                available_tool_names=[str(getattr(tool, "name", "") or "") for tool in tool_list],
                tool_contract_prompt=tool_contract.prompt_text(),
            ),
            checkpointer=_AGENT_NATIVE_MEMORY if memory_enabled else None,
            version="v2",
        )
        config: dict[str, Any] = {"recursion_limit": _recursion_limit()}
        if memory_enabled:
            config["configurable"] = {"thread_id": thread_id}

        with trace_step(
            trace,
            "agent_native.start",
            {
                "school_id": school_id,
                "question": question,
                "memory_enabled": memory_enabled,
                "thread_id": thread_id if memory_enabled else "",
            },
        ) as step:
            set_step_output(
                step,
                {
                    "history_sql_context_chars": len(sql_experience),
                    "ddl_context_chars": len(ddl_context),
                    "tool_count": len(tool_list),
                    "available_tools": [str(getattr(tool, "name", "") or "") for tool in tool_list],
                    "tool_contract": tool_contract.trace_payload(),
                },
            )

        enhanced_content = _enhanced_content(
            question=question,
            sql_experience=sql_experience,
            ddl_context=ddl_context,
            conversation_context=conversation_context,
        )
        latest_messages: list[Any] = []
        citation_sources: list[dict[str, Any]] = []
        llm_content_buffers: dict[str, list[str]] = {}
        llm_tool_call_runs: set[str] = set()
        llm_streaming_answer_runs: set[str] = set()
        llm_structured_reasoning_runs: set[str] = set()
        buffer_tool_planning_content = _should_buffer_tool_planning_content(model)
        final_handoff_done = False
        fallback_final_text = ""
        async for event in agent.astream_events({"messages": [HumanMessage(content=enhanced_content)]}, config=config, version="v2"):
            if not _is_final_model_langgraph_event(event, final_model=final_model, final_handoff_enabled=final_handoff_enabled):
                record_langgraph_event_as_trace_step(trace, event, prefix="agent_native.langgraph")
            _merge_citation_sources(citation_sources, _event_citation_sources(event))
            event_text = _event_visible_text(event) if _stream_tool_events_enabled() else ""
            if event_text:
                yield {
                    "type": "process",
                    "text": event_text,
                    "langgraph_event": str(event.get("event") or ""),
                    "tool_name": str(event.get("name") or ""),
                }
            asset_text = _event_asset_markdown(event)
            if asset_text:
                yield _content_event(asset_text)
            if final_handoff_enabled and event.get("event") == "on_tool_end" and str(event.get("name") or "") == "final_answer_handoff":
                handoff_block = _extract_final_handoff_block(_tool_output_text(event))
                if handoff_block:
                    yield {"type": "process", "text": "\n\n> ⚠️ **最终交接被工具合同拦截**：" + str(handoff_block.get("message") or "") + "\n\n"}
                    continue
                handoff_payload = _extract_final_handoff_payload(_tool_output_text(event))
                if not handoff_payload:
                    handoff_payload = _fallback_final_handoff_payload(
                        question=question,
                        tools=tools,
                        caveat="final_answer_handoff returned an unparsable payload",
                    )
                handoff_payload = _handoff_payload_with_tool_evidence(handoff_payload, tools=tools)
                final_answer_source = "handoff"
                async for chunk in _stream_fast_final_answer(
                    trace=trace,
                    final_model=final_model,
                    question=question,
                    handoff_payload=handoff_payload,
                    source_views=tools.source_views,
                ):
                    yield _content_event(chunk)
                final_handoff_done = True
                break
            if event.get("event") == "on_chat_model_stream":
                reasoning_content = _chunk_reasoning_content(event)
                content = _chunk_content(event)
                run_id = str(event.get("run_id") or "")
                if reasoning_content:
                    llm_structured_reasoning_runs.add(run_id)
                    yield {"type": "process", "text": _sanitize_process_text(reasoning_content)}
                if buffer_tool_planning_content and _chunk_has_tool_calls(event):
                    llm_tool_call_runs.add(run_id)
                if content:
                    if _should_suppress_natural_answer_for_direct_snapshot(question=question, tools=tools):
                        yield {"type": "process", "text": _sanitize_process_text(content)}
                    elif final_handoff_enabled:
                        llm_content_buffers.setdefault(run_id, []).append(content)
                        yield {"type": "process", "text": _sanitize_process_text(content)}
                    elif buffer_tool_planning_content and run_id in llm_structured_reasoning_runs:
                        yield _content_event(content)
                    elif buffer_tool_planning_content:
                        if run_id in llm_streaming_answer_runs:
                            yield _content_event(content)
                        else:
                            buffer = llm_content_buffers.setdefault(run_id, [])
                            buffer.append(content)
                            buffered_text = "".join(buffer)
                            if run_id not in llm_tool_call_runs:
                                split = _split_buffered_deepseek_content(buffered_text)
                                if split["ready"]:
                                    if split["process"]:
                                        yield {"type": "process", "text": _sanitize_process_text(split["process"])}
                                    if split["answer"]:
                                        yield _content_event(split["answer"])
                                    llm_streaming_answer_runs.add(run_id)
                                    llm_content_buffers.pop(run_id, None)
                    else:
                        yield _content_event(content)
            if event.get("event") == "on_chat_model_end" and (final_handoff_enabled or buffer_tool_planning_content):
                run_id = str(event.get("run_id") or "")
                buffered_text = "".join(llm_content_buffers.pop(run_id, []))
                if final_handoff_enabled:
                    handoff_payload = _extract_final_handoff_payload(buffered_text)
                    if handoff_payload and not final_handoff_done:
                        final_handoff_done = True
                        handoff_payload = _handoff_payload_with_tool_evidence(handoff_payload, tools=tools)
                        async for chunk in _stream_fast_final_answer(
                            trace=trace,
                            final_model=final_model,
                            question=question,
                            handoff_payload=handoff_payload,
                            source_views=tools.source_views,
                        ):
                            yield _content_event(chunk)
                    elif buffered_text:
                        fallback_final_text = buffered_text
                elif buffered_text:
                    if _should_suppress_natural_answer_for_direct_snapshot(question=question, tools=tools):
                        yield {"type": "process", "text": _sanitize_process_text(buffered_text)}
                    elif _chat_model_end_has_tool_calls(event) or run_id in llm_tool_call_runs:
                        yield {"type": "process", "text": _sanitize_process_text(buffered_text)}
                    else:
                        split = _split_buffered_deepseek_content(buffered_text, force=True)
                        if split["process"]:
                            yield {"type": "process", "text": _sanitize_process_text(split["process"])}
                        if split["answer"]:
                            yield _content_event(split["answer"])
                llm_tool_call_runs.discard(run_id)
                llm_streaming_answer_runs.discard(run_id)
                llm_structured_reasoning_runs.discard(run_id)
            messages = _messages_from_event(event)
            if messages:
                latest_messages = messages
        if final_handoff_enabled and not final_handoff_done:
            handoff_payload = _extract_final_handoff_payload(fallback_final_text)
            if not handoff_payload:
                handoff_payload = _fallback_final_handoff_payload(
                    question=question,
                    tools=tools,
                    caveat=(
                        "DataAgent finished without calling final_answer_handoff; "
                        "the final answer is generated from collected tool evidence only."
                    ),
                )
            handoff_payload = _handoff_payload_with_tool_evidence(handoff_payload, tools=tools)
            final_answer_source = "fallback"
            async for chunk in _stream_fast_final_answer(
                trace=trace,
                final_model=final_model,
                question=question,
                handoff_payload=handoff_payload,
                source_views=tools.source_views,
            ):
                yield _content_event(chunk)
        if direct_snapshot_mode and not final_handoff_enabled:
            direct_answer = _direct_snapshot_answer(
                question=question,
                handoff_payload=_handoff_payload_with_tool_evidence({}, tools=tools),
            )
            if direct_answer:
                final_answer_source = "direct_snapshot"
                yield _content_event(direct_answer)
        sql_lineages = _sql_lineages_from_evidence_by_task(getattr(tools, "evidence_by_task", {}) or {})
        openwebui_sources = _openwebui_sources_from_tool_sources(citation_sources)
        if not openwebui_sources:
            openwebui_sources = _openwebui_sources_from_sql_lineages(sql_lineages)
        if not openwebui_sources:
            openwebui_sources = _openwebui_sources_from_source_views(tools.source_views)
        if openwebui_sources:
            yield {"type": "sources", "sources": openwebui_sources}

        with trace_step(trace, "context.build", {"query_mode": "agent_native_react"}) as step:
            set_step_output(
                step,
                {
                    "context_source": f"school_schema.{school_id}.agent_native_react",
                    "payload_summary": {
                        "query_mode": "agent_native_react",
                        "answer_mode": "agent_native",
                        "task_count": len(tools.evidence_by_task),
                        "ddl_context_count": len(tools.ddl_contexts),
                        "source_views": tools.source_views,
                        "message_count": len(latest_messages),
                        "thread_id": thread_id if memory_enabled else "",
                        "trace_id": trace.trace_id if trace is not None else "",
                    },
                },
            )
        _record_final_answer()
        finish_trace(trace)
    except Exception as exc:
        with trace_step(trace, "agent_native.error", {"question": question}) as step:
            set_step_output(step, {"error": str(exc)})
        yield _content_event(build_upstream_error_text(exc))
        _record_final_answer()
        finish_trace(trace)


def _plan_tool_contract(*, trace: Any, model: Any, question: str, conversation_context: str) -> Any:
    available_tools = _contract_available_tools()
    with trace_step(
        trace,
        "agent_native.contract.plan",
        {
            "question": question,
            "available_tools": available_tools,
        },
    ) as step:
        plan = ContractPlanner(model).plan_turn(
            question=question,
            conversation_context=conversation_context,
            available_tools=available_tools,
        )
        set_step_output(
            step,
            {
                "input": {
                    "question": question,
                    "conversation_context_preview": trace_preview(conversation_context),
                    "available_tools": available_tools,
                },
                "decision": {
                    "required_outputs": list(plan.required_outputs),
                    "allowed_tools": list(plan.allowed_tools),
                    "answer_mode": plan.answer_mode,
                },
                "output": {
                    "reason": plan.reason,
                },
                "error": None,
                "required_outputs": list(plan.required_outputs),
                "allowed_tools": list(plan.allowed_tools),
                "answer_mode": plan.answer_mode,
                "reason": plan.reason,
            },
        )
        with trace_step(trace, "query_plan", {"question": question}) as plan_step:
            set_step_output(
                plan_step,
                {
                    "input": {
                        "question": question,
                        "conversation_context_preview": trace_preview(conversation_context),
                        "available_tools": available_tools,
                    },
                    "decision": {
                        "required_outputs": list(plan.required_outputs),
                        "allowed_tools": list(plan.allowed_tools),
                        "answer_mode": plan.answer_mode,
                        "reason": plan.reason,
                    },
                    "output": {"tool_contract_seed": plan.trace_payload() if hasattr(plan, "trace_payload") else {}},
                    "error": None,
                },
            )
        return plan


def _contract_available_tools() -> list[str]:
    return [
        "time",
        "business_prompt_context",
        "official_policy_search",
        "chart",
        "plot",
        "web_search",
        "generate_image_tool",
        "slide",
    ]


def agent_native_enabled_for_token(token: str | None) -> bool:
    clean_token = str(token or "").strip()
    if not clean_token:
        return False
    record = current_api_key_record()
    return is_school_record(record) and bool(str(getattr(record, "schema_name", "") or "").strip())


def _build_agent_schema_index(
    *,
    trace: Any,
    school_id: str,
    dsn: str,
    psycopg_module: Any,
) -> Any:
    record = _require_school_api_key_record()
    schema_name = str(record.schema_name or "").strip()
    school_name = str(record.display_name or record.school_id or school_id).strip()
    with trace_step(
        trace,
        "school_schema.index",
        {
            "school_id": school_id,
            "schema_name": schema_name,
            "source": "platform.gateway_api_keys",
        },
    ) as step:
        schema_index = build_school_schema_index(
            school_id=school_id,
            school_name=school_name,
            schema_name=schema_name,
            dsn=dsn,
            psycopg_module=psycopg_module,
        )
        set_step_output(step, _index_trace_payload(schema_index, source=_schema_index_source(schema_index), include_datasets=True))
        return schema_index


def _require_school_api_key_record() -> Any:
    record = current_api_key_record()
    if not is_school_record(record):
        raise ValueError("school API key is required for school SQL Agent")
    if not str(getattr(record, "schema_name", "") or "").strip():
        raise ValueError("school API key must configure schema_name in platform.gateway_api_keys")
    return record


def _index_trace_payload(schema_index: Any, *, source: str, include_datasets: bool = False) -> dict[str, Any]:
    dataset_limit = _trace_int_env("GATEWAY_TRACE_METADATA_DATASET_LIMIT", "SCHOOL_TRACE_METADATA_DATASET_LIMIT", 50)
    field_limit = _trace_int_env("GATEWAY_TRACE_METADATA_FIELD_LIMIT", "SCHOOL_TRACE_METADATA_FIELD_LIMIT", 30)
    payload = {
        "source": source,
        "school_id": schema_index.school_id,
        "school_name": schema_index.school_name,
        "schema_name": schema_index.source_schema,
        "datasets_count": len(schema_index.datasets),
        "fields_count": sum(len(dataset.fields) for dataset in schema_index.datasets),
        "sample_datasets": [
            {
                "dataset_id": dataset.dataset_id,
                "label": dataset.label,
                "source_view": dataset.source_view,
                "fields_count": len(dataset.fields),
            }
            for dataset in schema_index.datasets[:20]
        ],
    }
    if include_datasets:
        payload["datasets"] = [
            {
                "dataset_id": dataset.dataset_id,
                "label": dataset.label,
                "source_view": dataset.source_view,
                "fields_count": len(dataset.fields),
                "fields": [
                    {
                        "field_id": str(getattr(field, "field_id", "") or ""),
                        "source_field": str(getattr(field, "source_field", "") or ""),
                        "label": str(getattr(field, "label", "") or ""),
                        "type": str(getattr(field, "field_type", "") or ""),
                    }
                    for field in _business_trace_fields(list(dataset.fields), limit=field_limit)
                ],
            }
            for dataset in schema_index.datasets[:dataset_limit]
        ]
    return payload


def _business_trace_fields(fields: list[Any], *, limit: int) -> list[Any]:
    out: list[Any] = []
    for field in fields:
        source_field = str(getattr(field, "source_field", "") or "")
        field_id = str(getattr(field, "field_id", "") or "")
        label = str(getattr(field, "label", "") or "")
        text = f"{source_field} {field_id} {label}".lower()
        if source_field.startswith("__") or field_id.split(".")[-1].startswith("__"):
            continue
        if any(token in text for token in ["tenant", "uuid", "instance_id", "raw_json", "raw_value", "app_code"]):
            continue
        out.append(field)
        if len(out) >= limit:
            break
    return out


def _trace_int_env(primary: str, legacy: str, default: int) -> int:
    raw = _env_value(primary, legacy, str(default))
    try:
        return max(1, min(int(raw), 500))
    except Exception:
        return default


def _schema_index_source(schema_index: Any) -> str:
    datasets = list(getattr(schema_index, "datasets", []) or [])
    sources = {
        str(getattr(dataset, "raw", {}).get("source") or "").strip()
        for dataset in datasets
        if isinstance(getattr(dataset, "raw", None), dict)
    }
    sources.discard("")
    if len(sources) == 1:
        return next(iter(sources))
    if sources:
        return ",".join(sorted(sources))
    return "unknown"


def _env_value(primary: str, legacy: str = "", default: str = "") -> str:
    value = os.getenv(primary, "").strip()
    if value:
        return value
    if legacy:
        value = os.getenv(legacy, "").strip()
        if value:
            return value
    return default


def _ddl_embedding_fn(fallback_embedding_fn: Callable[[str], list[float] | None] | None) -> Callable[..., list[float] | None] | None:
    if _env_value("SCHOOL_DDL_EMBED_ENABLED", "TENANT_DDL_EMBED_ENABLED", "1").lower() in {"1", "true", "yes", "on"}:
        return ddl_embed_text
    return fallback_embedding_fn


def _agent_native_prompt(
    schema_index: Any,
    *,
    final_handoff_enabled: bool = False,
    available_tool_names: list[str] | None = None,
    tool_contract_prompt: str = "",
) -> str:
    available_tools = [name for name in (available_tool_names or []) if str(name or "").strip()]
    available_tools_text = "、".join(available_tools) if available_tools else "未提供"
    lines = [
            "角色：学校数据证据 Agent。",
            f"可用工具：{available_tools_text}。",
            "只调用可用工具；工具失败按返回 error 处理。",
            tool_contract_prompt or "工具合同：无。",
            "输入包含当前时间、历史 SQL、表目录和用户问题。",
            "表目录不是字段清单；写 SQL 前用 ddl_search、inspect_table_schema 或 sample_table_rows 确认表和字段。",
            "DDL/工具返回是硬约束；历史 SQL 只作参考。",
            "只执行当前 schema 的只读 SELECT；不得编造表、字段、数字、名单或政策。",
            "JSONB/数组字段先 sample_table_rows 或 inspect_jsonb_recordset；需要展开时用 jsonb_recordset_query。",
            "计数按稳定 ID 去重；没有 ID 时按姓名或名称去重；不要把 row_count 直接当人数。",
            "当前时间问题遇到 0/空结果时，先核验候选表 MAX 时间，再换候选表复查。",
            "统计、趋势、排名优先聚合 SQL；明细或完整名单按 has_more/OFFSET 翻页，并把未查全写入 caveats。",
            "只有用户明确要求时才调用联网、政策、图表、图片或 PPT 工具。",
            "外部原因先查校内数据，再用公开证据补证；没有证据就写 caveats。",
            f"学校：{schema_index.school_id} / {schema_index.school_name}；schema：{schema_index.source_schema}。",
    ]
    if final_handoff_enabled:
        lines.insert(
            -2,
            (
                "完成必要查询后调用 final_answer_handoff 交接 JSON；不要直接输出长答案。"
                "JSON 优先包含 result_id、row_count、pure_business_data_markdown、source_views、caveats。"
                "不要包含个人敏感明细。"
            ),
        )
    return "\n".join(lines)


def _final_handoff_enabled(final_model: Any) -> bool:
    if final_model is None:
        return False
    raw = _env_value("SCHOOL_AGENT_FINAL_MODEL_HANDOFF_ENABLED", "TENANT_AGENT_FINAL_MODEL_HANDOFF_ENABLED", "0")
    return str(raw or "0").strip().lower() in {"1", "true", "yes", "on"}


def _should_buffer_tool_planning_content(model: Any) -> bool:
    raw = _env_value("SCHOOL_AGENT_BUFFER_TOOL_CALL_CONTENT", "TENANT_AGENT_BUFFER_TOOL_CALL_CONTENT", "auto")
    clean = str(raw or "auto").strip().lower()
    if clean in {"1", "true", "yes", "on"}:
        return True
    if clean in {"0", "false", "no", "off"}:
        return False
    model_name = _model_name(model).lower()
    return "deepseek" in model_name or model_name in {"deepseek-chat", "deepseek-reasoner"}


def _chat_model_end_has_tool_calls(event: dict[str, Any]) -> bool:
    data = event.get("data") if isinstance(event.get("data"), dict) else {}
    return _value_has_tool_calls(data.get("output"))


def _chunk_has_tool_calls(event: dict[str, Any]) -> bool:
    data = event.get("data") if isinstance(event.get("data"), dict) else {}
    return _value_has_tool_calls(data.get("chunk"))


def _split_buffered_deepseek_content(text: str, *, force: bool = False) -> dict[str, Any]:
    raw = str(text or "")
    if not raw:
        return {"ready": False, "process": "", "answer": ""}
    marker_index = _final_answer_marker_index(raw)
    if marker_index > 0:
        prefix = raw[:marker_index]
        if not _looks_like_tool_planning_text(prefix):
            return {"ready": True, "process": "", "answer": raw}
        return {
            "ready": True,
            "process": prefix,
            "answer": raw[marker_index:],
        }
    if marker_index == 0:
        return {"ready": True, "process": "", "answer": raw}
    if _looks_like_tool_planning_text(raw) and not force:
        return {"ready": False, "process": "", "answer": ""}
    if force:
        return {"ready": True, "process": "", "answer": raw}
    if len(raw) >= _deepseek_answer_holdback_chars():
        return {"ready": True, "process": "", "answer": raw}
    return {"ready": False, "process": "", "answer": ""}


def _final_answer_marker_index(text: str) -> int:
    candidates: list[int] = []
    for pattern in [
        r"以下是.{0,180}(?:完整汇报|情况|名单|分析|报告|结果)",
        r"下面(?:是|给你|为你).{0,180}(?:汇报|情况|名单|分析|报告|结果)",
        r"正式(?:回答|汇报|结论)[:：]?",
        r"\n\s*#{1,4}\s+",
        r"\n\s*---\s*\n",
        r"根据(?:学校|本次|查询|数据)",
        r"(?:本月|本学期|本年度|本周).{0,16}(?:情况|概况|分析|如下)",
        r"(?:统计周期|数据来源)[:：]",
    ]:
        match = re.search(pattern, text)
        if match:
            candidates.append(match.start())
    return min(candidates) if candidates else -1


def _looks_like_tool_planning_text(text: str) -> bool:
    clean = re.sub(r"\s+", "", str(text or ""))
    return any(
        marker in clean
        for marker in [
            "我先",
            "先查",
            "查一下",
            "查看",
            "确认字段",
            "找到核心表",
            "现在查询",
            "现在我来查询",
            "现在我来整理",
            "数据非常完整",
            "数据已经",
            "已经获取",
            "已经拿到",
            "让我",
        ]
    )


def _sanitize_final_answer(text: str) -> str:
    raw = str(text or "").strip()
    if not raw:
        return ""
    marker_index = _formal_final_answer_marker_index(raw)
    if marker_index < 0:
        marker_index = _final_answer_marker_index(raw)
    if marker_index > 0 and _looks_like_tool_planning_text(raw[:marker_index]):
        return raw[marker_index:].lstrip(" \n\r\t：:")
    return raw


def _formal_final_answer_marker_index(text: str) -> int:
    candidates: list[int] = []
    for pattern in [
        r"以下是.{0,180}(?:完整汇报|情况|名单|分析|报告|结果)",
        r"下面(?:是|给你|为你).{0,180}(?:汇报|情况|名单|分析|报告|结果)",
        r"正式(?:回答|汇报|结论)[:：]?",
    ]:
        match = re.search(pattern, str(text or ""))
        if match:
            candidates.append(match.start())
    return min(candidates) if candidates else -1


def _deepseek_answer_holdback_chars() -> int:
    try:
        return max(40, min(int(_env_value("SCHOOL_AGENT_DEEPSEEK_ANSWER_HOLDBACK_CHARS", "TENANT_AGENT_DEEPSEEK_ANSWER_HOLDBACK_CHARS", "240") or "240"), 1000))
    except Exception:
        return 240


def _value_has_tool_calls(value: Any, *, depth: int = 0) -> bool:
    if depth > 4 or value is None:
        return False
    for attr in ("tool_calls", "tool_call_chunks"):
        try:
            attr_value = getattr(value, attr)
        except Exception:
            attr_value = None
        if attr_value:
            return True
    try:
        additional_kwargs = getattr(value, "additional_kwargs")
    except Exception:
        additional_kwargs = None
    if isinstance(additional_kwargs, dict) and additional_kwargs.get("tool_calls"):
        return True
    if isinstance(value, dict):
        for key in ("tool_calls", "tool_call_chunks"):
            if value.get(key):
                return True
        for key in ("message", "output", "generations", "llm_output"):
            if _value_has_tool_calls(value.get(key), depth=depth + 1):
                return True
        messages = value.get("messages")
        if isinstance(messages, list) and any(_value_has_tool_calls(item, depth=depth + 1) for item in messages):
            return True
    if isinstance(value, (list, tuple)):
        return any(_value_has_tool_calls(item, depth=depth + 1) for item in value)
    return False


def _extract_final_handoff_payload(text: str) -> dict[str, Any]:
    raw = str(text or "").strip()
    marker = "FINAL_ANSWER_HANDOFF_JSON:"
    marker_index = raw.find(marker)
    if marker_index < 0:
        return {}
    payload_text = _strip_json_fence(raw[marker_index + len(marker) :])
    parsed = _loads_json_object(payload_text)
    if parsed:
        return parsed
    match = re.search(r"\{.*\}", payload_text, flags=re.DOTALL)
    return _loads_json_object(match.group(0)) if match else {}


def _extract_final_handoff_block(text: str) -> dict[str, Any]:
    raw = str(text or "").strip()
    marker = "FINAL_ANSWER_HANDOFF_BLOCKED:"
    marker_index = raw.find(marker)
    if marker_index < 0:
        return {}
    payload_text = _strip_json_fence(raw[marker_index + len(marker) :])
    parsed = _loads_json_object(payload_text)
    if parsed:
        return parsed
    match = re.search(r"\{.*\}", payload_text, flags=re.DOTALL)
    return _loads_json_object(match.group(0)) if match else {}


def _strip_json_fence(text: str) -> str:
    clean = str(text or "").strip()
    if clean.startswith("```"):
        clean = re.sub(r"^```(?:json)?\s*", "", clean, flags=re.IGNORECASE)
        clean = re.sub(r"\s*```$", "", clean)
    return clean.strip()


def _loads_json_object(text: str) -> dict[str, Any]:
    try:
        parsed = json.loads(str(text or "").strip())
    except Exception:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _handoff_payload_with_tool_evidence(handoff_payload: dict[str, Any], *, tools: Any) -> dict[str, Any]:
    payload = dict(handoff_payload or {})
    if _first_truth_data_markdown(payload):
        return payload
    evidence_by_task = getattr(tools, "evidence_by_task", {}) or {}
    if isinstance(evidence_by_task, dict) and evidence_by_task:
        payload.setdefault("data_evidence", evidence_by_task)
    return payload


def _direct_snapshot_answer(*, question: str, handoff_payload: dict[str, Any]) -> str:
    if _env_value("SCHOOL_AGENT_DIRECT_DATA_SNAPSHOT_ENABLED", default="1").lower() not in {"1", "true", "yes", "on"}:
        return ""
    if not _is_direct_snapshot_request(question):
        return ""
    return _first_truth_data_markdown(handoff_payload).strip()


def _should_suppress_natural_answer_for_direct_snapshot(*, question: str, tools: Any) -> bool:
    del tools
    return _is_direct_snapshot_request(question)


def _is_direct_snapshot_request(question: str) -> bool:
    text = "".join(str(question or "").split())
    if not text:
        return False
    direct_tokens = [
        "只输出查询结果",
        "只输出结果",
        "只输出表格",
        "只要表格",
        "查询结果表格",
        "不要总结",
        "不用总结",
        "别总结",
        "不要分析",
        "不用分析",
        "直接输出数据",
        "直接给数据",
        "直接发数据",
        "纯数据",
        "原始结果",
        "真实结果数据",
    ]
    if any(token in text for token in direct_tokens):
        return True
    return "只输出" in text and any(token in text for token in ["数据", "结果", "表格", "明细"])


def _first_truth_data_markdown(value: Any) -> str:
    if isinstance(value, dict):
        markdown = value.get("truth_data_markdown")
        if isinstance(markdown, str) and markdown.strip():
            return markdown
        data_evidence = value.get("data_evidence")
        if isinstance(data_evidence, dict):
            for nested in reversed(list(data_evidence.values())):
                found = _first_truth_data_markdown(nested)
                if found:
                    return found
        for preferred_key in ("evidence_summary", "data_evidence", "evidence_board"):
            nested = value.get(preferred_key)
            found = _first_truth_data_markdown(nested)
            if found:
                return found
        for nested in value.values():
            found = _first_truth_data_markdown(nested)
            if found:
                return found
    if isinstance(value, list):
        for nested in value:
            found = _first_truth_data_markdown(nested)
            if found:
                return found
    return ""


async def _stream_fast_final_answer(
    *,
    trace: Any,
    final_model: Any,
    question: str,
    handoff_payload: dict[str, Any],
    source_views: list[str],
) -> AsyncIterator[str]:
    direct_answer = _direct_snapshot_answer(question=question, handoff_payload=handoff_payload)
    if direct_answer:
        yield direct_answer
        return

    model = agent_model_for_tool_loop(final_model)
    first_token_ms: int | None = None
    chunk_count = 0
    started = datetime.now().timestamp()
    prompt = _fast_final_answer_prompt(question=question, handoff_payload=handoff_payload, source_views=source_views)
    system_prompt = _fast_final_answer_system_prompt()
    handoff_text = json.dumps(handoff_payload, ensure_ascii=False, default=str)
    with trace_step(trace, "final_answer_context", {"question": question}) as context_step:
        set_step_output(
            context_step,
            {
                "input": {
                    "question": question,
                    "source_views": source_views,
                    "handoff_chars": len(handoff_text),
                },
                "decision": {
                    "model_name": _model_name(final_model),
                    "direct_snapshot": False,
                    "format_policy": "free",
                },
                "output": {
                    "handoff_json": trace_preview(handoff_text),
                    "system_prompt": trace_preview(system_prompt),
                    "final_prompt": trace_preview(prompt),
                },
                "error": None,
            },
        )
    with trace_step(
        trace,
        "agent_native.final_fast.llm",
        {
            "model_name": _model_name(final_model),
            "question": question,
            "handoff_chars": len(handoff_text),
            "source_views": source_views,
            "handoff_json": trace_preview(handoff_text),
            "final_prompt": trace_preview(prompt),
        },
    ) as step:
        usage = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
        async for chunk in model.astream(
            [
                SystemMessage(content=system_prompt),
                HumanMessage(content=prompt),
            ]
        ):
            chunk_usage = _extract_chunk_usage(chunk)
            if chunk_usage["total_tokens"] > 0:
                usage = chunk_usage
            text = _chunk_text(chunk)
            if not text:
                continue
            if first_token_ms is None:
                first_token_ms = max(0, int((datetime.now().timestamp() - started) * 1000))
            chunk_count += 1
            yield text
        set_step_output(
            step,
            {
                "model_name": _model_name(final_model),
                "first_token_ms": first_token_ms,
                "stream_chunk_count": chunk_count,
                "usage": usage,
                "final_prompt": trace_preview(prompt),
            },
        )
        if usage["total_tokens"] > 0:
            _add_trace_usage(usage)


def _final_answer_handoff_tool(
    *,
    trace: Any,
    final_model: Any,
    question: str,
    source_views_fn: Callable[[], list[str]],
    tool_contract: Any = None,
) -> StructuredTool:
    def _run(handoff_json: str) -> str:
        if tool_contract is not None:
            try:
                block_payload = tool_contract.handoff_block_payload()
            except Exception:
                block_payload = None
            if block_payload:
                return "FINAL_ANSWER_HANDOFF_BLOCKED: " + json.dumps(block_payload, ensure_ascii=False, default=str)
        handoff_payload = _loads_json_object(str(handoff_json or "")) or {"summary": str(handoff_json or "").strip()}
        handoff_payload.setdefault("question", question)
        handoff_payload.setdefault("source_views", source_views_fn())
        if tool_contract is not None:
            try:
                handoff_payload.setdefault("tool_contract", tool_contract.trace_payload())
            except Exception:
                pass
        return "FINAL_ANSWER_HANDOFF_JSON: " + json.dumps(handoff_payload, ensure_ascii=False, default=str)

    return StructuredTool.from_function(
        name="final_answer_handoff",
        description=(
            "当你已完成必要数据查询、联网/政策检索和业务证据核验，准备交接证据时调用。"
            "不要直接输出长答案；把紧凑 JSON 字符串放入 handoff_json。"
        ),
        func=_run,
    )


def _fallback_final_handoff_payload(*, question: str, tools: Any, caveat: str = "") -> dict[str, Any]:
    evidence_board: dict[str, Any] = {}
    try:
        evidence_board = tools.evidence_board_payload()
    except Exception:
        evidence_board = {}
    source_views = list(getattr(tools, "source_views", []) or [])
    evidence_by_task = getattr(tools, "evidence_by_task", {}) or {}
    return {
        "question": question,
        "data_evidence": evidence_by_task,
        "evidence_board": evidence_board,
        "external_evidence": [],
        "pure_business_data_markdown": _first_truth_data_markdown(
            {"data_evidence": evidence_by_task, "evidence_board": evidence_board}
        ),
        "source_views": source_views,
        "caveats": [caveat] if caveat else [],
    }


async def _is_guard_router_chat(*, trace: Any = None, model: Any, question: str) -> bool:
    if str(os.getenv("SCHOOL_AGENT_GUARD_ROUTER_ENABLED", "1")).strip().lower() in {"0", "false", "no", "off"}:
        return False
    prompt = (
        "你是学校智能助手入口路由。只输出 DATA 或 CHAT。\n"
        "需要查学校数据库、政策、公开网页、图表/图片/PPT或任何工具，输出 DATA。\n"
        "只是日常问答、打招呼、通用写作、翻译、编程或泛泛解释，输出 CHAT。"
    )
    try:
        timeout = max(0.3, min(float(os.getenv("SCHOOL_AGENT_GUARD_ROUTER_TIMEOUT_SEC", "2.5") or "2.5"), 8.0))
        with trace_step(trace, "agent_native.guard_router.llm", {"model_name": _model_name(model), "question": question}) as step:
            result = await asyncio.wait_for(
                agent_model_for_tool_loop(model).ainvoke([SystemMessage(content=prompt), HumanMessage(content=question)]),
                timeout=timeout,
            )
            usage = _extract_chunk_usage(result)
            set_step_output(step, {"decision": _chunk_text(result).strip(), "usage": usage, "model_name": _model_name(model)})
            if usage["total_tokens"] > 0:
                _add_trace_usage(usage)
        return _chunk_text(result).strip().upper().startswith("CHAT")
    except Exception:
        return False


async def _stream_guard_router_chat(*, trace: Any = None, model: Any, question: str, conversation_context: str = "") -> AsyncIterator[str]:
    user_prompt = question
    if str(conversation_context or "").strip():
        user_prompt = f"【最近对话上下文】\n{conversation_context[:4000]}\n\n【用户问题】\n{question}"
    messages = [
        SystemMessage(content="直接回答用户问题。不要提工具、数据库、SQL或内部思考。"),
        HumanMessage(content=user_prompt),
    ]
    first_token_ms: int | None = None
    chunk_count = 0
    usage = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
    started = datetime.now().timestamp()
    with trace_step(trace, "agent_native.guard_chat.llm", {"model_name": _model_name(model), "question": question}) as step:
        async for chunk in agent_model_for_tool_loop(model).astream(messages):
            chunk_usage = _extract_chunk_usage(chunk)
            if chunk_usage["total_tokens"] > 0:
                usage = chunk_usage
            text = _chunk_text(chunk)
            if text:
                if first_token_ms is None:
                    first_token_ms = max(0, int((datetime.now().timestamp() - started) * 1000))
                chunk_count += 1
                yield text
        set_step_output(
            step,
            {
                "model_name": _model_name(model),
                "first_token_ms": first_token_ms,
                "stream_chunk_count": chunk_count,
                "usage": usage,
            },
        )
        if usage["total_tokens"] > 0:
            _add_trace_usage(usage)


def _run_fast_final_answer_sync(
    *,
    trace: Any,
    final_model: Any,
    question: str,
    handoff_payload: dict[str, Any],
    source_views: list[str],
) -> str:
    direct_answer = _direct_snapshot_answer(question=question, handoff_payload=handoff_payload)
    if direct_answer:
        return direct_answer

    model = agent_model_for_tool_loop(final_model)
    first_token_ms: int | None = None
    chunk_count = 0
    started = datetime.now().timestamp()
    answer_parts: list[str] = []
    prompt = _fast_final_answer_prompt(question=question, handoff_payload=handoff_payload, source_views=source_views)
    system_prompt = _fast_final_answer_system_prompt()
    handoff_text = json.dumps(handoff_payload, ensure_ascii=False, default=str)
    with trace_step(trace, "final_answer_context", {"question": question}) as context_step:
        set_step_output(
            context_step,
            {
                "input": {
                    "question": question,
                    "source_views": source_views,
                    "handoff_chars": len(handoff_text),
                },
                "decision": {
                    "model_name": _model_name(final_model),
                    "direct_snapshot": False,
                    "format_policy": "free",
                },
                "output": {
                    "handoff_json": trace_preview(handoff_text),
                    "system_prompt": trace_preview(system_prompt),
                    "final_prompt": trace_preview(prompt),
                },
                "error": None,
            },
        )
    with trace_step(
        trace,
        "agent_native.final_fast.llm",
        {
            "model_name": _model_name(final_model),
            "question": question,
            "handoff_chars": len(handoff_text),
            "source_views": source_views,
            "handoff_json": trace_preview(handoff_text),
            "final_prompt": trace_preview(prompt),
        },
    ) as step:
        usage = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
        for chunk in model.stream(
            [
                SystemMessage(content=system_prompt),
                HumanMessage(content=prompt),
            ]
        ):
            chunk_usage = _extract_chunk_usage(chunk)
            if chunk_usage["total_tokens"] > 0:
                usage = chunk_usage
            text = _chunk_text(chunk)
            if not text:
                continue
            if first_token_ms is None:
                first_token_ms = max(0, int((datetime.now().timestamp() - started) * 1000))
            chunk_count += 1
            answer_parts.append(text)
        set_step_output(
            step,
            {
                "model_name": _model_name(final_model),
                "first_token_ms": first_token_ms,
                "stream_chunk_count": chunk_count,
                "usage": usage,
                "final_prompt": trace_preview(prompt),
            },
        )
        if usage["total_tokens"] > 0:
            _add_trace_usage(usage)
    return "".join(answer_parts)


def _fast_final_answer_system_prompt() -> str:
    return (
        "你根据客观证据包回答学校数据问题。"
        "所有数字、名单、判断、政策与来源必须来自证据，不得新增未提供的事实。"
        "禁止向用户泄露 SQL、数据表名、工具名、Handoff JSON、内部节点名或“我先查询/我需要查看”等过程性话术。"
        "在不改变事实的前提下，可以自由组织最终呈现方式。"
    )


def _fast_final_answer_prompt(*, question: str, handoff_payload: dict[str, Any], source_views: list[str]) -> str:
    style_guide = _fast_final_answer_style_guide(
        question=question,
        handoff_payload=handoff_payload,
        source_views=source_views,
    )
    return "\n".join(
        [
            "客观证据包：",
            style_guide,
            "基于证据回答用户问题。呈现方式自由；不要泄露内部结构，不要复述工具过程，不要说“根据校医院反馈”等未提供来源。",
        ]
    )


def _fast_final_answer_style_guide(*, question: str, handoff_payload: dict[str, Any], source_views: list[str]) -> str:
    q = str(question or "")
    views = " ".join(str(item or "") for item in source_views)
    payload_text = json.dumps(handoff_payload, ensure_ascii=False, default=str)
    combined = f"{q} {views} {payload_text}"
    evidence_matrix: dict[str, Any] = {
        "user_original_question": q,
        "source_views": list(source_views or []),
        "handoff_evidence": handoff_payload,
    }
    evidence_notes: list[str] = []
    if any(token in combined for token in ["德育", "行规", "扣分", "纪律"]):
        evidence_notes.append("德育/行规边界：注意区分检查方、值周方、扣分类别、班级/年级和时间窗口。")
    if any(token in combined for token in ["请假", "病假", "事假", "返校", "晨午检"]):
        evidence_notes.append(
            "请假/健康边界：涉及疾病原因要保守处理，只能使用证据中出现的请假类型、时间、对象和风险线索。"
        )
        evidence_notes.append(
            "未提供课表、代课安排、考勤签到或全员在岗证据时，不得判断课程已安排代课，不得判断教学秩序正常，不得判断无需调代课，不得判断其余教师均正常在岗；只能说“是否需要调代课需结合课表/代课安排进一步确认”。"
        )
    if any(token in combined for token in ["教师", "积分", "成果", "申报", "职称", "荣誉", "述职", "评优"]):
        evidence_notes.append("教师发展边界：积分、成果、级别、等第、主办单位和政策条件只能按证据判断；政策不足时写待核验。")
    if any(token in combined for token in ["政策", "官网", "通知", "链接", "引用来源"]):
        evidence_notes.append("政策或联网证据必须使用证据中的来源；不要编造政策名称、年份、文号或链接。")
    if any(token in combined for token in ["图表", "图片", "PPT", "汇报图"]):
        evidence_notes.append("视觉工具证据只作为已生成资产或读图证据；不要重复输出内部 artifact JSON。")
    if evidence_notes:
        evidence_matrix["evidence_boundary_notes"] = evidence_notes
    return (
        "========================================================\n"
        f"{json.dumps(evidence_matrix, ensure_ascii=False, default=str, indent=2)}\n"
        "========================================================"
    )


def _chunk_text(chunk: Any) -> str:
    content = getattr(chunk, "content", "")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        pieces: list[str] = []
        for item in content:
            if isinstance(item, dict):
                pieces.append(str(item.get("text") or item.get("content") or ""))
            else:
                pieces.append(str(item or ""))
        return "".join(pieces)
    return str(content or "")


def _extract_chunk_usage(chunk: Any) -> dict[str, int]:
    metadata: dict[str, Any] = {}
    response_metadata = getattr(chunk, "response_metadata", None)
    usage_metadata = getattr(chunk, "usage_metadata", None)
    if isinstance(response_metadata, dict):
        raw = response_metadata.get("token_usage") or response_metadata.get("usage") or response_metadata
        metadata = raw if isinstance(raw, dict) else {}
    if not metadata and isinstance(usage_metadata, dict):
        metadata = usage_metadata
    prompt_tokens = _safe_int(metadata.get("prompt_tokens") or metadata.get("input_tokens"))
    completion_tokens = _safe_int(metadata.get("completion_tokens") or metadata.get("output_tokens"))
    total_tokens = _safe_int(metadata.get("total_tokens"))
    if total_tokens <= 0:
        total_tokens = prompt_tokens + completion_tokens
    return {
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_tokens": total_tokens,
    }


def _safe_int(value: Any) -> int:
    try:
        return max(0, int(value or 0))
    except Exception:
        return 0


def _model_name(model: Any) -> str:
    for attr in ("model_name", "model", "name"):
        value = str(getattr(model, attr, "") or "").strip()
        if value:
            return value
    return type(model).__name__


def _is_final_model_langgraph_event(event: dict[str, Any], *, final_model: Any, final_handoff_enabled: bool) -> bool:
    if not final_handoff_enabled or final_model is None:
        return False
    if str(event.get("event") or "") not in {"on_chat_model_start", "on_chat_model_stream", "on_chat_model_end", "on_chat_model_error"}:
        return False
    metadata = event.get("metadata") if isinstance(event.get("metadata"), dict) else {}
    event_model = str(metadata.get("ls_model_name") or metadata.get("model_name") or metadata.get("model") or "").strip()
    return bool(event_model and event_model == _model_name(final_model))


def _enhanced_content(*, question: str, sql_experience: str, ddl_context: str, conversation_context: str = "") -> str:
    now = datetime.now()
    startup_ddl_context = _startup_catalog_context_for_prompt(ddl_context)
    parts = [
        f"【当前系统参照时间】: {now.strftime('%Y-%m-%d %H:%M:%S')} ({now.strftime('%A')})",
    ]
    if str(conversation_context or "").strip():
        parts.append(
            "【本轮 OpenWebUI 会话上下文】:\n"
            f"{_truncate(conversation_context, 6000)}\n"
            "请用它理解“那这个学期呢、主要集中在哪些班级、继续、这些数据”等追问指代；"
            "但事实仍必须通过工具重新核验。"
        )
    parts.extend(
        [
            f"【历史相似 SQL 案例】:\n{_truncate(sql_experience, 6000)}",
            (
                "【数据库表目录（无详细DDL）】:\n"
                f"{startup_ddl_context}\n\n"
                "说明：这里默认只放表名和一句话业务描述，避免每轮 Agent 调用携带全量表结构。"
                "确定表后请调用 ddl_search 或 inspect_table_schema 动态载入当前问题需要的精确字段；"
                "无关表结构不要继续保留在推理中。"
            ),
            f"【用户原始问题】: {question}",
        ]
    )
    return "\n\n".join(parts)


def _startup_catalog_context_for_prompt(ddl_context: str) -> str:
    mode = _env_value("SCHOOL_AGENT_STARTUP_DDL_MODE", "TENANT_AGENT_STARTUP_DDL_MODE", "summary").lower()
    if mode in {"full", "legacy"}:
        return _truncate(ddl_context, _startup_ddl_max_chars(default=9000))
    if mode in {"off", "none", "disabled"}:
        return "未内联 DDL；请先调用 ddl_search 检索相关表，再用 inspect_table_schema 确认字段。"
    summary = _summarize_ddl_candidates(ddl_context, max_tables=_startup_ddl_max_tables())
    if summary:
        return _truncate(summary, _startup_ddl_max_chars(default=1800))
    return _truncate(str(ddl_context or ""), _startup_ddl_max_chars(default=1200))


def _summarize_ddl_candidates(ddl_context: str, *, max_tables: int) -> str:
    lines: list[str] = []
    current: list[str] = []
    table_count = 0
    for raw_line in str(ddl_context or "").splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if line.startswith("Table:"):
            if current:
                lines.extend(current)
                current = []
            if table_count >= max_tables:
                break
            table_count += 1
            current.append(line)
            continue
        if current and (line.startswith("Desc:") or line.startswith("Similarity:")):
            current.append(line)
    if current:
        lines.extend(current)
    return "\n".join(lines)


def _startup_ddl_max_chars(*, default: int) -> int:
    try:
        return max(200, min(int(_env_value("SCHOOL_AGENT_STARTUP_DDL_MAX_CHARS", "TENANT_AGENT_STARTUP_DDL_MAX_CHARS", str(default)) or default), 12000))
    except Exception:
        return default


def _startup_ddl_max_tables() -> int:
    try:
        return max(1, min(int(_env_value("SCHOOL_AGENT_STARTUP_DDL_MAX_TABLES", "TENANT_AGENT_STARTUP_DDL_MAX_TABLES", "6") or "6"), 20))
    except Exception:
        return 6


def _schema_catalog_context(schema_index: Any, *, question: str = "") -> str:
    datasets = list(getattr(schema_index, "datasets", []) or [])
    if not datasets:
        return "未加载到数据库表目录；请先调用 list_available_tables 或 ddl_search。"
    max_tables = _startup_catalog_max_tables()
    scored = sorted(
        datasets,
        key=lambda item: _catalog_dataset_score(item, question=question),
        reverse=True,
    )
    # 启动上下文只注入分层摘要；完整表目录仍保留在 schema_index，后续可用 ddl_search 扩展。
    strong, possible, low = _tier_catalog_datasets(scored, question=question)
    strong_limit = max(1, min(len(strong), max_tables))
    possible_limit = max(0, min(len(possible), max_tables - strong_limit))
    lines: list[str] = []
    if strong:
        lines.append("强相关候选：")
        lines.extend(_format_catalog_dataset_line(dataset, fallback_schema=schema_index.source_schema) for dataset in strong[:strong_limit])
    if possible_limit:
        lines.append("可能相关但需二次确认：")
        lines.extend(
            _format_catalog_dataset_line(dataset, fallback_schema=schema_index.source_schema)
            for dataset in possible[:possible_limit]
        )
    if not lines:
        lines.append("强相关候选：")
        lines.extend(_format_catalog_dataset_line(dataset, fallback_schema=schema_index.source_schema) for dataset in scored[:max_tables])
    shown = strong[:strong_limit] + possible[:possible_limit]
    omitted = max(0, len(datasets) - len(shown))
    if omitted:
        low_hint = "低相关表" if low else "候选表"
        lines.append(f"已省略：{omitted} 张{low_hint}未注入启动上下文；需要时调用 list_available_tables 或 ddl_search 扩展检索。")
    return _truncate("\n".join(lines), _startup_catalog_max_chars())


def _tier_catalog_datasets(datasets: list[Any], *, question: str) -> tuple[list[Any], list[Any], list[Any]]:
    # 先分层再截断，避免低相关但命中“教师/学生”的表挤占关键业务表注意力。
    strong: list[Any] = []
    possible: list[Any] = []
    low: list[Any] = []
    for dataset in datasets:
        tier = _catalog_dataset_tier(dataset, question=question)
        if tier == "strong":
            strong.append(dataset)
        elif tier == "possible":
            possible.append(dataset)
        else:
            low.append(dataset)
    if not strong and possible:
        strong.append(possible.pop(0))
    return strong, possible, low


def _catalog_dataset_tier(dataset: Any, *, question: str) -> str:
    text = str(question or "")
    haystack = _catalog_dataset_text(dataset)
    if any(token in text for token in ["请假", "销假", "离校", "考勤"]):
        # 请假/考勤类问题优先保留真实业务表；人事、课表、代课类表只作为补证候选。
        leave_terms = ["请假", "销假", "离校", "考勤"]
        person_terms = ["教师", "老师", "教职工", "人事"]
        support_terms = ["人事", "人员", "档案", "执勤", "排班", "课后服务", "课表", "代课"]
        if any(term in haystack for term in leave_terms) and any(term in haystack for term in person_terms):
            return "strong"
        if any(term in haystack for term in leave_terms):
            return "possible"
        if any(term in haystack for term in support_terms) and any(term in haystack for term in person_terms):
            return "possible"
        return "low"
    score = _catalog_dataset_score(dataset, question=question)
    if score >= 2:
        return "strong"
    if score > 0:
        return "possible"
    return "low"


def _format_catalog_dataset_line(dataset: Any, *, fallback_schema: str = "") -> str:
    source_schema = str(getattr(dataset, "source_schema", "") or fallback_schema or "").strip()
    source_view = str(getattr(dataset, "source_view", "") or getattr(dataset, "dataset_id", "") or "").strip()
    label = str(getattr(dataset, "label", "") or source_view).strip()
    desc = str(getattr(dataset, "description", "") or getattr(dataset, "searchable_text", "") or "").strip()
    table_ref = f"{source_schema}.{source_view}" if source_schema and source_view else source_view
    if desc:
        desc = _truncate(re.sub(r"\s+", " ", desc), 120)
        return f"- {table_ref}: {label}；{desc}"
    return f"- {table_ref}: {label}"


def _catalog_dataset_score(dataset: Any, *, question: str) -> float:
    text = str(question or "")
    haystack = _catalog_dataset_text(dataset)
    score = 0.0
    for term in _catalog_terms(text):
        if term and term in haystack:
            score += 2.0 if term in str(getattr(dataset, "source_view", "") or "") else 1.0
    return score


def _catalog_dataset_text(dataset: Any) -> str:
    return " ".join(
        [
            str(getattr(dataset, "source_view", "") or ""),
            str(getattr(dataset, "label", "") or ""),
            str(getattr(dataset, "description", "") or ""),
            str(getattr(dataset, "searchable_text", "") or ""),
        ]
    )


def _catalog_terms(text: str) -> list[str]:
    raw = [item.strip() for item in re.split(r"[\s,，。；;、？?]+", str(text or "")) if len(item.strip()) >= 2]
    domain_terms: list[str] = []
    rules = {
        "德育": ["德育", "行规", "扣分", "纪律"],
        "行规": ["德育", "行规", "扣分"],
        "扣分": ["德育", "行规", "扣分"],
        "请假": ["请假", "考勤"],
        "文印": ["文印", "打印", "复印"],
        "报修": ["报修", "维修", "资产"],
        "教师": ["教师", "老师", "教职工"],
        "学生": ["学生", "同学"],
    }
    for marker, terms in rules.items():
        if marker in text:
            domain_terms.extend(terms)
    out: list[str] = []
    for term in [*domain_terms, *raw]:
        if term and term not in out:
            out.append(term)
    return out[:16]


def _startup_catalog_max_tables() -> int:
    try:
        return max(5, min(int(_env_value("SCHOOL_AGENT_STARTUP_CATALOG_MAX_TABLES", "TENANT_AGENT_STARTUP_CATALOG_MAX_TABLES", "80") or "80"), 300))
    except Exception:
        return 80


def _startup_catalog_max_chars() -> int:
    try:
        return max(800, min(int(_env_value("SCHOOL_AGENT_STARTUP_CATALOG_MAX_CHARS", "TENANT_AGENT_STARTUP_CATALOG_MAX_CHARS", "5000") or "5000"), 20000))
    except Exception:
        return 5000


def _event_visible_text(event: dict[str, Any]) -> str:
    kind = str(event.get("event") or "")
    tool_name = str(event.get("name") or "")
    data = event.get("data") if isinstance(event.get("data"), dict) else {}
    if kind == "on_tool_start":
        raw_input = data.get("input")
        sql = _extract_sql(raw_input)
        if tool_name == "sql_db_query":
            if sql and _stream_include_sql():
                return "\n<details>\n<summary>⚙️ 执行数据库查询...</summary>\n\n```sql\n" + sql + "\n```\n</details>\n\n"
            return "\n\n> ⚙️ **正在执行数据库查询**\n\n"
        return f"\n\n- ⚙️ **正在调用工具**: `{tool_name}` ...\n"
    if kind == "on_tool_end" and tool_name == "sql_db_query":
        return "\n\n> ✅ **查询完成**\n\n"
    return ""


def _event_asset_markdown(event: dict[str, Any]) -> str:
    if str(event.get("event") or "") != "on_tool_end":
        return ""
    tool_name = str(event.get("name") or "").strip()
    if tool_name not in {"chart", "plot", "image", "generate_image_tool", "slide"}:
        return ""
    parsed = _tool_output_json(event)
    assets = parsed.get("artifacts") if isinstance(parsed, dict) else None
    if tool_name == "generate_image_tool":
        if isinstance(assets, list):
            return _generate_image_tool_markdown(assets)
        return _image_markdown_from_raw_output(event)
    if not isinstance(assets, list):
        if tool_name == "image":
            return _image_markdown_from_raw_output(event)
        return ""
    if tool_name == "plot":
        return _plot_asset_markdown(assets)
    if tool_name == "chart":
        return _chart_asset_markdown(assets)
    if tool_name == "slide":
        return _slide_asset_markdown(assets)
    return _image_asset_markdown(assets)


def _chart_asset_markdown(assets: list[Any]) -> str:
    chart_asset = _first_asset(assets, "chart")
    if not chart_asset:
        return ""
    preview_url = _asset_download_url(chart_asset)
    if not preview_url:
        preview_url = str(chart_asset.get("preview_url") or "").strip()
    if not preview_url:
        return ""
    title = _escape_markdown_text(str(chart_asset.get("title") or "数据图表").strip())
    raw_image_url = str(chart_asset.get("image_url") or chart_asset.get("svg_url") or "").strip()
    image_url = _absolute_url(raw_image_url) if raw_image_url else ""
    lines = [
        f"### 📈 已生成图表：{title}",
    ]
    if image_url:
        lines.append(f"![{title}]({image_url})")
    lines.extend(
        [
            f"- 👀 [在线查看图表]({preview_url})",
            f"- 图表地址：<{preview_url}>",
        ]
    )
    data_url = str(chart_asset.get("data_url") or "").strip()
    if data_url:
        lines.append(f"- 🧩 [下载图表数据 JSON]({_absolute_url(data_url)})")
    return "\n\n" + "\n".join(lines) + "\n\n"


def _plot_asset_markdown(assets: list[Any]) -> str:
    plot_asset = _first_asset(assets, "plot")
    if not plot_asset:
        return ""
    image_url = str(plot_asset.get("image_url") or plot_asset.get("download_url") or "").strip()
    if not image_url:
        return ""
    image_url = _absolute_url(image_url)
    title = _escape_markdown_text(str(plot_asset.get("title") or "数据图").strip())
    lines = [
        f"### 📈 已生成数据图：{title}",
        f"![{title}]({image_url})",
        f"- 📎 [下载 PNG 图片]({image_url})",
    ]
    data_url = str(plot_asset.get("data_url") or "").strip()
    if data_url:
        lines.append(f"- 🧩 [下载图表数据 JSON]({_absolute_url(data_url)})")
    return "\n\n" + "\n".join(lines) + "\n\n"


def _image_asset_markdown(assets: list[Any]) -> str:
    blocks: list[str] = []
    for index, asset in enumerate(assets, 1):
        if not isinstance(asset, dict) or asset.get("type") != "image":
            continue
        download_url = _asset_download_url(asset) or _asset_url(asset)
        if not download_url:
            continue
        image_url = _asset_original_url(asset) or _asset_url(asset) or download_url
        alt = f"生成图片 {index}" if index > 1 else "生成图片"
        blocks.append(
            "\n".join(
                [
                    f"### 🖼️ {alt}",
                    f"![{alt}]({image_url})",
                    f"- 🔍 [打开图片预览]({image_url})",
                    f"- 图片地址：<{image_url}>",
                    f"- 📎 [下载图片]({download_url})",
                ]
            )
        )
    if not blocks:
        return ""
    return "\n\n" + "\n\n".join(blocks) + "\n\n"


def _generate_image_tool_markdown(assets: list[Any]) -> str:
    for asset in assets:
        if not isinstance(asset, dict) or asset.get("type") != "image":
            continue
        image_url = _asset_download_url(asset) or _asset_url(asset) or _asset_original_url(asset)
        if not image_url:
            continue
        lines = [f"![生成的图片]({image_url})"]
        download_url = _asset_download_url(asset)
        if download_url:
            lines.append(f"- 📎 [下载图片]({download_url})")
        return "\n\n" + "\n".join(lines) + "\n\n"
    return ""


def _image_markdown_from_raw_output(event: dict[str, Any]) -> str:
    raw_text = _tool_output_text(event)
    match = re.search(r"https?://\S+", raw_text)
    if not match:
        return ""
    image_url = match.group(0).strip().rstrip(").,，。；;\"'")
    return f"\n\n![生成的图片]({image_url})\n\n"


def _slide_asset_markdown(assets: list[Any]) -> str:
    pptx_asset = _first_asset(assets, "pptx")
    preview_asset = _first_asset(assets, "slide_preview")
    source_asset = _first_asset(assets, "deck_source")
    pptx_url = _asset_download_url(pptx_asset) if pptx_asset else ""
    preview_url = _asset_download_url(preview_asset) if preview_asset else ""
    source_url = _asset_download_url(source_asset) if source_asset else ""
    if not pptx_url and not preview_url:
        return ""
    title = _escape_markdown_text(str((pptx_asset or preview_asset or {}).get("title") or "汇报 PPT").strip())
    filename = str((pptx_asset or {}).get("filename") or f"{title}.pptx").strip()
    lines = [f"### 📊 已生成汇报 PPT：{title}"]
    if preview_url:
        lines.append(f"- 👀 [在线预览 PPT]({preview_url})")
        lines.append(f"- 预览地址：<{preview_url}>")
    if pptx_url:
        lines.append(f"- 📎 [下载可编辑版本：{filename}]({pptx_url})")
    if source_url:
        lines.append(f"- 🧩 [下载 AI 编辑源文件]({source_url})")
    return "\n\n" + "\n".join(lines) + "\n\n"


def _event_citation_sources(event: dict[str, Any]) -> list[dict[str, Any]]:
    if str(event.get("event") or "") != "on_tool_end":
        return []
    tool_name = str(event.get("name") or "").strip()
    if tool_name not in {"official_policy_search", "web_search"}:
        return []
    return _extract_tool_sources(_tool_output_json(event))


def _openwebui_sources_from_tool_sources(sources: list[dict[str, Any]]) -> list[dict[str, Any]]:
    formatted: list[dict[str, Any]] = []
    for index, source in enumerate(sources[:12], 1):
        formatted.append(
            {
                "ref": index,
                "title": str(source.get("title") or source.get("source") or source.get("url") or f"引用来源 {index}").strip(),
                "url": str(source.get("url") or source.get("official_url") or "").strip(),
                "source_type": str(source.get("source_type") or source.get("type") or "source").strip(),
                "supports": str(source.get("supports") or source.get("content") or source.get("snippet") or source.get("title") or "")[:500],
            }
        )
    return openwebui_sources_from_citation_sources(formatted)


def _openwebui_sources_from_source_views(source_views: list[str]) -> list[dict[str, Any]]:
    formatted: list[dict[str, Any]] = []
    seen: set[str] = set()
    for view in source_views:
        title = str(view or "").strip()
        if not title or title in seen:
            continue
        seen.add(title)
        formatted.append(
            {
                "ref": len(formatted) + 1,
                "title": f"数据来源：{title}",
                "url": "",
                "source_type": "school_data",
                "supports": f"本回答使用校内只读数据表/视图：{title}",
            }
        )
        if len(formatted) >= 12:
            break
    return openwebui_sources_from_citation_sources(formatted)


def _sql_lineages_from_evidence_by_task(evidence_by_task: dict[str, Any]) -> list[dict[str, Any]]:
    lineages: list[dict[str, Any]] = []
    for task in (evidence_by_task or {}).values():
        if not isinstance(task, dict):
            continue
        lineage = task.get("sql_lineage")
        if isinstance(lineage, dict):
            lineages.append(lineage)
    return lineages


def _openwebui_sources_from_sql_lineages(lineages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    seen_hashes: set[str] = set()
    for lineage in lineages:
        if not isinstance(lineage, dict):
            continue
        sql_hash = str(lineage.get("sql_hash") or "").strip()
        if len(sql_hash) != 64:
            continue
        if sql_hash in seen_hashes:
            continue
        seen_hashes.add(sql_hash)
        purpose = str(lineage.get("query_purpose") or f"SQL 查询证据 {len(out) + 1}").strip()
        tables = [str(item or "").strip() for item in lineage.get("tables_used") or [] if str(item or "").strip()]
        row_count = int(lineage.get("row_count") or 0)
        fingerprint = str(lineage.get("sample_row_fingerprint") or "").strip()
        metadata = {
            "source": purpose,
            "type": "sql_lineage",
            "evidence_ref_id": str(lineage.get("evidence_ref_id") or ""),
            "sql_hash": sql_hash,
            "tables_used": tables,
            "row_count": row_count,
            "sample_row_fingerprint": fingerprint,
            "time_range": lineage.get("time_range") if isinstance(lineage.get("time_range"), dict) else {},
            "meta_context": lineage.get("meta_context") if isinstance(lineage.get("meta_context"), dict) else {},
        }
        document = (
            f"执行证据：{purpose}；涉及表/视图：{', '.join(tables) if tables else '未记录'}；"
            f"返回行数：{row_count}；SQL Hash：{sql_hash[:12]}；样本指纹：{fingerprint[:12]}"
        )
        out.append(
            {
                "document": [document],
                "metadata": [metadata],
                "source": {"name": f"查询证据：{purpose}"},
            }
        )
        if len(out) >= 12:
            break
    return out


def _merge_citation_sources(target: list[dict[str, Any]], incoming: list[dict[str, Any]]) -> None:
    seen = {
        (
            str(item.get("title") or "").strip(),
            str(item.get("url") or item.get("official_url") or "").strip(),
        )
        for item in target
    }
    for item in incoming:
        key = (
            str(item.get("title") or "").strip(),
            str(item.get("url") or item.get("official_url") or "").strip(),
        )
        if key in seen or not any(key):
            continue
        seen.add(key)
        target.append(item)


def _extract_tool_sources(parsed: dict[str, Any]) -> list[dict[str, Any]]:
    sources = parsed.get("sources") if isinstance(parsed, dict) else None
    if isinstance(sources, list) and sources:
        return [
            {
                "title": item.get("title") or item.get("name") or item.get("source") or "",
                "url": item.get("official_url") or item.get("url") or item.get("link") or "",
                "source": item.get("source_name") or item.get("source") or "",
                "source_type": "official_policy" if "policy" in str(parsed).lower() else "web",
                "supports": item.get("chunk") or item.get("content") or item.get("snippet") or item.get("title") or "",
                "published_at": item.get("published_at") or item.get("publishedDate") or "",
            }
            for item in sources
            if isinstance(item, dict)
        ]

    found: list[dict[str, Any]] = []
    artifacts = parsed.get("artifacts") if isinstance(parsed, dict) else None
    if not isinstance(artifacts, list):
        return found
    for artifact in artifacts:
        if not isinstance(artifact, dict):
            continue
        content = artifact.get("content")
        if not isinstance(content, dict):
            continue
        for item in content.get("policy_evidence") or content.get("results") or []:
            if not isinstance(item, dict):
                continue
            found.append(
                {
                    "title": item.get("title") or item.get("name") or item.get("source_name") or "",
                    "url": item.get("official_url") or item.get("url") or item.get("link") or "",
                    "source": item.get("source_name") or item.get("source") or "",
                    "source_type": "official_policy" if item.get("official_url") else "web",
                    "supports": item.get("chunk") or item.get("content") or item.get("snippet") or item.get("description") or "",
                    "published_at": item.get("published_at") or item.get("publishedDate") or "",
                }
            )
    return found


def _first_asset(assets: list[Any], asset_type: str) -> dict[str, Any] | None:
    for asset in assets:
        if isinstance(asset, dict) and asset.get("type") == asset_type:
            return asset
    return None


def _escape_markdown_text(text: str) -> str:
    return str(text or "").replace("[", "\\[").replace("]", "\\]").strip()


def _tool_output_json(event: dict[str, Any]) -> dict[str, Any]:
    data = event.get("data") if isinstance(event.get("data"), dict) else {}
    raw_output = data.get("output")
    content = getattr(raw_output, "content", raw_output)
    if isinstance(content, dict):
        return content
    try:
        parsed = json.loads(str(content or ""))
    except Exception:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _tool_output_text(event: dict[str, Any]) -> str:
    data = event.get("data") if isinstance(event.get("data"), dict) else {}
    raw_output = data.get("output")
    content = getattr(raw_output, "content", raw_output)
    if isinstance(content, str):
        return content
    if isinstance(content, dict):
        explicit_text = content.get("text") or content.get("output") or content.get("content")
        if explicit_text:
            return str(explicit_text)
    try:
        return json.dumps(content, ensure_ascii=False, default=str)
    except Exception:
        return str(content or "")


def _asset_url(asset: dict[str, Any]) -> str:
    download_url = _asset_download_url(asset)
    if download_url:
        return download_url
    return str(asset.get("url") or asset.get("image_url") or asset.get("output_url") or "").strip()


def _asset_original_url(asset: dict[str, Any]) -> str:
    url = str(asset.get("url") or asset.get("image_url") or asset.get("output_url") or "").strip()
    if url:
        return url
    content = asset.get("content") if isinstance(asset.get("content"), dict) else {}
    return str(content.get("url") or content.get("image_url") or content.get("output_url") or "").strip()


def _asset_download_url(asset: dict[str, Any]) -> str:
    download_url = str(asset.get("download_url") or "").strip()
    if not download_url:
        content = asset.get("content") if isinstance(asset.get("content"), dict) else {}
        download_url = str(content.get("download_url") or "").strip()
    if not download_url:
        return ""
    return _absolute_url(download_url)


def _absolute_url(url: str) -> str:
    if url.startswith(("http://", "https://")):
        return url
    base_url = (
        os.getenv("GATEWAY_PUBLIC_BASE_URL", "").strip()
        or os.getenv("AGENT_JOB_GATEWAY_BASE_URL", "").strip()
        or os.getenv("GATEWAY_BASE_URL", "").strip()
        or "http://127.0.0.1:8008"
    )
    return base_url.rstrip("/") + "/" + url.lstrip("/")


def _chunk_content(event: dict[str, Any]) -> str:
    return _chunk_text_field(event, "content")


def _chunk_reasoning_content(event: dict[str, Any]) -> str:
    return _chunk_text_field(event, "reasoning_content")


def _chunk_text_field(event: dict[str, Any], field: str) -> str:
    data = event.get("data") if isinstance(event.get("data"), dict) else {}
    chunk = data.get("chunk")
    if isinstance(chunk, dict):
        value = chunk.get(field)
        if not value and isinstance(chunk.get("additional_kwargs"), dict):
            value = chunk["additional_kwargs"].get(field)
        return value if isinstance(value, str) else ""
    value = getattr(chunk, field, "")
    if not value:
        additional_kwargs = getattr(chunk, "additional_kwargs", {})
        if isinstance(additional_kwargs, dict):
            value = additional_kwargs.get(field)
    return value if isinstance(value, str) else ""


def _messages_from_event(event: dict[str, Any]) -> list[Any]:
    data = event.get("data") if isinstance(event.get("data"), dict) else {}
    output = data.get("output")
    if isinstance(output, dict) and isinstance(output.get("messages"), list):
        return output["messages"]
    return []


def _extract_sql(value: Any) -> str:
    if isinstance(value, str) and value.strip().lower().startswith(("select", "with")):
        return value.strip()
    if isinstance(value, dict):
        for key in ("query", "sql", "raw_sql"):
            text = str(value.get(key) or "").strip()
            if text.lower().startswith(("select", "with")):
                return text
        args = value.get("args")
        if isinstance(args, dict):
            return _extract_sql(args)
    return ""


def _stream_include_sql() -> bool:
    return str(
        os.getenv("SCHOOL_AGENT_STREAM_INCLUDE_SQL")
        or os.getenv("GATEWAY_AGENT_STREAM_INCLUDE_SQL")
        or "0"
    ).strip().lower() in {"1", "true", "yes", "on"}


def _sanitize_process_text(text: str) -> str:
    clean = str(text or "")
    if not clean or _stream_include_sql():
        return clean
    replacement = "\n> ⚙️ **正在执行数据库查询**\n\n"
    clean = re.sub(
        r"(?is)<details>\s*<summary>\s*⚙️\s*执行数据库查询.*?</details>",
        replacement,
        clean,
    )
    clean = re.sub(
        r"(?is)&lt;details&gt;\s*&lt;summary&gt;\s*⚙️\s*执行数据库查询.*?&lt;/details&gt;",
        replacement,
        clean,
    )
    clean = re.sub(r"(?is)```sql\s*.*?```", "```sql\n[SQL 已隐藏]\n```", clean)
    return clean


def _stream_tool_events_enabled() -> bool:
    return str(
        os.getenv("SCHOOL_AGENT_STREAM_TOOL_EVENTS_ENABLED")
        or os.getenv("GATEWAY_AGENT_STREAM_TOOL_EVENTS_ENABLED")
        or "0"
    ).strip().lower() in {"1", "true", "yes", "on"}


def _truncate(text: str, limit: int) -> str:
    clean = str(text or "").strip()
    if len(clean) <= limit:
        return clean
    return clean[:limit].rstrip() + "...[truncated]"


def _recursion_limit() -> int:
    try:
        return max(16, min(int(_env_value("SCHOOL_AGENT_NATIVE_RECURSION_LIMIT", "TENANT_AGENT_NATIVE_RECURSION_LIMIT", "85") or "85"), 120))
    except Exception:
        return 85



def _hash_token(token: str | None) -> str:
    clean = str(token or "").strip()
    if not clean:
        return ""
    import hashlib

    return hashlib.sha256(clean.encode("utf-8")).hexdigest()[:16]
