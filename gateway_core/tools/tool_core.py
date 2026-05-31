from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass, field, is_dataclass
from typing import Any, Dict, List, Mapping, Optional


JsonDict = Dict[str, Any]


def json_safe(value: Any) -> Any:
    """Return a value that can be passed to json.dumps without custom encoders."""
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if is_dataclass(value):
        return json_safe(asdict(value))
    if isinstance(value, Mapping):
        return {str(key): json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [json_safe(item) for item in value]
    try:
        json.dumps(value, ensure_ascii=False)
        return value
    except (TypeError, ValueError):
        return str(value)


@dataclass
class AgentToolInput:
    arguments: JsonDict = field(default_factory=dict)
    evidence: List[JsonDict] = field(default_factory=list)
    sources: List[JsonDict] = field(default_factory=list)

    def to_dict(self) -> JsonDict:
        return json_safe(asdict(self))


@dataclass
class ToolExecutionContext:
    request_id: str = ""
    tenant_id: str = ""
    user_id: str = ""
    metadata: JsonDict = field(default_factory=dict)

    def to_dict(self) -> JsonDict:
        return json_safe(asdict(self))


@dataclass
class AgentToolOutput:
    ok: bool
    evidence: List[JsonDict] = field(default_factory=list)
    artifacts: List[JsonDict] = field(default_factory=list)
    sources: List[JsonDict] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    error: Optional[str] = None
    duration_ms: float = 0.0

    def to_dict(self) -> JsonDict:
        return json_safe(asdict(self))


class AgentTool:
    name: str = ""
    description: str = ""

    def run(self, tool_input: AgentToolInput, context: ToolExecutionContext) -> AgentToolOutput:
        raise NotImplementedError


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: Dict[str, AgentTool] = {}

    def register(self, tool: AgentTool) -> None:
        if not tool.name:
            raise ValueError("tool name is required")
        if tool.name in self._tools:
            raise ValueError(f"tool already registered: {tool.name}")
        self._tools[tool.name] = tool

    def get(self, name: str) -> Optional[AgentTool]:
        return self._tools.get(name)

    def run(
        self,
        name: str,
        tool_input: AgentToolInput,
        context: Optional[ToolExecutionContext] = None,
    ) -> AgentToolOutput:
        started = time.perf_counter()
        tool = self.get(name)
        if tool is None:
            return AgentToolOutput(
                ok=False,
                error=f"tool not registered: {name}",
                duration_ms=_elapsed_ms(started),
            )

        try:
            output = tool.run(tool_input, context or ToolExecutionContext())
        except Exception as exc:
            return AgentToolOutput(ok=False, error=str(exc), duration_ms=_elapsed_ms(started))

        if output.duration_ms <= 0:
            output.duration_ms = _elapsed_ms(started)
        return AgentToolOutput(**output.to_dict())


def _elapsed_ms(started: float) -> float:
    return round((time.perf_counter() - started) * 1000, 3)

