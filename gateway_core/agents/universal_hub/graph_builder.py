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
from gateway_core.agents.universal_hub.supervisor_core import determine_required_outputs
from gateway_core.agents.universal_hub.state import UniversalAgentState
from gateway_core.prompts import prompt_domains


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


def _latest_user_text(state: UniversalAgentState) -> str:
    messages = list(state.get("messages", []))
    for message in reversed(messages):
        role = str(getattr(message, "type", "") or getattr(message, "role", "") or "").lower()
        if role in {"human", "user"}:
            content = getattr(message, "content", None)
            return str(content or "")
        if isinstance(message, (tuple, list)) and len(message) >= 2:
            role = str(message[0]).lower()
            if role in {"human", "user"}:
                return str(message[1])
        if isinstance(message, dict):
            role = str(message.get("role") or message.get("type") or "").lower()
            if role in {"human", "user"}:
                return str(message.get("content") or "")
    for message in reversed(messages):
        content = getattr(message, "content", None)
        if content is not None:
            return str(content)
        if isinstance(message, (tuple, list)) and len(message) >= 2:
            return str(message[1])
        if isinstance(message, dict):
            return str(message.get("content") or "")
    return ""


def _valid_sql_lineage(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    sql_hash = str(value.get("sql_hash") or "").strip()
    if len(sql_hash) != 64:
        return None
    return dict(value)


def _sql_lineages_from_sources(sources: Any) -> list[dict[str, Any]]:
    lineages: list[dict[str, Any]] = []
    if not isinstance(sources, list):
        return lineages
    for source in sources:
        if not isinstance(source, dict):
            continue
        metadata_items = source.get("metadata")
        if isinstance(metadata_items, dict):
            metadata_items = [metadata_items]
        if not isinstance(metadata_items, list):
            metadata_items = [source]
        for metadata in metadata_items:
            if not isinstance(metadata, dict):
                continue
            if metadata.get("type") != "sql_lineage" and "sql_hash" not in metadata:
                continue
            lineage = _valid_sql_lineage(metadata)
            if lineage is not None:
                lineages.append(lineage)
    return lineages


def _sql_lineages_from_event_data(data: Any) -> list[dict[str, Any]]:
    if not isinstance(data, dict):
        return []

    candidates: list[Any] = []
    for key in ("lineage_ledger", "sql_lineage", "sql_lineages", "executed_sql_lineage"):
        value = data.get(key)
        if isinstance(value, list):
            candidates.extend(value)
        elif isinstance(value, dict):
            candidates.append(value)

    payload = data.get("payload")
    if isinstance(payload, dict):
        for key in ("lineage_ledger", "sql_lineage", "sql_lineages", "executed_sql_lineage"):
            value = payload.get(key)
            if isinstance(value, list):
                candidates.extend(value)
            elif isinstance(value, dict):
                candidates.append(value)

    lineages = [lineage for item in candidates if (lineage := _valid_sql_lineage(item)) is not None]
    lineages.extend(_sql_lineages_from_sources(data.get("sources")))
    return lineages


def _runtime_sql_lineages(runtime_ctx: RuntimeContext | dict[str, Any]) -> list[dict[str, Any]]:
    if isinstance(runtime_ctx, dict):
        value = runtime_ctx.get("current_sql_lineage_cache")
    else:
        value = getattr(runtime_ctx, "current_sql_lineage_cache", None)
    if isinstance(value, dict):
        value = [value]
    if not isinstance(value, list):
        return []
    return [lineage for item in value if (lineage := _valid_sql_lineage(item)) is not None]


def _dedupe_sql_lineages(lineages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for lineage in lineages:
        sql_hash = str(lineage.get("sql_hash") or "")
        if sql_hash in seen:
            continue
        seen.add(sql_hash)
        out.append(lineage)
    return out


def _make_supervisor_node(registry: Mapping[str, SkillSpec]):
    async def supervisor_node(state: UniversalAgentState) -> Command:
        existing_required = list(
            set(state.get("required_outputs", [])) - set(prompt_domains.TEMPORARY_OUTPUT_SLOTS)
        )
        required = determine_required_outputs(_latest_user_text(state), existing_required)
        completed = list(state.get("completed_outputs", []))
        candidates = _candidate_skills(
            registry=registry,
            required_outputs=required,
            completed_outputs=completed,
        )
        if candidates == ["FINISH"]:
            return Command(update={"required_outputs": required, "candidate_skills": candidates}, goto=END)

        current_calls = int(state.get("skill_call_count", 0))
        max_calls = int(state.get("max_skill_calls", 4))
        if current_calls >= max_calls:
            return Command(
                update={
                    "required_outputs": required,
                    "candidate_skills": candidates,
                    "hub_error": "skill_budget_exhausted",
                },
                goto=END,
            )

        if not candidates:
            return Command(
                update={
                    "required_outputs": required,
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
                "required_outputs": required,
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
        sql_lineages: list[dict[str, Any]] = []

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
            if event.event_type in {"evidence", "evidence_completed"}:
                sql_lineages.extend(_sql_lineages_from_event_data(event.data))

        if content_pieces:
            messages.append(AIMessage(content="".join(content_pieces)))

        completed = list(state.get("completed_outputs", []))
        for output in sorted(spec.outputs):
            if output not in completed:
                completed.append(output)

        meta_context = dict(state.get("meta_context") or {})
        sql_lineages.extend(_runtime_sql_lineages(runtime_ctx))
        sql_lineages = _dedupe_sql_lineages(sql_lineages)
        if sql_lineages:
            meta_context["executed_sql_lineage"] = sql_lineages

        return Command(
            update={
                "messages": messages,
                "completed_outputs": completed,
                "meta_context": meta_context,
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
