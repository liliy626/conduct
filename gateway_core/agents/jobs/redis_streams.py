from __future__ import annotations

import json
from typing import Any, AsyncIterator

from gateway_core.agents.jobs.models import AgentJobEvent


class RedisUnavailable(RuntimeError):
    pass


def _redis_async_module() -> Any:
    try:
        import redis.asyncio as redis_async  # type: ignore
    except Exception as exc:  # pragma: no cover - depends on optional package
        raise RedisUnavailable("redis package is not installed; install redis>=5 or disable agent jobs") from exc
    return redis_async


class RedisAgentJobQueue:
    def __init__(self, *, redis_url: str, queue_stream: str, stream_maxlen: int = 5000, event_ttl_seconds: int = 86400):
        redis_async = _redis_async_module()
        self.client = redis_async.from_url(redis_url, decode_responses=True)
        self.queue_stream = queue_stream
        self.stream_maxlen = stream_maxlen
        self.event_ttl_seconds = event_ttl_seconds

    async def enqueue_job(self, *, job_id: str, tenant_id: str, payload: dict[str, Any]) -> str:
        return await self.client.xadd(
            self.queue_stream,
            {"job_id": job_id, "tenant_id": tenant_id, "payload": json.dumps(payload, ensure_ascii=False)},
            maxlen=self.stream_maxlen,
            approximate=True,
        )

    async def publish_event(self, event: AgentJobEvent) -> str:
        key = event_stream_key(event.job_id)
        message_id = await self.client.xadd(
            key,
            {"event": event.model_dump_json()},
            maxlen=self.stream_maxlen,
            approximate=True,
        )
        if self.event_ttl_seconds > 0:
            await self.client.expire(key, self.event_ttl_seconds)
        return message_id

    async def stream_events(self, *, job_id: str, last_id: str = "0-0") -> AsyncIterator[AgentJobEvent]:
        key = event_stream_key(job_id)
        current_id = last_id or "0-0"
        while True:
            result = await self.client.xread({key: current_id}, count=20, block=30000)
            if not result:
                yield AgentJobEvent(job_id=job_id, event_type="heartbeat", payload={})
                continue
            for _, messages in result:
                for message_id, fields in messages:
                    current_id = message_id
                    raw = fields.get("event") or "{}"
                    event = AgentJobEvent.model_validate_json(raw)
                    yield event
                    if event.event_type in {"job_succeeded", "job_failed", "job_cancelled"}:
                        return


def event_stream_key(job_id: str) -> str:
    return f"agent_job:{job_id}:events"
