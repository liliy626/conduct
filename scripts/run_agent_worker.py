from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
import socket
import sys
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from gateway_core.agents.jobs.config import AgentJobConfig
from gateway_core.agents.jobs.redis_streams import RedisAgentJobQueue
from gateway_core.agents.jobs.service import AgentJobService
from gateway_core.agents.jobs.store import PostgresAgentJobStore
from gateway_core.agents.jobs.worker import AgentJobWorker
from gateway_core.runtime.runtime_context import psycopg


def _is_redis_exception(exc: Exception, names: set[str]) -> bool:
    cls = exc.__class__
    return cls.__name__ in names and cls.__module__.startswith("redis")


async def read_worker_messages_once(
    client: Any,
    *,
    group: str,
    consumer: str,
    queue_stream: str,
    capacity: int,
    block_ms: int = 5000,
) -> list[Any]:
    try:
        return await client.xreadgroup(
            group,
            consumer,
            {queue_stream: ">"},
            count=capacity,
            block=block_ms,
        )
    except (TimeoutError, asyncio.TimeoutError) as exc:
        if _is_redis_exception(exc, {"TimeoutError"}) or isinstance(exc, (TimeoutError, asyncio.TimeoutError)):
            return []
        raise
    except Exception as exc:
        if _is_redis_exception(exc, {"TimeoutError"}):
            return []
        if _is_redis_exception(exc, {"ConnectionError"}):
            await asyncio.sleep(3)
            return []
        raise


async def main() -> None:
    cfg = AgentJobConfig.from_env()
    queue = RedisAgentJobQueue(
        redis_url=cfg.redis_url,
        queue_stream=cfg.queue_stream,
        stream_maxlen=cfg.stream_maxlen,
        event_ttl_seconds=cfg.event_ttl_seconds,
    )
    store = PostgresAgentJobStore(dsn=cfg.postgres_dsn, psycopg_module=psycopg)
    service = AgentJobService(store=store, queue=queue)
    worker = AgentJobWorker(service=service, gateway_base_url=cfg.gateway_base_url)
    client = queue.client
    group = cfg.worker_group
    consumer = os.getenv("AGENT_JOB_WORKER_NAME", f"{socket.gethostname()}-{os.getpid()}")
    max_concurrency = cfg.max_concurrency
    try:
        await client.xgroup_create(cfg.queue_stream, group, id="0", mkstream=True)
    except Exception:
        pass
    print(
        "agent worker listening "
        f"stream={cfg.queue_stream} group={group} consumer={consumer} concurrency={max_concurrency}",
        flush=True,
    )
    running: set[asyncio.Task[None]] = set()

    async def handle_message(message_id: str, fields: dict[str, Any]) -> None:
        job_id = fields.get("job_id", "")
        payload = json.loads(fields.get("payload") or "{}")
        try:
            await worker.run_job(
                job_id=job_id,
                authorization_token=payload.get("authorization_token", ""),
                school_scope=payload.get("school_scope", ""),
            )
        finally:
            await client.xack(cfg.queue_stream, group, message_id)

    while True:
        if running:
            done = {task for task in running if task.done()}
            for task in done:
                running.remove(task)
                task.result()
        capacity = max_concurrency - len(running)
        if capacity <= 0:
            done, _pending = await asyncio.wait(running, return_when=asyncio.FIRST_COMPLETED)
            for task in done:
                running.remove(task)
                task.result()
            continue
        result = await read_worker_messages_once(
            client,
            group=group,
            consumer=consumer,
            queue_stream=cfg.queue_stream,
            capacity=capacity,
        )
        if not result:
            continue
        for _, messages in result:
            for message_id, fields in messages:
                running.add(asyncio.create_task(handle_message(message_id, fields)))


if __name__ == "__main__":
    asyncio.run(main())
