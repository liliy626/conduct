from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from gateway_core.tools.core.schemas import (
    ANY_OBJECT_SCHEMA,
    GATEWAY_TOOL_RESULT_SCHEMA,
    ToolContext,
    ToolInput,
    ToolResult,
)
from gateway_core.tools.tool_core import AgentTool, AgentToolInput, ToolExecutionContext


@dataclass
class AgentToolGatewayAdapter:
    agent_tool: AgentTool
    name: str
    title: str = ""
    description: str = ""
    input_schema: dict[str, Any] = field(default_factory=lambda: dict(ANY_OBJECT_SCHEMA))
    output_schema: dict[str, Any] = field(default_factory=lambda: dict(GATEWAY_TOOL_RESULT_SCHEMA))
    tags: tuple[str, ...] = ()
    risk_level: str = "low"
    scopes: tuple[str, ...] = ()
    aliases: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.title:
            self.title = self.name
        if not self.description:
            self.description = str(getattr(self.agent_tool, "description", "") or self.name)

    def run(self, tool_input: ToolInput, context: ToolContext) -> ToolResult:
        started = time.perf_counter()
        output = self.agent_tool.run(
            AgentToolInput(
                arguments=dict(tool_input.arguments or {}),
                evidence=list(tool_input.evidence or []),
                sources=list(tool_input.sources or []),
            ),
            ToolExecutionContext(
                request_id=context.request_id,
                tenant_id=context.tenant_id,
                user_id=context.user_id,
                metadata={**dict(context.metadata or {}), "scopes": list(context.scopes or [])},
            ),
        )
        payload = output.to_dict()
        return ToolResult(
            ok=bool(payload.get("ok")),
            structured_content=_structured_content(payload),
            evidence=[item for item in payload.get("evidence") or [] if isinstance(item, dict)],
            artifacts=[item for item in payload.get("artifacts") or [] if isinstance(item, dict)],
            sources=[item for item in payload.get("sources") or [] if isinstance(item, dict)],
            lineage=[item for item in payload.get("lineage") or [] if isinstance(item, dict)],
            warnings=[str(item) for item in payload.get("warnings") or []],
            error=str(payload.get("error") or "") or None,
            duration_ms=float(payload.get("duration_ms") or round((time.perf_counter() - started) * 1000, 3)),
        )


def _structured_content(payload: dict[str, Any]) -> dict[str, Any]:
    artifacts = payload.get("artifacts")
    if isinstance(artifacts, list) and len(artifacts) == 1 and isinstance(artifacts[0], dict):
        content = artifacts[0].get("content")
        if isinstance(content, dict):
            return content
    return {
        "evidence": payload.get("evidence") or [],
        "artifacts": payload.get("artifacts") or [],
        "sources": payload.get("sources") or [],
    }
