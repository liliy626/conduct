from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class PerTurnContractPlan(BaseModel):
    """Semantic contract planned before a ReAct turn starts."""

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
    answer_focus: str = Field(default="", description="Brief description of what the final answer should cover.")
    reason: str = Field(default="", description="Short semantic reason for the contract.")
