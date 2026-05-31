from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Sequence

from langchain_core.messages import BaseMessage

from gateway_core.agents.base_skill import RuntimeContext
from gateway_core.agents.universal_hub.state import UniversalAgentState


@dataclass(frozen=True)
class LiveReplaySandboxContext:
    """State/runtime pair for offline live Skill replay.

    Serializable replay state stays in ``state``. Non-serializable runtime
    dependencies, such as model clients and DB modules, stay in ``runtime_ctx``.
    """

    state: UniversalAgentState
    runtime_ctx: RuntimeContext


class MockFastAPIRequestHandle:
    async def is_disconnected(self) -> bool:
        return False


class LiveInjectionProtocol:
    @staticmethod
    def prepare_sandbox(
        *,
        school_id: str,
        schema_name: str,
        thread_id: str,
        turn_id: int,
        history_messages: Sequence[BaseMessage],
        required_outputs: Sequence[str],
        runtime_overrides: dict[str, Any] | None = None,
    ) -> LiveReplaySandboxContext:
        state: UniversalAgentState = {
            "messages": list(history_messages),
            "session_context": {
                "school_id": str(school_id or ""),
                "schema_name": str(schema_name or ""),
                "thread_id": str(thread_id or ""),
                "turn_id": int(turn_id),
                "is_shadow_live_replay": True,
            },
            "active_skill": "school_sql",
            "required_outputs": list(required_outputs),
            "completed_outputs": [],
            "evidence_board_id": f"shadow_evb_{thread_id}_{turn_id}",
            "evidence_refs": [],
            "artifact_refs": [],
            "visited_skills": [],
            "skill_call_count": 0,
            "max_skill_calls": 1,
        }

        runtime_ctx = RuntimeContext()
        runtime_ctx["request_handle"] = MockFastAPIRequestHandle()
        for key, value in (runtime_overrides or {}).items():
            runtime_ctx[str(key)] = value
        return LiveReplaySandboxContext(state=state, runtime_ctx=runtime_ctx)
