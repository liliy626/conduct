from __future__ import annotations

from typing import Any

from gateway_core.agents.contracts import ContractPlanner
from gateway_core.agents.contracts.output_contracts import OPTIONAL_TOOL_OUTPUTS, OPTIONAL_TOOLS
from gateway_core.agents.contracts.planner import (
    compact_business_prompt_for_contract,
    compact_metadata_catalog_for_contract,
    compact_sql_experience_for_contract,
)
from gateway_core.agents.contracts.workflow_contracts import SCHOOL_DATA_ANSWER_WORKFLOW
from gateway_core.agents.contracts.workflow_trace import workflow_input_payload, workflow_output_payload
from gateway_core.school.trace import set_step_output, trace_preview, trace_step


def plan_tool_contract(
    *,
    trace: Any,
    model: Any,
    question: str,
    conversation_context: str,
    metadata_catalog_context: str = "",
    ddl_vector_context: str = "",
    business_prompt_context: str = "",
    sql_experience_context: str = "",
) -> Any:
    available_tools = contract_available_tools()
    metadata_catalog_contract_preview = compact_metadata_catalog_for_contract(metadata_catalog_context)
    business_prompt_contract_preview = compact_business_prompt_for_contract(business_prompt_context)
    sql_experience_contract_preview = compact_sql_experience_for_contract(sql_experience_context)
    with trace_step(
        trace,
        "agent_native.contract.plan",
        workflow_input_payload(
            SCHOOL_DATA_ANSWER_WORKFLOW,
            "contract.plan",
            {
                "question": question,
                "available_tools": available_tools,
            },
        ),
    ) as step:
        plan = ContractPlanner(model).plan_turn(
            question=question,
            conversation_context=conversation_context,
            metadata_catalog_context=metadata_catalog_context,
            ddl_vector_context=ddl_vector_context,
            business_prompt_context=business_prompt_context,
            sql_experience_context=sql_experience_context,
            available_tools=available_tools,
        )
        planner_trace_output = _planner_trace_output(
            plan=plan,
            question=question,
            conversation_context=conversation_context,
            metadata_catalog_context=metadata_catalog_context,
            ddl_vector_context=ddl_vector_context,
            business_prompt_context=business_prompt_context,
            sql_experience_context=sql_experience_context,
            metadata_catalog_contract_preview=metadata_catalog_contract_preview,
            business_prompt_contract_preview=business_prompt_contract_preview,
            sql_experience_contract_preview=sql_experience_contract_preview,
            available_tools=available_tools,
        )
        set_step_output(
            step,
            workflow_output_payload(SCHOOL_DATA_ANSWER_WORKFLOW, "contract.plan", planner_trace_output),
        )
        with trace_step(
            trace,
            "query_plan",
            workflow_input_payload(SCHOOL_DATA_ANSWER_WORKFLOW, "contract.plan", {"question": question}),
        ) as plan_step:
            set_step_output(
                plan_step,
                workflow_output_payload(
                    SCHOOL_DATA_ANSWER_WORKFLOW,
                    "contract.plan",
                    {
                        "input": planner_trace_output["input"],
                        "decision": {
                            "route": getattr(plan, "route", "data"),
                            "required_outputs": list(plan.required_outputs),
                            "allowed_tools": list(plan.allowed_tools),
                            "answer_mode": plan.answer_mode,
                            "answer_focus": getattr(plan, "answer_focus", ""),
                            "reason": plan.reason,
                        },
                        "output": {"tool_contract_seed": plan.trace_payload() if hasattr(plan, "trace_payload") else {}},
                        "error": None,
                    },
                ),
            )
        return plan


def prune_tool_contract_for_disabled_tools(tool_contract: Any, disabled_tools: set[str]) -> None:
    """Remove outputs that cannot be completed by tools disabled for this phase."""
    if not disabled_tools or tool_contract is None:
        return
    disabled_outputs = {
        output_name
        for tool_name, output_name in OPTIONAL_TOOL_OUTPUTS.items()
        if str(tool_name or "").strip() in disabled_tools
    }
    required_outputs = getattr(tool_contract, "required_outputs", None)
    if isinstance(required_outputs, set):
        required_outputs.difference_update(disabled_outputs)
    allowed_tools = getattr(tool_contract, "allowed_tools", None)
    if isinstance(allowed_tools, set):
        allowed_tools.difference_update(disabled_tools)


def contract_available_tools() -> list[str]:
    return list(OPTIONAL_TOOLS)


def _planner_trace_output(
    *,
    plan: Any,
    question: str,
    conversation_context: str,
    metadata_catalog_context: str,
    ddl_vector_context: str,
    business_prompt_context: str,
    sql_experience_context: str,
    metadata_catalog_contract_preview: str,
    business_prompt_contract_preview: str,
    sql_experience_contract_preview: str,
    available_tools: list[str],
) -> dict[str, Any]:
    return {
        "input": {
            "question": question,
            "conversation_context_preview": trace_preview(conversation_context),
            "metadata_catalog_preview": metadata_catalog_contract_preview,
            "ddl_vector_preview": trace_preview(ddl_vector_context),
            "business_prompt_preview": business_prompt_contract_preview,
            "sql_experience_preview": sql_experience_contract_preview,
            "available_tools": available_tools,
        },
        "input_chars": {
            "conversation_context": len(str(conversation_context or "")),
            "metadata_catalog": len(str(metadata_catalog_context or "")),
            "ddl_vector": len(str(ddl_vector_context or "")),
            "business_prompt": len(str(business_prompt_context or "")),
            "sql_experience": len(str(sql_experience_context or "")),
        },
        "planner_input_chars": {
            "metadata_catalog": len(metadata_catalog_contract_preview),
            "business_prompt": len(business_prompt_contract_preview),
            "sql_experience": len(sql_experience_contract_preview),
        },
        "decision": {
            "route": getattr(plan, "route", "data"),
            "required_outputs": list(plan.required_outputs),
            "allowed_tools": list(plan.allowed_tools),
            "answer_mode": plan.answer_mode,
            "answer_focus": getattr(plan, "answer_focus", ""),
        },
        "output": {
            "reason": plan.reason,
        },
        "error": None,
        "route": getattr(plan, "route", "data"),
        "required_outputs": list(plan.required_outputs),
        "allowed_tools": list(plan.allowed_tools),
        "answer_mode": plan.answer_mode,
        "answer_focus": getattr(plan, "answer_focus", ""),
        "reason": plan.reason,
    }
