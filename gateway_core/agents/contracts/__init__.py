from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from gateway_core.agents.contracts.inter_agent_state import InterAgentState
    from gateway_core.agents.contracts.models import PerTurnContractPlan
    from gateway_core.agents.contracts.planner import ContractPlanner
    from gateway_core.agents.contracts.tool_contract import ToolContract
    from gateway_core.agents.contracts.workflow_contracts import WorkflowContract

__all__ = [
    "ContractPlanner",
    "InterAgentState",
    "OUTPUT_CONTRACT_VERSION",
    "PerTurnContractPlan",
    "SCHOOL_DATA_ANSWER_WORKFLOW",
    "ToolContract",
    "WorkflowContract",
    "build_inter_agent_state",
    "build_tool_contract",
]


def __getattr__(name: str):
    if name == "PerTurnContractPlan":
        from gateway_core.agents.contracts.models import PerTurnContractPlan

        return PerTurnContractPlan
    if name == "OUTPUT_CONTRACT_VERSION":
        from gateway_core.agents.contracts.output_contracts import OUTPUT_CONTRACT_VERSION

        return OUTPUT_CONTRACT_VERSION
    if name == "ContractPlanner":
        from gateway_core.agents.contracts.planner import ContractPlanner

        return ContractPlanner
    if name in {"InterAgentState", "build_inter_agent_state"}:
        from gateway_core.agents.contracts.inter_agent_state import InterAgentState, build_inter_agent_state

        return {"InterAgentState": InterAgentState, "build_inter_agent_state": build_inter_agent_state}[name]
    if name in {"ToolContract", "build_tool_contract"}:
        from gateway_core.agents.contracts.tool_contract import ToolContract, build_tool_contract

        return {"ToolContract": ToolContract, "build_tool_contract": build_tool_contract}[name]
    if name in {"WorkflowContract", "SCHOOL_DATA_ANSWER_WORKFLOW"}:
        from gateway_core.agents.contracts.workflow_contracts import SCHOOL_DATA_ANSWER_WORKFLOW, WorkflowContract

        return {
            "WorkflowContract": WorkflowContract,
            "SCHOOL_DATA_ANSWER_WORKFLOW": SCHOOL_DATA_ANSWER_WORKFLOW,
        }[name]
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
