from __future__ import annotations

from gateway_core.prompts.agents.contract_planner import (
    CONTRACT_PLANNER_PROMPT_VERSION,
    CONTRACT_PLANNER_SYSTEM_PROMPT_ID,
    build_contract_planner_system_prompt,
)
from gateway_core.prompts.agents.final_answer import (
    FINAL_ANSWER_PROMPT_VERSION,
    FINAL_ANSWER_USER_PROMPT_ID,
    build_final_answer_prompt,
)
from gateway_core.prompts.agents.school_sql_agent import (
    SCHOOL_SQL_AGENT_PROMPT_ID,
    SCHOOL_SQL_AGENT_PROMPT_VERSION,
    build_school_sql_agent_prompt,
)
from gateway_core.prompts.prompt_registry import (
    ANSWER_CONTEXT_PAYLOAD_PROMPT_ID,
    ANSWER_CONTEXT_PROMPT_ID,
    ANSWER_CONTEXT_PROMPT_VERSION,
    build_context_prompt,
    build_answer_style_guard_prompt,
    render_answer_style_guard_prompt,
    render_context_prompt,
)
from gateway_core.prompts.prompt_layer import PROMPT_REGISTRY, PromptBuilder, PromptRegistry, PromptTemplate
from gateway_core.prompts.rules.answer_context import (
    ANSWER_AUDIENCE_REMINDER_PROMPT_ID,
    ANSWER_AUDIENCE_REMINDER_PROMPT_VERSION,
    ANSWER_CITATION_FORMAT_PROMPT_ID,
    ANSWER_CITATION_FORMAT_PROMPT_VERSION,
    ANSWER_CONTEXT_BASE_PROMPT_ID,
    ANSWER_CONTEXT_BASE_PROMPT_VERSION,
    ANSWER_QUESTION_TYPE_PROMPT_ID,
    ANSWER_QUESTION_TYPE_PROMPT_VERSION,
    ANSWER_REQUEST_PROTOCOL_PROMPT_ID,
    ANSWER_REQUEST_PROTOCOL_PROMPT_VERSION,
    ANSWER_TEACHER_DEVELOPMENT_PROMPT_ID,
    ANSWER_TEACHER_DEVELOPMENT_PROMPT_VERSION,
    ANSWER_TITLE_EVALUATION_PROMPT_ID,
    ANSWER_TITLE_EVALUATION_PROMPT_VERSION,
)
from gateway_core.prompts.rules.answer_evidence import (
    ANSWER_COMPLEX_EVIDENCE_PROMPT_ID,
    ANSWER_EVIDENCE_BOARD_PROMPT_ID,
    ANSWER_EVIDENCE_PROMPT_VERSION,
    ANSWER_POLICY_EVIDENCE_PROMPT_ID,
)
from gateway_core.prompts.rules.answer_style import (
    ANSWER_STYLE_JSON_PROMPT_ID,
    ANSWER_STYLE_NO_DATA_PROMPT_ID,
    ANSWER_STYLE_PROMPT_VERSION,
    ANSWER_STYLE_WITH_DATA_PROMPT_ID,
)


def test_prompt_registry_resolves_explicit_and_latest_versions():
    registry = PromptRegistry()
    registry.register(PromptTemplate(prompt_id="demo.prompt", version="2026-06-01.1", renderer=lambda name: f"old:{name}"))
    registry.register(PromptTemplate(prompt_id="demo.prompt", version="2026-06-03.1", renderer=lambda name: f"new:{name}"))

    assert registry.render("demo.prompt", "2026-06-01.1", name="A").text == "old:A"
    assert registry.render("demo.prompt", name="B").text == "new:B"
    assert registry.versions("demo.prompt") == ("2026-06-01.1", "2026-06-03.1")


def test_prompt_builder_keeps_part_versions():
    registry = PromptRegistry()
    registry.register(PromptTemplate(prompt_id="part.a", version="v1", renderer=lambda: "A"))
    registry.register(PromptTemplate(prompt_id="part.b", version="v2", renderer=lambda value: f"B:{value}"))

    rendered = (
        PromptBuilder(registry)
        .add("part.a")
        .add_if(False, "part.b", value="skipped")
        .add_if(True, "part.b", value="x")
        .build(prompt_id="combined")
    )

    assert rendered.text == "A\n\nB:x"
    assert rendered.version == "part.a@v1+part.b@v2"
    assert [part.prompt_id for part in rendered.parts] == ["part.a", "part.b"]


def test_migrated_agent_prompts_are_registered_with_versions():
    assert PROMPT_REGISTRY.versions(CONTRACT_PLANNER_SYSTEM_PROMPT_ID) == (CONTRACT_PLANNER_PROMPT_VERSION,)
    assert PROMPT_REGISTRY.versions(FINAL_ANSWER_USER_PROMPT_ID) == (FINAL_ANSWER_PROMPT_VERSION,)
    assert PROMPT_REGISTRY.versions(SCHOOL_SQL_AGENT_PROMPT_ID) == (SCHOOL_SQL_AGENT_PROMPT_VERSION,)

    assert (
        PROMPT_REGISTRY.render(
            CONTRACT_PLANNER_SYSTEM_PROMPT_ID,
            CONTRACT_PLANNER_PROMPT_VERSION,
            available_tools=["time"],
        ).text
        == build_contract_planner_system_prompt(["time"])
    )
    assert (
        PROMPT_REGISTRY.render(
            FINAL_ANSWER_USER_PROMPT_ID,
            FINAL_ANSWER_PROMPT_VERSION,
            evidence_packet="证据",
        ).text
        == build_final_answer_prompt("证据")
    )
    assert (
        PROMPT_REGISTRY.render(
            SCHOOL_SQL_AGENT_PROMPT_ID,
            SCHOOL_SQL_AGENT_PROMPT_VERSION,
            available_tool_names=["ddl_search"],
            tool_contract_prompt="工具合同",
            school_id="s1",
            school_name="学校",
            source_schema="public",
            final_handoff_enabled=False,
        ).text
        == build_school_sql_agent_prompt(
            available_tool_names=["ddl_search"],
            tool_contract_prompt="工具合同",
            school_id="s1",
            school_name="学校",
            source_schema="public",
            final_handoff_enabled=False,
        )
    )


def test_answer_context_prompt_is_registered_and_renderable():
    payload = {
        "query_mode": "ddl_react_agent",
        "policy_evidence_required": True,
        "web_evidence": [{"title": "官网通知", "url": "https://example.edu/notice", "snippet": "通知摘要"}],
        "evidence_board": {"tasks": []},
        "rows": [{"年级": "七年级", "人数": 12}],
    }

    rendered = render_context_prompt(payload=payload, question="学校职称成果整体情况", viewer_role="校长")

    assert PROMPT_REGISTRY.versions(ANSWER_CONTEXT_PROMPT_ID) == (ANSWER_CONTEXT_PROMPT_VERSION,)
    assert rendered.prompt_id == ANSWER_CONTEXT_PROMPT_ID
    assert rendered.version == ANSWER_CONTEXT_PROMPT_VERSION
    assert rendered.text == build_context_prompt(payload=payload, question="学校职称成果整体情况", viewer_role="校长")
    part_ids = [part.prompt_id for part in rendered.parts]
    assert part_ids[0] == "answers.context.agent_label"
    assert ANSWER_REQUEST_PROTOCOL_PROMPT_ID in part_ids
    assert ANSWER_CONTEXT_BASE_PROMPT_ID in part_ids
    assert ANSWER_QUESTION_TYPE_PROMPT_ID in part_ids
    assert ANSWER_CITATION_FORMAT_PROMPT_ID in part_ids
    assert ANSWER_POLICY_EVIDENCE_PROMPT_ID in part_ids
    assert ANSWER_COMPLEX_EVIDENCE_PROMPT_ID in part_ids
    assert ANSWER_EVIDENCE_BOARD_PROMPT_ID in part_ids
    assert ANSWER_TEACHER_DEVELOPMENT_PROMPT_ID in part_ids
    assert ANSWER_TITLE_EVALUATION_PROMPT_ID in part_ids
    assert ANSWER_AUDIENCE_REMINDER_PROMPT_ID in part_ids
    assert part_ids[-1] == ANSWER_CONTEXT_PAYLOAD_PROMPT_ID
    assert PROMPT_REGISTRY.versions(ANSWER_CONTEXT_BASE_PROMPT_ID) == (ANSWER_CONTEXT_BASE_PROMPT_VERSION,)
    assert PROMPT_REGISTRY.versions(ANSWER_REQUEST_PROTOCOL_PROMPT_ID) == (ANSWER_REQUEST_PROTOCOL_PROMPT_VERSION,)
    assert PROMPT_REGISTRY.versions(ANSWER_QUESTION_TYPE_PROMPT_ID) == (ANSWER_QUESTION_TYPE_PROMPT_VERSION,)
    assert PROMPT_REGISTRY.versions(ANSWER_CITATION_FORMAT_PROMPT_ID) == (ANSWER_CITATION_FORMAT_PROMPT_VERSION,)
    assert PROMPT_REGISTRY.versions(ANSWER_POLICY_EVIDENCE_PROMPT_ID) == (ANSWER_EVIDENCE_PROMPT_VERSION,)
    assert PROMPT_REGISTRY.versions(ANSWER_COMPLEX_EVIDENCE_PROMPT_ID) == (ANSWER_EVIDENCE_PROMPT_VERSION,)
    assert PROMPT_REGISTRY.versions(ANSWER_EVIDENCE_BOARD_PROMPT_ID) == (ANSWER_EVIDENCE_PROMPT_VERSION,)
    assert PROMPT_REGISTRY.versions(ANSWER_TEACHER_DEVELOPMENT_PROMPT_ID) == (ANSWER_TEACHER_DEVELOPMENT_PROMPT_VERSION,)
    assert PROMPT_REGISTRY.versions(ANSWER_TITLE_EVALUATION_PROMPT_ID) == (ANSWER_TITLE_EVALUATION_PROMPT_VERSION,)
    assert PROMPT_REGISTRY.versions(ANSWER_AUDIENCE_REMINDER_PROMPT_ID) == (ANSWER_AUDIENCE_REMINDER_PROMPT_VERSION,)
    assert "检索结果(JSON)" in rendered.text
    assert "学校职称成果整体情况" in rendered.text
    assert "最终请求层级说明" in rendered.text
    assert "轻量回答提示" in rendered.text
    assert "引用格式要求" in rendered.text
    assert "政策证据回答要求" in rendered.text
    assert "复杂问题的回答要求" in rendered.text
    assert "Evidence Board 回答要求" in rendered.text
    assert "教师发展成果表达要求" in rendered.text
    assert "职称判断要求" in rendered.text
    assert "受众提醒：以校长视角" in rendered.text


def test_answer_style_guard_fragments_are_registered_and_compatible():
    cases = [
        (True, False, ANSWER_STYLE_JSON_PROMPT_ID, "直接输出合法 JSON"),
        (False, True, ANSWER_STYLE_WITH_DATA_PROMPT_ID, "当前数据上下文"),
        (False, False, ANSWER_STYLE_NO_DATA_PROMPT_ID, "不要编造数据"),
    ]

    for wants_json, has_data_context, prompt_id, expected in cases:
        rendered = render_answer_style_guard_prompt(wants_json, has_data_context)

        assert PROMPT_REGISTRY.versions(prompt_id) == (ANSWER_STYLE_PROMPT_VERSION,)
        assert rendered.prompt_id == prompt_id
        assert rendered.version == ANSWER_STYLE_PROMPT_VERSION
        assert rendered.text == build_answer_style_guard_prompt(wants_json, has_data_context)
        assert expected in rendered.text
