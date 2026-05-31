from __future__ import annotations

import json
import time
from typing import Any, AsyncIterator

from gateway_core.agents.universal_hub.models import SkillEvent
from gateway_core.prompts import prompt_domains


class UniversalHubStreamAdapter:
    """Convert normalized SkillEvents into OpenAI-compatible SSE chunks."""

    @staticmethod
    async def to_openai_sse(
        skill_event_stream: AsyncIterator[SkillEvent],
        *,
        model_id: str,
        completion_id: str,
        stream_tool_events: bool = False,
        include_done: bool = True,
    ) -> AsyncIterator[str]:
        async for event in skill_event_stream:
            try:
                for chunk in _openai_chunks_for_event(
                    event,
                    model_id=model_id,
                    completion_id=completion_id,
                    stream_tool_events=stream_tool_events,
                ):
                    yield chunk
            finally:
                del event
        if include_done:
            yield _to_openai_done_chunk(model_id=model_id, completion_id=completion_id)
            yield "data: [DONE]\n\n"


def _to_openai_chunk(
    *,
    model_id: str,
    completion_id: str,
    field: str = "content",
    text: str = "",
    sources: list[dict[str, Any]] | None = None,
) -> str:
    delta = {field: text, "role": "assistant"} if text or not sources else {"content": "", "role": "assistant"}
    payload: dict[str, Any] = {
        "id": completion_id,
        "object": "chat.completion.chunk",
        "created": int(time.time()),
        "model": model_id,
        "choices": [{"index": 0, "delta": delta, "finish_reason": None}],
    }
    if sources:
        payload["sources"] = sources
        payload["citations"] = sources
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


def _to_openai_done_chunk(*, model_id: str, completion_id: str) -> str:
    payload = {
        "id": completion_id,
        "object": "chat.completion.chunk",
        "created": int(time.time()),
        "model": model_id,
        "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
    }
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


def _openai_chunks_for_event(
    event: SkillEvent,
    *,
    model_id: str,
    completion_id: str,
    stream_tool_events: bool,
) -> tuple[str, ...]:
    if event.event_type == "process":
        return _text_delta_chunks(_event_text(event.data), "reasoning_content", model_id, completion_id)
    if event.event_type == "content":
        return _text_delta_chunks(_event_text(event.data), "content", model_id, completion_id)
    if event.event_type == "artifact":
        return _text_delta_chunks(_rendered_artifact_markdown(event.data), "content", model_id, completion_id)
    if event.event_type == "evidence":
        return _sources_delta_chunks(_event_sources(event.data), model_id, completion_id)
    if event.event_type == "evidence_completed":
        return (
            *_text_delta_chunks(_evidence_completed_markdown(event.data), "content", model_id, completion_id),
            *_sources_delta_chunks(_evidence_completed_sources(event.data), model_id, completion_id),
        )
    if event.event_type in {"tool_start", "tool_end"} and stream_tool_events:
        return _text_delta_chunks(_tool_event_text(event), "reasoning_content", model_id, completion_id)
    return ()


def _text_delta_chunks(text: str, field: str, model_id: str, completion_id: str) -> tuple[str, ...]:
    if not text:
        return ()
    return (
        _to_openai_chunk(
            model_id=model_id,
            completion_id=completion_id,
            field=field,
            text=text,
        ),
    )


def _sources_delta_chunks(
    sources: list[dict[str, Any]],
    model_id: str,
    completion_id: str,
) -> tuple[str, ...]:
    if not sources:
        return ()
    return (
        _to_openai_chunk(
            model_id=model_id,
            completion_id=completion_id,
            sources=sources,
        ),
    )


def _event_text(data: Any) -> str:
    if isinstance(data, str):
        return data
    if isinstance(data, dict):
        for key in ("text", "content", "message"):
            value = data.get(key)
            if value:
                return str(value)
    return ""


def _event_sources(data: Any) -> list[dict[str, Any]]:
    if not isinstance(data, dict):
        return []
    sources = data.get("sources") or data.get("citations")
    if not isinstance(sources, list):
        return []
    return [item for item in sources if isinstance(item, dict)]


def _evidence_completed_markdown(data: Any) -> str:
    return _rendered_artifact_markdown(data)


def _rendered_artifact_markdown(data: Any) -> str:
    if not isinstance(data, dict):
        return ""
    render = prompt_domains.OUTPUT_RENDER_MATRIX.get(str(data.get("type") or ""))
    if render is None:
        return ""
    payload = data.get("payload") or data
    if not isinstance(payload, dict):
        return ""
    return str(render(payload)).strip()


def _evidence_completed_sources(data: Any) -> list[dict[str, Any]]:
    if not isinstance(data, dict):
        return []
    render = prompt_domains.OUTPUT_SOURCE_MATRIX.get(str(data.get("type") or ""))
    if render is None:
        return []
    payload = data.get("payload")
    if not isinstance(payload, dict):
        return []
    return [source for source in render(payload) if isinstance(source, dict)]


def _tool_event_text(event: SkillEvent) -> str:
    data = event.data if isinstance(event.data, dict) else {}
    tool_name = str(data.get("tool") or data.get("tool_name") or "")
    if not tool_name:
        return ""
    if event.event_type == "tool_start":
        return f"- 正在调用工具: `{tool_name}`\n"
    return f"- 工具完成: `{tool_name}`\n"
