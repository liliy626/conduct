from __future__ import annotations

from typing import Any, AsyncIterator, Callable

from gateway_core.agents.base_skill import BaseAgentSkill, RuntimeContext
from gateway_core.agents.policy_only.agent_stream import stream_policy_only_agent_native
from gateway_core.agents.school_sql.school_sql_skill import _latest_question, _session_context, _skill_event_from_raw
from gateway_core.agents.universal_hub.models import SkillEvent
from gateway_core.agents.universal_hub.state import UniversalAgentState


class PolicySkill(BaseAgentSkill):
    @property
    def name(self) -> str:
        return "policy"

    async def astream(
        self,
        state: UniversalAgentState,
        ctx: RuntimeContext | dict[str, Any],
    ) -> AsyncIterator[SkillEvent]:
        session_context = _session_context(state)
        stream_fn = _stream_fn(ctx, session_context)
        async for raw_event in stream_fn(
            question=_latest_question(state),
            token=session_context.get("token") or ctx.get("token"),
            model=session_context.get("model") or ctx.get("model"),
            policy_evidence_search_fn=session_context.get("policy_evidence_search_fn")
            or ctx.get("policy_evidence_search_fn"),
            openwebui_chat_id=session_context.get("openwebui_chat_id") or ctx.get("openwebui_chat_id", ""),
            conversation_context=session_context.get("conversation_context") or ctx.get("conversation_context", ""),
        ):
            yield _skill_event_from_raw(raw_event)


def _stream_fn(ctx: dict[str, Any], session_context: dict[str, Any]) -> Callable[..., AsyncIterator[dict[str, Any]]]:
    candidate = ctx.get("stream_fn") or session_context.get("stream_fn")
    if candidate is not None:
        return candidate
    return stream_policy_only_agent_native
