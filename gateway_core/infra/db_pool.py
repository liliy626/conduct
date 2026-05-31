from __future__ import annotations

import os
import shlex
import threading
from contextlib import contextmanager
from typing import Any, Dict, Iterator, Optional, Tuple

try:
    from psycopg_pool import ConnectionPool
except Exception:
    ConnectionPool = None


_DB_POOL_LOCK = threading.Lock()
_DB_POOLS: Dict[str, Any] = {}
_SESSION_RESET_DEFAULTS = {
    "app.school_key": "",
    "app.enforce_school_rls": "0",
}


def _truthy_env(name: str, default: str = "0") -> bool:
    """中文注释：封装_truthy_env的内部处理逻辑。"""
    return str(os.getenv(name, default) or "").strip().lower() in {"1", "true", "yes", "on"}


def _db_pool_min_size() -> int:
    """中文注释：封装_db_pool_min_size的内部处理逻辑。"""
    raw = os.getenv("DB_CONNECTION_POOL_MIN_SIZE", "2").strip()
    try:
        return max(1, int(raw))
    except Exception:
        return 2


def _db_pool_max_size() -> int:
    """中文注释：封装_db_pool_max_size的内部处理逻辑。"""
    raw = os.getenv("DB_CONNECTION_POOL_MAX_SIZE", "10").strip()
    try:
        return max(_db_pool_min_size(), int(raw))
    except Exception:
        return max(10, _db_pool_min_size())


def _can_use_db_pool(psycopg_module: Any) -> bool:
    """中文注释：封装_can_use_db_pool的内部处理逻辑。"""
    return (
        ConnectionPool is not None
        and bool(psycopg_module)
        and getattr(psycopg_module, "__name__", "") == "psycopg"
        and _truthy_env("DB_CONNECTION_POOL_ENABLED", "1")
    )


def _split_session_options_from_conninfo(psycopg_module: Any, dsn: str) -> Tuple[str, Dict[str, str]]:
    """中文注释：封装_split_session_options_from_conninfo的内部处理逻辑。"""
    if not dsn or not psycopg_module:
        return dsn, {}
    conninfo_mod = getattr(psycopg_module, "conninfo", None)
    if conninfo_mod is None:
        return dsn, {}
    to_dict = getattr(conninfo_mod, "conninfo_to_dict", None)
    make_conninfo = getattr(conninfo_mod, "make_conninfo", None)
    if not callable(to_dict) or not callable(make_conninfo):
        return dsn, {}

    try:
        params = dict(to_dict(dsn))
    except Exception:
        return dsn, {}

    options_raw = str(params.get("options") or "").strip()
    if not options_raw:
        return dsn, {}

    session_vars: Dict[str, str] = {}
    keep_tokens = []
    tokens = shlex.split(options_raw)

    def consume_setting(setting: str) -> bool:
        """中文注释：实现consume_setting的核心业务处理流程。"""
        if "=" not in setting:
            return False
        key, value = setting.split("=", 1)
        k = str(key or "").strip()
        if k not in {"app.school_key", "app.enforce_school_rls"}:
            return False
        session_vars[k] = str(value or "")
        return True

    i = 0
    while i < len(tokens):
        token = tokens[i]
        if token == "-c" and i + 1 < len(tokens):
            setting = tokens[i + 1]
            if consume_setting(setting):
                i += 2
                continue
            keep_tokens.extend([token, setting])
            i += 2
            continue
        if token.startswith("-c") and len(token) > 2:
            setting = token[2:]
            if consume_setting(setting):
                i += 1
                continue
        keep_tokens.append(token)
        i += 1

    if keep_tokens:
        params["options"] = " ".join(keep_tokens)
    else:
        params.pop("options", None)

    try:
        base_dsn = make_conninfo(**params)
    except Exception:
        return dsn, session_vars
    return base_dsn, session_vars


def _pool_key(psycopg_module: Any, dsn: str) -> str:
    """中文注释：封装_pool_key的内部处理逻辑。"""
    return f"{getattr(psycopg_module, '__name__', 'unknown')}::{dsn}"


def _get_db_pool(psycopg_module: Any, dsn: str) -> Optional[Any]:
    """中文注释：封装_get_db_pool的内部处理逻辑。"""
    if not dsn or not _can_use_db_pool(psycopg_module):
        return None
    key = _pool_key(psycopg_module, dsn)
    with _DB_POOL_LOCK:
        pool = _DB_POOLS.get(key)
        if pool is not None:
            return pool
        try:
            pool = ConnectionPool(
                conninfo=dsn,
                min_size=_db_pool_min_size(),
                max_size=_db_pool_max_size(),
            )
        except Exception:
            return None
        _DB_POOLS[key] = pool
        return pool


def _apply_session_vars(conn: Any, session_vars: Dict[str, str]) -> None:
    """中文注释：封装_apply_session_vars的内部处理逻辑。"""
    merged = dict(_SESSION_RESET_DEFAULTS)
    for key, value in (session_vars or {}).items():
        merged[str(key)] = str(value)
    if not merged:
        return
    with conn.cursor() as cur:
        for key, value in merged.items():
            cur.execute("SELECT set_config(%s, %s, false)", (key, value))


@contextmanager
def connect_db(psycopg_module: Any, dsn: str) -> Iterator[Any]:
    """中文注释：实现connect_db的核心业务处理流程。"""
    if psycopg_module is None:
        raise RuntimeError("psycopg module is required")
    base_dsn, session_vars = _split_session_options_from_conninfo(psycopg_module, dsn)
    pool = _get_db_pool(psycopg_module, base_dsn)
    if pool is not None:
        with pool.connection() as conn:
            _apply_session_vars(conn, session_vars)
            yield conn
        return
    with psycopg_module.connect(base_dsn) as conn:
        _apply_session_vars(conn, session_vars)
        yield conn
