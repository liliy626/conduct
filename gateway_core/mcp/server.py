from __future__ import annotations

from typing import Any

from gateway_core.infra.api_keys import current_api_key_record
from gateway_core.runtime.runtime_context import _require_gateway_auth
from gateway_core.tools.adapters.mcp_adapter import mcp_call_tool, mcp_tools_list
from gateway_core.tools.catalog import build_non_sql_tool_registry
from gateway_core.tools.core import GatewayToolRegistry, ToolContext


MCP_PROTOCOL_VERSION = "2025-06-18"


def route_mcp_json_rpc(payload: dict[str, Any], authorization: str | None = None) -> dict[str, Any]:
    token = _require_gateway_auth(authorization)
    record = current_api_key_record()
    context = ToolContext(
        tenant_id=str(getattr(record, "school_id", "") or getattr(record, "schema_name", "") or ""),
        metadata={
            "gateway_token_present": bool(str(token or "").strip()),
            "api_key_type": str(getattr(record, "key_type", "") or ""),
        },
    )
    return handle_mcp_message(payload, registry=build_non_sql_tool_registry(), context=context)


def handle_mcp_message(
    payload: dict[str, Any],
    *,
    registry: GatewayToolRegistry,
    context: ToolContext | None = None,
) -> dict[str, Any]:
    request_id = payload.get("id")
    method = str(payload.get("method") or "").strip()
    params = payload.get("params") if isinstance(payload.get("params"), dict) else {}
    if method == "initialize":
        return _result(
            request_id,
            {
                "protocolVersion": MCP_PROTOCOL_VERSION,
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": "conduct-gateway-mcp", "version": "0.1.0"},
            },
        )
    if method == "ping":
        return _result(request_id, {})
    if method == "notifications/initialized":
        return _result(request_id, {})
    if method == "tools/list":
        return _result(request_id, mcp_tools_list(registry))
    if method == "tools/call":
        name = str(params.get("name") or "").strip()
        arguments = params.get("arguments") if isinstance(params.get("arguments"), dict) else {}
        if not name:
            return _error(request_id, -32602, "tools/call requires params.name")
        return _result(
            request_id,
            mcp_call_tool(
                registry,
                name=name,
                arguments=arguments,
                context=context or ToolContext(),
            ),
        )
    return _error(request_id, -32601, f"method not found: {method}")


def _result(request_id: Any, result: dict[str, Any]) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def _error(request_id: Any, code: int, message: str) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}}
