from __future__ import annotations

import os
from datetime import datetime
from typing import Any, AsyncIterator, Callable

from langchain_core.messages import HumanMessage
from langgraph.checkpoint.memory import MemorySaver
from langgraph.prebuilt import create_react_agent

from gateway_core.agents.streaming.langgraph_event_stream import record_langgraph_event_as_trace_step
from gateway_core.tools.langchain_tools import build_langchain_agent_tools
from gateway_core.conversation.threading import build_langgraph_thread_id
from gateway_core.agents.school_sql.agent_model import agent_model_for_tool_loop
from gateway_core.agents.school_sql.agent_stream import (
    _chunk_content,
    _event_asset_markdown,
    _event_citation_sources,
    _event_visible_text,
    _hash_token,
    _merge_citation_sources,
    _messages_from_event,
    _openwebui_sources_from_tool_sources,
)
from gateway_core.infra.api_keys import current_api_key_record, is_policy_record
from gateway_core.school.trace import finish_trace, new_school_trace, set_step_output, trace_step


_POLICY_ONLY_AGENT_MEMORY = MemorySaver()


async def stream_policy_only_agent_native(
    *,
    question: str,
    token: str | None,
    model: Any,
    policy_evidence_search_fn: Callable[[str], list[dict[str, Any]]] | None = None,
    openwebui_chat_id: str = "",
    conversation_context: str = "",
) -> AsyncIterator[dict[str, Any]]:
    """Run a policy-only ReAct Agent for policy API keys.

    This is not the school question router. It deliberately has no sql_db_query
    or DDL tools, so policy-only API keys can search official policy, web, time,
    chart/image/slide as needed without touching school data.
    """
    trace = new_school_trace(question)
    school_id = "official_policy"
    try:
        if trace is not None:
            trace.school_id = school_id

        tool_list = build_langchain_agent_tools(
            school_id=school_id,
            trace=trace,
            policy_evidence_search_fn=policy_evidence_search_fn,
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
            prompt=_policy_only_agent_prompt(),
            checkpointer=_POLICY_ONLY_AGENT_MEMORY if memory_enabled else None,
            version="v2",
        )
        config: dict[str, Any] = {"recursion_limit": _recursion_limit()}
        if memory_enabled:
            config["configurable"] = {"thread_id": thread_id}

        with trace_step(
            trace,
            "policy_only_agent.start",
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
                    "tool_names": [getattr(tool, "name", "") for tool in tool_list],
                    "tool_count": len(tool_list),
                },
            )

        yield {"type": "process", "text": "我会用政策库、联网搜索和时间工具核实信息，再整合成带出处的回答。\n"}
        latest_messages: list[Any] = []
        citation_sources: list[dict[str, Any]] = []
        async for event in agent.astream_events(
            {"messages": [HumanMessage(content=_enhanced_policy_content(question=question, conversation_context=conversation_context))]},
            config=config,
            version="v2",
        ):
            record_langgraph_event_as_trace_step(trace, event, prefix="policy_only_agent.langgraph")
            _merge_citation_sources(citation_sources, _event_citation_sources(event))
            event_text = _event_visible_text(event)
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
            if event.get("event") == "on_chat_model_stream":
                content = _chunk_content(event)
                if content:
                    yield {"type": "content", "text": content}
            messages = _messages_from_event(event)
            if messages:
                latest_messages = messages
        openwebui_sources = _openwebui_sources_from_tool_sources(citation_sources)
        if openwebui_sources:
            yield {"type": "sources", "sources": openwebui_sources}

        with trace_step(trace, "context.build", {"query_mode": "policy_only_agent_native"}) as step:
            set_step_output(
                step,
                {
                    "context_source": "official_policy.policy_only_agent_native",
                    "payload_summary": {
                        "query_mode": "policy_only_agent_native",
                        "answer_mode": "policy_only_agent",
                        "message_count": len(latest_messages),
                        "thread_id": thread_id if memory_enabled else "",
                        "trace_id": trace.trace_id if trace is not None else "",
                    },
                },
            )
        finish_trace(trace)
    except Exception as exc:
        with trace_step(trace, "policy_only_agent.error", {"question": question}) as step:
            set_step_output(step, {"error": str(exc)})
        finish_trace(trace)
        yield {"type": "content", "text": f"抱歉，政策助手查询链路遇到错误：{exc}"}


def policy_only_agent_enabled_for_token(token: str | None) -> bool:
    clean_token = str(token or "").strip()
    if not clean_token:
        return False
    record = current_api_key_record()
    if record is not None:
        return is_policy_record(record) and str(os.getenv("POLICY_AGENT_NATIVE_ENABLED", "1") or "1").strip().lower() in {
            "1",
            "true",
            "yes",
            "on",
        }
    allowed = {
        item.strip()
        for item in (os.getenv("GATEWAY_POLICY_API_KEYS", "") or os.getenv("POLICY_API_KEYS", "")).replace(";", ",").split(",")
        if item.strip()
    }
    if clean_token not in allowed:
        return False
    return str(os.getenv("POLICY_AGENT_NATIVE_ENABLED", "1") or "1").strip().lower() in {"1", "true", "yes", "on"}


def _policy_only_agent_prompt() -> str:
    return "\n".join(
        [
            "你是 policy-only 政策检索与公开信息核验 Agent。",
            "你没有学校数据库查询权限，不能调用或编造 sql_db_query；如问题需要学校内部数据，应说明当前 key 只能查政策/公开信息。",
            "可用工具按实际绑定为准；只调用可用工具。",
            "只有用户明确要求时才调用 chart、generate_image_tool 或 slide；工具输入中的数字、日期、来源必须来自已检索证据。",
            "用户说“搜索、最新、近期、动态、通知、链接、出处、官网、网页可以引用”时，必须优先调用 web_search；涉及政策依据、职称、荣誉、申报条件、年限、评审主体、一票否决等规则时，必须调用 official_policy_search。",
            "政策、通知和公开网页证据有强时效性；检索到多条结果时，必须优先考虑发布时间、更新时间和用户问题中的时间范围。",
            "如果用户问“最近、近期、最新、近三年、本年度”等，必须优先选择时间更近、来源更权威、主题更贴合的结果；旧政策、旧通知、旧网页只能作为背景，不得覆盖更新证据。",
            "当检索结果很多时，排序优先级为：官方来源 > 发布时间较新 > 与问题主题精确匹配 > 内容完整度；不要因为关键词命中就把招聘公告、无关新闻、泛泛动态混入教研、培训、课题通知回答。",
            "如果检索结果时间跨度较大，回答中要说明最新可见证据是什么、较早证据仅作参考；如果结果没有发布时间，要标注“未提供发布时间”，不能把它当作最新依据。",
            "如果 web_search 返回结果为空或工具不可用，要明确说明未检索到实时网页证据，不能假装已经联网。",
            "回答中的政策名称、发布时间、链接、文号、来源必须来自工具返回；不能编造。",
        ]
    )


def _enhanced_policy_content(*, question: str, conversation_context: str = "") -> str:
    now = datetime.now()
    parts = [
        f"【当前系统参照时间】: {now.strftime('%Y-%m-%d %H:%M:%S')} ({now.strftime('%A')})",
        "【权限边界】: 当前为 policy-only agent，只能使用 official_policy_search、web_search、time 等非学校数据库工具。",
    ]
    if str(conversation_context or "").strip():
        parts.append(
            "【本轮 OpenWebUI 会话上下文】:\n"
            f"{conversation_context[:6000]}\n"
            "请用它理解追问指代；政策和公开信息事实仍必须通过工具重新核验。"
        )
    parts.append(f"【用户原始问题】: {question}")
    return "\n\n".join(parts)


def _recursion_limit() -> int:
    try:
        return max(10, min(int(os.getenv("POLICY_AGENT_NATIVE_RECURSION_LIMIT", "45") or "45"), 80))
    except Exception:
        return 45
