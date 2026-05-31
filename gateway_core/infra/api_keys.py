from __future__ import annotations

import contextvars
import os
import time
from dataclasses import dataclass
from typing import Any

from gateway_core.infra.db_pool import connect_db


@dataclass(frozen=True)
class GatewayApiKeyRecord:
    api_key: str
    key_prefix: str
    key_type: str
    school_id: str
    schema_name: str
    display_name: str
    enabled: bool
    rate_limit_rpm: int


_API_KEY_RECORD_CTX: contextvars.ContextVar[GatewayApiKeyRecord | None] = contextvars.ContextVar(
    "gateway_api_key_record",
    default=None,
)
_CACHE: dict[str, tuple[float, GatewayApiKeyRecord | None]] = {}


CREATE_GATEWAY_API_KEYS_SQL = """
CREATE TABLE IF NOT EXISTS "{schema}"."{table}" (
    id             BIGSERIAL PRIMARY KEY,
    api_key        TEXT NOT NULL UNIQUE,
    key_prefix     TEXT NOT NULL DEFAULT '',
    key_type       TEXT NOT NULL DEFAULT 'school',
    school_id      TEXT NOT NULL DEFAULT '',
    schema_name    TEXT NOT NULL DEFAULT '',
    display_name   TEXT NOT NULL DEFAULT '',
    enabled        BOOLEAN NOT NULL DEFAULT TRUE,
    rate_limit_rpm INTEGER NOT NULL DEFAULT 20,
    notes          TEXT NOT NULL DEFAULT '',
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at     TIMESTAMPTZ NOT NULL DEFAULT now()
)
"""


def current_api_key_record() -> GatewayApiKeyRecord | None:
    return _API_KEY_RECORD_CTX.get()


def set_current_api_key_record(record: GatewayApiKeyRecord | None) -> None:
    _API_KEY_RECORD_CTX.set(record)


def api_key_db_enabled() -> bool:
    return str(os.getenv("GATEWAY_API_KEY_DB_ENABLED", "0") or "0").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def api_key_table_schema() -> str:
    return _clean_identifier(os.getenv("GATEWAY_API_KEY_TABLE_SCHEMA", "platform"), fallback="platform")


def api_key_table_name() -> str:
    return _clean_identifier(os.getenv("GATEWAY_API_KEY_TABLE", "gateway_api_keys"), fallback="gateway_api_keys")


def init_gateway_api_key_table(*, dsn: str, psycopg_module: Any) -> bool:
    if not str(dsn or "").strip() or psycopg_module is None:
        return False
    schema = api_key_table_schema()
    table = api_key_table_name()
    try:
        with connect_db(psycopg_module, dsn) as conn:
            with conn.cursor() as cur:
                cur.execute(f'CREATE SCHEMA IF NOT EXISTS "{schema}"')
                cur.execute(CREATE_GATEWAY_API_KEYS_SQL.format(schema=schema, table=table))
                cur.execute(f'CREATE INDEX IF NOT EXISTS idx_{table}_type_enabled ON "{schema}"."{table}" (key_type, enabled)')
                cur.execute(f'CREATE INDEX IF NOT EXISTS idx_{table}_school ON "{schema}"."{table}" (school_id)')
                cur.execute(f'CREATE INDEX IF NOT EXISTS idx_{table}_schema_name ON "{schema}"."{table}" (schema_name)')
                conn.commit()
        return True
    except Exception:
        return False


def lookup_gateway_api_key(
    *,
    token: str,
    dsn: str,
    psycopg_module: Any,
    initialize: bool = False,
) -> GatewayApiKeyRecord | None:
    clean_token = str(token or "").strip()
    if not clean_token or not str(dsn or "").strip() or psycopg_module is None:
        return None
    cache_key = clean_token
    now = time.time()
    ttl = _cache_ttl_seconds()
    cached = _CACHE.get(cache_key)
    if cached is not None and cached[0] > now:
        return cached[1]
    schema = api_key_table_schema()
    table = api_key_table_name()
    if initialize:
        init_gateway_api_key_table(dsn=dsn, psycopg_module=psycopg_module)
    try:
        with connect_db(psycopg_module, dsn) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    SELECT api_key, key_prefix, key_type, school_id, schema_name,
                           display_name, enabled, rate_limit_rpm
                    FROM "{schema}"."{table}"
                    WHERE api_key = %s
                    LIMIT 1
                    """,
                    [clean_token],
                )
                row = cur.fetchone()
    except Exception:
        _CACHE[cache_key] = (now + min(ttl, 5), None)
        return None
    record = _row_to_record(row) if row else None
    _CACHE[cache_key] = (now + ttl, record)
    return record


def clear_api_key_cache() -> None:
    _CACHE.clear()


def is_admin_record(record: GatewayApiKeyRecord | None) -> bool:
    return str(getattr(record, "key_type", "") or "").strip().lower() == "admin"


def is_policy_record(record: GatewayApiKeyRecord | None) -> bool:
    return str(getattr(record, "key_type", "") or "").strip().lower() == "policy"


def is_school_record(record: GatewayApiKeyRecord | None) -> bool:
    return str(getattr(record, "key_type", "") or "").strip().lower() == "school"


def _row_to_record(row: Any) -> GatewayApiKeyRecord:
    return GatewayApiKeyRecord(
        api_key=str(row[0] or ""),
        key_prefix=str(row[1] or "") or str(row[0] or "")[:8],
        key_type=str(row[2] or "school").strip().lower() or "school",
        school_id=str(row[3] or "").strip(),
        schema_name=str(row[4] or "").strip(),
        display_name=str(row[5] or "").strip(),
        enabled=bool(row[6]),
        rate_limit_rpm=int(row[7] or 20),
    )


def _cache_ttl_seconds() -> int:
    try:
        return max(1, min(int(os.getenv("GATEWAY_API_KEY_CACHE_TTL_SEC", "60") or "60"), 3600))
    except Exception:
        return 60


def _clean_identifier(value: str, *, fallback: str) -> str:
    text = str(value or "").strip().replace('"', "")
    if not text:
        return fallback
    if all(ch == "_" or ch.isalnum() for ch in text) and not text[0].isdigit():
        return text
    return fallback
