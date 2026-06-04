from __future__ import annotations

from typing import Any

from gateway_core.tools.core import GatewayTool, GatewayToolRegistry, ToolContext, ToolInput


def mcp_tool_descriptor(tool: GatewayTool) -> dict[str, Any]:
    return {
        "name": tool.name,
        "title": tool.title,
        "description": tool.description,
        "inputSchema": tool.input_schema,
        "outputSchema": tool.output_schema,
        "annotations": {
            "tags": list(tool.tags),
            "risk_level": tool.risk_level,
            "scopes": list(tool.scopes),
            "aliases": list(tool.aliases),
        },
    }


def mcp_tools_list(registry: GatewayToolRegistry) -> dict[str, Any]:
    return {"tools": [mcp_tool_descriptor(tool) for tool in registry.list()]}


def mcp_call_tool(
    registry: GatewayToolRegistry,
    *,
    name: str,
    arguments: dict[str, Any] | None = None,
    context: ToolContext | None = None,
) -> dict[str, Any]:
    output = registry.run(name, ToolInput(arguments=dict(arguments or {})), context or ToolContext())
    payload = output.to_dict()
    content_text = str(payload.get("error") or "") if not output.ok else _summary_text(payload)
    return {
        "content": [{"type": "text", "text": content_text}],
        "structuredContent": payload.get("structured_content") or payload,
        "isError": not bool(output.ok),
    }


def _summary_text(payload: dict[str, Any]) -> str:
    if payload.get("structured_content"):
        return "tool call completed"
    if payload.get("sources"):
        return f"tool call completed with {len(payload.get('sources') or [])} source(s)"
    if payload.get("artifacts"):
        return f"tool call completed with {len(payload.get('artifacts') or [])} artifact(s)"
    return "tool call completed"
