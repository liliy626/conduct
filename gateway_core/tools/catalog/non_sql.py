from __future__ import annotations

import os
from typing import Any

from gateway_core.tools import (
    ChartTool,
    GenerateImageTool,
    PolicyTool,
    PlotTool,
    SlideTool,
    TimeTool,
    WebSearchTool,
)
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
            input_schema=_time_input_schema(),
            tags=("time",),
            aliases=("time",),
        ),
        AgentToolGatewayAdapter(
            PolicyTool(),
            name="policy.official_policy_search",
            title="Official Policy Search",
            input_schema=_policy_search_input_schema(),
            tags=("policy", "evidence"),
            risk_level="medium",
            scopes=("policy:read",),
            aliases=("official_policy_search",),
        ),
        AgentToolGatewayAdapter(
            WebSearchTool(enabled=_truthy_env("GATEWAY_WEB_SEARCH_ENABLED", "0"), provider=build_web_search_provider()),
            name="web.search",
            title="Web Search",
            input_schema=_web_search_input_schema(),
            tags=("web", "evidence"),
            risk_level="high",
            scopes=("web:search",),
            aliases=("web_search",),
        ),
        AgentToolGatewayAdapter(
            ChartTool(),
            name="artifact.chart",
            title="Chart Artifact",
            input_schema=_chart_input_schema(),
            tags=("artifact", "chart"),
            aliases=("chart",),
        ),
        AgentToolGatewayAdapter(
            PlotTool(),
            name="artifact.plot",
            title="Plot Artifact",
            input_schema=_plot_input_schema(),
            tags=("artifact", "plot"),
            aliases=("plot",),
        ),
        AgentToolGatewayAdapter(
            GenerateImageTool(enabled=_image_tool_enabled()),
            name="artifact.image_generate",
            title="Generate Image",
            input_schema=_image_input_schema(),
            tags=("artifact", "image"),
            risk_level="medium",
            scopes=("artifact:image_generate",),
            aliases=("generate_image_tool", "image"),
        ),
        AgentToolGatewayAdapter(
            SlideTool(),
            name="artifact.slide_generate",
            title="Generate Slides",
            input_schema=_slide_input_schema(),
            tags=("artifact", "slide"),
            aliases=("slide",),
        ),
    ]


def _time_input_schema() -> dict[str, Any]:
    return _object_schema(
        {
            "question": {"type": "string", "description": "Natural-language time expression to resolve."},
            "text": {"type": "string", "description": "Alias for question."},
            "timezone": {"type": "string", "description": "IANA timezone, defaults to gateway timezone."},
            "reference_date": {"type": "string", "format": "date"},
        }
    )


def _policy_search_input_schema() -> dict[str, Any]:
    return _object_schema(
        {
            "query": {"type": "string", "description": "Official-policy search query."},
            "question": {"type": "string", "description": "Alias for query."},
            "input": {"type": "string", "description": "Legacy alias for query."},
        },
        any_of=("query", "question", "input"),
    )


def _web_search_input_schema() -> dict[str, Any]:
    return _object_schema(
        {
            "query": {"type": "string", "description": "Sanitized external web-search query."},
            "input": {"type": "string", "description": "Legacy alias for query."},
            "locale": {"type": "string"},
            "date": {"type": "string"},
            "topic": {"type": "string"},
            "policy_domain": {"type": "string"},
        },
        any_of=("query", "input"),
    )


def _chart_input_schema() -> dict[str, Any]:
    return _object_schema(
        {
            "rows": _array_of_objects("Local evidence rows to render."),
            "evidence_rows": _array_of_objects("Alias for rows."),
            "chart_type": {"type": "string", "enum": ["line", "bar", "stacked_bar", "pie", "table"]},
            "title": {"type": "string"},
            "x": {"type": "string"},
            "x_field": {"type": "string"},
            "y": {"type": "string"},
            "y_field": {"type": "string"},
            "y_fields": {"type": "array", "items": {"type": "string"}},
            "series": {"type": "string"},
            "source_task_id": {"type": "string"},
        }
    )


def _plot_input_schema() -> dict[str, Any]:
    return _object_schema(
        {
            "rows": _array_of_objects("Local evidence rows to render."),
            "evidence_rows": _array_of_objects("Alias for rows."),
            "chart_type": {"type": "string", "enum": ["bar", "line", "scatter", "pie"]},
            "type": {"type": "string", "enum": ["bar", "line", "scatter", "pie"]},
            "title": {"type": "string"},
            "x": {"type": "string"},
            "x_field": {"type": "string"},
            "y": {"type": "string"},
            "y_field": {"type": "string"},
        }
    )


def _image_input_schema() -> dict[str, Any]:
    return _object_schema(
        {
            "prompt": {"type": "string", "description": "Required image generation or edit prompt."},
            "style": {"type": "string"},
            "size": {
                "type": "string",
                "enum": ["1024x1024", "1024x1536", "1536x1024", "1792x1024", "1024x1792"],
            },
            "image_ref": {"type": "string", "description": "Single source image reference for editing."},
            "image_refs": {"type": "array", "items": {"type": "string"}},
            "mask_ref": {"type": "string"},
        },
        required=("prompt",),
    )


def _slide_input_schema() -> dict[str, Any]:
    return _object_schema(
        {
            "title": {"type": "string"},
            "sections": {"type": "array", "items": {"type": "object", "additionalProperties": True}},
            "charts": {"type": "array", "items": {"type": "object", "additionalProperties": True}},
            "evidence_by_task": {"type": "object", "additionalProperties": True},
            "external_context": {
                "description": "Optional context; rejected when it contains sensitive person data."
            },
        }
    )


def _object_schema(
    properties: dict[str, Any],
    *,
    required: tuple[str, ...] = (),
    any_of: tuple[str, ...] = (),
) -> dict[str, Any]:
    schema: dict[str, Any] = {
        "type": "object",
        "properties": properties,
        "additionalProperties": True,
    }
    if required:
        schema["required"] = list(required)
    if any_of:
        schema["anyOf"] = [{"required": [field]} for field in any_of]
    return schema


def _array_of_objects(description: str) -> dict[str, Any]:
    return {
        "type": "array",
        "items": {"type": "object", "additionalProperties": True},
        "description": description,
    }


def _truthy_env(name: str, default: str = "0") -> bool:
    return (os.getenv(name, default).strip().lower() or default) in {"1", "true", "yes", "on"}


def _image_tool_enabled() -> bool:
    return _truthy_env("GATEWAY_IMAGE_TOOL_ENABLED", "0") or _truthy_env("DASHSCOPE_IMAGE_ENABLED", "0")
