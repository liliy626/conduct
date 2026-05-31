from __future__ import annotations

import os


def postgres_dsn() -> str:
    """Return the primary PostgreSQL DSN.

    `POSTGRES_DSN` is the canonical deployment variable. The legacy
    `MEILANHU_POSTGRES_DSN` name is kept only as a compatibility fallback.
    """
    return os.getenv("POSTGRES_DSN", "").strip() or os.getenv("MEILANHU_POSTGRES_DSN", "").strip()


def agent_job_postgres_dsn() -> str:
    """Return the DSN used by async agent job persistence."""
    return os.getenv("AGENT_JOB_POSTGRES_DSN", "").strip() or postgres_dsn()


def gateway_api_key_postgres_dsn() -> str:
    """Return the DSN used by DB-backed gateway API key lookup."""
    return os.getenv("GATEWAY_API_KEY_POSTGRES_DSN", "").strip() or postgres_dsn()


def postgres_dsn_configured() -> bool:
    return bool(postgres_dsn())
