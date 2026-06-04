from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, Field

from gateway_core.agents.contracts.output_contracts import OUTPUT_CONTRACT_VERSION


WORKFLOW_CONTRACT_VERSION = "2026-06-04.1"


class WorkflowTrigger(BaseModel):
    route: str = ""
    required_api_key_type: str = ""
    input_message: str = "last_user_question"


class ToolPolicy(BaseModel):
    allowed_tools: list[str] = Field(default_factory=list)
    tool_call_budget: int = 0


class CompletionCondition(BaseModel):
    required_outputs: list[str] = Field(default_factory=list)
    field_exists: str = ""
    max_step_count: int = 0


class ErrorPolicy(BaseModel):
    strategy: str = "fail_fast"
    fallback_outputs: list[str] = Field(default_factory=list)


class TracePolicy(BaseModel):
    name: str = ""
    required_steps: list[str] = Field(default_factory=list)
    must_record: list[str] = Field(default_factory=list)
    include_contract_version: bool = True


class ExecutorBinding(BaseModel):
    executor_id: str
    executor_type: str = "workflow_node"
    runtime: str = ""
    implementation: str = ""
    role: str = ""
    responsibility: str
    output_contract: str = ""


class NodeContract(BaseModel):
    step_id: str
    node_type: str
    executor: ExecutorBinding
    reads: list[str] = Field(default_factory=list)
    writes: list[str] = Field(default_factory=list)
    must_not_read: list[str] = Field(default_factory=list)
    requires: list[str] = Field(default_factory=list)
    produces: list[str] = Field(default_factory=list)
    tool_policy: ToolPolicy = Field(default_factory=ToolPolicy)
    completion_condition: CompletionCondition = Field(default_factory=CompletionCondition)
    on_error: ErrorPolicy = Field(default_factory=ErrorPolicy)
    trace: TracePolicy = Field(default_factory=TracePolicy)

    @property
    def agent_id(self) -> str:
        return self.executor.executor_id


StepContract = NodeContract


class WorkflowContract(BaseModel):
    workflow_id: str
    workflow_version: str = WORKFLOW_CONTRACT_VERSION
    output_contract_version: str = OUTPUT_CONTRACT_VERSION
    trigger: WorkflowTrigger = Field(default_factory=WorkflowTrigger)
    input_contract: str = "WorkflowInput"
    state_contract: str = "InterAgentState"
    steps: list[StepContract] = Field(default_factory=list)
    completion: CompletionCondition = Field(default_factory=CompletionCondition)
    trace_policy: TracePolicy = Field(default_factory=TracePolicy)
    error_policy: ErrorPolicy = Field(default_factory=ErrorPolicy)


class WorkflowEvent(BaseModel):
    event_id: str = ""
    workflow_id: str
    workflow_version: str = WORKFLOW_CONTRACT_VERSION
    node_id: str = ""
    step_id: str = ""
    executor_id: str = ""
    agent_id: str = ""
    event_type: str
    state_delta: dict[str, Any] = Field(default_factory=dict)
    trace_ref: str = ""
    timestamp: str = ""


class WorkflowError(BaseModel):
    error_type: str
    message: str = ""
    node_id: str = ""
    executor_id: str = ""
    agent_id: str = ""
    step_id: str = ""
    recoverable: bool = False
    fallback_strategy: str = "fail_fast"
    state_impact: dict[str, Any] = Field(default_factory=dict)


def workflow_contract_trace_payload(workflow: WorkflowContract) -> dict[str, Any]:
    """Compact workflow identity for runtime traces."""
    return {
        "workflow_id": workflow.workflow_id,
        "workflow_version": workflow.workflow_version,
        "output_contract_version": workflow.output_contract_version,
        "input_contract": workflow.input_contract,
        "state_contract": workflow.state_contract,
        "trigger_route": workflow.trigger.route,
        "nodes": [step.step_id for step in workflow.steps],
        "executors": sorted({step.executor.executor_id for step in workflow.steps}),
        "step_ids": [step.step_id for step in workflow.steps],
        "completion": {
            "required_outputs": list(workflow.completion.required_outputs),
            "field_exists": workflow.completion.field_exists,
            "max_step_count": workflow.completion.max_step_count,
        },
        "trace_policy": {
            "name": workflow.trace_policy.name,
            "required_steps": list(workflow.trace_policy.required_steps),
            "include_contract_version": workflow.trace_policy.include_contract_version,
        },
    }


def workflow_step_trace_payload(workflow: WorkflowContract, step_id: str) -> dict[str, Any]:
    """Compact step contract identity for runtime traces."""
    step = _workflow_step(workflow, step_id)
    if step is None:
        return {
            "workflow_id": workflow.workflow_id,
            "workflow_version": workflow.workflow_version,
            "output_contract_version": workflow.output_contract_version,
            "step_id": str(step_id or ""),
            "missing_step_contract": True,
        }
    return {
        "workflow_id": workflow.workflow_id,
        "workflow_version": workflow.workflow_version,
        "output_contract_version": workflow.output_contract_version,
        "step_id": step.step_id,
        "node_type": step.node_type,
        "executor_id": step.executor.executor_id,
        "executor_type": step.executor.executor_type,
        "executor_role": step.executor.role,
        "implementation": step.executor.implementation,
        "agent_id": step.agent_id,
        "reads": list(step.reads),
        "writes": list(step.writes),
        "must_not_read": list(step.must_not_read),
        "requires": list(step.requires),
        "produces": list(step.produces),
        "trace_name": step.trace.name,
        "trace_must_record": list(step.trace.must_record),
        "allowed_tools": list(step.tool_policy.allowed_tools),
        "tool_call_budget": step.tool_policy.tool_call_budget,
    }


def _workflow_step(workflow: WorkflowContract, step_id: str) -> Optional[StepContract]:
    clean_step_id = str(step_id or "").strip()
    for step in workflow.steps:
        if step.step_id == clean_step_id:
            return step
    return None


SCHOOL_DATA_ANSWER_WORKFLOW = WorkflowContract(
    workflow_id="school_data_answer",
    trigger=WorkflowTrigger(
        route="school_agent_native",
        required_api_key_type="school",
        input_message="last_user_question",
    ),
    steps=[
        StepContract(
            step_id="route.resolve",
            node_type="router",
            executor=ExecutorBinding(
                executor_id="gateway",
                executor_type="deterministic",
                runtime="fastapi",
                implementation="gateway_core.api.openai_compat.chat_pipeline",
                role="route",
                responsibility="resolve tenant, auth, and route for the request",
                output_contract="RouteDecision",
            ),
            reads=["request.headers", "messages"],
            writes=["route", "tenant"],
            produces=["route_decision"],
            trace=TracePolicy(name="route.resolve", must_record=["input", "decision", "output", "error"]),
        ),
        StepContract(
            step_id="context.build_school",
            node_type="context_selector",
            executor=ExecutorBinding(
                executor_id="context_builder",
                executor_type="deterministic",
                runtime="python",
                implementation="gateway_core.schema_context",
                role="context",
                responsibility="select and compress school metadata context",
                output_contract="SelectedContext",
            ),
            reads=["tenant", "question"],
            writes=["schema_catalog_context", "sql_experience_context", "business_prompt_context"],
            produces=["selected_context"],
            trace=TracePolicy(name="domain_context", must_record=["input", "selected_context", "output", "error"]),
        ),
        StepContract(
            step_id="contract.plan",
            node_type="planner",
            executor=ExecutorBinding(
                executor_id="contract_planner",
                executor_type="llm_planner",
                runtime="langchain",
                implementation="gateway_core.agents.contracts.planner.ContractPlanner",
                role="planner",
                responsibility="plan per-turn route, tools, required outputs, and answer focus",
                output_contract="PerTurnContractPlan",
            ),
            reads=[
                "question",
                "conversation_context",
                "schema_catalog_context",
                "ddl_vector_context",
                "business_prompt_context",
                "sql_experience_context",
            ],
            writes=["tool_contract", "required_outputs"],
            produces=["PerTurnContractPlan"],
            completion_condition=CompletionCondition(field_exists="tool_contract"),
            on_error=ErrorPolicy(strategy="fallback_contract", fallback_outputs=[]),
            trace=TracePolicy(name="agent_native.contract.plan", must_record=["input", "decision", "output", "error"]),
        ),
        StepContract(
            step_id="school_sql.react_execute",
            node_type="tool_react_executor",
            executor=ExecutorBinding(
                executor_id="school_sql_react",
                executor_type="react_executor",
                runtime="langgraph",
                implementation="gateway_core.agents.school_sql.agent_stream.stream_school_sql_agent_native",
                role="executor",
                responsibility="query school data evidence through DDL and read-only SQL tools",
                output_contract="data_evidence",
            ),
            reads=["question", "tool_contract", "schema_catalog_context", "conversation_context"],
            writes=["data_evidence", "evidence_board", "source_views"],
            must_not_read=["raw_rows_without_ref", "runtime_secret"],
            requires=["tool_contract"],
            produces=["data_evidence"],
            tool_policy=ToolPolicy(
                allowed_tools=[
                    "school.ddl_search",
                    "school.inspect_table_schema",
                    "school.sample_table_rows",
                    "school.sql_db_query",
                    "school.jsonb_recordset_query",
                ],
                tool_call_budget=12,
            ),
            trace=TracePolicy(name="agent_native.langgraph", must_record=["input", "decision", "output", "error"]),
        ),
        StepContract(
            step_id="evidence.normalize_inter_agent_state",
            node_type="evidence_normalizer",
            executor=ExecutorBinding(
                executor_id="workflow",
                executor_type="deterministic",
                runtime="python",
                implementation="gateway_core.agents.contracts.inter_agent_state.build_inter_agent_state",
                role="evidence",
                responsibility="normalize SQL evidence into serializable InterAgentState",
                output_contract="InterAgentState",
            ),
            reads=["data_evidence", "evidence_board", "source_views", "tool_contract"],
            writes=["inter_agent_state"],
            must_not_read=["raw_rows_without_ref", "runtime_secret"],
            requires=["data_evidence"],
            produces=["InterAgentState"],
            trace=TracePolicy(name="inter_agent_state.build", must_record=["input", "output", "error"]),
        ),
        StepContract(
            step_id="handoff.final_answer",
            node_type="handoff_builder",
            executor=ExecutorBinding(
                executor_id="workflow",
                executor_type="deterministic",
                runtime="python",
                implementation="gateway_core.agents.school_sql.final_handoff",
                role="handoff",
                responsibility="build compact final-answer handoff from InterAgentState",
                output_contract="FinalAnswerHandoff",
            ),
            reads=["inter_agent_state"],
            writes=["handoff_payload"],
            requires=["InterAgentState"],
            produces=["FinalAnswerHandoff"],
            trace=TracePolicy(name="final_answer_handoff", must_record=["input", "output", "error"]),
        ),
        StepContract(
            step_id="answer.compose",
            node_type="answer",
            executor=ExecutorBinding(
                executor_id="final_answer",
                executor_type="llm_answer",
                runtime="langchain",
                implementation="gateway_core.agents.school_sql.final_handoff",
                role="answer",
                responsibility="compose the final natural-language answer from verified evidence",
                output_contract="FinalAnswer",
            ),
            reads=["handoff_payload", "business_prompt_context"],
            writes=["final_answer"],
            must_not_read=["raw_rows_without_ref", "runtime_secret"],
            requires=["FinalAnswerHandoff"],
            produces=["final_answer"],
            trace=TracePolicy(name="agent_native.final_fast.llm", must_record=["input", "output", "error"]),
        ),
    ],
    completion=CompletionCondition(required_outputs=["data_evidence", "final_answer"], max_step_count=12),
    trace_policy=TracePolicy(
        name="school_data_answer",
        required_steps=["route", "plan", "tool", "evidence", "handoff", "answer"],
        include_contract_version=True,
    ),
    error_policy=ErrorPolicy(strategy="fail_fast"),
)
