from __future__ import annotations

import os
from typing import Any

from gateway_core.tools import ChartTool, GenerateImageTool, PolicyTool, PlotTool, SlideTool, TimeTool, WebSearchTool
from gateway_core.tools.core import AgentToolGatewayAdapter, GatewayToolRegistry
from gateway_core.tools.langchain_web_provider import build_web_search_provider


def build_non_sql_tool_registry() -> GatewayToolRegistry:
    registry = GatewayToolRegistry()
    for tool in _non_sql_tools():
        registry.register(tool)
    return registry


def _non_sql_tools() -> list[AgentToolGatewayAdapter]:
    return [
        AgentToolGatewayAdapter(
            TimeTool(),
            name="time.resolve",
            title="Resolve Time",
            tags=("time",),
            aliases=("time",),
        ),
        AgentToolGatewayAdapter(
            PolicyTool(),
            name="policy.official_policy_search",
            title="Official Policy Search",
            tags=("policy", "evidence"),
            risk_level="medium",
            scopes=("policy:read",),
            aliases=("official_policy_search",),
        ),
        AgentToolGatewayAdapter(
            WebSearchTool(enabled=_truthy_env("GATEWAY_WEB_SEARCH_ENABLED", "0"), provider=build_web_search_provider()),
            name="web.search",
            title="Web Search",
            tags=("web", "evidence"),
            risk_level="high",
            scopes=("web:search",),
            aliases=("web_search",),
        ),
        AgentToolGatewayAdapter(
            ChartTool(),
            name="artifact.chart",
            title="Chart Artifact",
            tags=("artifact", "chart"),
            aliases=("chart",),
        ),
        AgentToolGatewayAdapter(
            PlotTool(),
            name="artifact.plot",
            title="Plot Artifact",
            tags=("artifact", "plot"),
            aliases=("plot",),
        ),
        AgentToolGatewayAdapter(
            GenerateImageTool(enabled=_image_tool_enabled()),
            name="artifact.image_generate",
            title="Generate Image",
            tags=("artifact", "image"),
            risk_level="medium",
            aliases=("generate_image_tool", "image"),
        ),
        AgentToolGatewayAdapter(
            SlideTool(),
            name="artifact.slide_generate",
            title="Generate Slides",
            tags=("artifact", "slide"),
            aliases=("slide",),
        ),
    ]


def _truthy_env(name: str, default: str = "0") -> bool:
    return (os.getenv(name, default).strip().lower() or default) in {"1", "true", "yes", "on"}


def _image_tool_enabled() -> bool:
    return _truthy_env("GATEWAY_IMAGE_TOOL_ENABLED", "0") or _truthy_env("DASHSCOPE_IMAGE_ENABLED", "0")
