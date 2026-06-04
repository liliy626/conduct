from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from gateway_core.agents.contracts.output_contracts import OUTPUT_CONTRACT_VERSION


class PerTurnContractPlan(BaseModel):
    """Semantic contract planned before a ReAct turn starts."""

    contract_version: str = Field(
        default=OUTPUT_CONTRACT_VERSION,
        description="Version of the output contract that produced this plan.",
    )
    required_outputs: list[str] = Field(
        default_factory=list,
        description="Artifacts or evidence outputs that must exist before final handoff.",
    )
    allowed_tools: list[str] = Field(
        default_factory=list,
        description="Optional non-SQL tools the Agent is allowed to see this turn.",
    )
    route: Literal["chat", "data"] = Field(
        default="data",
        description="Whether this turn should be answered as plain chat or routed into the school data agent.",
    )
    answer_mode: Literal["text", "data", "image", "plot", "chart", "slide", "multi"] = "data"
    answer_focus: Any = Field(default="", description="Brief description of what the final answer should cover.")
    required_artifacts: list[str] = Field(
        default_factory=list,
        description="Artifact outputs requested by the compact planner schema; normalized into required_outputs.",
    )
    reason: str = Field(default="", description="Short semantic reason for the contract.")
