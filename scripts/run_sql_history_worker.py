#!/usr/bin/env python3
from __future__ import annotations

import json
import os
from pathlib import Path
import socket
import sys
import time
from typing import Any

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from gateway_core.infra.postgres_dsn import postgres_dsn
from gateway_core.runtime.runtime_rag_embedding import _rag_embed_text
from gateway_core.schema_context.sql_history_write_queue import process_sql_history_payload


def _decode_payload(raw: str) -> dict[str, Any]:
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return payload if isinstance(payload, dict) else {}


def _is_redis_retryable_error(exc: BaseException) -> bool:
    exc_type = type(exc)
    return exc_type.__name__ in {"TimeoutError", "ConnectionError"} and str(exc_type.__module__).startswith("redis.")


def ensure_sql_history_group(client: Any, *, stream: str, group: str, start_id: str) -> None:
    try:
        client.xgroup_create(stream, group, id=start_id, mkstream=True)
    except Exception as exc:
        if "BUSYGROUP" in str(exc):
            return
        raise


def read_sql_history_messages(
    client: Any,
    *,
    stream: str,
    group: str,
    consumer: str,
    count: int,
    block_ms: int,
) -> list[tuple[str, dict[str, Any]]]:
    try:
        result = client.xreadgroup(group, consumer, {stream: ">"}, count=count, block=block_ms)
    except Exception as exc:
        if _is_redis_retryable_error(exc):
            return []
        raise
    messages: list[tuple[str, dict[str, Any]]] = []
    for _key, batch in result or []:
        messages.extend(batch)
    return messages


def main() -> int:
    load_dotenv(ROOT / ".env", override=False)

    import psycopg
    import redis

    dsn = postgres_dsn()
    if not dsn:
        raise RuntimeError("missing POSTGRES_DSN")

    redis_url = os.getenv("REDIS_URL", "redis://127.0.0.1:6379/0")
    stream = os.getenv("SQL_HISTORY_WRITE_STREAM", "sql_history:write")
    group = os.getenv("SQL_HISTORY_WORKER_GROUP", "sql_history_workers")
    group_start_id = os.getenv("SQL_HISTORY_WORKER_GROUP_START_ID", "0")
    consumer = os.getenv("SQL_HISTORY_WORKER_NAME", f"{socket.gethostname()}-{os.getpid()}")
    client = redis.from_url(redis_url, decode_responses=True)
    ensure_sql_history_group(client, stream=stream, group=group, start_id=group_start_id)
    print(f"sql history worker listening stream={stream} group={group} consumer={consumer}", flush=True)

    while True:
        messages = read_sql_history_messages(
            client,
            stream=stream,
            group=group,
            consumer=consumer,
            count=20,
            block_ms=5000,
        )
        if not messages:
            continue

        for message_id, fields in messages:
            try:
                payload = _decode_payload(fields.get("payload") or "{}")
                ok = process_sql_history_payload(
                    payload=payload,
                    dsn=dsn,
                    psycopg_module=psycopg,
                    embedding_fn=_rag_embed_text,
                )
                print(
                    json.dumps(
                        {
                            "id": message_id,
                            "ok": ok,
                            "schema": payload.get("schema"),
                            "row_count": payload.get("row_count"),
                            "ts": time.time(),
                        },
                        ensure_ascii=False,
                    ),
                    flush=True,
                )
            finally:
                client.xack(stream, group, message_id)


if __name__ == "__main__":
    raise SystemExit(main())
