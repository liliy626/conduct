from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from langchain_core.callbacks.manager import adispatch_custom_event
from langchain_core.messages import AIMessage
from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command

from gateway_core.agents.base_skill import RuntimeContext
from gateway_core.agents.universal_hub.models import SkillEvent, SkillSpec
from gateway_core.agents.universal_hub.registry import SKILL_REGISTRY
from gateway_core.agents.universal_hub.state import UniversalAgentState


def _candidate_skills(
    *,
    registry: Mapping[str, SkillSpec],
    required_outputs: list[str],
    completed_outputs: list[str],
) -> list[str]:
    missing = frozenset(required_outputs).difference(completed_outputs)
    if not missing:
        return ["FINISH"]
    return [name for name, spec in registry.items() if spec.outputs.intersection(missing)]


def _runtime_ctx_from_config(config: RunnableConfig | None) -> RuntimeContext | dict[str, Any]:
    configurable = (config or {}).get("configurable") or {}
    runtime_ctx = configurable.get("runtime_ctx")
    if runtime_ctx is None:
        return RuntimeContext()
    return runtime_ctx


def _text_from_event(event: SkillEvent) -> str:
    data = event.data
    if isinstance(data, dict):
        return str(data.get("text") or data.get("content") or "")
    return str(data)


def _make_supervisor_node(registry: Mapping[str, SkillSpec]):
    async def supervisor_node(state: UniversalAgentState) -> Command:
        required = list(state.get("required_outputs", []))
        completed = list(state.get("completed_outputs", []))
        candidates = _candidate_skills(
            registry=registry,
            required_outputs=required,
            completed_outputs=completed,
        )
        if candidates == ["FINISH"]:
            return Command(update={"candidate_skills": candidates}, goto=END)

        current_calls = int(state.get("skill_call_count", 0))
        max_calls = int(state.get("max_skill_calls", 4))
        if current_calls >= max_calls:
            return Command(
                update={
                    "candidate_skills": candidates,
                    "hub_error": "skill_budget_exhausted",
                },
                goto=END,
            )

        if not candidates:
            return Command(
                update={
                    "candidate_skills": [],
                    "hub_error": "no_candidate_skill",
                },
                goto=END,
            )

        # Phase 2F deliberately stays deterministic. LLM tie-breaking can be
        # introduced only after graph-level isolation tests prove stable.
        active_skill = candidates[0]
        visited = list(state.get("visited_skills", []))
        visited.append(active_skill)
        return Command(
            update={
                "active_skill": active_skill,
                "candidate_skills": candidates,
                "visited_skills": visited,
                "skill_call_count": current_calls + 1,
            },
            goto="skill_runner_node",
        )

    return supervisor_node


def _make_skill_runner_node(registry: Mapping[str, SkillSpec]):
    async def skill_runner_node(state: UniversalAgentState, config: RunnableConfig) -> Command:
        active_skill = state.get("active_skill")
        if not active_skill or active_skill not in registry:
            return Command(update={"hub_error": "unknown_active_skill"}, goto=END)

        spec = registry[active_skill]
        skill = spec.skill_cls()
        runtime_ctx = _runtime_ctx_from_config(config)
        messages = list(state.get("messages", []))
        content_pieces: list[str] = []

        async for event in skill.astream(state, runtime_ctx):
            await adispatch_custom_event(
                "skill_stream_chunk",
                {"event_type": event.event_type, "data": event.data},
                config=config,
            )
            if event.event_type == "content":
                text = _text_from_event(event)
                if text:
                    content_pieces.append(text)

        if content_pieces:
            messages.append(AIMessage(content="".join(content_pieces)))

        completed = list(state.get("completed_outputs", []))
        for output in sorted(spec.outputs):
            if output not in completed:
                completed.append(output)

        return Command(
            update={
                "messages": messages,
                "completed_outputs": completed,
            },
            goto="supervisor_node",
        )

    return skill_runner_node


def compile_universal_hub_graph(skill_registry: Mapping[str, SkillSpec] | None = None):
    """Compile the isolated Phase 2F two-node universal hub graph.

    This graph is intentionally not wired into any HTTP route. Runtime-only
    objects must be provided through ``config["configurable"]["runtime_ctx"]``.
    """

    registry = skill_registry or SKILL_REGISTRY
    workflow = StateGraph(UniversalAgentState)
    workflow.add_node("supervisor_node", _make_supervisor_node(registry))
    workflow.add_node("skill_runner_node", _make_skill_runner_node(registry))
    workflow.add_edge(START, "supervisor_node")
    return workflow.compile()
