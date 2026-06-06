from __future__ import annotations

import json

from gateway_core.agents.contracts.output_contracts import (
    ANSWER_MODES,
    ARTIFACT_OUTPUTS,
    FINAL_HANDOFF_FIELDS,
    OPTIONAL_TOOLS,
    OUTPUT_CONTRACT_VERSION,
    REQUIRED_OUTPUTS,
    ROUTES,
    final_answer_handoff_tool_description,
    per_turn_contract_plan_schema_text,
)
from gateway_core.agents.contracts.models import PerTurnContractPlan
from gateway_core.agents.contracts.inter_agent_state import build_inter_agent_state
from gateway_core.agents.contracts.tool_contract import build_tool_contract
from gateway_core.prompts.output_contracts.final_handoff import FINAL_ANSWER_HANDOFF_TOOL_DESCRIPTION
from gateway_core.prompts.output_contracts.per_turn_contract_plan import PER_TURN_CONTRACT_PLAN_SCHEMA_TEXT


def test_per_turn_prompt_schema_is_generated_from_runtime_contract() -> None:
    schema = json.loads(PER_TURN_CONTRACT_PLAN_SCHEMA_TEXT)

    assert PER_TURN_CONTRACT_PLAN_SCHEMA_TEXT == per_turn_contract_plan_schema_text()
    assert schema["contract_version"] == OUTPUT_CONTRACT_VERSION
    assert schema["route"] == "|".join(ROUTES)
    assert schema["answer_mode"] == "|".join(ANSWER_MODES)
    assert schema["allowed_tools"] == list(OPTIONAL_TOOLS)
    assert schema["required_outputs"] == list(REQUIRED_OUTPUTS)
    assert schema["required_artifacts"] == list(ARTIFACT_OUTPUTS)


def test_final_handoff_description_is_generated_from_runtime_contract() -> None:
    assert FINAL_ANSWER_HANDOFF_TOOL_DESCRIPTION == final_answer_handoff_tool_description()
    assert "不要把完整 Markdown 长答案放入 handoff_json" in FINAL_ANSWER_HANDOFF_TOOL_DESCRIPTION
    for field in ("contract_version", "question", "data_evidence", "evidence_board", "tool_contract"):
        assert field in FINAL_HANDOFF_FIELDS
        assert field in FINAL_ANSWER_HANDOFF_TOOL_DESCRIPTION


def test_runtime_plan_and_tool_contract_carry_contract_version() -> None:
    plan = PerTurnContractPlan(required_outputs=["policy_evidence"], allowed_tools=["official_policy_search"])
    contract = build_tool_contract("继续教育学时影响职称吗", plan=plan)

    assert plan.contract_version == OUTPUT_CONTRACT_VERSION
    assert contract.trace_payload()["contract_version"] == OUTPUT_CONTRACT_VERSION
    assert contract.handoff_block_payload()["contract_version"] == OUTPUT_CONTRACT_VERSION


def test_inter_agent_state_uses_ref_sample_and_lineage_without_raw_rows() -> None:
    state = build_inter_agent_state(
        question="最近请假的老师有哪些？",
        data_evidence={
            "ddl_sql_query_1": {
                "intent": "raw_sql_select",
                "dataset_label": "教师销假_请假明细",
                "allowed": True,
                "row_count": 87,
                "total_row_count": 87,
                "query_may_have_more": False,
                "raw_sql_handle": "trace://raw_rows/ddl_sql_query_1",
                "raw_rows": [{"教师": "不应进入状态", "blob": "x" * 1000}],
                "row_sample": [
                    {"教师": "张三", "请假类型": "病假"},
                    {"教师": "李四", "请假类型": "事假"},
                ],
                "sql_lineage": {
                    "evidence_ref_id": "ddl_sql_query_1:abc123",
                    "sql_hash": "a" * 64,
                    "tables_used": ["zx_mlh.教师销假_请假明细"],
                    "time_range": {"start": "2026-05-04", "end": "2026-06-03"},
                    "row_count": 87,
                },
                "evidence_summary": {
                    "truth_data_markdown": "【真实数据快照】",
                    "row_sample": [{"教师": "张三", "请假类型": "病假"}],
                },
            }
        },
        evidence_board={"business_clues": [{"value": "教师请假"}]},
        source_views=["zx_mlh.教师销假_请假明细"],
        tool_contract={"required_outputs": ["data_evidence"]},
        completed_outputs=["data_evidence"],
    )

    payload = state.model_dump()
    task = payload["data_evidence"]["ddl_sql_query_1"]

    assert payload["contract_version"] == OUTPUT_CONTRACT_VERSION
    assert payload["completed_outputs"] == ["data_evidence"]
    assert task["ref"]["id"] == "trace://raw_rows/ddl_sql_query_1"
    assert task["ref"]["evidence_ref_id"] == "ddl_sql_query_1:abc123"
    assert task["sample"] == [
        {"教师": "张三", "请假类型": "病假"},
        {"教师": "李四", "请假类型": "事假"},
    ]
    assert task["lineage"]["sql_hash"] == "a" * 64
    assert task["raw_data_policy"]["embedded_row_count"] == 2
    assert "raw_rows" not in task
