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
    answer_mode: Literal["text", "data", "image", "plot", "chart", "slide", "multi"] = "data"
    reason: str = Field(default="", description="Short semantic reason for the contract.")

