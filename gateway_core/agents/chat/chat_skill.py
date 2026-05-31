from __future__ import annotations

from typing import Any, AsyncIterator

from gateway_core.agents.base_skill import BaseAgentSkill, RuntimeContext
from gateway_core.agents.school_sql.school_sql_skill import _latest_question
from gateway_core.agents.universal_hub.models import SkillEvent
from gateway_core.agents.universal_hub.state import UniversalAgentState


class ChatSkill(BaseAgentSkill):
    @property
    def name(self) -> str:
        return "chat"

    async def astream(
        self,
        state: UniversalAgentState,
        ctx: RuntimeContext | dict[str, Any],
    ) -> AsyncIterator[SkillEvent]:
        stream_fn = ctx.get("chat_stream_fn") if isinstance(ctx, dict) else None
        if stream_fn is not None:
            async for raw_event in stream_fn(question=_latest_question(state), state=state, ctx=ctx):
                event_type = str(raw_event.get("type") or "content")
                yield SkillEvent(event_type="process" if event_type == "process" else "content", data=raw_event)
            return
        yield SkillEvent(event_type="content", data={"type": "content", "text": _latest_question(state)})
