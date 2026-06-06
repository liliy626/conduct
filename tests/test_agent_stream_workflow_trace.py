from __future__ import annotations

from types import SimpleNamespace

import gateway_core.agents.school_sql.agent_stream as agent_stream
from gateway_core.agents.contracts.models import PerTurnContractPlan
from gateway_core.agents.contracts.tool_contract import build_tool_contract
from gateway_core.agents.contracts.workflow_contracts import SCHOOL_DATA_ANSWER_WORKFLOW
from gateway_core.agents.contracts.workflow_trace import record_workflow_start
from gateway_core.school.trace import new_tenant_trace


def test_workflow_start_trace_records_contract_identity() -> None:
    trace = new_tenant_trace("今天教师请假情况")

    record_workflow_start(trace, SCHOOL_DATA_ANSWER_WORKFLOW, question="今天教师请假情况")

    assert trace is not None
    assert trace.steps[-1].name == "workflow.start"
    assert trace.steps[-1].output["workflow"]["workflow_id"] == "school_data_answer"
    assert trace.steps[-1].output["state_contract"] == "InterAgentState"


def test_inter_agent_state_build_trace_records_ref_sample_lineage() -> None:
    trace = new_tenant_trace("今天请假的老师有哪些？")
    tools = SimpleNamespace(
        trace=trace,
        source_views=["zx_mlh.教师销假_请假明细"],
            evidence_by_task={
                "ddl_sql_query_1": {
                    "task_id": "ddl_sql_query_1",
                    "allowed": True,
                    "intent": "raw_sql_select",
                    "dataset_label": "教师销假_请假明细",
                    "row_count": 1,
                    "raw_sql_handle": "trace://current/task/ddl_sql_query_1/raw_rows",
                    "row_sample": [{"teacher_name": "王老师", "leave_type": "病假"}],
                    "raw_rows": [{"teacher_name": "王老师", "leave_type": "病假"}],
                    "evidence_summary": {
                        "row_sample": [{"teacher_name": "王老师", "leave_type": "病假"}],
                },
                "sql_lineage": {
                    "sql": "select teacher_name, leave_type from zx_mlh.教师销假_请假明细",
                    "source_view": "zx_mlh.教师销假_请假明细",
                },
            }
        },
    )

    payload = agent_stream._handoff_payload_with_tool_evidence(
        {
            "question": "今天请假的老师有哪些？",
            "source_views": ["zx_mlh.教师销假_请假明细"],
        },
        tools=tools,
    )

    assert trace is not None
    build_step = trace.steps[-1]
    assert build_step.name == "inter_agent_state.build"
    assert build_step.output["workflow_step"]["step_id"] == "evidence.normalize_inter_agent_state"
    task = build_step.output["data_evidence_tasks"]["ddl_sql_query_1"]
    assert task["ref"]["id"].startswith("trace://current/task/ddl_sql_query_1")
    assert task["sample_count"] == 1
    assert "sql" in task["lineage_keys"]
    assert "raw_rows" not in payload["inter_agent_state"]["data_evidence"]["ddl_sql_query_1"]


def test_disabled_multimodal_tools_do_not_block_inner_sql_handoff() -> None:
    plan = PerTurnContractPlan(
        required_outputs=["image_artifact", "chart_artifact", "policy_evidence"],
        allowed_tools=["generate_image_tool", "chart", "official_policy_search"],
        answer_mode="multi",
        reason="用户要求图片，外层多模态 worker 负责生成",
    )
    contract = build_tool_contract("生成这个学期的工作安排报告图片", plan=plan)

    agent_stream._prune_tool_contract_for_disabled_tools(
        contract,
        {"generate_image_tool", "chart", "plot", "slide"},
    )

    assert contract.required_outputs == {"policy_evidence"}
    assert contract.allowed_tools == {"official_policy_search"}
