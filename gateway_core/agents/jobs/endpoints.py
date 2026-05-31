from __future__ import annotations

from functools import lru_cache
from typing import Any, Optional

from fastapi import APIRouter, Header, HTTPException, Request
from fastapi.responses import StreamingResponse

from gateway_core.agents.jobs.config import AgentJobConfig
from gateway_core.agents.jobs.models import AgentJobCreateRequest, AgentJobStatusResponse
from gateway_core.agents.jobs.redis_streams import RedisAgentJobQueue, RedisUnavailable
from gateway_core.agents.jobs.service import AgentJobService
from gateway_core.agents.jobs.sse import format_sse_event
from gateway_core.agents.jobs.store import PostgresAgentJobStore
from gateway_core.runtime.runtime_context import _require_gateway_auth, psycopg
from gateway_core.infra.api_keys import current_api_key_record, is_school_record

router = APIRouter(prefix="/v1/agent/jobs", tags=["agent-jobs"])


@router.post("")
async def create_agent_job(
    payload: AgentJobCreateRequest,
    authorization: Optional[str] = Header(default=None),
    x_school_scope: Optional[str] = Header(default=None, alias="X-School-Scope"),
    x_user_id: Optional[str] = Header(default=None, alias="X-User-Id"),
    x_user_role: Optional[str] = Header(default=None, alias="X-User-Role"),
) -> dict[str, Any]:
    token = _require_gateway_auth(authorization)
    tenant_id = _resolve_tenant(token=token, school_scope=x_school_scope)
    service = _service()
    result = await service.create_job(
        request=payload,
        authorization_token=token,
        tenant_id=tenant_id,
        school_scope=x_school_scope or "",
        user_context={"user_id": x_user_id or "", "user_role": x_user_role or ""},
    )
    return result.model_dump()


@router.get("/{job_id}")
def get_agent_job(
    job_id: str,
    authorization: Optional[str] = Header(default=None),
    x_school_scope: Optional[str] = Header(default=None, alias="X-School-Scope"),
) -> dict[str, Any]:
    token = _require_gateway_auth(authorization)
    status = _job_status_for_request(job_id=job_id, token=token, school_scope=x_school_scope)
    return status.model_dump()


@router.get("/{job_id}/events")
async def stream_agent_job_events(
    job_id: str,
    request: Request,
    authorization: Optional[str] = Header(default=None),
    x_school_scope: Optional[str] = Header(default=None, alias="X-School-Scope"),
) -> StreamingResponse:
    token = _require_gateway_auth(authorization)
    service = _service()
    _job_status_for_request(job_id=job_id, token=token, school_scope=x_school_scope)
    last_id = request.headers.get("Last-Event-ID") or "0-0"
    if "-" not in last_id:
        last_id = "0-0"
    queue = service.queue
    if not hasattr(queue, "stream_events"):
        raise HTTPException(status_code=503, detail="agent job event stream is not configured")

    async def event_stream():
        async for event in queue.stream_events(job_id=job_id, last_id=last_id):  # type: ignore[attr-defined]
            yield format_sse_event(event)

    return StreamingResponse(event_stream(), media_type="text/event-stream")


@router.post("/{job_id}/cancel")
async def cancel_agent_job(
    job_id: str,
    authorization: Optional[str] = Header(default=None),
    x_school_scope: Optional[str] = Header(default=None, alias="X-School-Scope"),
) -> dict[str, Any]:
    token = _require_gateway_auth(authorization)
    _job_status_for_request(job_id=job_id, token=token, school_scope=x_school_scope)
    status = await _service().cancel_job(job_id)
    if not status:
        raise HTTPException(status_code=404, detail="agent job not found")
    return status.model_dump()


@lru_cache(maxsize=1)
def _service() -> AgentJobService:
    cfg = AgentJobConfig.from_env()
    if not cfg.enabled:
        raise HTTPException(status_code=503, detail="agent job queue disabled: set AGENT_JOB_QUEUE_ENABLED=1")
    try:
        queue = RedisAgentJobQueue(
            redis_url=cfg.redis_url,
            queue_stream=cfg.queue_stream,
            stream_maxlen=cfg.stream_maxlen,
            event_ttl_seconds=cfg.event_ttl_seconds,
        )
    except RedisUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    store = PostgresAgentJobStore(dsn=cfg.postgres_dsn, psycopg_module=psycopg)
    return AgentJobService(store=store, queue=queue)


def _resolve_tenant(*, token: str, school_scope: str | None) -> str:
    record = current_api_key_record()
    if is_school_record(record) and record is not None:
        tenant_id = record.school_id or record.schema_name
        if tenant_id:
            return tenant_id
    raise HTTPException(status_code=400, detail="school API key is required for agent jobs")


def _job_status_for_request(*, job_id: str, token: str, school_scope: str | None) -> AgentJobStatusResponse:
    service = _service()
    raw = service.store.get_job(job_id)
    if not raw:
        raise HTTPException(status_code=404, detail="agent job not found")
    tenant_id = _resolve_tenant(token=token, school_scope=school_scope)
    if str(raw.get("tenant_id") or "") != tenant_id:
        raise HTTPException(status_code=404, detail="agent job not found")
    return AgentJobStatusResponse(**raw)
