from __future__ import annotations

from typing import Any, AsyncIterator, Callable

from gateway_core.agents.base_skill import BaseAgentSkill, RuntimeContext
from gateway_core.agents.universal_hub.models import SkillEvent
from gateway_core.agents.universal_hub.state import UniversalAgentState
from gateway_core.agents.school_sql.agent_stream import stream_school_sql_agent_native


class SchoolSqlSkill(BaseAgentSkill):
    @property
    def name(self) -> str:
        return "school_sql"

    async def astream(
        self,
        state: UniversalAgentState,
        ctx: RuntimeContext | dict[str, Any],
    ) -> AsyncIterator[SkillEvent]:
        session_context = _session_context(state)
        stream_fn = _stream_fn(ctx, session_context)
        async for raw_event in stream_fn(**_school_stream_kwargs(state, ctx, session_context)):
            yield _skill_event_from_raw(raw_event)


def _session_context(state: UniversalAgentState | dict[str, Any]) -> dict[str, Any]:
    value = state.get("session_context", {}) if isinstance(state, dict) else {}
    return value if isinstance(value, dict) else {}


def _stream_fn(ctx: dict[str, Any], session_context: dict[str, Any]) -> Callable[..., AsyncIterator[dict[str, Any]]]:
    candidate = ctx.get("stream_fn") or session_context.get("stream_fn")
    if candidate is not None:
        return candidate
    return stream_school_sql_agent_native


def _school_stream_kwargs(
    state: UniversalAgentState | dict[str, Any],
    ctx: dict[str, Any],
    session_context: dict[str, Any],
) -> dict[str, Any]:
    return {
        "question": _latest_question(state),
        "token": session_context.get("token") or ctx.get("token"),
        "school_scope": session_context.get("school_scope") or ctx.get("school_scope"),
        "dsn": session_context.get("dsn") or ctx.get("dsn", ""),
        "psycopg_module": session_context.get("psycopg_module") or ctx.get("psycopg_module"),
        "model": session_context.get("model") or ctx.get("model"),
        "final_model": session_context.get("final_model") or ctx.get("final_model"),
        "embedding_fn": session_context.get("embedding_fn") or ctx.get("embedding_fn"),
        "policy_evidence_search_fn": session_context.get("policy_evidence_search_fn")
        or ctx.get("policy_evidence_search_fn"),
        "openwebui_chat_id": session_context.get("openwebui_chat_id") or ctx.get("openwebui_chat_id", ""),
        "conversation_context": session_context.get("conversation_context") or ctx.get("conversation_context", ""),
        "sql_logger": session_context.get("sql_logger") or ctx.get("sql_logger"),
    }


def _latest_question(state: UniversalAgentState | dict[str, Any]) -> str:
    messages = state.get("messages", []) if isinstance(state, dict) else []
    if not messages:
        return ""
    last = messages[-1]
    content = getattr(last, "content", None)
    if content is not None:
        return str(content)
    if isinstance(last, (tuple, list)) and len(last) >= 2:
        return str(last[1])
    if isinstance(last, dict):
        return str(last.get("content") or "")
    return str(last)


def _skill_event_from_raw(raw_event: dict[str, Any]) -> SkillEvent:
    event_type = str(raw_event.get("type") or "")
    if event_type == "content":
        return SkillEvent(event_type="content", data=raw_event)
    if event_type == "sources":
        return SkillEvent(event_type="evidence", data=raw_event)
    if event_type == "artifact":
        return SkillEvent(event_type="artifact", data=raw_event)
    if event_type == "tool_start":
        return SkillEvent(event_type="tool_start", data=raw_event)
    if event_type == "tool_end":
        return SkillEvent(event_type="tool_end", data=raw_event)
    return SkillEvent(event_type="process", data=raw_event)
