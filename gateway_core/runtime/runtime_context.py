"""Compatibility exports for pipeline-level modules.

This module keeps `chat_pipeline` and admin route modules decoupled from
`gateway_runtime` by centralizing historical names they rely on.
"""
from __future__ import annotations

from gateway_core import gateway_runtime as runtime

_RUNTIME_EXPORTS = [
    "ChatCompletionRequest",
    "GATEWAY",
    "REQUEST_CACHE",
    "_export_question_monitor_daily_merged",
    "_gateway_auth_enabled",
    "_get_gateway_keys",
    "_postgres_statement_timeout_ms",
    "_question_monitor_enabled",
    "_question_monitor_log_path",
    "_read_question_monitor_recent",
    "_require_gateway_auth",
    "_truthy_env",
    "psycopg",
    "LOGGER",
    "_add_trace_usage",
    "_clip_monitor_text",
    "_cm_last_user_text",
    "_cm_last_user_text_raw",
    "_cm_now_ts",
    "_cm_previous_user_text",
    "_cm_stringify_content",
    "_cm_to_lc_messages",
    "_config_request_cache_key",
    "_current_context_stage_trace",
    "_current_gateway_api_key_record",
    "_current_gateway_key_is_admin",
    "_current_user_id",
    "_current_trace_usage",
    "_current_viewer_role",
    "_ec_is_meta_instruction_prompt",
    "_hash_token_for_monitor",
    "_rag_embed_text",
    "_set_route_name",
    "_set_trace_usage",
    "_write_question_monitor_event",
    "_zero_usage",
]

_MISSING_RUNTIME_SYMBOLS = [name for name in _RUNTIME_EXPORTS if not hasattr(runtime, name)]
if _MISSING_RUNTIME_SYMBOLS:
    raise AttributeError(
        "runtime_context missing symbols from gateway_runtime: "
        + ", ".join(_MISSING_RUNTIME_SYMBOLS)
    )

for _name in _RUNTIME_EXPORTS:
    globals()[_name] = getattr(runtime, _name)

__all__ = list(_RUNTIME_EXPORTS)

del _RUNTIME_EXPORTS
del _MISSING_RUNTIME_SYMBOLS
