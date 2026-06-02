from __future__ import annotations

import json
import os
import threading
from typing import Any, Callable

from gateway_core.schema_context.query_experience_store import record_query_experience


def enqueue_sql_history_write(
    *,
    payload: dict[str, Any],
    dsn: str,
    psycopg_module: Any,
    embedding_fn: Callable[[str], list[float] | None] | None,
) -> str:
    """Queue a successful SQL step for non-blocking history persistence."""
    if not _enabled():
        return "disabled"
    if _redis_enabled():
        if _enqueue_redis(payload):
            return "redis"
    if _background_fallback_enabled():
        thread = threading.Thread(
            target=_write_payload_safe,
            kwargs={"payload": payload, "dsn": dsn, "psycopg_module": psycopg_module, "embedding_fn": embedding_fn},
            daemon=True,
        )
        thread.start()
        return "background_thread"
    return "skipped"


def process_sql_history_payload(
    *,
    payload: dict[str, Any],
    dsn: str,
    psycopg_module: Any,
    embedding_fn: Callable[[str], list[float] | None] | None,
) -> bool:
    return _write_payload_safe(payload=payload, dsn=dsn, psycopg_module=psycopg_module, embedding_fn=embedding_fn)


def _write_payload_safe(
    *,
    payload: dict[str, Any],
    dsn: str,
    psycopg_module: Any,
    embedding_fn: Callable[[str], list[float] | None] | None,
) -> bool:
    try:
        return record_query_experience(
            tenant_id=str(payload.get("tenant_id") or ""),
            question=str(payload.get("question") or ""),
            raw_sql=str(payload.get("sql") or ""),
            row_count=int(payload.get("row_count") or 0),
            answer_summary=str(payload.get("answer_summary") or ""),
            used_datasets=payload.get("used_datasets") if isinstance(payload.get("used_datasets"), list) else [],
            table_refs=[str(item) for item in payload.get("table_refs") or []],
            column_refs=[str(item) for item in payload.get("column_refs") or []],
            guardrail_version=str(payload.get("guardrail_version") or "v1"),
            dsn=dsn,
            psycopg_module=psycopg_module,
            embedding_fn=embedding_fn,
            schema=str(payload.get("schema") or ""),
            table=str(payload.get("table") or "sql_history_vector_documents"),
        )
    except Exception:
        return False


def _enqueue_redis(payload: dict[str, Any]) -> bool:
    try:
        import redis  # type: ignore

        client = redis.from_url(os.getenv("REDIS_URL", "redis://127.0.0.1:6379/0"), decode_responses=True)
        client.xadd(
            _redis_stream(),
            {"payload": json.dumps(payload, ensure_ascii=False, default=str)},
            maxlen=_redis_maxlen(),
            approximate=True,
        )
        return True
    except Exception:
        return False


def _enabled() -> bool:
    return str(os.getenv("SQL_HISTORY_WRITE_ENABLED", "1") or "1").strip().lower() in {"1", "true", "yes", "on"}


def _redis_enabled() -> bool:
    return str(os.getenv("SQL_HISTORY_WRITE_REDIS_ENABLED", "1") or "1").strip().lower() in {"1", "true", "yes", "on"}


def _background_fallback_enabled() -> bool:
    return str(os.getenv("SQL_HISTORY_WRITE_BACKGROUND_FALLBACK", "1") or "1").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def _redis_stream() -> str:
    return os.getenv("SQL_HISTORY_WRITE_STREAM", "sql_history:write").strip() or "sql_history:write"


def _redis_maxlen() -> int:
    try:
        return max(100, min(int(os.getenv("SQL_HISTORY_WRITE_STREAM_MAXLEN", "10000") or "10000"), 100000))
    except Exception:
        return 10000
