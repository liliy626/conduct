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
from gateway_core.school.trace import finish_trace, new_tenant_trace, set_step_output, trace_step


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
) -> AsyncIterator[dict[str, Any]]:
    """Run the school-schema DDL ReAct Agent as the primary streaming answerer.

    This intentionally mirrors the old yili-ai-backend shape: retrieve history
    and DDL context, pass an enhanced user message into a LangGraph ReAct Agent,
    stream observable tool calls, and let that same Agent produce the final
    natural-language answer.
    """
    trace = new_tenant_trace(question)
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

        if await _is_guard_router_chat(trace=trace, model=model, question=question):
            async for chunk in _stream_guard_router_chat(
                trace=trace,
                model=model,
                question=question,
                conversation_context=conversation_context,
            ):
                yield {"type": "content", "text": chunk}
            finish_trace(trace)
            return

        schema_index = _build_agent_schema_index(
            trace=trace,
            school_id=school_id,
            dsn=dsn,
            psycopg_module=psycopg_module,
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
        final_handoff_enabled = _final_handoff_enabled(final_model)
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
                yield {"type": "content", "text": asset_text}
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
                async for chunk in _stream_fast_final_answer(
                    trace=trace,
                    final_model=final_model,
                    question=question,
                    handoff_payload=handoff_payload,
                    source_views=tools.source_views,
                ):
                    yield {"type": "content", "text": chunk}
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
                        yield {"type": "content", "text": content}
                    elif buffer_tool_planning_content:
                        if run_id in llm_streaming_answer_runs:
                            yield {"type": "content", "text": content}
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
                                        yield {"type": "content", "text": split["answer"]}
                                    llm_streaming_answer_runs.add(run_id)
                                    llm_content_buffers.pop(run_id, None)
                    else:
                        yield {"type": "content", "text": content}
            if event.get("event") == "on_chat_model_end" and (final_handoff_enabled or buffer_tool_planning_content):
                run_id = str(event.get("run_id") or "")
                buffered_text = "".join(llm_content_buffers.pop(run_id, []))
                if final_handoff_enabled:
                    handoff_payload = _extract_final_handoff_payload(buffered_text)
                    if handoff_payload and not final_handoff_done:
                        final_handoff_done = True
                        async for chunk in _stream_fast_final_answer(
                            trace=trace,
                            final_model=final_model,
                            question=question,
                            handoff_payload=handoff_payload,
                            source_views=tools.source_views,
                        ):
                            yield {"type": "content", "text": chunk}
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
                            yield {"type": "content", "text": split["answer"]}
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
            async for chunk in _stream_fast_final_answer(
                trace=trace,
                final_model=final_model,
                question=question,
                handoff_payload=handoff_payload,
                source_views=tools.source_views,
            ):
                yield {"type": "content", "text": chunk}
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
        finish_trace(trace)
    except Exception as exc:
        with trace_step(trace, "agent_native.error", {"question": question}) as step:
            set_step_output(step, {"error": str(exc)})
        finish_trace(trace)
        yield {"type": "content", "text": build_upstream_error_text(exc)}


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
                "required_outputs": list(plan.required_outputs),
                "allowed_tools": list(plan.allowed_tools),
                "answer_mode": plan.answer_mode,
                "reason": plan.reason,
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
        set_step_output(step, _index_trace_payload(schema_index, source="information_schema"))
        return schema_index


def _require_school_api_key_record() -> Any:
    record = current_api_key_record()
    if not is_school_record(record):
        raise ValueError("school API key is required for school SQL Agent")
    if not str(getattr(record, "schema_name", "") or "").strip():
        raise ValueError("school API key must configure schema_name in platform.gateway_api_keys")
    return record


def _index_trace_payload(schema_index: Any, *, source: str) -> dict[str, Any]:
    return {
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
            "你是一个资深的学校数据分析专家与教学管理助手。",
            "你现在是主问答 Agent：你要自己决定调用哪些工具，并直接给用户最终自然语言回答。",
            "所有能力都在同一层 tools 中：ddl_search 查 DDL 表结构，sample_table_rows 看样例数据，inspect_jsonb_recordset 探测 JSONB 子表结构，jsonb_recordset_query 打平 JSONB 子表查询，sql_experience_search 查历史 SQL 经验，sql_db_query 查学校数据库，trend_analysis 看趋势，anomaly_detection 找异常，cohort_compare 做同类对比，official_policy_search 查政策库，web_search 联网检索，chart 生成结构化数据图表，plot 基于已查 rows 生成 PNG 数据图，generate_image_tool 生成图片/视觉素材，slide 生成汇报 PPT，time 解析时间口径，business_prompt_context 提供业务视角提示词板块。",
            f"【本轮实际可用工具清单】：{available_tools_text}。",
            "只能根据【本轮实际可用工具清单】判断工具是否可用。清单里有某个工具时，严禁说“当前环境没有该工具/该工具不可用”；如调用失败，应基于工具返回的 error 说明失败原因。清单里没有的工具不能调用，也不要假装调用过。",
            tool_contract_prompt or "【本轮工具合同】：无强制产物；按问题需要选择工具。",
            "系统会在用户问题前提供【当前系统参照时间】、【历史相似 SQL 案例】、【数据库表目录（无详细DDL）】和【用户原始问题】。",
            "如果历史 SQL 与问题高度相似，优先参考它的表选择和聚合思路，但必须根据当前问题修改 WHERE 条件。",
            "入口数据库表目录只是认路地图，不是完整字段清单；第一轮不会内联详细 DDL，不要把目录当作全量数据库上下文。",
            "当前 DDL 是硬约束，历史 SQL 只是参考；如果历史 SQL 的表或字段不在 ddl_search/inspect_table_schema/sample_table_rows 返回内容或白名单中，必须忽略。",
            "写 SQL 前必须确保相关表已由 ddl_search、inspect_table_schema 或 sample_table_rows 加入白名单；选定表后优先调用 inspect_table_schema 获取精确字段，再执行当前学校 schema 的只读 SELECT。",
            "遇到 JSON/数组/枚举字段、字段格式不确定时，先调用 sample_table_rows；遇到趋势、异常、同类排名问题，先 sql_db_query 查到证据 rows，再调用 trend_analysis、anomaly_detection 或 cohort_compare 做结构化分析。",
            "低代码 JSONB 子表准则：当 DDL 或字段样例显示明细数据存储在 JSONB/JSON 数组字段中，且用户问题涉及子表内部属性（如星期、负责人、工作内容、项目、地点、截止时间）时，禁止直接手写复杂 JSON 路径或大量 jsonb_to_recordset SQL；第一步调用 inspect_jsonb_recordset 探测内部 key 和 record_schema_suggestion，第二步调用 jsonb_recordset_query 受控打平查询。where 只引用 m. 主表字段和 s. 子表字段，例如 s.\"星期\" = '星期三'。",
            "数据源可能存在一人/一学生/一班级多行展开。回答“有多少教师/学生/班级/人数”时，优先按稳定 ID 去重；没有 ID 时按姓名或名称去重；SQL 应尽量使用 COUNT(DISTINCT ...) 或先查明细后依据工具返回的 distinct_entity_count 作答，不能把 row_count 直接当作人数。",
            "高效原则：优先编写能够一次性获取所需证据的复合 SQL；严禁先查一小块、再无理由抽样、再补查一小块的碎步慢查。只有字段格式不确定、JSON/数组字段、或上一次查询明确报错时，才需要 sample_table_rows。",
            "工具额度与深度：每一个独立的业务证据方向允许最多 2 次 sql_db_query；遇到多维度概况、趋势、对比、诊断、归因或发展建议时，允许跨业务表补证。不要因为已经查到一张主事实表就急于结束；也不要无目的试探，补查必须服务于明确的证据方向。",
            "候选表全量遍历与并行核验：ddl_search 返回的 top_k 是候选池，不是只选第一张表的排序答案。学校业务经常存在新旧表、多系统并存、因公/因私拆表、主表/明细表并行。只要候选池里出现多张与当前问题高度相关的表，且你无法百分百确定哪张才是当前在用表，必须同时或连续核验这些候选表：至少比较 count、MAX(业务时间字段/提交时间/审批时间/__instance_time) 和当前时间段命中数。对于本月、本周、本学期等当前时间统计，必须优先采用在当前时间段有活跃更新状态的表；如果一个问题同时涉及因公和因私、学生和教师、主表和明细表等并列表，必须同时查询对应表，严禁只查其中一张就交卷。",
            "数据迁移与时间断层追查：学校业务系统经常换表、迁移或分阶段停更。如果你用当前系统参照时间在某张候选表中查询当前时间段查出为 0、空结果或明显低于预期，绝对不要直接把 0 当作业务事实交卷。必须先对该表补查一次 MAX(时间字段)，例如最新提交时间、审批时间、业务日期、开始时间或 __instance_time；如果最新时间停留在几周前、上月、去年，说明当前表是历史表或已停更旧表。此时必须重新调用 ddl_search 检索同业务方向候选表，重点查找带新系统、销假、审批、明细、汇总、v_、new、当前年份/学期等语义的表，并优先选择在当前时间段有活跃更新状态的表重新统计。",
            "工具克制：用户没有明确要求图表、图片、PPT、联网搜索或政策依据时，默认禁止调用 chart、generate_image_tool、slide、web_search、official_policy_search，也默认禁止调用 plot；仅通过学校数据库证据和文字闭环回答。用户明确要求图片/视觉图/大屏图且本轮工具清单包含 generate_image_tool 时，应在查到必要数据后自主判断是否调用 generate_image_tool，不能把图片生成改写成纯文字方案来替代。",
            "外部原因例外：如果用户询问病假、发热、流感、呼吸道感染等是否存在季节性或外部公共卫生原因，应先用学校数据库确认校内趋势，再调用 web_search 获取公开公共卫生或流感季节性证据；不要只凭校内 SQL 下因果结论。",
            "动态探路：下一步查什么由你根据工具结果自主决定，禁止在没有证据时盲猜跨业务方向；每一轮只围绕当前证据最支持的下一步推进。",
            "同一决策节点可以在同一轮并行调用多个工具：例如同时下发多个 inspect_table_schema 或 sql_db_query 获取同一业务方向的必要证据；但不要把尚未被数据支持的归因方向提前写成固定计划。",
            "单轮多步：如果当前决策节点已经确定，且需要总量、分组、Top 项、趋势等多个口径，优先在同一条 SQL 中用 CTE、GROUP BY、窗口函数或 UNION ALL 一次取齐，避免多轮碎片化查询。",
            "单次 sql_db_query 默认最多返回有限行数；如果工具返回 query_may_have_more=true、display_rows_has_more=true，或用户要求“完整名单/全部明细/查完”，必须继续补查：可以使用相同 WHERE 加 OFFSET 翻页，或按时间、班级、类别、教师等条件拆分，直到最后一批返回行数低于上限；最终回答要说明是否已查完整。",
            "如果问题本质是统计/趋势/排名，优先写聚合 SQL，不要把大量明细全取出来再让模型心算；只有用户明确要明细或完整名单时才翻页补查。",
            "只有用户明确需要画图、联网搜索、生成图片或 PPT 时，才调用对应 tool；不要把这些工具视为回答后的默认附加链路。",
            "plot 与 generate_image_tool 的边界：plot 用于基于 sql_db_query 已查 rows 生成严谨 PNG 数据图；generate_image_tool 用于大屏视觉图、海报、宣传图、视觉素材或含设计风格的图片。plot 不执行 SQL，必须先用 sql_db_query 查到 rows，再把 rows/evidence_rows 传给 plot。",
            "用户明确说“生成图片、画图、图表、趋势图、分布图、趋势分布图、视觉化展示、生成汇报图”时，先查询必要数据，再优先调用 generate_image_tool 生成可直接展示的图片；调用时 prompt 必须包含图表主题、真实数据、统计周期、指标、分类、关键标签和需要呈现的业务结论；prompt 中没有查到的数字、日期、百分比、峰值、环比、同比、合计、均值、排名、坐标轴刻度和汇总卡片一律不要写，禁止让生图模型自行补数字；只有用户明确要求交互式图表、可下载数据源、JSON/HTML 图表时，才调用 chart。",
            "用户明确说“编辑图片、修改图片、改图、修图、换背景、局部修改”且提供了图片 URL、图片路径或上一轮生成图片链接时，也调用 generate_image_tool；此时必须把图片引用放到 image_url/image_path/images/image_urls/image_paths 参数中，prompt 只描述要修改的内容；如果用户要求局部编辑且提供 mask，再传 mask_url 或 mask_path。不要把图片链接只写在 prompt 文本里。",
            "generate_image_tool 和 slide 属于重型视觉工具：同一轮回答最多各调用一次。generate_image_tool 成功后系统会直接展示图片；slide 成功后系统会展示预览/下载链接；不要重复调用；失败时说明失败原因并停止重试。",
            "如果本轮调用了 generate_image_tool，最终回答只需说明图片已生成，不要再次手写图片链接或 Markdown 图片，系统会在工具结束事件中把图片直接展示给用户。",
            "在形成最终回答前，如问题涉及教师发展、德育、请假健康、校级驾驶舱、政策、设施维护等学校业务场景，应调用 business_prompt_context 获取业务角色、判断视角和证据边界；它不是固定回答模板，不要被它限制格式。",
            "图表/PPT 必须基于已查询到的 evidence rows 或政策证据生成，不能编造数据。",
            "联网搜索和图片生成不能发送学生、教师、家长等个人敏感明细；工具会做隐私拦截，拦截时要说明原因并改用本地证据。",
            "政策、通知和公开网页证据有强时效性；检索到多条结果时，必须优先考虑发布时间、更新时间和用户问题中的时间范围。",
            "如果用户问“最近、近期、最新、近三年、本年度”等，必须优先选择时间更近、来源更权威、主题更贴合的结果；旧政策、旧通知、旧网页只能作为背景，不得覆盖更新证据。",
            "当检索结果很多时，排序优先级为：官方来源 > 发布时间较新 > 与问题主题精确匹配 > 内容完整度；不要因为关键词命中就把招聘公告、无关新闻、泛泛动态混入教研、培训、课题通知回答。",
            "如果检索结果时间跨度较大，回答中要说明最新可见证据是什么、较早证据仅作参考；如果结果没有发布时间，要标注“未提供发布时间”，不能把它当作最新依据。",
            "职称条件/是否具备申报资格类问题，必须至少查询：教师基础档案、当前学年积分、按学年积分趋势、成果申报记录，并调用 official_policy_search 查询政策条件。",
            "证据边界：只能把工具返回的数据和 official_policy_search 返回的政策内容当作事实；没有查到的师德考核、继续教育学分、年度考核、班主任/导师经历、一票否决项，只能写“待核实”，不能默认达标。",
            "政策边界：政策名称、文号、年份、链接必须来自 official_policy_search 的返回内容；不能编造或沿用历史经验中的政策文号。",
            "链接输出必须兼容 OpenWebUI：使用 Markdown 链接 `[标题](https://...)` 或列表；不要输出裸 JSON、HTML iframe、source/citation 对象。",
            "判断口径：可以给出“已满足/基本满足/待核实/暂不能判断”，但每个判断必须说明对应证据来源；证据不足时优先保守表达。",
            "成果/述职/评优/职称材料类问题，要输出可直接使用的材料化分析，包含代表性成果表格。",
            "最终回答不要输出 JSON；要像 yili-ai-backend 一样给用户清晰、完整、可读的业务分析。",
            f"当前学校：{schema_index.school_id} / {schema_index.school_name}；当前 schema：{schema_index.source_schema}。",
    ]
    if final_handoff_enabled:
        lines.insert(
            -2,
            (
                "最终答案交接与业务完整性审查：当你已经完成必要工具调用并准备 Finish 时，必须调用 final_answer_handoff 工具，"
                "不要直接输出最终自然语言长答案；由该工具交给快速最终答案模型生成面向用户的回答。"
                "调用 final_answer_handoff 前必须做一次自我质询：当前查询是否只依赖了一张孤立主事实表？"
                "对于涉及对比、诊断、归因分析、多维度概况、发展建议的问题，严禁只查一张表就结案。"
                "请先判断：1）现有证据是否只能回答“是什么”，不能解释“为什么”；"
                "2）是否需要通过 ddl_search 检索并加入关联业务表白名单，例如德育/行规关联班级、值周、执勤或学生管理，"
                "请假健康关联晨午检、学期日历或公开卫生信息，设施异常关联报修/维修/资产，教师发展关联教师档案、积分、成果和政策；"
                "3）如果回答要呈现趋势、占比、同类排名或资格判断，是否已经查到支撑这些判断的关联维度。"
                "如果证据链单薄，必须暂缓调用 final_answer_handoff，主动跨方向 ddl_search 并补证。"
                "handoff_json 必须紧凑且可解析，包含 conclusion、key_facts、data_evidence、external_evidence、caveats、suggested_structure；"
                "不要包含个人敏感明细，不要编造未查到的数据。"
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


async def _stream_fast_final_answer(
    *,
    trace: Any,
    final_model: Any,
    question: str,
    handoff_payload: dict[str, Any],
    source_views: list[str],
) -> AsyncIterator[str]:
    model = agent_model_for_tool_loop(final_model)
    first_token_ms: int | None = None
    chunk_count = 0
    started = datetime.now().timestamp()
    prompt = _fast_final_answer_prompt(question=question, handoff_payload=handoff_payload, source_views=source_views)
    with trace_step(
        trace,
        "agent_native.final_fast.llm",
        {
            "model_name": _model_name(final_model),
            "question": question,
            "handoff_chars": len(json.dumps(handoff_payload, ensure_ascii=False, default=str)),
            "source_views": source_views,
        },
    ) as step:
        usage = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
        async for chunk in model.astream(
            [
                SystemMessage(content=_fast_final_answer_system_prompt()),
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
            "当你已完成必要数据查询、联网/政策检索和业务证据核验，准备给用户最终答案时调用。"
            "不要直接输出长答案；把紧凑 JSON 字符串放入 handoff_json，网关会交给最终答案模型生成自然语言回答。"
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
        "conclusion": "已完成工具证据检索，请基于 evidence_board 和 evidence_by_task 生成最终回答。",
        "key_facts": [],
        "data_evidence": evidence_by_task,
        "evidence_board": evidence_board,
        "external_evidence": [],
        "source_views": source_views,
        "caveats": [caveat] if caveat else [],
        "suggested_structure": ["结论", "关键数据", "主要变化/原因", "建议", "证据边界"],
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
        SystemMessage(content="你是亲切、专业的校园智能助手。直接回答，不要提工具、数据库、SQL或内部思考。"),
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
    model = agent_model_for_tool_loop(final_model)
    first_token_ms: int | None = None
    chunk_count = 0
    started = datetime.now().timestamp()
    answer_parts: list[str] = []
    prompt = _fast_final_answer_prompt(question=question, handoff_payload=handoff_payload, source_views=source_views)
    with trace_step(
        trace,
        "agent_native.final_fast.llm",
        {
            "model_name": _model_name(final_model),
            "question": question,
            "handoff_chars": len(json.dumps(handoff_payload, ensure_ascii=False, default=str)),
            "source_views": source_views,
        },
    ) as step:
        usage = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
        for chunk in model.stream(
            [
                SystemMessage(content=_fast_final_answer_system_prompt()),
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
            },
        )
        if usage["total_tokens"] > 0:
            _add_trace_usage(usage)
    return "".join(answer_parts)


def _fast_final_answer_system_prompt() -> str:
    return (
        "你是亲切、活泼且专业的学校智能问答助手，也就是 ReporterAgent。"
        "DataAgent 已经完成查表、查政策、联网或工具分析；你只负责把 handoff JSON 里的证据写成给用户看的最终回答。"
        "只能基于用户问题和 handoff JSON 中的证据作答，不得新增未提供的数据、链接、政策或校内事实。"
        "禁止输出数据库字段名、表名、SQL、工具名、JSON、内部推理过程或“我先查询/我需要查看”等过程性话术。"
        "回答要恢复 yili-ai-backend 那种校内业务分析风格：结论先行、数据支撑充分、Markdown 分段清楚、必要时用表格。"
        "语气要像一位懂学校业务的助手在认真汇报：温暖、清楚、有一点灵动感，可以自然使用少量 Emoji 或颜文字增强可读性，但不要卖萌过度。"
        "不要机械套模板，但默认要让用户一眼看出：总体判断、关键数据、结构/趋势/异常、建议和证据边界。"
    )


def _fast_final_answer_prompt(*, question: str, handoff_payload: dict[str, Any], source_views: list[str]) -> str:
    style_guide = _fast_final_answer_style_guide(
        question=question,
        handoff_payload=handoff_payload,
        source_views=source_views,
    )
    return "\n".join(
        [
            f"用户问题：{question}",
            f"已使用数据表/视图：{', '.join(source_views) if source_views else '未记录'}",
            "业务回答风格提示：",
            style_guide,
            "handoff JSON：",
            json.dumps(handoff_payload, ensure_ascii=False, default=str, indent=2),
            "请以学校智能问答助手的亲切、专业、可读口吻生成最终中文回答。不要输出 JSON。不要说“根据校医院反馈”等未提供来源。",
        ]
    )


def _fast_final_answer_style_guide(*, question: str, handoff_payload: dict[str, Any], source_views: list[str]) -> str:
    q = str(question or "")
    views = " ".join(str(item or "") for item in source_views)
    payload_text = json.dumps(handoff_payload, ensure_ascii=False, default=str)
    combined = f"{q} {views} {payload_text}"
    lines = [
        "1. 先用1段给出简短结论，直接回答用户问的是什么；可以用一句轻量、自然的开场，但不要空泛寒暄。",
        "2. 如果有总量、占比、排名、环比、趋势或分布，优先用 Markdown 表格或清晰项目符号呈现，不要压成一整段。",
        "3. 如果问题是概况/总体情况/趋势/对比，回答应包含：总体判断、关键指标、结构或变化解读、需要关注的点、下一步建议。",
        "4. 如果证据不完整，最后用一句话说明统计口径或边界；不要把样本、LIMIT 或阶段性数据说成全量。",
        "5. 可自然使用少量 Emoji 作为段落提示或重点标识，但不要为了活泼牺牲事实严谨性。",
    ]
    if any(token in combined for token in ["德育", "行规", "扣分", "纪律"]):
        lines.append(
            "业务补充：德育/行规类要像德育处简报：说明扣分总量、类别结构、集中班级/年级、高频事项和治理建议；注意不要混淆检查方和值周方。"
        )
    if any(token in combined for token in ["请假", "病假", "事假", "返校", "晨午检"]):
        lines.append(
            "业务补充：请假/健康类要像校务健康态势简报：说明规模、日度波动、类型结构、年级/班级集中度和需要跟进的对象或风险；涉及疾病原因要保守表达。"
        )
    if any(token in combined for token in ["教师", "积分", "成果", "申报", "职称", "荣誉", "述职", "评优"]):
        lines.append(
            "业务补充：教师发展类要像教师发展中心材料：先概括总量和定位，再按指标/成果层级/级别/等第/积分贡献分层，必要时列代表性成果表格；政策不足时写待核验。"
        )
    if any(token in combined for token in ["政策", "官网", "通知", "链接", "引用来源"]):
        lines.append(
            "业务补充：政策或联网证据必须使用证据中的来源；正文句末可用 [1]、[2] 编号引用，不要手写网址清单，OpenWebUI 会展示原生引用来源。"
        )
    if any(token in combined for token in ["图表", "图片", "PPT", "汇报图"]):
        lines.append("业务补充：视觉工具成功时，只需简要说明生成内容和读图要点，不要重复输出内部 artifact JSON。")
    return "\n".join(lines)


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
    lines: list[str] = []
    for dataset in scored[:max_tables]:
        source_schema = str(getattr(dataset, "source_schema", "") or getattr(schema_index, "source_schema", "") or "").strip()
        source_view = str(getattr(dataset, "source_view", "") or getattr(dataset, "dataset_id", "") or "").strip()
        label = str(getattr(dataset, "label", "") or source_view).strip()
        desc = str(getattr(dataset, "description", "") or getattr(dataset, "searchable_text", "") or "").strip()
        table_ref = f"{source_schema}.{source_view}" if source_schema and source_view else source_view
        if desc:
            desc = _truncate(re.sub(r"\s+", " ", desc), 120)
            lines.append(f"- {table_ref}: {label}；{desc}")
        else:
            lines.append(f"- {table_ref}: {label}")
    omitted = max(0, len(datasets) - min(len(datasets), max_tables))
    if omitted:
        lines.append(f"- ... 还有 {omitted} 张表未展示；需要时调用 list_available_tables 或 ddl_search 扩展检索。")
    return _truncate("\n".join(lines), _startup_catalog_max_chars())


def _catalog_dataset_score(dataset: Any, *, question: str) -> float:
    text = str(question or "")
    haystack = " ".join(
        [
            str(getattr(dataset, "source_view", "") or ""),
            str(getattr(dataset, "label", "") or ""),
            str(getattr(dataset, "description", "") or ""),
            str(getattr(dataset, "searchable_text", "") or ""),
        ]
    )
    score = 0.0
    for term in _catalog_terms(text):
        if term and term in haystack:
            score += 2.0 if term in str(getattr(dataset, "source_view", "") or "") else 1.0
    return score


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
