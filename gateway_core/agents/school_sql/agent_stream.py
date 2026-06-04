from __future__ import annotations

import json
import os
import re
from datetime import datetime
from typing import Any, AsyncIterator, Callable

from langchain_core.messages import HumanMessage, SystemMessage
from langgraph.checkpoint.memory import MemorySaver
from langgraph.prebuilt import create_react_agent

from gateway_core.api.openai_compat.chat_pipeline_parts.request_parts import build_upstream_error_text
from gateway_core.api.openai_compat.citation_formatter import openwebui_sources_from_citation_sources
from gateway_core.agents.streaming.langgraph_event_stream import (
    flush_active_langgraph_llm_runs,
    record_langgraph_event_as_trace_step,
)
from gateway_core.agents.contracts import build_tool_contract
from gateway_core.agents.contracts.workflow_contracts import (
    SCHOOL_DATA_ANSWER_WORKFLOW,
)
from gateway_core.agents.contracts.workflow_trace import (
    record_workflow_start,
    workflow_input_payload,
    workflow_output_payload,
    workflow_trace_context,
)
from gateway_core.agents.school_sql.agent_stream_contract import (
    contract_available_tools as _contract_available_tools,
    plan_tool_contract as _plan_tool_contract,
    prune_tool_contract_for_disabled_tools as _prune_tool_contract_for_disabled_tools,
)
from gateway_core.agents.school_sql.final_handoff import (
    _append_business_disclaimer,
    _business_answer_markdown,
    _compact_final_handoff_payload,
    _evidence_board_digest,
    _evidence_digest,
    _extract_final_handoff_block,
    _extract_final_handoff_payload,
    _fallback_final_handoff_payload,
    _fast_final_answer_prompt,
    _fast_final_answer_style_guide,
    _fast_final_answer_system_prompt,
    _final_answer_handoff_tool,
    _final_handoff_enabled,
    _handoff_payload_with_tool_evidence,
    _run_fast_final_answer_sync,
    _scripted_handoff_answer,
    _stream_fast_final_answer,
)
from gateway_core.tools.langchain_tools import build_langchain_agent_tools
from gateway_core.tools.business_prompt_tool import BusinessPromptContextTool
from gateway_core.tools.tool_core import AgentToolInput, ToolExecutionContext
from gateway_core.conversation.threading import build_langgraph_thread_id
from gateway_core.agents.school_sql.agent_model import agent_model_for_tool_loop
from gateway_core.schema_context.ddl_embedding import ddl_embed_text
from gateway_core.agents.school_sql.sql_tools import DDLReactTools
from gateway_core.agents.school_sql.sql_utils import ddl_top_k, ddl_vector_table
from gateway_core.infra.utils import chunk_text as _chunk_text
from gateway_core.infra.utils import env_value as _env_value
from gateway_core.infra.utils import extract_chunk_usage as _extract_chunk_usage
from gateway_core.infra.utils import loads_json_object as _loads_json_object
from gateway_core.infra.utils import model_name as _model_name
from gateway_core.infra.utils import truncate as _truncate_text
from gateway_core.prompts.agents.school_sql_agent import build_school_sql_agent_prompt
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
    record_workflow_start(trace, SCHOOL_DATA_ANSWER_WORKFLOW, question=question)
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
            workflow_input_payload(
                SCHOOL_DATA_ANSWER_WORKFLOW,
                "route.resolve",
                {
                    "token_present": bool(str(token or "").strip()),
                    "school_scope": school_scope or "",
                    "api_key_db_required": True,
                    "agent_native": True,
                },
            ),
        ) as step:
            record = _require_school_api_key_record()
            school_id = record.school_id or record.schema_name
            if trace is not None:
                trace.school_id = school_id
            set_step_output(
                step,
                workflow_output_payload(
                    SCHOOL_DATA_ANSWER_WORKFLOW,
                    "route.resolve",
                    {
                        "school_id": school_id,
                        "schema_name": record.schema_name,
                        "display_name": record.display_name,
                        "key_type": record.key_type,
                    },
                ),
            )

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
        with trace_step(
            trace,
            "domain_context",
            workflow_input_payload(
                SCHOOL_DATA_ANSWER_WORKFLOW,
                "context.build_school",
                {"question": question, "school_id": school_id},
            ),
        ) as step:
            set_step_output(
                step,
                workflow_output_payload(
                    SCHOOL_DATA_ANSWER_WORKFLOW,
                    "context.build_school",
                    {
                        "input": {
                            "question": question,
                            "school_id": school_id,
                            "schema_name": schema_index.source_schema,
                        },
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
                ),
        )
        final_handoff_enabled = _final_handoff_enabled(final_model)
        business_prompt_context = _contract_business_prompt_context(question=question, school_id=school_id)
        ddl_vector_context = _ddl_vector_context(schema_index)
        contract_plan = _plan_tool_contract(
            trace=trace,
            model=agent_model_for_tool_loop(model),
            question=question,
            conversation_context=conversation_context,
            metadata_catalog_context=ddl_context,
            ddl_vector_context=ddl_vector_context,
            business_prompt_context=business_prompt_context,
            sql_experience_context=sql_experience,
        )
        is_chat_route = str(getattr(contract_plan, "route", "data") or "data").strip().lower() == "chat"
        with trace_step(trace, "route_result", {"question": question}) as step:
            set_step_output(
                step,
                {
                    "decision": "chat" if is_chat_route else "data",
                    "route": "contract_plan" if is_chat_route else "school_sql_agent",
                    "input": {
                        "question": question,
                        "conversation_context_chars": len(str(conversation_context or "")),
                        "contract_plan_route": getattr(contract_plan, "route", "data"),
                    },
                    "output": {"uses_database_agent": not is_chat_route},
                    "error": None,
                },
            )
        if is_chat_route:
            final_answer_source = "contract_plan_chat"
            async for chunk in _stream_contract_plan_chat(
                trace=trace,
                model=model,
                question=question,
                conversation_context=conversation_context,
            ):
                yield _content_event(chunk)
            _record_final_answer()
            finish_trace(trace)
            return
        disabled_tools = {str(name or "").strip() for name in disabled_tool_names if str(name or "").strip()}
        tool_contract = build_tool_contract(question, plan=contract_plan)
        _prune_tool_contract_for_disabled_tools(tool_contract, disabled_tools)
        tool_list = [
            *tools.as_langchain_tools(),
            *build_langchain_agent_tools(
                school_id=school_id,
                trace=trace,
                question=question,
                tool_contract=tool_contract,
            ),
        ]
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
        react_execute_trace_context = workflow_trace_context(SCHOOL_DATA_ANSWER_WORKFLOW, "school_sql.react_execute")
        async for event in agent.astream_events({"messages": [HumanMessage(content=enhanced_content)]}, config=config, version="v2"):
            if not _is_final_model_langgraph_event(event, final_model=final_model, final_handoff_enabled=final_handoff_enabled):
                record_langgraph_event_as_trace_step(
                    trace,
                    event,
                    prefix="agent_native.langgraph",
                    trace_context=react_execute_trace_context,
                )
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
                    business_prompt_context=business_prompt_context,
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
                    if final_handoff_enabled:
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
                            business_prompt_context=business_prompt_context,
                        ):
                            yield _content_event(chunk)
                    elif buffered_text:
                        fallback_final_text = buffered_text
                elif buffered_text:
                    if _chat_model_end_has_tool_calls(event) or run_id in llm_tool_call_runs:
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
        flush_active_langgraph_llm_runs(
            trace,
            prefix="agent_native.langgraph",
            trace_context=react_execute_trace_context,
        )
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
                business_prompt_context=business_prompt_context,
            ):
                yield _content_event(chunk)
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


def _ddl_vector_context(schema_index: Any) -> str:
    schema_name = str(getattr(schema_index, "source_schema", "") or "").strip()
    return (
        f"schema={schema_name or 'unknown'}; "
        f"vector_table={ddl_vector_table()}; "
        f"top_k={ddl_top_k()}; "
        "retriever=ddl_search/retrieve_lean_ddl_context"
    )


def _contract_business_prompt_context(*, question: str, school_id: str = "") -> str:
    try:
        output = BusinessPromptContextTool().run(
            AgentToolInput(arguments={"question": question}),
            ToolExecutionContext(tenant_id=school_id, metadata={"layer": "contract_planner"}),
        )
        payload = output.to_dict()
    except Exception as exc:
        payload = {"ok": False, "error": f"{type(exc).__name__}: {str(exc)[:200]}"}
    return json.dumps(payload, ensure_ascii=False, default=str)


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
            load_fields=_agent_schema_index_load_fields(),
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
        "field_load_mode": _schema_index_field_load_mode(schema_index),
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
                "field_load_mode": str(getattr(dataset, "raw", {}).get("field_load_mode") or ""),
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
                "field_load_mode": str(getattr(dataset, "raw", {}).get("field_load_mode") or ""),
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


def _agent_schema_index_load_fields() -> bool:
    raw = _env_value("SCHOOL_AGENT_SCHEMA_INDEX_LOAD_FIELDS", "TENANT_AGENT_SCHEMA_INDEX_LOAD_FIELDS", "0")
    return str(raw or "0").strip().lower() in {"1", "true", "yes", "on"}


def _schema_index_field_load_mode(schema_index: Any) -> str:
    datasets = list(getattr(schema_index, "datasets", []) or [])
    modes = {
        str(getattr(dataset, "raw", {}).get("field_load_mode") or "").strip()
        for dataset in datasets
        if isinstance(getattr(dataset, "raw", None), dict)
    }
    modes.discard("")
    if len(modes) == 1:
        return next(iter(modes))
    if modes:
        return ",".join(sorted(modes))
    return "unknown"


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
    return build_school_sql_agent_prompt(
        available_tool_names=available_tools,
        tool_contract_prompt=tool_contract_prompt,
        school_id=str(schema_index.school_id),
        school_name=str(schema_index.school_name),
        source_schema=str(schema_index.source_schema),
        final_handoff_enabled=final_handoff_enabled,
    )


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


def _strip_json_fence(text: str) -> str:
    clean = str(text or "").strip()
    if clean.startswith("```"):
        clean = re.sub(r"^```(?:json)?\s*", "", clean, flags=re.IGNORECASE)
        clean = re.sub(r"\s*```$", "", clean)
    return clean.strip()


async def _stream_contract_plan_chat(*, trace: Any = None, model: Any, question: str, conversation_context: str = "") -> AsyncIterator[str]:
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


def _is_final_model_langgraph_event(event: dict[str, Any], *, final_model: Any, final_handoff_enabled: bool) -> bool:
    # The final answer LLM is streamed outside LangGraph and already has its own
    # agent_native.final_fast.llm span. LangGraph chat model events here belong
    # to the ReAct tool-planning loop, even when it uses the same model name as
    # final_model, so filtering by model name hides the main agent latency.
    return False


def _enhanced_content(
    *,
    question: str,
    sql_experience: str,
    ddl_context: str,
    conversation_context: str = "",
) -> str:
    now = datetime.now()
    startup_ddl_context = _startup_catalog_context_for_prompt(ddl_context)
    parts = [
        f"【当前系统参照时间】: {now.strftime('%Y-%m-%d %H:%M:%S')} ({now.strftime('%A')})",
    ]
    if str(conversation_context or "").strip():
        parts.append(
            "【本轮 OpenWebUI 会话上下文】:\n"
            f"{_truncate_text(conversation_context, 6000, strip=True, rstrip=True)}\n"
            "请用它理解“那这个学期呢、主要集中在哪些班级、继续、这些数据”等追问指代；"
            "但事实仍必须通过工具重新核验。"
        )
    parts.extend(
        [
            f"【历史相似 SQL 案例】:\n{_truncate_text(sql_experience, 6000, strip=True, rstrip=True)}",
            (
                "【数据库表目录（无详细DDL）】:\n"
                f"{startup_ddl_context}\n\n"
                "说明：这里默认只放表名和一句话业务描述，避免每轮 Agent 调用携带全量表结构。"
                "需要写 SQL 查询具体事实数据时，先调用 ddl_search 动态载入候选表和 evidence packet；"
                "若 sql_ready=true 且风险低，优先直接 sql_db_query；否则再 inspect/sample 补证。"
                "无关表结构不要继续保留在推理中。"
            ),
        ]
    )
    parts.append(f"【用户原始问题】: {question}")
    return "\n\n".join(parts)


def _startup_catalog_context_for_prompt(ddl_context: str) -> str:
    mode = _env_value("SCHOOL_AGENT_STARTUP_DDL_MODE", "TENANT_AGENT_STARTUP_DDL_MODE", "summary").lower()
    if mode in {"full", "legacy"}:
        return _truncate_text(ddl_context, _startup_ddl_max_chars(default=9000), strip=True, rstrip=True)
    if mode in {"off", "none", "disabled"}:
        return "未内联 DDL；请先调用 ddl_search 检索相关表，再用 inspect_table_schema 确认字段。"
    summary = _summarize_ddl_candidates(ddl_context, max_tables=_startup_ddl_max_tables())
    if summary:
        return _truncate_text(summary, _startup_ddl_max_chars(default=1800), strip=True, rstrip=True)
    return _truncate_text(str(ddl_context or ""), _startup_ddl_max_chars(default=1200), strip=True, rstrip=True)


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
    shown = scored[:max_tables]
    lines = ["候选表目录："]
    lines.extend(_format_catalog_dataset_line(dataset, fallback_schema=schema_index.source_schema) for dataset in shown)
    omitted = max(0, len(datasets) - len(shown))
    if omitted:
        lines.append(f"已省略：{omitted} 张表未注入启动上下文；需要时调用 list_available_tables 或 ddl_search 扩展检索。")
    return _truncate_text("\n".join(lines), _startup_catalog_max_chars(), strip=True, rstrip=True)


def _format_catalog_dataset_line(dataset: Any, *, fallback_schema: str = "") -> str:
    source_schema = str(getattr(dataset, "source_schema", "") or fallback_schema or "").strip()
    source_view = str(getattr(dataset, "source_view", "") or getattr(dataset, "dataset_id", "") or "").strip()
    label = str(getattr(dataset, "label", "") or source_view).strip()
    desc = str(getattr(dataset, "description", "") or getattr(dataset, "searchable_text", "") or "").strip()
    table_ref = f"{source_schema}.{source_view}" if source_schema and source_view else source_view
    if desc:
        desc = _truncate_text(re.sub(r"\s+", " ", desc), 120, strip=True, rstrip=True)
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
    out: list[str] = []
    for term in [item.strip() for item in re.split(r"[\s,，。；;、？?]+", str(text or "")) if len(item.strip()) >= 2]:
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
