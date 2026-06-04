from __future__ import annotations

import json
import os
from typing import Any

from langchain_core.tools import StructuredTool

from gateway_core.school.trace import set_step_output, trace_step
from gateway_core.tools.core import GatewayTool, GatewayToolRegistry, ToolContext, ToolInput


def build_langchain_tools_from_registry(
    registry: GatewayToolRegistry,
    *,
    allowed_names: set[str],
    tenant_id: str = "",
    trace: Any = None,
    tool_contract: Any = None,
) -> list[StructuredTool]:
    tool_call_counts: dict[str, int] = {}
    tools: list[StructuredTool] = []
    seen_tool_names: set[str] = set()
    for requested_name in sorted(allowed_names):
        tool = registry.get(requested_name)
        if tool is None:
            continue
        if tool.name in seen_tool_names:
            continue
        seen_tool_names.add(tool.name)
        exposed_name = requested_name if requested_name in getattr(tool, "aliases", ()) else tool.name
        tools.append(
            _to_structured_tool(
                tool,
                exposed_name=exposed_name,
                tenant_id=tenant_id,
                trace=trace,
                tool_call_counts=tool_call_counts,
                tool_contract=tool_contract,
            )
        )
    return tools


def _to_structured_tool(
    tool: GatewayTool,
    *,
    exposed_name: str,
    tenant_id: str,
    trace: Any,
    tool_call_counts: dict[str, int],
    tool_contract: Any = None,
) -> StructuredTool:
    def _run(input: Any = "") -> str:
        arguments, evidence, sources = _coerce_tool_payload(input)
        allowed, count, max_calls = _consume_tool_budget(exposed_name, tool_call_counts)
        if not allowed:
            return json.dumps(
                {
                    "ok": False,
                    "error": f"tool call budget exceeded: {exposed_name} max {max_calls} per answer",
                    "warnings": [
                        f"{exposed_name} 已经调用 {count} 次；请停止重复调用该工具，基于已有结果或失败原因给出回答。"
                    ],
                    "evidence": [],
                    "artifacts": [],
                    "sources": [],
                    "duration_ms": 0,
                },
                ensure_ascii=False,
                default=str,
            )
        with trace_step(
            trace,
            f"agent_native.tool.{exposed_name}",
            {
                "tenant_id": tenant_id,
                "tool_name": tool.name,
                "exposed_name": exposed_name,
                "arguments": arguments,
                "call_count": count,
                "max_calls": max_calls,
            },
        ) as step:
            output = tool.run(
                ToolInput(arguments=arguments, evidence=evidence, sources=sources),
                ToolContext(tenant_id=tenant_id, metadata={"layer": "langgraph_agent_tools", "exposed_name": exposed_name}),
            )
            payload = output.to_dict()
            _contract_record(tool_contract, exposed_name, payload)
            set_step_output(step, payload)
        return json.dumps(payload, ensure_ascii=False, default=str)

    return StructuredTool.from_function(
        name=exposed_name,
        description=tool.description,
        func=_run,
    )


def _contract_record(tool_contract: Any, tool_name: str, payload: dict[str, Any]) -> None:
    if tool_contract is None:
        return
    try:
        tool_contract.record_tool_result(tool_name, payload)
    except Exception:
        return


def _consume_tool_budget(tool_name: str, tool_call_counts: dict[str, int]) -> tuple[bool, int, int]:
    max_calls = _tool_max_calls(tool_name)
    current = int(tool_call_counts.get(tool_name, 0) or 0)
    if current >= max_calls:
        return False, current, max_calls
    current += 1
    tool_call_counts[tool_name] = current
    return True, current, max_calls


def _tool_max_calls(tool_name: str) -> int:
    specific = os.getenv(f"GATEWAY_TOOL_MAX_CALLS_{tool_name.upper()}", "").strip()
    if specific:
        return _positive_int(specific, 1)
    if tool_name in {"generate_image_tool", "slide", "artifact.image_generate", "artifact.slide_generate"}:
        return _positive_int(os.getenv("GATEWAY_VISUAL_TOOL_MAX_CALLS", "1"), 1)
    return _positive_int(os.getenv("GATEWAY_TOOL_MAX_CALLS_DEFAULT", "8"), 8)


def _positive_int(raw: str, default: int) -> int:
    try:
        return max(1, int(str(raw or default).strip()))
    except Exception:
        return default


def _coerce_tool_payload(value: Any) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
    payload = value
    if isinstance(payload, str):
        text = payload.strip()
        if text.startswith("{") and text.endswith("}"):
            try:
                payload = json.loads(text)
            except Exception:
                payload = {"input": text}
        else:
            payload = {"input": text, "query": text}
    if not isinstance(payload, dict):
        payload = {"input": str(value or "")}

    arguments = dict(payload.get("arguments") or payload.get("args") or _payload_arguments(payload))
    evidence = payload.get("evidence")
    sources = payload.get("sources")
    if not isinstance(evidence, list):
        evidence = []
    if not isinstance(sources, list):
        sources = []
    return arguments, [item for item in evidence if isinstance(item, dict)], [item for item in sources if isinstance(item, dict)]


def _payload_arguments(payload: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in payload.items() if key not in {"evidence", "sources"}}
