#!/usr/bin/env python3
from __future__ import annotations

"""Runtime singletons and helpers for the OpenWebUI gateway.

This module no longer imports entry routing, feature routing, controlled-plan,
or answer fallback logic. The chat endpoint is Agent-native; this file only
owns shared infrastructure.
"""

import logging
import os
import sys
from pathlib import Path
from typing import Any

try:
    from dotenv import load_dotenv
except Exception:

    def load_dotenv(*_args: Any, **_kwargs: Any) -> bool:  # type: ignore[override]
        return False

from gateway_core.conversation.messages import (
    ChatCompletionRequest,
    now_ts as _cm_now_ts,
    stringify_content as _cm_stringify_content,
    to_lc_messages as _cm_to_lc_messages,
)
from gateway_core.observability.question_monitor import (
    _clip_monitor_text,
    _export_question_monitor_daily_merged,
    _hash_token_for_monitor,
    _question_monitor_enabled,
    _question_monitor_log_path,
    _read_question_monitor_recent,
    _write_question_monitor_event,
)
from gateway_core.runtime.gateway_config import (
    Gateway,
    RequestResultCache,
    _current_gateway_api_key_record,
    _current_gateway_key_is_admin,
    _current_user_id,
    _current_viewer_role,
    _gateway_auth_enabled,
    _get_gateway_keys,
    _require_gateway_auth,
    _request_cache_key as _config_request_cache_key,
    truthy_env as _truthy_env,
)
from gateway_core.runtime.runtime_postgres import _postgres_statement_timeout_ms
from gateway_core.runtime.runtime_rag_embedding import _rag_embed_text
from gateway_core.runtime.runtime_trace_context import (
    _add_trace_usage,
    _current_context_stage_trace,
    _current_trace_usage,
    _set_route_name,
    _set_trace_usage,
    _zero_usage,
)


load_dotenv(override=False)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.append(str(PROJECT_ROOT))

try:
    import psycopg
except Exception:
    psycopg = None


LOGGER = logging.getLogger("gateway.app")

_DEFAULT_ROOT = Path(__file__).resolve().parents[2]
CONFIG_PATH = Path(os.getenv("MODEL_CONFIG_PATH", _DEFAULT_ROOT / "model_config.yaml"))
GATEWAY = Gateway(CONFIG_PATH)
REQUEST_CACHE = RequestResultCache()


def _cm_last_user_text_raw(messages: list[Any], *, stringify_content=_cm_stringify_content) -> str:
    for message in reversed(messages or []):
        if str(getattr(message, "role", "") or "").lower() == "user":
            return stringify_content(getattr(message, "content", ""))
    return ""


def _cm_last_user_text(
    messages: list[Any],
    *,
    stringify_content=_cm_stringify_content,
    is_meta_instruction_prompt=None,
) -> str:
    text = _cm_last_user_text_raw(messages, stringify_content=stringify_content)
    if is_meta_instruction_prompt and is_meta_instruction_prompt(text):
        return ""
    return text


def _cm_previous_user_text(
    messages: list[Any],
    *,
    stringify_content=_cm_stringify_content,
    is_meta_instruction_prompt=None,
) -> str:
    seen_last = False
    for message in reversed(messages or []):
        if str(getattr(message, "role", "") or "").lower() != "user":
            continue
        text = stringify_content(getattr(message, "content", ""))
        if is_meta_instruction_prompt and is_meta_instruction_prompt(text):
            continue
        if not seen_last:
            seen_last = True
            continue
        return text
    return ""


def _ec_is_meta_instruction_prompt(question: str) -> bool:
    q = (question or "").strip().lower()
    if not q:
        return False
    markers = [
        "### task:",
        "### guidelines:",
        "### output:",
        "<chat_history>",
        "chat history",
        "follow-up questions",
        "response must be a json array",
        "json format:",
    ]
    return sum(1 for marker in markers if marker in q) >= 2


__all__ = [name for name in globals().keys() if not name.startswith("__")]
