from __future__ import annotations

import time
import uuid
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field


JobStatus = Literal["queued", "running", "succeeded", "failed", "cancelled"]


class AgentJobCreateRequest(BaseModel):
    model_config = ConfigDict(extra="allow")

    model: Optional[str] = None
    messages: List[Dict[str, Any]]
    chat_id: Optional[str] = None
    conversation_id: Optional[str] = None
    session_id: Optional[str] = None
    thread_id: Optional[str] = None
    metadata: Optional[Dict[str, Any]] = None
    stream: Optional[bool] = None


class AgentJobRecord(BaseModel):
    job_id: str = Field(default_factory=lambda: f"job_{uuid.uuid4().hex}")
    tenant_id: str
    status: JobStatus = "queued"
    request_payload: dict[str, Any]
    api_key_hash: str = ""
    created_at: float = Field(default_factory=time.time)
    updated_at: float = Field(default_factory=time.time)
    started_at: Optional[float] = None
    finished_at: Optional[float] = None
    result_text: str = ""
    error: str = ""
    trace_id: str = ""


class AgentJobCreateResponse(BaseModel):
    job_id: str
    tenant_id: str
    status: JobStatus
    events_url: str
    status_url: str


class AgentJobStatusResponse(BaseModel):
    job_id: str
    tenant_id: str
    status: JobStatus
    created_at: float
    updated_at: float
    started_at: Optional[float] = None
    finished_at: Optional[float] = None
    result_text: str = ""
    error: str = ""
    trace_id: str = ""


class AgentJobEvent(BaseModel):
    event_id: str = Field(default_factory=lambda: f"evt_{uuid.uuid4().hex}")
    job_id: str
    event_type: str
    payload: Dict[str, Any] = Field(default_factory=dict)
    seq: int = 0
    created_at: float = Field(default_factory=time.time)
