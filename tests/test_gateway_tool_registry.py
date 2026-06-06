from __future__ import annotations

from dataclasses import dataclass, field

import pytest

from gateway_core.tools.adapters.langchain_adapter import build_langchain_tools_from_registry
from gateway_core.tools.adapters.mcp_adapter import mcp_call_tool, mcp_tools_list
from gateway_core.tools.catalog import build_non_sql_tool_registry
from gateway_core.tools.core import (
    ANY_OBJECT_SCHEMA,
    GATEWAY_TOOL_RESULT_SCHEMA,
    AgentToolGatewayAdapter,
    GatewayToolRegistry,
    ToolContext,
    ToolInput,
    ToolResult,
)
from gateway_core.tools.tool_core import AgentTool, AgentToolInput, ToolExecutionContext


def test_non_sql_registry_exposes_namespaced_tools_and_legacy_aliases() -> None:
    registry = build_non_sql_tool_registry()

    assert registry.get("policy.official_policy_search") is not None
    assert registry.get("official_policy_search") is registry.get("policy.official_policy_search")
    assert registry.get("time") is registry.get("time.resolve")
    assert "policy.official_policy_search" in registry.names()
    assert "official_policy_search" in registry.names(include_aliases=True)


def test_non_sql_registry_tools_follow_normalized_public_contract() -> None:
    registry = build_non_sql_tool_registry()
    tools = registry.list()

    assert tools
    for tool in tools:
        assert "." in tool.name
        assert tool.name == tool.name.strip()
        assert tool.title.strip()
        assert tool.description.strip()
        assert tool.input_schema.get("type") == "object"
        assert tool.output_schema.get("type") == "object"
        assert tool.tags
        assert tool.risk_level in {"low", "medium", "high"}
        assert all(alias.strip() == alias and "." not in alias for alias in tool.aliases)
        if tool.risk_level in {"medium", "high"}:
            assert tool.scopes


def test_non_sql_registry_tools_expose_explicit_input_schema() -> None:
    registry = build_non_sql_tool_registry()

    for tool in registry.list():
        assert tool.input_schema is not ANY_OBJECT_SCHEMA
        assert tool.input_schema.get("properties")
        assert set(tool.input_schema["properties"]) != {"arguments"}

    assert registry.get("policy.official_policy_search").input_schema["anyOf"] == [
        {"required": ["query"]},
        {"required": ["question"]},
        {"required": ["input"]},
    ]
    assert registry.get("web.search").input_schema["anyOf"] == [
        {"required": ["query"]},
        {"required": ["input"]},
    ]
    assert registry.get("artifact.image_generate").input_schema["required"] == ["prompt"]


def test_non_sql_registry_has_expected_canonical_tool_order() -> None:
    registry = build_non_sql_tool_registry()

    assert [tool.name for tool in registry.list()] == [
        "artifact.chart",
        "artifact.image_generate",
        "artifact.plot",
        "artifact.slide_generate",
        "policy.official_policy_search",
        "time.resolve",
        "web.search",
    ]


def test_registry_alias_conflict_does_not_leave_partial_registration() -> None:
    registry = GatewayToolRegistry()
    registry.register(_EchoTool(name="test.one", aliases=("echo",)))

    with pytest.raises(ValueError, match="tool alias already registered"):
        registry.register(_EchoTool(name="test.two", aliases=("echo",)))

    assert registry.get("test.two") is None
    assert registry.resolve_name("echo") == "test.one"
    assert registry.names(include_aliases=True) == {"test.one", "echo"}


def test_registry_rejects_canonical_name_that_conflicts_with_existing_alias() -> None:
    registry = GatewayToolRegistry()
    registry.register(_EchoTool(name="test.one", aliases=("echo",)))

    with pytest.raises(ValueError, match="tool name conflicts with registered alias"):
        registry.register(_EchoTool(name="echo"))

    assert registry.get("echo") is registry.get("test.one")
    assert registry.names() == {"test.one"}


def test_mcp_tools_list_uses_namespaced_names_not_legacy_aliases() -> None:
    payload = mcp_tools_list(build_non_sql_tool_registry())
    names = {item["name"] for item in payload["tools"]}

    assert "policy.official_policy_search" in names
    assert "official_policy_search" not in names
    assert "artifact.chart" in names


def test_mcp_call_tool_returns_structured_content() -> None:
    registry = GatewayToolRegistry()
    registry.register(_EchoTool())

    payload = mcp_call_tool(
        registry,
        name="test.echo",
        arguments={"message": "hello"},
        context=ToolContext(tenant_id="sch_test"),
    )

    assert payload["isError"] is False
    assert payload["structuredContent"]["ok"] is True
    assert payload["structuredContent"]["structured_content"]["message"] == "hello"
    assert set(GATEWAY_TOOL_RESULT_SCHEMA["properties"]).issubset(payload["structuredContent"])


def test_langchain_adapter_dedupes_canonical_name_and_alias() -> None:
    registry = GatewayToolRegistry()
    registry.register(_EchoTool(aliases=("echo",)))

    tools = build_langchain_tools_from_registry(registry, allowed_names={"test.echo", "echo"})

    assert [tool.name for tool in tools] == ["echo"]


def test_agent_tool_adapter_preserves_lineage() -> None:
    adapter = AgentToolGatewayAdapter(_LineageAgentTool(), name="test.lineage")

    output = adapter.run(ToolInput(arguments={"message": "ok"}), ToolContext(tenant_id="sch_test"))

    assert output.ok is True
    assert output.lineage == [{"tool": "legacy", "tenant_id": "sch_test"}]


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


class _LineageAgentTool(AgentTool):
    name = "legacy_lineage"
    description = "Legacy lineage tool"

    def run(self, tool_input: AgentToolInput, context: ToolExecutionContext) -> object:
        del tool_input

        class _Output:
            def to_dict(self) -> dict:
                return {
                    "ok": True,
                    "lineage": [{"tool": "legacy", "tenant_id": context.tenant_id}],
                }

        return _Output()
