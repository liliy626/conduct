from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class AgentProcessEvent(BaseModel):
    event_type: str
    phase: str = ""
    summary: str = ""
    payload: Dict[str, Any] = Field(default_factory=dict)


class PublicThinkingFrame(BaseModel):
    title: str = "可公开的分析过程"
    events: List[AgentProcessEvent] = Field(default_factory=list)
    text: str = ""
    trace_id: Optional[str] = None
