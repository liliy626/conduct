from __future__ import annotations

import os
from dataclasses import dataclass

from gateway_core.infra.postgres_dsn import agent_job_postgres_dsn


def truthy(value: str | None, default: str = "0") -> bool:
    return str(value if value is not None else default).strip().lower() in {"1", "true", "yes", "on"}


def positive_int_env(name: str, default: int = 1, maximum: int = 200) -> int:
    try:
        value = int(os.getenv(name, str(default)) or str(default))
    except ValueError:
        return default
    return max(1, min(maximum, value))


@dataclass(frozen=True)
class AgentJobConfig:
    enabled: bool
    redis_url: str
    postgres_dsn: str
    gateway_base_url: str
    queue_stream: str
    worker_group: str
    event_ttl_seconds: int
    stream_maxlen: int
    max_concurrency: int

    @classmethod
    def from_env(cls) -> "AgentJobConfig":
        return cls(
            enabled=truthy(os.getenv("AGENT_JOB_QUEUE_ENABLED"), "0"),
            redis_url=os.getenv("REDIS_URL", "redis://127.0.0.1:6379/0").strip(),
            postgres_dsn=agent_job_postgres_dsn(),
            gateway_base_url=os.getenv("AGENT_JOB_GATEWAY_BASE_URL", "http://127.0.0.1:8008").rstrip("/"),
            queue_stream=os.getenv("AGENT_JOB_QUEUE_STREAM", "agent_jobs:queue").strip(),
            worker_group=os.getenv("AGENT_JOB_WORKER_GROUP", "gateway_workers").strip(),
            event_ttl_seconds=int(os.getenv("AGENT_JOB_EVENT_TTL_SECONDS", "86400") or "86400"),
            stream_maxlen=int(os.getenv("AGENT_JOB_STREAM_MAXLEN", "5000") or "5000"),
            max_concurrency=positive_int_env("AGENT_JOB_MAX_CONCURRENCY", default=1, maximum=200),
        )
