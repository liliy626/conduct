from __future__ import annotations

import time
from dataclasses import asdict, dataclass, field
from typing import Any, Protocol

from gateway_core.tools.tool_core import json_safe


ANY_OBJECT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": True,
}

GATEWAY_TOOL_RESULT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "ok": {"type": "boolean"},
        "structured_content": {"type": "object", "additionalProperties": True},
        "evidence": {"type": "array", "items": {"type": "object", "additionalProperties": True}},
        "artifacts": {"type": "array", "items": {"type": "object", "additionalProperties": True}},
        "sources": {"type": "array", "items": {"type": "object", "additionalProperties": True}},
        "lineage": {"type": "array", "items": {"type": "object", "additionalProperties": True}},
        "warnings": {"type": "array", "items": {"type": "string"}},
        "error": {"type": ["string", "null"]},
        "duration_ms": {"type": "number"},
    },
    "required": ["ok"],
    "additionalProperties": True,
}


@dataclass
class ToolInput:
    arguments: dict[str, Any] = field(default_factory=dict)
    evidence: list[dict[str, Any]] = field(default_factory=list)
    sources: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return json_safe(asdict(self))


@dataclass
class ToolContext:
    request_id: str = ""
    tenant_id: str = ""
    user_id: str = ""
    scopes: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return json_safe(asdict(self))


@dataclass
class ToolResult:
    ok: bool
    structured_content: dict[str, Any] = field(default_factory=dict)
    evidence: list[dict[str, Any]] = field(default_factory=list)
    artifacts: list[dict[str, Any]] = field(default_factory=list)
    sources: list[dict[str, Any]] = field(default_factory=list)
    lineage: list[dict[str, Any]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    error: str | None = None
    duration_ms: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return json_safe(asdict(self))


class GatewayTool(Protocol):
    name: str
    title: str
    description: str
    input_schema: dict[str, Any]
    output_schema: dict[str, Any]
    tags: tuple[str, ...]
    risk_level: str
    scopes: tuple[str, ...]
    aliases: tuple[str, ...]

    def run(self, tool_input: ToolInput, context: ToolContext) -> ToolResult:
        ...


def elapsed_ms(started: float) -> float:
    return round((time.perf_counter() - started) * 1000, 3)
