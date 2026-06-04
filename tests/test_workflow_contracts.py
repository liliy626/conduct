from __future__ import annotations

from gateway_core.agents.contracts.workflow_contracts import (
    SCHOOL_DATA_ANSWER_WORKFLOW,
    WORKFLOW_CONTRACT_VERSION,
    WorkflowEvent,
    workflow_contract_trace_payload,
    workflow_step_trace_payload,
)
from gateway_core.agents.contracts.workflow_trace import (
    workflow_input_payload,
    workflow_output_payload,
    workflow_trace_context,
)


def test_school_data_answer_workflow_contract_declares_workflow_boundaries() -> None:
    workflow = SCHOOL_DATA_ANSWER_WORKFLOW

    assert workflow.workflow_id == "school_data_answer"
    assert workflow.workflow_version == WORKFLOW_CONTRACT_VERSION
    assert workflow.state_contract == "InterAgentState"
    assert workflow.trigger.route == "school_agent_native"

    step_ids = [step.step_id for step in workflow.steps]
    assert step_ids == [
        "route.resolve",
        "context.build_school",
        "contract.plan",
        "school_sql.react_execute",
        "evidence.normalize_inter_agent_state",
        "handoff.final_answer",
        "answer.compose",
    ]
    assert workflow.steps[3].agent_id == "school_sql_react"
    assert workflow.steps[3].node_type == "tool_react_executor"
    assert workflow.steps[3].executor.executor_type == "react_executor"
    assert "data_evidence" in workflow.steps[3].produces
    assert "inter_agent_state" in workflow.steps[4].writes


def test_workflow_contract_uses_nodes_not_top_level_agents() -> None:
    workflow = SCHOOL_DATA_ANSWER_WORKFLOW
    sql_node = workflow.steps[3]
    answer_node = workflow.steps[6]

    assert not hasattr(workflow, "agents")
    assert sql_node.executor.executor_id == "school_sql_react"
    assert "raw_rows_without_ref" in sql_node.must_not_read
    assert "data_evidence" in sql_node.writes
    assert answer_node.reads == ["handoff_payload", "business_prompt_context"]
    assert "raw_rows_without_ref" in answer_node.must_not_read


def test_workflow_event_carries_contract_identity_and_state_delta() -> None:
    event = WorkflowEvent(
        workflow_id="school_data_answer",
        step_id="evidence.normalize_inter_agent_state",
        agent_id="workflow",
        event_type="state_updated",
        state_delta={"completed_outputs": ["data_evidence"]},
    )

    assert event.workflow_version == WORKFLOW_CONTRACT_VERSION
    assert event.state_delta["completed_outputs"] == ["data_evidence"]


def test_workflow_trace_payload_is_compact_and_versioned() -> None:
    payload = workflow_contract_trace_payload(SCHOOL_DATA_ANSWER_WORKFLOW)

    assert payload["workflow_id"] == "school_data_answer"
    assert payload["workflow_version"] == WORKFLOW_CONTRACT_VERSION
    assert payload["state_contract"] == "InterAgentState"
    assert payload["step_ids"][:3] == ["route.resolve", "context.build_school", "contract.plan"]
    assert "school_sql_react" in payload["executors"]
    assert "school_sql.react_execute" in payload["nodes"]


def test_workflow_step_trace_payload_declares_agent_boundaries() -> None:
    payload = workflow_step_trace_payload(SCHOOL_DATA_ANSWER_WORKFLOW, "evidence.normalize_inter_agent_state")

    assert payload["workflow_id"] == "school_data_answer"
    assert payload["step_id"] == "evidence.normalize_inter_agent_state"
    assert payload["agent_id"] == "workflow"
    assert payload["executor_id"] == "workflow"
    assert payload["node_type"] == "evidence_normalizer"
    assert "data_evidence" in payload["reads"]
    assert "inter_agent_state" in payload["writes"]
    assert payload["trace_name"] == "inter_agent_state.build"


def test_workflow_trace_adapter_attaches_step_contract_to_payloads() -> None:
    input_payload = workflow_input_payload(
        SCHOOL_DATA_ANSWER_WORKFLOW,
        "context.build_school",
        {"question": "今天学校请假情况"},
    )
    output_payload = workflow_output_payload(
        SCHOOL_DATA_ANSWER_WORKFLOW,
        "route.resolve",
        {"school_id": "zx_mlh"},
    )

    assert input_payload["workflow_step"]["step_id"] == "context.build_school"
    assert input_payload["workflow_step"]["agent_id"] == "context_builder"
    assert output_payload["workflow_step"]["step_id"] == "route.resolve"
    assert output_payload["workflow_step"]["agent_id"] == "gateway"


def test_workflow_trace_context_carries_react_execute_step() -> None:
    context = workflow_trace_context(SCHOOL_DATA_ANSWER_WORKFLOW, "school_sql.react_execute")

    assert context["workflow_step"]["step_id"] == "school_sql.react_execute"
    assert context["workflow_step"]["agent_id"] == "school_sql_react"
    assert context["workflow_step"]["executor_id"] == "school_sql_react"
    assert context["workflow_step"]["executor_type"] == "react_executor"
    assert "data_evidence" in context["workflow_step"]["produces"]
