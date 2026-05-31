from __future__ import annotations

import os
from typing import Any, List

from gateway_core.infra.db_pool import connect_db
from gateway_core.infra.postgres_dsn import postgres_dsn
from gateway_core.runtime.gateway_config import truthy_env as _truthy_env

try:
    import psycopg
except Exception:
    psycopg = None

def _connect_pg(dsn: str) -> Any:
    """按统一连接池策略拿一个 PostgreSQL 连接。"""
    return connect_db(psycopg, dsn)


def _should_use_request_cache(spec: ModelSpec, req: ChatCompletionRequest) -> bool:
    """判断这次请求要不要走结果缓存。"""
    return _policy_should_use_request_cache(spec, req, REQUEST_CACHE.max_cache_temperature())


def _postgres_statement_timeout_ms() -> int:
    """读取数据库语句超时配置，单位毫秒。"""
    raw = os.getenv("POSTGRES_STATEMENT_TIMEOUT_MS", "8000").strip()
    try:
        return max(0, int(float(raw)))
    except Exception:
        return 8000


def _escape_pg_option_value(value: str) -> str:
    """转义 PostgreSQL options 里的值，避免空格和反斜杠破坏 DSN。"""
    return str(value or "").replace("\\", "\\\\").replace(" ", "\\ ")


def _postgres_dsn_for_scope(school_scope: str = "") -> str:
    """为当前学校作用域拼出可直接连接数据库的 DSN。

    这里除了原始 DSN，还会附带语句超时、学校隔离等运行时参数。
    """
    dsn = postgres_dsn()
    if not dsn or psycopg is None:
        return dsn

    options_parts: List[str] = []
    stmt_timeout_ms = _postgres_statement_timeout_ms()
    if stmt_timeout_ms > 0:
        options_parts.append(f"-c statement_timeout={stmt_timeout_ms}")

    if school_scope:
        options_parts.append(f"-c app.school_key={_escape_pg_option_value(school_scope)}")

    if _truthy_env("POSTGRES_RLS_ENFORCE_SCHOOL", "0"):
        options_parts.append("-c app.enforce_school_rls=1")

    if not options_parts:
        return dsn

    try:
        return psycopg.conninfo.make_conninfo(dsn, options=" ".join(options_parts))
    except Exception:
        return dsn
