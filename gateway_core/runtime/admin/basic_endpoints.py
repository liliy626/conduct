from __future__ import annotations

import os
from typing import Any, Dict

from gateway_core.infra.china_llm_defaults import DEFAULT_CHINA_EMBED_MODEL
from gateway_core.infra.api_keys import api_key_db_enabled, api_key_table_name, api_key_table_schema
from gateway_core.infra.postgres_dsn import postgres_dsn_configured
from gateway_core.runtime.runtime_context import (
    GATEWAY,
    REQUEST_CACHE,
    _export_question_monitor_daily_merged,
    _gateway_auth_enabled,
    _get_gateway_keys,
    _postgres_statement_timeout_ms,
    _question_monitor_enabled,
    _question_monitor_log_path,
    _read_question_monitor_recent,
    _truthy_env,
    psycopg,
)


def build_health_payload() -> Dict[str, Any]:
    pg_dsn_set = postgres_dsn_configured()
    return {
        "status": "ok",
        "default_model": GATEWAY.default_model,
        "model_count": len(GATEWAY.models),
        "config": str(GATEWAY.config_path),
        "gateway_auth_enabled": _gateway_auth_enabled(),
        "gateway_auth_keys_configured": bool(_get_gateway_keys()),
        "gateway_api_key_db_enabled": api_key_db_enabled(),
        "gateway_api_key_table": f"{api_key_table_schema()}.{api_key_table_name()}",
        "postgres_dsn_set": pg_dsn_set,
        "postgres_driver_loaded": psycopg is not None,
        "postgres_statement_timeout_ms": _postgres_statement_timeout_ms(),
        "school_schema_index_enabled": _truthy_env("SCHOOL_SCHEMA_INDEX_ENABLED", "1"),
        "intent_router_mode": os.getenv("INTENT_ROUTER_MODE", "hybrid").strip().lower() or "hybrid",
        "intent_router_embed_model": os.getenv("INTENT_ROUTER_EMBED_MODEL", DEFAULT_CHINA_EMBED_MODEL).strip(),
        "teacher_sensitive_auth_enabled": _truthy_env("TEACHER_SENSITIVE_AUTH_ENABLED", "1"),
        "teacher_sensitive_allowed_roles": [
            item.strip()
            for item in os.getenv(
                "TEACHER_SENSITIVE_ALLOWED_ROLES",
                "admin,principal,vice_principal,school_admin,hr,authorized_user,行政管理,行政管理员,校长,副校长,人事,授权用户",
            ).replace(";", ",").split(",")
            if item.strip()
        ],
        "teacher_sensitive_allowed_permissions": [
            item.strip()
            for item in os.getenv(
                "TEACHER_SENSITIVE_ALLOWED_PERMISSIONS",
                "teacher_sensitive,teacher_privacy,teacher_sensitive_view,teacher_private",
            ).replace(";", ",").split(",")
            if item.strip()
        ],
        "policy_vector_enabled": _truthy_env("POLICY_VECTOR_ENABLED", "0"),
        "policy_vector_schema": os.getenv("POLICY_VECTOR_SCHEMA", "official_policy").strip() or "official_policy",
        "policy_vector_top_k": os.getenv("POLICY_VECTOR_TOP_K", "5").strip() or "5",
        "policy_vector_min_relevance": os.getenv("POLICY_VECTOR_MIN_RELEVANCE", "0.20").strip() or "0.20",
        "request_cache": REQUEST_CACHE.stats(),
    }


def reload_gateway_runtime() -> Dict[str, Any]:
    GATEWAY.reload_config()
    REQUEST_CACHE.clear()
    return {
        "ok": True,
        "default_model": GATEWAY.default_model,
        "models": list(GATEWAY.models.keys()),
    }


def get_recent_question_monitor_payload(limit: int = 50) -> Dict[str, Any]:
    safe_limit = max(1, min(limit, 500))
    rows = _read_question_monitor_recent(safe_limit)
    return {
        "enabled": _question_monitor_enabled(),
        "log_path": str(_question_monitor_log_path()),
        "count": len(rows),
        "rows": rows,
    }


def export_daily_merged_monitor_payload(day: str = "") -> Dict[str, Any]:
    summary = _export_question_monitor_daily_merged(day=day)
    return {"ok": True, **summary}


def build_models_payload() -> Dict[str, Any]:
    data = [
        {
            "id": spec.model_id,
            "object": "model",
            "created": 0,
            "owned_by": "langchain-gateway",
        }
        for spec in GATEWAY.models.values()
    ]
    return {"object": "list", "data": data}
