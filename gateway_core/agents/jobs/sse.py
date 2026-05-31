from __future__ import annotations

import json

from gateway_core.agents.jobs.models import AgentJobEvent


def format_sse_event(event: AgentJobEvent) -> str:
    payload = event.model_dump()
    return (
        f"id: {event.event_id}\n"
        f"event: {event.event_type}\n"
        f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"
    )
