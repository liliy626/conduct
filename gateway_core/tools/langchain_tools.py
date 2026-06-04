from __future__ import annotations

import os
from typing import Any

from langchain_core.tools import StructuredTool

from gateway_core.tools.adapters.langchain_adapter import build_langchain_tools_from_registry
from gateway_core.tools.catalog import build_non_sql_tool_registry


def build_langchain_agent_tools(
    *,
    school_id: str = "",
    tenant_id: str = "",
    trace: Any,
    question: str = "",
    tool_contract: Any = None,
) -> list[StructuredTool]:
    """Expose optional non-SQL tools through the unified GatewayTool registry."""
    del question
    clean_tenant_id = str(school_id or tenant_id or "").strip()
    tool_policy = _tool_policy_for_contract(tool_contract) if tool_contract is not None else _default_tool_policy()
    registry = build_non_sql_tool_registry()
    return build_langchain_tools_from_registry(
        registry,
        allowed_names=tool_policy,
        tenant_id=clean_tenant_id,
        trace=trace,
        tool_contract=tool_contract,
    )


def _default_tool_policy() -> set[str]:
    return {
        "time",
        "official_policy_search",
        "web_search",
        "plot",
        "chart",
        "generate_image_tool",
        "slide",
    }


def _tool_policy_for_contract(tool_contract: Any) -> set[str]:
    names = {"time"}
    allowed_tools = getattr(tool_contract, "allowed_tools", set()) or set()
    default_policy = _default_tool_policy()
    names.update(str(item or "").strip() for item in allowed_tools if str(item or "").strip() in default_policy)
    required_outputs = getattr(tool_contract, "required_outputs", set()) or set()
    output_to_tool = {
        "policy_evidence": "official_policy_search",
        "web_evidence": "web_search",
        "chart_artifact": "chart",
        "plot_artifact": "plot",
        "image_artifact": "generate_image_tool",
        "slide_artifact": "slide",
    }
    for output_name in required_outputs:
        tool_name = output_to_tool.get(str(output_name or "").strip())
        if tool_name:
            names.add(tool_name)
    if _dynamic_tool_pruning_disabled():
        names.update(default_policy)
    return names


def _dynamic_tool_pruning_disabled() -> bool:
    return _truthy_env("SCHOOL_AGENT_DYNAMIC_TOOL_PRUNING_DISABLED", "0") or _truthy_env(
        "TENANT_AGENT_DYNAMIC_TOOL_PRUNING_DISABLED",
        "0",
    )


def _truthy_env(name: str, default: str = "0") -> bool:
    return (os.getenv(name, default).strip().lower() or default) in {"1", "true", "yes", "on"}
