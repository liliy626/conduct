from __future__ import annotations

from dataclasses import dataclass, field

from gateway_core.mcp.server import handle_mcp_message
from gateway_core.tools.core import ANY_OBJECT_SCHEMA, GATEWAY_TOOL_RESULT_SCHEMA, GatewayToolRegistry, ToolContext, ToolInput, ToolResult


def test_mcp_initialize_advertises_tool_capability() -> None:
    response = handle_mcp_message(
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
        registry=GatewayToolRegistry(),
    )

    assert response["result"]["capabilities"]["tools"]["listChanged"] is False


def test_mcp_tools_call_uses_registry_only() -> None:
    registry = GatewayToolRegistry()
    registry.register(_EchoTool())

    response = handle_mcp_message(
        {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {"name": "test.echo", "arguments": {"message": "ok"}},
        },
        registry=registry,
        context=ToolContext(tenant_id="sch_test"),
    )

    result = response["result"]
    assert result["isError"] is False
    assert result["structuredContent"]["message"] == "ok"


@dataclass
class _EchoTool:
    name: str = "test.echo"
    title: str = "Echo"
    description: str = "Echo test tool"
    input_schema: dict = field(default_factory=lambda: dict(ANY_OBJECT_SCHEMA))
    output_schema: dict = field(default_factory=lambda: dict(GATEWAY_TOOL_RESULT_SCHEMA))
    tags: tuple[str, ...] = ("test",)
    risk_level: str = "low"
    scopes: tuple[str, ...] = ()
    aliases: tuple[str, ...] = ()

    def run(self, tool_input: ToolInput, context: ToolContext) -> ToolResult:
        del context
        return ToolResult(ok=True, structured_content={"message": tool_input.arguments.get("message")})
