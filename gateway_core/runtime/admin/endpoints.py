from __future__ import annotations

from typing import Any, Dict, Optional

from gateway_core.runtime.admin.basic_endpoints import (
    build_health_payload,
    build_models_payload,
    export_daily_merged_monitor_payload,
    get_recent_question_monitor_payload,
    reload_gateway_runtime,
)
from gateway_core.runtime.admin.school_trace_html import build_school_trace_dashboard_html
from gateway_core.runtime.admin.token_usage_html import build_token_usage_dashboard_html
from gateway_core.runtime.admin.school_trace_metrics import (
    build_api_usage_metrics,
    build_school_trace_metrics,
    build_token_usage_payload,
    extract_trace_token_usage,
)
from gateway_core.runtime.runtime_context import _hash_token_for_monitor, _read_question_monitor_recent, _require_gateway_auth
from gateway_core.infra.api_keys import current_api_key_record, is_admin_record, is_policy_record, is_school_record
from gateway_core.observability.langfuse_exporter import langfuse_status
from gateway_core.school.trace import build_trace_timing, get_trace, recent_traces

def route_health() -> Dict[str, Any]:
    return build_health_payload()


def route_reload_config(authorization: Optional[str]) -> Dict[str, Any]:
    _require_gateway_auth(authorization)
    return reload_gateway_runtime()


def route_recent_question_monitor(
    authorization: Optional[str], limit: int = 50
) -> Dict[str, Any]:
    _require_gateway_auth(authorization)
    return get_recent_question_monitor_payload(limit=limit)


def route_export_daily_merged_monitor(authorization: Optional[str], day: str = "") -> Dict[str, Any]:
    _require_gateway_auth(authorization)
    return export_daily_merged_monitor_payload(day=day)


def route_list_models(authorization: Optional[str]) -> Dict[str, Any]:
    _require_gateway_auth(authorization)
    return build_models_payload()


def route_school_trace_recent(authorization: Optional[str], limit: int = 50) -> Dict[str, Any]:
    token = _require_gateway_auth(authorization)
    school_id = _school_trace_scope_for_token(token)
    payload = recent_traces(limit=limit, school_id=school_id)
    items = payload.get("items") if isinstance(payload.get("items"), list) else []
    traces = []
    for item in items:
        if not isinstance(item, dict):
            continue
        trace_id = str(item.get("trace_id") or "").strip()
        if not trace_id:
            continue
        trace = get_trace(trace_id, school_id=school_id)
        if isinstance(trace, dict):
            traces.append(trace)
            item["token_usage"] = extract_trace_token_usage(trace)
    payload["metrics"] = build_school_trace_metrics(traces)
    monitor_rows = _read_question_monitor_recent(max(200, min(max(limit, 1) * 5, 500)))
    if school_id is not None:
        token_hash = _hash_token_for_monitor(token)
        monitor_rows = [row for row in monitor_rows if isinstance(row, dict) and row.get("token_hash") == token_hash]
    payload["api_usage_metrics"] = build_api_usage_metrics(monitor_rows)
    return payload


def route_school_trace_dashboard_html() -> str:
    return build_school_trace_dashboard_html()


def route_token_usage_dashboard_html() -> str:
    return build_token_usage_dashboard_html()


def route_token_usage(authorization: Optional[str], limit: int = 1000) -> Dict[str, Any]:
    token = _require_gateway_auth(authorization)
    safe_limit = max(1, min(int(limit or 1000), 5000))
    rows = _read_question_monitor_recent(safe_limit)
    record = current_api_key_record()
    if not is_admin_record(record):
        token_hash = _hash_token_for_monitor(token)
        rows = [row for row in rows if isinstance(row, dict) and row.get("token_hash") == token_hash]
    return {
        "limit": safe_limit,
        "scope": "all" if is_admin_record(record) else "current_api_key",
        **build_token_usage_payload(rows),
    }


def route_langfuse_status(authorization: Optional[str] = None) -> Dict[str, Any]:
    _require_gateway_auth(authorization)
    return langfuse_status()


def route_school_trace_detail(authorization: Optional[str], trace_id: str) -> Dict[str, Any]:
    token = _require_gateway_auth(authorization)
    school_id = _school_trace_scope_for_token(token)
    trace = get_trace(trace_id, school_id=school_id)
    if trace is None:
        return {"trace_id": trace_id, "found": False}
    trace["found"] = True
    trace["timing"] = build_trace_timing(trace)
    trace["token_usage"] = extract_trace_token_usage(trace)
    return trace


def _school_trace_scope_for_token(token: Optional[str]) -> str | None:
    record = current_api_key_record()
    if is_admin_record(record):
        return None
    if is_policy_record(record):
        return "official_policy"
    if is_school_record(record) and record is not None:
        return record.school_id or record.schema_name or "__no_school_scope__"
    return "__no_school_scope__"
