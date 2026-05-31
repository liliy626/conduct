from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, AsyncIterator

from gateway_core.agents.universal_hub.models import SkillEvent
from gateway_core.agents.universal_hub.state import UniversalAgentState


class RuntimeContext(dict):
    """Runtime-only context for non-serializable objects.

    FastAPI Request handles, emitters, trace objects, model clients, and test
    doubles belong here, never in UniversalAgentState.
    """


class BaseAgentSkill(ABC):
    @property
    @abstractmethod
    def name(self) -> str:
        raise NotImplementedError

    @abstractmethod
    async def astream(
        self,
        state: UniversalAgentState,
        ctx: RuntimeContext | dict[str, Any],
    ) -> AsyncIterator[SkillEvent]:
        raise NotImplementedError

    async def arun(self, state: UniversalAgentState, ctx: RuntimeContext | dict[str, Any]) -> dict[str, Any]:
        events: list[SkillEvent] = []
        async for event in self.astream(state, ctx):
            events.append(event)
        return {"events": [event.model_dump() for event in events]}
