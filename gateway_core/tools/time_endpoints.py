from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from .time_tool import TimeTool, current_time_context
from .tool_core import AgentToolInput, ToolExecutionContext


router = APIRouter(prefix="/v1/tools/time", tags=["tools"])


@router.get("/current")
def get_current_time(timezone: str = "") -> dict[str, Any]:
    """Return the gateway's authoritative current time context."""
    return current_time_context(timezone=timezone or "Asia/Shanghai")


@router.post("/resolve")
def resolve_time(payload: dict[str, Any]) -> dict[str, Any]:
    """Resolve natural-language time expressions through the same Agent tool."""
    output = TimeTool().run(
        AgentToolInput(arguments=payload),
        ToolExecutionContext(metadata={"timezone": str(payload.get("timezone") or "")}),
    )
    return output.to_dict()
