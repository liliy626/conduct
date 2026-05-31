from __future__ import annotations

import hashlib
from typing import Any, Protocol

from gateway_core.agents.jobs.models import (
    AgentJobCreateRequest,
    AgentJobCreateResponse,
    AgentJobEvent,
    AgentJobRecord,
    AgentJobStatusResponse,
)


class AgentJobStore(Protocol):
    def create_job(self, record: AgentJobRecord) -> AgentJobRecord: ...
    def get_job(self, job_id: str) -> dict[str, Any] | None: ...
    def update_job_status(self, job_id: str, status: str, **fields: Any) -> None: ...
    def append_event(self, event: AgentJobEvent) -> None: ...


class AgentJobQueue(Protocol):
    async def enqueue_job(self, *, job_id: str, tenant_id: str, payload: dict[str, Any]) -> str: ...
    async def publish_event(self, event: AgentJobEvent) -> str: ...


class AgentJobService:
    def __init__(self, *, store: AgentJobStore, queue: AgentJobQueue) -> None:
        self.store = store
        self.queue = queue

    async def create_job(
        self,
        *,
        request: AgentJobCreateRequest,
        authorization_token: str,
        tenant_id: str,
        school_scope: str = "",
        user_context: dict[str, Any] | None = None,
    ) -> AgentJobCreateResponse:
        payload = request.model_dump(exclude_none=True)
        payload["stream"] = True
        record = AgentJobRecord(
            tenant_id=tenant_id,
            request_payload=payload,
            api_key_hash=_hash_token(authorization_token),
        )
        self.store.create_job(record)
        await self.queue.enqueue_job(
            job_id=record.job_id,
            tenant_id=tenant_id,
            payload={
                "authorization_token": authorization_token,
                "school_scope": school_scope,
                "user_context": user_context or {},
            },
        )
        await self.emit(record.job_id, "job_queued", {"tenant_id": tenant_id})
        return AgentJobCreateResponse(
            job_id=record.job_id,
            tenant_id=tenant_id,
            status="queued",
            events_url=f"/v1/agent/jobs/{record.job_id}/events",
            status_url=f"/v1/agent/jobs/{record.job_id}",
        )

    def get_status(self, job_id: str) -> AgentJobStatusResponse | None:
        raw = self.store.get_job(job_id)
        if not raw:
            return None
        return AgentJobStatusResponse(**raw)

    async def cancel_job(self, job_id: str) -> AgentJobStatusResponse | None:
        raw = self.store.get_job(job_id)
        if not raw:
            return None
        if raw.get("status") not in {"succeeded", "failed", "cancelled"}:
            self.store.update_job_status(job_id, "cancelled")
            await self.emit(job_id, "job_cancelled", {})
        return self.get_status(job_id)

    async def emit(self, job_id: str, event_type: str, payload: dict[str, Any] | None = None) -> AgentJobEvent:
        event = AgentJobEvent(job_id=job_id, event_type=event_type, payload=payload or {})
        self.store.append_event(event)
        await self.queue.publish_event(event)
        return event


def _hash_token(token: str) -> str:
    clean = str(token or "").strip()
    if not clean:
        return ""
    return hashlib.sha256(clean.encode("utf-8")).hexdigest()[:16]
