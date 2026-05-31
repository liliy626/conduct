from __future__ import annotations

import ast
import asyncio
import inspect
import textwrap
from typing import Any, AsyncIterator

from langchain_core.messages import AIMessage, HumanMessage

from gateway_core.agents.base_skill import BaseAgentSkill, RuntimeContext
from gateway_core.agents.universal_hub.models import SkillEvent, SkillSpec


class _FakeDataSkill(BaseAgentSkill):
    calls: list[dict[str, Any]] = []

    @property
    def name(self) -> str:
        return "fake_data"

    async def astream(
        self,
        state: dict[str, Any],
        ctx: RuntimeContext | dict[str, Any],
    ) -> AsyncIterator[SkillEvent]:
        self.calls.append({"state": state, "ctx": ctx})
        assert ctx["runtime_marker"] == "ctx-only"
        assert "runtime_marker" not in state
        assert "runtime_ctx" not in state
        assert "runtime_marker" not in state.get("session_context", {})
        yield SkillEvent(event_type="content", data={"text": "fake data evidence ready"})


class _ExplodingSkill(BaseAgentSkill):
    @property
    def name(self) -> str:
        return "explode"

    async def astream(
        self,
        state: dict[str, Any],
        ctx: RuntimeContext | dict[str, Any],
    ) -> AsyncIterator[SkillEvent]:
        raise AssertionError("skill should not have been dispatched")
        yield SkillEvent(event_type="content", data={"text": "unreachable"})


class _ChunkyDataSkill(BaseAgentSkill):
    @property
    def name(self) -> str:
        return "chunky_data"

    async def astream(
        self,
        state: dict[str, Any],
        ctx: RuntimeContext | dict[str, Any],
    ) -> AsyncIterator[SkillEvent]:
        for index in range(100):
            yield SkillEvent(event_type="content", data={"text": f"chunk-{index};"})


class _LineageDataSkill(BaseAgentSkill):
    @property
    def name(self) -> str:
        return "lineage_data"

    async def astream(
        self,
        state: dict[str, Any],
        ctx: RuntimeContext | dict[str, Any],
    ) -> AsyncIterator[SkillEvent]:
        yield SkillEvent(
            event_type="evidence_completed",
            data={
                "lineage_ledger": [
                    {
                        "sql_hash": "a" * 64,
                        "tables_used": ["test_schema.行规检查_行规检查"],
                        "row_count": 11,
                        "query_purpose": "眼保健操纪律分析",
                    }
                ]
            },
        )
        yield SkillEvent(event_type="content", data={"text": "discipline data ready"})


class _ImageSkillNeedsLineage(BaseAgentSkill):
    seen_lineage: list[dict[str, Any]] = []

    @property
    def name(self) -> str:
        return "image_generator"

    async def astream(
        self,
        state: dict[str, Any],
        ctx: RuntimeContext | dict[str, Any],
    ) -> AsyncIterator[SkillEvent]:
        lineages = (state.get("meta_context") or {}).get("executed_sql_lineage") or []
        type(self).seen_lineage = list(lineages)
        assert type(self).seen_lineage
        assert type(self).seen_lineage[-1]["sql_hash"] == "a" * 64
        yield SkillEvent(event_type="content", data={"text": "image artifact ready"})


def _fake_registry(skill_cls: type[BaseAgentSkill] = _FakeDataSkill) -> dict[str, SkillSpec]:
    return {
        "fake_data": SkillSpec(
            name="fake_data",
            skill_cls=skill_cls,
            outputs=frozenset({"data_evidence"}),
            tools=frozenset({"fake_query"}),
            supports_stream=True,
            default_model_role="data_agent",
        )
    }


def _fake_multimodal_registry() -> dict[str, SkillSpec]:
    return {
        "fake_data": SkillSpec(
            name="fake_data",
            skill_cls=_LineageDataSkill,
            outputs=frozenset({"data_evidence"}),
            tools=frozenset({"fake_query"}),
            supports_stream=True,
            default_model_role="data_agent",
        ),
        "image_generator": SkillSpec(
            name="image_generator",
            skill_cls=_ImageSkillNeedsLineage,
            outputs=frozenset({"image_artifact"}),
            tools=frozenset({"image_generation"}),
            supports_stream=True,
            default_model_role="visual_agent",
        ),
    }


def test_visual_query_adds_image_artifact_to_required_outputs() -> None:
    from gateway_core.agents.universal_hub.supervisor_core import determine_required_outputs

    required = determine_required_outputs(
        "帮我统计本周眼保健操纪律最差的年级，顺便画一张警示感管理插图",
        ["data_evidence"],
    )

    assert required == ["data_evidence", "image_artifact"]
    assert determine_required_outputs("请画一张图", required).count("image_artifact") == 1


def test_non_visual_followup_drops_stale_image_artifact_requirement() -> None:
    from gateway_core.agents.universal_hub.supervisor_core import determine_required_outputs

    required = determine_required_outputs("那上周呢？", ["data_evidence", "image_artifact"])

    assert required == ["data_evidence"]


def test_visual_keywords_are_loaded_from_prompt_domains(monkeypatch) -> None:
    from gateway_core.prompts import prompt_domains
    import gateway_core.agents.universal_hub.supervisor_core as supervisor_core

    monkeypatch.setattr(
        prompt_domains,
        "REQUIRED_OUTPUT_RULES",
        (
            prompt_domains.RequiredOutputRule(
                keywords=("画布测试词",),
                outputs=("data_evidence", "image_artifact"),
            ),
        ),
    )

    assert supervisor_core.determine_required_outputs("请生成画布测试词", ["data_evidence"]) == [
        "data_evidence",
        "image_artifact",
    ]


def test_temporary_output_slots_are_loaded_from_prompt_domains(monkeypatch) -> None:
    from gateway_core.prompts import prompt_domains
    import gateway_core.agents.universal_hub.supervisor_core as supervisor_core

    monkeypatch.setattr(prompt_domains, "TEMPORARY_OUTPUT_SLOTS", frozenset({"custom_artifact"}))

    required = supervisor_core.determine_required_outputs("那上周呢？", ["data_evidence", "custom_artifact"])

    assert required == ["data_evidence"]


def test_multimodal_temporary_slots_are_declared_outside_graph_builder() -> None:
    from gateway_core.prompts import prompt_domains

    assert prompt_domains.MULTIMODAL_TEMPORARY_SLOTS == prompt_domains.TEMPORARY_OUTPUT_SLOTS


def test_determine_required_outputs_has_no_statement_level_for_or_if() -> None:
    from gateway_core.agents.universal_hub.supervisor_core import determine_required_outputs

    tree = ast.parse(textwrap.dedent(inspect.getsource(determine_required_outputs)))

    assert not any(isinstance(node, (ast.For, ast.If)) for node in ast.walk(tree))
    assert not any(
        isinstance(node, (ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp, ast.comprehension))
        for node in ast.walk(tree)
    )


def test_graph_builder_has_no_business_specific_skill_or_slot_literals() -> None:
    import gateway_core.agents.universal_hub.graph_builder as graph_builder

    tree = ast.parse(inspect.getsource(graph_builder))
    string_constants = {node.value for node in ast.walk(tree) if isinstance(node, ast.Constant) and isinstance(node.value, str)}

    assert "image_artifact" not in string_constants
    assert "school_sql" not in string_constants


def test_universal_hub_graph_dispatches_missing_output_to_registered_skill() -> None:
    from gateway_core.agents.universal_hub.graph_builder import compile_universal_hub_graph

    _FakeDataSkill.calls.clear()
    graph = compile_universal_hub_graph(skill_registry=_fake_registry())

    final_state = asyncio.run(
        graph.ainvoke(
            {
                "messages": [HumanMessage(content="查一下数据")],
                "session_context": {"school_id": "sch_test", "schema_name": "test_schema"},
                "required_outputs": ["data_evidence"],
                "completed_outputs": [],
                "evidence_refs": [],
                "artifact_refs": [],
                "visited_skills": [],
                "skill_call_count": 0,
                "max_skill_calls": 3,
            },
            config={"configurable": {"runtime_ctx": RuntimeContext(runtime_marker="ctx-only")}},
        )
    )

    assert final_state["completed_outputs"] == ["data_evidence"]
    assert final_state["visited_skills"] == ["fake_data"]
    assert final_state["skill_call_count"] == 1
    assert isinstance(final_state["messages"][-1], AIMessage)
    assert final_state["messages"][-1].content == "fake data evidence ready"
    assert _FakeDataSkill.calls
    assert "runtime_ctx" not in final_state
    assert "runtime_marker" not in final_state["session_context"]


def test_universal_hub_graph_finishes_without_dispatch_when_outputs_are_complete() -> None:
    from gateway_core.agents.universal_hub.graph_builder import compile_universal_hub_graph

    graph = compile_universal_hub_graph(skill_registry=_fake_registry(_ExplodingSkill))

    final_state = asyncio.run(
        graph.ainvoke(
            {
                "messages": [HumanMessage(content="已经有证据了")],
                "session_context": {"school_id": "sch_test"},
                "required_outputs": ["data_evidence"],
                "completed_outputs": ["data_evidence"],
                "visited_skills": [],
                "skill_call_count": 0,
                "max_skill_calls": 3,
            },
            config={"configurable": {"runtime_ctx": RuntimeContext(runtime_marker="ctx-only")}},
        )
    )

    assert final_state["completed_outputs"] == ["data_evidence"]
    assert final_state["visited_skills"] == []
    assert final_state["skill_call_count"] == 0
    assert "hub_error" not in final_state


def test_universal_hub_graph_stops_before_dispatch_when_skill_budget_is_exhausted() -> None:
    from gateway_core.agents.universal_hub.graph_builder import compile_universal_hub_graph

    graph = compile_universal_hub_graph(skill_registry=_fake_registry(_ExplodingSkill))

    final_state = asyncio.run(
        graph.ainvoke(
            {
                "messages": [HumanMessage(content="还缺证据")],
                "session_context": {"school_id": "sch_test"},
                "required_outputs": ["data_evidence"],
                "completed_outputs": [],
                "visited_skills": ["fake_data", "fake_data"],
                "skill_call_count": 2,
                "max_skill_calls": 2,
            },
            config={"configurable": {"runtime_ctx": RuntimeContext(runtime_marker="ctx-only")}},
        )
    )

    assert final_state["completed_outputs"] == []
    assert final_state["visited_skills"] == ["fake_data", "fake_data"]
    assert final_state["skill_call_count"] == 2
    assert final_state["hub_error"] == "skill_budget_exhausted"


def test_skill_runner_collapses_many_content_chunks_into_one_message() -> None:
    from gateway_core.agents.universal_hub.graph_builder import compile_universal_hub_graph

    graph = compile_universal_hub_graph(skill_registry=_fake_registry(_ChunkyDataSkill))

    final_state = asyncio.run(
        graph.ainvoke(
            {
                "messages": [HumanMessage(content="查一下数据")],
                "session_context": {"school_id": "sch_test", "schema_name": "test_schema"},
                "required_outputs": ["data_evidence"],
                "completed_outputs": [],
                "evidence_refs": [],
                "artifact_refs": [],
                "visited_skills": [],
                "skill_call_count": 0,
                "max_skill_calls": 3,
            },
            config={"configurable": {"runtime_ctx": RuntimeContext(runtime_marker="ctx-only")}},
        )
    )

    assert len(final_state["messages"]) == 2
    assert isinstance(final_state["messages"][-1], AIMessage)
    assert final_state["messages"][-1].content.startswith("chunk-0;chunk-1;")
    assert "chunk-99;" in final_state["messages"][-1].content


def test_visual_query_runs_data_then_image_and_passes_sql_lineage() -> None:
    from gateway_core.agents.universal_hub.graph_builder import compile_universal_hub_graph

    _ImageSkillNeedsLineage.seen_lineage = []
    graph = compile_universal_hub_graph(skill_registry=_fake_multimodal_registry())

    final_state = asyncio.run(
        graph.ainvoke(
            {
                "messages": [
                    HumanMessage(
                        content="帮我统计本周眼保健操纪律最差的年级，顺便画一张警示感管理插图"
                    )
                ],
                "session_context": {"school_id": "sch_test", "schema_name": "test_schema"},
                "required_outputs": ["data_evidence"],
                "completed_outputs": [],
                "evidence_refs": [],
                "artifact_refs": [],
                "visited_skills": [],
                "skill_call_count": 0,
                "max_skill_calls": 4,
                "meta_context": {},
                "multimodal_artifacts": {},
            },
            config={"configurable": {"runtime_ctx": RuntimeContext(runtime_marker="ctx-only")}},
        )
    )

    assert final_state["required_outputs"] == ["data_evidence", "image_artifact"]
    assert final_state["completed_outputs"] == ["data_evidence", "image_artifact"]
    assert final_state["visited_skills"] == ["fake_data", "image_generator"]
    assert final_state["meta_context"]["executed_sql_lineage"][-1]["sql_hash"] == "a" * 64
    assert _ImageSkillNeedsLineage.seen_lineage[-1]["row_count"] == 11


def test_non_visual_followup_does_not_dispatch_stale_image_artifact() -> None:
    from gateway_core.agents.universal_hub.graph_builder import compile_universal_hub_graph

    _ImageSkillNeedsLineage.seen_lineage = []
    graph = compile_universal_hub_graph(skill_registry=_fake_multimodal_registry())

    final_state = asyncio.run(
        graph.ainvoke(
            {
                "messages": [HumanMessage(content="那上周呢？")],
                "session_context": {"school_id": "sch_test", "schema_name": "test_schema"},
                "required_outputs": ["data_evidence", "image_artifact"],
                "completed_outputs": [],
                "evidence_refs": [],
                "artifact_refs": [],
                "visited_skills": [],
                "skill_call_count": 0,
                "max_skill_calls": 4,
                "meta_context": {},
                "multimodal_artifacts": {},
            },
            config={"configurable": {"runtime_ctx": RuntimeContext(runtime_marker="ctx-only")}},
        )
    )

    assert final_state["required_outputs"] == ["data_evidence"]
    assert final_state["completed_outputs"] == ["data_evidence"]
    assert final_state["visited_skills"] == ["fake_data"]
    assert _ImageSkillNeedsLineage.seen_lineage == []
