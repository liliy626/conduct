from __future__ import annotations

from typing import Any

from gateway_core.agents.contracts.workflow_contracts import (
    WorkflowContract,
    workflow_contract_trace_payload,
    workflow_step_trace_payload,
)
from gateway_core.school.trace import set_step_output, trace_preview, trace_step


def workflow_step_payload(workflow: WorkflowContract, step_id: str) -> dict[str, Any]:
    return workflow_step_trace_payload(workflow, step_id)


def workflow_input_payload(
    workflow: WorkflowContract,
    step_id: str,
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    out = dict(payload or {})
    out["workflow_step"] = workflow_step_payload(workflow, step_id)
    return out


def workflow_output_payload(
    workflow: WorkflowContract,
    step_id: str,
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    out = dict(payload or {})
    out["workflow_step"] = workflow_step_payload(workflow, step_id)
    return out


def workflow_trace_context(workflow: WorkflowContract, step_id: str) -> dict[str, Any]:
    return {"workflow_step": workflow_step_payload(workflow, step_id)}


def record_workflow_start(trace: Any, workflow: WorkflowContract, *, question: str) -> None:
    workflow_payload = workflow_contract_trace_payload(workflow)
    with trace_step(
        trace,
        "workflow.start",
        {
            "question": question,
            "workflow": workflow_payload,
        },
    ) as step:
        set_step_output(
            step,
            {
                "workflow": workflow_payload,
                "state_contract": workflow.state_contract,
                "output_contract_version": workflow.output_contract_version,
            },
        )


def record_inter_agent_state_build(
    trace: Any,
    workflow: WorkflowContract,
    *,
    question: str,
    state_payload: dict[str, Any],
) -> None:
    if trace is None:
        return
    data_evidence = state_payload.get("data_evidence") if isinstance(state_payload, dict) else {}
    if not isinstance(data_evidence, dict):
        data_evidence = {}
    with trace_step(
        trace,
        "inter_agent_state.build",
        workflow_input_payload(
            workflow,
            "evidence.normalize_inter_agent_state",
            {
                "question": question,
                "task_count": len(data_evidence),
            },
        ),
    ) as step:
        set_step_output(
            step,
            workflow_output_payload(
                workflow,
                "evidence.normalize_inter_agent_state",
                {
                    "contract_version": state_payload.get("contract_version"),
                    "completed_outputs": state_payload.get("completed_outputs") or [],
                    "required_outputs": state_payload.get("required_outputs") or [],
                    "source_views": state_payload.get("source_views") or [],
                    "evidence_board_keys": sorted((state_payload.get("evidence_board") or {}).keys()),
                    "data_evidence_tasks": _data_evidence_task_trace_summaries(data_evidence),
                    "state_preview": trace_preview(state_payload),
                },
            ),
        )


def _data_evidence_task_trace_summaries(data_evidence: dict[str, Any]) -> dict[str, Any]:
    return {
        task_id: {
            "ref": task.get("ref") if isinstance(task, dict) else {},
            "sample_count": len(task.get("sample") or []) if isinstance(task, dict) else 0,
            "lineage_keys": sorted((task.get("lineage") or {}).keys()) if isinstance(task, dict) else [],
            "raw_data_policy": task.get("raw_data_policy") if isinstance(task, dict) else {},
        }
        for task_id, task in data_evidence.items()
    }
