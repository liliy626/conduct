from __future__ import annotations

import json
import time
from typing import Any

from gateway_core.agents.jobs.models import AgentJobEvent, AgentJobRecord


class PostgresAgentJobStore:
    def __init__(self, *, dsn: str, psycopg_module: Any, schema: str = "platform") -> None:
        if not dsn:
            raise RuntimeError("agent job postgres dsn is required")
        if psycopg_module is None:
            raise RuntimeError("psycopg is required for agent job store")
        self.dsn = dsn
        self.psycopg = psycopg_module
        self.schema = schema
        self.ensure_tables()

    def ensure_tables(self) -> None:
        with self.psycopg.connect(self.dsn, connect_timeout=3) as conn:
            with conn.cursor() as cur:
                cur.execute(f"CREATE SCHEMA IF NOT EXISTS {self.schema}")
                cur.execute(
                    f"""
                    CREATE TABLE IF NOT EXISTS {self.schema}.agent_jobs (
                        job_id text PRIMARY KEY,
                        tenant_id text NOT NULL,
                        status text NOT NULL,
                        request_payload jsonb NOT NULL,
                        api_key_hash text NOT NULL DEFAULT '',
                        created_at double precision NOT NULL,
                        updated_at double precision NOT NULL,
                        started_at double precision,
                        finished_at double precision,
                        result_text text NOT NULL DEFAULT '',
                        error text NOT NULL DEFAULT '',
                        trace_id text NOT NULL DEFAULT ''
                    )
                    """
                )
                cur.execute(
                    f"""
                    CREATE TABLE IF NOT EXISTS {self.schema}.agent_job_events (
                        id bigserial PRIMARY KEY,
                        event_id text NOT NULL,
                        job_id text NOT NULL,
                        event_type text NOT NULL,
                        payload jsonb NOT NULL,
                        seq integer NOT NULL DEFAULT 0,
                        created_at double precision NOT NULL
                    )
                    """
                )
            conn.commit()

    def create_job(self, record: AgentJobRecord) -> AgentJobRecord:
        with self.psycopg.connect(self.dsn, connect_timeout=3) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    INSERT INTO {self.schema}.agent_jobs (
                        job_id, tenant_id, status, request_payload, api_key_hash,
                        created_at, updated_at, started_at, finished_at, result_text, error, trace_id
                    )
                    VALUES (%s, %s, %s, %s::jsonb, %s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        record.job_id,
                        record.tenant_id,
                        record.status,
                        json.dumps(record.request_payload, ensure_ascii=False),
                        record.api_key_hash,
                        record.created_at,
                        record.updated_at,
                        record.started_at,
                        record.finished_at,
                        record.result_text,
                        record.error,
                        record.trace_id,
                    ),
                )
            conn.commit()
        return record

    def get_job(self, job_id: str) -> dict[str, Any] | None:
        with self.psycopg.connect(self.dsn, connect_timeout=3) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    SELECT job_id, tenant_id, status, request_payload, api_key_hash,
                           created_at, updated_at, started_at, finished_at, result_text, error, trace_id
                    FROM {self.schema}.agent_jobs
                    WHERE job_id = %s
                    """,
                    (job_id,),
                )
                row = cur.fetchone()
        if not row:
            return None
        return {
            "job_id": row[0],
            "tenant_id": row[1],
            "status": row[2],
            "request_payload": row[3],
            "api_key_hash": row[4],
            "created_at": row[5],
            "updated_at": row[6],
            "started_at": row[7],
            "finished_at": row[8],
            "result_text": row[9],
            "error": row[10],
            "trace_id": row[11],
        }

    def update_job_status(self, job_id: str, status: str, **fields: Any) -> None:
        allowed = {"started_at", "finished_at", "result_text", "error", "trace_id"}
        set_parts = ["status = %s", "updated_at = %s"]
        params: list[Any] = [status, time.time()]
        for key, value in fields.items():
            if key in allowed:
                set_parts.append(f"{key} = %s")
                params.append(value)
        params.append(job_id)
        with self.psycopg.connect(self.dsn, connect_timeout=3) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"UPDATE {self.schema}.agent_jobs SET {', '.join(set_parts)} WHERE job_id = %s",
                    tuple(params),
                )
            conn.commit()

    def append_event(self, event: AgentJobEvent) -> None:
        with self.psycopg.connect(self.dsn, connect_timeout=3) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    INSERT INTO {self.schema}.agent_job_events
                        (event_id, job_id, event_type, payload, seq, created_at)
                    VALUES (%s, %s, %s, %s::jsonb, %s, %s)
                    """,
                    (
                        event.event_id,
                        event.job_id,
                        event.event_type,
                        json.dumps(event.payload, ensure_ascii=False),
                        event.seq,
                        event.created_at,
                    ),
                )
            conn.commit()
