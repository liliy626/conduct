from __future__ import annotations

import json
import os
import re
from datetime import datetime
from typing import Any, AsyncIterator, Callable

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.tools import StructuredTool
from langgraph.checkpoint.memory import MemorySaver
from langgraph.prebuilt import create_react_agent

from gateway_core.api.openai_compat.chat_pipeline_parts.request_parts import build_upstream_error_text
from gateway_core.api.openai_compat.citation_formatter import openwebui_sources_from_citation_sources
from gateway_core.agents.streaming.langgraph_event_stream import (
    flush_active_langgraph_llm_runs,
    record_langgraph_event_as_trace_step,
)
from gateway_core.agents.contracts import ContractPlanner, build_tool_contract
from gateway_core.tools.langchain_tools import build_langchain_agent_tools
from gateway_core.tools.business_prompt_tool import BusinessPromptContextTool
from gateway_core.tools.tool_core import AgentToolInput, ToolExecutionContext
from gateway_core.conversation.threading import build_langgraph_thread_id
from gateway_core.agents.school_sql.agent_model import agent_model_for_tool_loop
from gateway_core.schema_context.ddl_embedding import ddl_embed_text
from gateway_core.agents.school_sql.sql_tools import DDLReactTools
from gateway_core.agents.school_sql.sql_utils import ddl_top_k, ddl_vector_table
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
        prefetched_ddl_context = _prefetch_startup_ddl_context(
            trace=trace,
            tools=tools,
            question=question,
            answer_focus=str(getattr(contract_plan, "answer_focus", "") or ""),
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
                    "prefetched_ddl_context_chars": len(prefetched_ddl_context),
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
            prefetched_ddl_context=prefetched_ddl_context,
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
                            business_prompt_context=business_prompt_context,
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
        flush_active_langgraph_llm_runs(trace, prefix="agent_native.langgraph")
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


def _plan_tool_contract(
    *,
    trace: Any,
    model: Any,
    question: str,
    conversation_context: str,
    metadata_catalog_context: str = "",
    ddl_vector_context: str = "",
    business_prompt_context: str = "",
    sql_experience_context: str = "",
) -> Any:
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
            metadata_catalog_context=metadata_catalog_context,
            ddl_vector_context=ddl_vector_context,
            business_prompt_context=business_prompt_context,
            sql_experience_context=sql_experience_context,
            available_tools=available_tools,
        )
        set_step_output(
            step,
            {
                "input": {
                    "question": question,
                    "conversation_context_preview": trace_preview(conversation_context),
                    "metadata_catalog_preview": trace_preview(metadata_catalog_context),
                    "ddl_vector_preview": trace_preview(ddl_vector_context),
                    "business_prompt_preview": trace_preview(business_prompt_context),
                    "sql_experience_preview": trace_preview(sql_experience_context),
                    "available_tools": available_tools,
                },
                "input_chars": {
                    "conversation_context": len(str(conversation_context or "")),
                    "metadata_catalog": len(str(metadata_catalog_context or "")),
                    "ddl_vector": len(str(ddl_vector_context or "")),
                    "business_prompt": len(str(business_prompt_context or "")),
                    "sql_experience": len(str(sql_experience_context or "")),
                },
                "decision": {
                    "route": getattr(plan, "route", "data"),
                    "required_outputs": list(plan.required_outputs),
                    "allowed_tools": list(plan.allowed_tools),
                    "answer_mode": plan.answer_mode,
                    "answer_focus": getattr(plan, "answer_focus", ""),
                },
                "output": {
                    "reason": plan.reason,
                },
                "error": None,
                "route": getattr(plan, "route", "data"),
                "required_outputs": list(plan.required_outputs),
                "allowed_tools": list(plan.allowed_tools),
                "answer_mode": plan.answer_mode,
                "answer_focus": getattr(plan, "answer_focus", ""),
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
                        "metadata_catalog_preview": trace_preview(metadata_catalog_context),
                        "ddl_vector_preview": trace_preview(ddl_vector_context),
                        "business_prompt_preview": trace_preview(business_prompt_context),
                        "sql_experience_preview": trace_preview(sql_experience_context),
                        "available_tools": available_tools,
                    },
                    "decision": {
                        "route": getattr(plan, "route", "data"),
                        "required_outputs": list(plan.required_outputs),
                        "allowed_tools": list(plan.allowed_tools),
                        "answer_mode": plan.answer_mode,
                        "answer_focus": getattr(plan, "answer_focus", ""),
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


def _prefetch_startup_ddl_context(*, trace: Any, tools: Any, question: str, answer_focus: str = "") -> str:
    if _env_value("SCHOOL_AGENT_STARTUP_DDL_PREFETCH_ENABLED", "TENANT_AGENT_STARTUP_DDL_PREFETCH_ENABLED", "1").lower() not in {
        "1",
        "true",
        "yes",
        "on",
    }:
        return ""
    queries = _startup_ddl_prefetch_queries(question=question, answer_focus=answer_focus)
    if not queries:
        return ""
    summaries: list[str] = []
    with trace_step(
        trace,
        "agent_native.ddl_prefetch",
        {"question": question, "answer_focus": answer_focus, "query_count": len(queries)},
    ) as step:
        outputs: list[dict[str, Any]] = []
        for query in queries:
            try:
                raw = tools.ddl_search(query)
                payload = _loads_json_object(raw)
            except Exception as exc:
                payload = {"query": query, "error": f"{type(exc).__name__}: {str(exc)[:200]}"}
            summary = _startup_ddl_prefetch_summary(query=query, payload=payload)
            if summary:
                summaries.append(summary)
            outputs.append(
                {
                    "query": query,
                    "doc_count": payload.get("doc_count") if isinstance(payload, dict) else None,
                    "table_refs": (payload.get("table_refs") or [])[:8] if isinstance(payload, dict) else [],
                    "error": payload.get("error", "") if isinstance(payload, dict) else "invalid_payload",
                }
            )
        context = "\n\n".join(summaries).strip()
        set_step_output(
            step,
            {
                "input": {"question": question, "answer_focus": answer_focus, "queries": queries},
                "decision": {"prefetch_enabled": True, "query_count": len(queries)},
                "output": {
                    "queries": outputs,
                    "context_chars": len(context),
                    "context_preview": trace_preview(context),
                },
                "error": None,
            },
        )
        return context


def _startup_ddl_prefetch_queries(*, question: str, answer_focus: str = "") -> list[str]:
    text = f"{question} {answer_focus}"
    if not text.strip():
        return []
    if "教师" not in text:
        return []
    profile_tokens = ["画像", "整体", "全貌", "概况", "分布", "教师队伍"]
    if not any(token in text for token in profile_tokens):
        return []
    return [
        "人事档案_人员信息 教师 职称 学历 学科 编制 性别 年龄",
    ]


def _startup_ddl_prefetch_summary(*, query: str, payload: dict[str, Any]) -> str:
    if not isinstance(payload, dict):
        return ""
    packets = payload.get("candidate_evidence_packets")
    if not isinstance(packets, list):
        packets = []
    lines = [f"预取查询：{query}"]
    if payload.get("error"):
        lines.append(f"错误：{payload.get('error')}")
    shown = 0
    for packet in packets:
        if not isinstance(packet, dict):
            continue
        table_ref = str(packet.get("table_ref") or "").strip()
        if not table_ref:
            continue
        latest_row = packet.get("latest_row_preview") if isinstance(packet.get("latest_row_preview"), dict) else {}
        latest_fields = list(latest_row.keys())[:12]
        lines.append(
            "- "
            f"{table_ref}; "
            f"freshness={packet.get('freshness_status') or 'unknown'}; "
            f"recommended_time_field={packet.get('recommended_time_field') or ''}; "
            f"sql_ready={bool(packet.get('sql_ready'))}; "
            f"risk={packet.get('sql_ready_risk') or ''}; "
            f"latest_fields={latest_fields}"
        )
        shown += 1
        if shown >= _startup_prefetch_tables_per_query():
            break
    if shown == 0:
        table_refs = payload.get("table_refs") if isinstance(payload.get("table_refs"), list) else []
        if table_refs:
            lines.append("候选表：" + "、".join(str(item) for item in table_refs[:_startup_prefetch_tables_per_query()]))
    return "\n".join(lines).strip()


def _startup_prefetch_tables_per_query() -> int:
    try:
        return max(1, min(int(_env_value("SCHOOL_AGENT_STARTUP_DDL_PREFETCH_TABLES_PER_QUERY", default="4") or "4"), 8))
    except Exception:
        return 4


def _startup_prefetch_context_max_chars() -> int:
    try:
        return max(1000, min(int(_env_value("SCHOOL_AGENT_STARTUP_DDL_PREFETCH_MAX_CHARS", default="9000") or "9000"), 24000))
    except Exception:
        return 9000


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
            "表目录不是字段清单；需要写 SQL 查询具体事实数据前，先用 ddl_search 获取相关表和 candidate_evidence_packets。",
            "如果用户的问题是学校有哪些业务领域、有哪些表、支持什么功能模块等宏观目录盘点，"
            "且输入已提供当前学校业务表目录快照，通常可先依据快照按表名前缀/主题词归纳，并调用 final_answer_handoff 交接。",
            "若 ddl_search 返回 sql_ready=true、sql_ready_risk=low，且问题可由明确单表字段回答，"
            "优先使用 recommended_time_field、latest_row_preview 和候选字段直接调用 sql_db_query。",
            "若工具合同/回答焦点包含 P0/P1/P2：首轮只围绕 P0 调用 ddl_search 和 SQL；"
            "P1/P2 只有在其触发条件满足后才补查，角色提示词只影响分析角度，不能自动扩大查询范围。",
            "只有字段含义、JSONB/数组展开、多表关联、大小写敏感列名或时间口径不明确时，才继续 inspect_table_schema/sample_table_rows，并说明原因。",
            "DDL/工具返回是硬约束；生成 SQL 时只使用当前表 DDL/evidence/inspect 中出现的精确字段名，"
            "不要把其他候选表或历史 SQL 的字段迁移到本表；字段没有证据时先 inspect_table_schema 修正。历史 SQL 只作参考。",
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
                "JSON 优先包含 result_id、row_count、business_domains/items、source_views、caveats。"
                "不要把完整 Markdown 长答案放入 handoff_json；最终自然表达由 final answer 模型完成。"
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


def _scripted_handoff_answer(handoff_payload: dict[str, Any], *, business_prompt_context: str = "") -> str:
    if _env_value("SCHOOL_AGENT_SCRIPTED_HANDOFF_FINAL_ENABLED", default="1").lower() not in {"1", "true", "yes", "on"}:
        return ""
    if not isinstance(handoff_payload, dict):
        return ""
    markdown = _business_answer_markdown(handoff_payload)
    if not markdown:
        return ""
    answer = _sanitize_final_answer(markdown)
    caveats: list[str] = []
    for payload in _business_handoff_payloads(handoff_payload):
        caveats = _business_caveats(payload.get("caveats"))
        if caveats:
            break
    if caveats and not _answer_already_has_caveats(answer):
        answer = answer.rstrip() + "\n\n### 注意事项\n" + "\n".join(f"- {item}" for item in caveats)
    return _append_business_disclaimer(answer.strip(), business_prompt_context=business_prompt_context)


def _business_answer_markdown(handoff_payload: dict[str, Any]) -> str:
    for payload in _business_handoff_payloads(handoff_payload):
        for key in (
            "pure_business_data_markdown",
            "final_answer",
            "answer",
            "answer_markdown",
            "summary_markdown",
            "summary",
        ):
            value = payload.get(key)
            if isinstance(value, str) and value.strip() and not _looks_like_internal_payload(value):
                return value.strip()
    return ""


def _business_handoff_payloads(handoff_payload: dict[str, Any]) -> list[dict[str, Any]]:
    if not isinstance(handoff_payload, dict):
        return []
    out: list[dict[str, Any]] = []
    queue: list[dict[str, Any]] = [handoff_payload]
    seen: set[int] = set()
    while queue and len(out) < 8:
        payload = queue.pop(0)
        identity = id(payload)
        if identity in seen:
            continue
        seen.add(identity)
        out.append(payload)
        for key in ("summary", "final_answer", "answer", "answer_markdown", "handoff_json"):
            parsed = _loads_json_object(payload.get(key))
            if parsed:
                queue.append(parsed)
    return out


def _append_business_disclaimer(answer: str, *, business_prompt_context: str = "") -> str:
    clean = str(answer or "").strip()
    disclaimer = _business_prompt_disclaimer(business_prompt_context)
    if not clean or not disclaimer or disclaimer in clean:
        return clean
    return clean.rstrip() + "\n\n" + disclaimer


def _business_prompt_disclaimer(business_prompt_context: str) -> str:
    payload = _loads_json_object(str(business_prompt_context or ""))
    found = _first_key_text(payload, "disclaimer")
    return found.strip()


def _first_key_text(value: Any, key_name: str) -> str:
    if isinstance(value, dict):
        value_at_key = value.get(key_name)
        if isinstance(value_at_key, str) and value_at_key.strip():
            return value_at_key
        for nested in value.values():
            found = _first_key_text(nested, key_name)
            if found:
                return found
    if isinstance(value, list):
        for nested in value:
            found = _first_key_text(nested, key_name)
            if found:
                return found
    return ""


def _business_caveats(value: Any) -> list[str]:
    if isinstance(value, str):
        raw_items = [item.strip() for item in re.split(r"[\n;；]+", value) if item.strip()]
    elif isinstance(value, list):
        raw_items = [str(item).strip() for item in value if str(item).strip()]
    else:
        raw_items = []
    out: list[str] = []
    for item in raw_items:
        clean = _strip_internal_caveat_text(item)
        if clean and clean not in out:
            out.append(clean)
    return out[:6]


def _strip_internal_caveat_text(text: str) -> str:
    clean = str(text or "").strip()
    if not clean:
        return ""
    internal_tokens = [
        "sql",
        "ddl_",
        "tool_",
        "handoff",
        "json",
        "trace",
        "langgraph",
        "source_views",
        "evidence_board",
        "tool_contract",
    ]
    lowered = clean.lower()
    if any(token in lowered for token in internal_tokens):
        return ""
    return clean


def _looks_like_internal_payload(text: str) -> bool:
    clean = str(text or "").strip()
    if not clean:
        return True
    if clean.startswith("{") and clean.endswith("}"):
        return True
    lowered = clean.lower()
    return any(token in lowered for token in ["final_answer_handoff_json", "handoff json", "tool_contract"])


def _answer_already_has_caveats(answer: str) -> bool:
    text = str(answer or "")
    return any(token in text for token in ["注意事项", " caveat", "Caveat", "局限", "说明"])


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
    business_prompt_context: str = "",
) -> AsyncIterator[str]:
    direct_answer = _direct_snapshot_answer(question=question, handoff_payload=handoff_payload)
    if direct_answer:
        yield direct_answer
        return

    scripted_answer = _scripted_handoff_answer(handoff_payload, business_prompt_context=business_prompt_context)
    if scripted_answer:
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
                        "model_name": "",
                        "direct_snapshot": False,
                        "scripted_handoff": True,
                        "format_policy": "scripted_business_markdown",
                    },
                    "output": {
                        "answer_chars": len(scripted_answer),
                        "handoff_json": trace_preview(handoff_text),
                    },
                    "error": None,
                },
            )
        yield scripted_answer
        return

    model = agent_model_for_tool_loop(final_model)
    first_token_ms: int | None = None
    chunk_count = 0
    answer_parts: list[str] = []
    started = datetime.now().timestamp()
    prompt = _fast_final_answer_prompt(
        question=question,
        handoff_payload=handoff_payload,
        source_views=source_views,
        business_prompt_context=business_prompt_context,
    )
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
                    "business_prompt_chars": len(str(business_prompt_context or "")),
                },
                "decision": {
                    "model_name": _model_name(final_model),
                    "direct_snapshot": False,
                    "format_policy": "free",
                },
                "output": {
                    "handoff_json": trace_preview(handoff_text),
                    "business_prompt_context": trace_preview(business_prompt_context),
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
            "business_prompt_context": trace_preview(business_prompt_context),
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
            answer_parts.append(text)
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
    disclaimer = _business_prompt_disclaimer(business_prompt_context)
    if disclaimer and disclaimer not in "".join(answer_parts):
        yield "\n\n" + disclaimer


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
            "把紧凑 JSON 字符串放入 handoff_json。"
            "不要把完整 Markdown 长答案放入 handoff_json；最终自然语言表达由 final answer 模型完成。"
            "目录盘点类问题优先交接 business_domains/items/source_views/caveats 等结构化字段，"
            "每项只保留名称、数量、代表性表或关键证据短语。"
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
    payload = {
        "question": question,
        "data_evidence": evidence_by_task,
        "evidence_board": evidence_board,
        "external_evidence": [],
        "pure_business_data_markdown": "",
        "source_views": source_views,
        "caveats": [caveat] if caveat else [],
    }
    if _is_direct_snapshot_request(question):
        payload["pure_business_data_markdown"] = _first_truth_data_markdown(
            {"data_evidence": evidence_by_task, "evidence_board": evidence_board}
        )
    return payload


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


def _run_fast_final_answer_sync(
    *,
    trace: Any,
    final_model: Any,
    question: str,
    handoff_payload: dict[str, Any],
    source_views: list[str],
    business_prompt_context: str = "",
) -> str:
    direct_answer = _direct_snapshot_answer(question=question, handoff_payload=handoff_payload)
    if direct_answer:
        return direct_answer

    scripted_answer = _scripted_handoff_answer(handoff_payload, business_prompt_context=business_prompt_context)
    if scripted_answer:
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
                        "model_name": "",
                        "direct_snapshot": False,
                        "scripted_handoff": True,
                        "format_policy": "scripted_business_markdown",
                    },
                    "output": {
                        "answer_chars": len(scripted_answer),
                        "handoff_json": trace_preview(handoff_text),
                    },
                    "error": None,
                },
            )
        return scripted_answer

    model = agent_model_for_tool_loop(final_model)
    first_token_ms: int | None = None
    chunk_count = 0
    started = datetime.now().timestamp()
    answer_parts: list[str] = []
    prompt = _fast_final_answer_prompt(
        question=question,
        handoff_payload=handoff_payload,
        source_views=source_views,
        business_prompt_context=business_prompt_context,
    )
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
                    "business_prompt_chars": len(str(business_prompt_context or "")),
                },
                "decision": {
                    "model_name": _model_name(final_model),
                    "direct_snapshot": False,
                    "format_policy": "free",
                },
                "output": {
                    "handoff_json": trace_preview(handoff_text),
                    "business_prompt_context": trace_preview(business_prompt_context),
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
            "business_prompt_context": trace_preview(business_prompt_context),
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
    return _append_business_disclaimer("".join(answer_parts), business_prompt_context=business_prompt_context)


def _fast_final_answer_system_prompt() -> str:
    return (
        "你根据客观证据包回答学校数据问题。"
        "所有数字、名单、判断、政策与来源必须来自证据，不得新增未提供的事实。"
        "禁止向用户泄露 SQL、数据表名、工具名、Handoff JSON、内部节点名或“我先查询/我需要查看”等过程性话术。"
        "在不改变事实的前提下，可以自由组织最终呈现方式；用自然、克制、面向学校管理者的语言，避免模板腔和生硬口号。"
    )


def _fast_final_answer_prompt(
    *,
    question: str,
    handoff_payload: dict[str, Any],
    source_views: list[str],
    business_prompt_context: str = "",
) -> str:
    style_guide = _fast_final_answer_style_guide(
        question=question,
        handoff_payload=handoff_payload,
        source_views=source_views,
        business_prompt_context=business_prompt_context,
    )
    return "\n".join(
        [
            "客观证据包：",
            style_guide,
            "基于证据回答用户问题。呈现方式自由；结合业务提示词里的证据边界，用清楚、柔和、可读的学校业务语言表达；不要泄露内部结构，不要复述工具过程，不要说“根据校医院反馈”等未提供来源。",
        ]
    )


def _fast_final_answer_style_guide(
    *,
    question: str,
    handoff_payload: dict[str, Any],
    source_views: list[str],
    business_prompt_context: str = "",
) -> str:
    q = str(question or "")
    views = " ".join(str(item or "") for item in source_views)
    payload_text = json.dumps(handoff_payload, ensure_ascii=False, default=str)
    business_prompt_text = str(business_prompt_context or "").strip()
    combined = f"{q} {views} {payload_text} {business_prompt_text}"
    evidence_matrix: dict[str, Any] = {
        "user_original_question": q,
        "source_views": list(source_views or []),
        "handoff_evidence": _compact_final_handoff_payload(handoff_payload),
    }
    if business_prompt_text:
        evidence_matrix["业务提示词/证据边界"] = business_prompt_text
        evidence_matrix["business_prompt_context"] = business_prompt_text
    evidence_notes: list[str] = []
    if any(token in combined for token in ["业务领域", "业务域", "功能模块", "数据资产", "表目录"]):
        evidence_notes.append("业务领域边界：按表名前缀或主题词归纳，说明这是目录口径；不要把目录盘点写成具体运营指标结论。")
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


def _compact_final_handoff_payload(handoff_payload: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(handoff_payload, dict):
        return {}
    compact: dict[str, Any] = {}
    for key in (
        "question",
        "status",
        "answer_focus",
        "summary",
        "final_answer",
        "answer",
        "answer_markdown",
        "pure_business_data_markdown",
        "business_domains",
        "items",
        "metrics",
        "key_findings",
        "caveats",
        "source_views",
        "external_evidence",
    ):
        value = handoff_payload.get(key)
        if value not in (None, "", [], {}):
            compact[key] = _compact_json_value(value, max_chars=3000)
    digest = _evidence_digest(handoff_payload.get("data_evidence"))
    if digest:
        compact["evidence_digest"] = digest
    board_digest = _evidence_board_digest(handoff_payload.get("evidence_board"))
    if board_digest:
        compact["evidence_board_digest"] = board_digest
    return compact


def _evidence_digest(data_evidence: Any) -> list[dict[str, Any]]:
    if not isinstance(data_evidence, dict):
        return []
    out: list[dict[str, Any]] = []
    for task_id, task in data_evidence.items():
        if not isinstance(task, dict):
            continue
        summary = task.get("evidence_summary") if isinstance(task.get("evidence_summary"), dict) else {}
        item: dict[str, Any] = {
            "task_id": str(task_id),
            "row_count": task.get("row_count", summary.get("row_count")),
            "referenced_views": task.get("referenced_views", summary.get("referenced_views")),
        }
        for key in ("dataset_label", "intent", "total_row_count", "query_may_have_more", "total_count_error"):
            value = task.get(key)
            if value not in (None, "", [], {}):
                item[key] = value
        for key in ("truth_data_markdown", "notable_findings", "top_items", "row_sample"):
            value = summary.get(key)
            if value not in (None, "", [], {}):
                item[key] = _compact_json_value(value, max_chars=1800)
        sql_lineage = task.get("sql_lineage")
        if isinstance(sql_lineage, dict):
            item["sql_lineage"] = {
                key: sql_lineage.get(key)
                for key in ("tables_used", "row_count", "time_range")
                if sql_lineage.get(key) not in (None, "", [], {})
            }
        out.append({key: value for key, value in item.items() if value not in (None, "", [], {})})
        if len(out) >= _final_answer_evidence_digest_limit():
            break
    return out


def _evidence_board_digest(evidence_board: Any) -> dict[str, Any]:
    if not isinstance(evidence_board, dict):
        return {}
    out: dict[str, Any] = {}
    for key in ("business_clues", "caveats", "source_views", "tasks"):
        value = evidence_board.get(key)
        if value not in (None, "", [], {}):
            out[key] = _compact_json_value(value, max_chars=2000)
    return out


def _compact_json_value(value: Any, *, max_chars: int) -> Any:
    if isinstance(value, str):
        return _truncate(value, max_chars)
    if isinstance(value, list):
        return [_compact_json_value(item, max_chars=max_chars) for item in value[:20]]
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        for index, (key, nested) in enumerate(value.items()):
            if index >= 30:
                break
            if str(key) in {"raw_rows", "rows", "data_evidence", "evidence_board"}:
                continue
            out[str(key)] = _compact_json_value(nested, max_chars=max_chars)
        return out
    return value


def _final_answer_evidence_digest_limit() -> int:
    try:
        return max(3, min(int(_env_value("SCHOOL_FINAL_ANSWER_EVIDENCE_DIGEST_LIMIT", default="24") or "24"), 80))
    except Exception:
        return 24


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
    prefetched_ddl_context: str = "",
) -> str:
    now = datetime.now()
    startup_ddl_context = _startup_catalog_context_for_prompt(ddl_context)
    history_context = _history_context_for_prompt(question=question, sql_experience=sql_experience)
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
            f"【历史相似 SQL 案例】:\n{_truncate(history_context, 6000)}",
            (
                "【数据库表目录（无详细DDL）】:\n"
                f"{startup_ddl_context}\n\n"
                "说明：这里默认只放表名和一句话业务描述，避免每轮 Agent 调用携带全量表结构。"
                f"{_catalog_overview_decision_guide(question)}"
                "需要写 SQL 查询具体事实数据时，先调用 ddl_search 动态载入候选表和 evidence packet；"
                "若 sql_ready=true 且风险低，优先直接 sql_db_query；否则再 inspect/sample 补证。"
                "无关表结构不要继续保留在推理中。"
            ),
        ]
    )
    if str(prefetched_ddl_context or "").strip():
        parts.append(
            "【启动前已预取 DDL/evidence】:\n"
            f"{_truncate(prefetched_ddl_context, _startup_prefetch_context_max_chars())}\n"
            "说明：这部分由代码在进入 ReAct 前调用 ddl_search 预取，等同于相关表的 ddl_search 已完成；"
            "如果字段、时间口径和单表/多表关系已经清楚，可以直接基于这些 evidence 生成 sql_db_query。"
            "只有缺少字段证据、JSON/数组结构不清或关联口径不明时，再补充调用 ddl_search/inspect/sample。"
        )
    parts.append(f"【用户原始问题】: {question}")
    return "\n\n".join(parts)


def _history_context_for_prompt(*, question: str, sql_experience: str) -> str:
    if not _is_catalog_overview_question(question):
        return str(sql_experience or "")
    parsed = _loads_json_object(sql_experience)
    if not parsed:
        return str(sql_experience or "")
    experiences = parsed.get("experiences")
    if not isinstance(experiences, list):
        return str(sql_experience or "")
    clean_items: list[dict[str, Any]] = []
    for item in experiences[:5]:
        if not isinstance(item, dict):
            continue
        clean_items.append(
            {
                "question": item.get("question"),
                "table_refs": item.get("table_refs") or [],
                "answer_summary": item.get("answer_summary") or "",
                "source": item.get("source") or "",
                "manual_hint": bool(item.get("manual_hint")),
            }
        )
    payload = {
        "source": parsed.get("source") or "sql_history_vector_documents",
        "query": parsed.get("query") or question,
        "experience_count": parsed.get("experience_count") or len(clean_items),
        "manual_hint_count": parsed.get("manual_hint_count") or 0,
        "usage_hint": "目录盘点题只参考历史口径和表选择提示；不直接沿用历史 SQL。",
        "experiences": clean_items,
    }
    return json.dumps(payload, ensure_ascii=False, default=str)


def _startup_catalog_context_for_prompt(ddl_context: str) -> str:
    if _is_full_catalog_snapshot_context(ddl_context):
        return _truncate(str(ddl_context or ""), _startup_catalog_max_chars())
    mode = _env_value("SCHOOL_AGENT_STARTUP_DDL_MODE", "TENANT_AGENT_STARTUP_DDL_MODE", "summary").lower()
    if mode in {"full", "legacy"}:
        return _truncate(ddl_context, _startup_ddl_max_chars(default=9000))
    if mode in {"off", "none", "disabled"}:
        return "未内联 DDL；请先调用 ddl_search 检索相关表，再用 inspect_table_schema 确认字段。"
    summary = _summarize_ddl_candidates(ddl_context, max_tables=_startup_ddl_max_tables())
    if summary:
        return _truncate(summary, _startup_ddl_max_chars(default=1800))
    return _truncate(str(ddl_context or ""), _startup_ddl_max_chars(default=1200))


def _is_full_catalog_snapshot_context(ddl_context: str) -> bool:
    text = str(ddl_context or "")
    return "全量业务表目录快照" in text


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
    if _is_catalog_overview_question(question):
        return _full_catalog_snapshot_context(schema_index, datasets=datasets)
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


def _full_catalog_snapshot_context(schema_index: Any, *, datasets: list[Any]) -> str:
    max_tables = _startup_catalog_max_tables()
    ordered = sorted(datasets, key=lambda item: str(getattr(item, "source_view", "") or getattr(item, "label", "") or ""))
    shown = ordered[:max_tables]
    lines = [
        "全量业务表目录快照（当前学校租户已动态加载的业务表，格式：表名: 业务含义描述）：",
        *[_format_catalog_dataset_line(dataset, fallback_schema=schema_index.source_schema) for dataset in shown],
    ]
    omitted = max(0, len(ordered) - len(shown))
    if omitted:
        lines.append(f"已省略：{omitted} 张表未放入本次快照；需要完整清单时可再扩展目录查询。")
    lines.append(_catalog_overview_decision_guide("学校有哪些业务领域？").strip())
    return _truncate("\n".join(lines), _startup_catalog_max_chars())


def _catalog_overview_decision_guide(question: str) -> str:
    if not _is_catalog_overview_question(question):
        return ""
    return (
        "运行时决策提示：如果当前问题属于宏观全局盘点型发问，例如询问学校有哪些业务领域、有哪些表、"
        "支持什么功能模块等，以上目录快照通常情况下已经包含本校功能大盘事实；"
        "可直接依据目录快照按表名前缀或主题词归纳业务领域，并调用 final_answer_handoff 交接。"
        "如果用户继续追问某个领域的字段、明细、时间范围、人数、异常或具体指标，再下钻调用工具补证。"
    )


def _is_catalog_overview_question(question: str) -> bool:
    text = str(question or "")
    if not text:
        return False
    return bool(
        any(token in text for token in ["业务领域", "业务域", "功能模块", "业务模块", "数据资产", "表目录", "哪些表", "有哪些表"])
        or ("有哪些" in text and any(token in text for token in ["领域", "模块", "功能"]))
    )


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
