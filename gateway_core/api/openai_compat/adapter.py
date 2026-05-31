from __future__ import annotations

import json
import time
from typing import Any, AsyncIterator

from gateway_core.agents.universal_hub.models import SkillEvent


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
            if event.event_type == "process":
                text = _event_text(event.data)
                if text:
                    yield _chunk(
                        model_id=model_id,
                        completion_id=completion_id,
                        delta={"reasoning_content": text, "role": "assistant"},
                    )
            elif event.event_type == "content":
                text = _event_text(event.data)
                if text:
                    yield _chunk(
                        model_id=model_id,
                        completion_id=completion_id,
                        delta={"content": text, "role": "assistant"},
                    )
            elif event.event_type == "artifact":
                content = _artifact_markdown(event.data)
                if content:
                    yield _chunk(
                        model_id=model_id,
                        completion_id=completion_id,
                        delta={"content": content, "role": "assistant"},
                    )
            elif event.event_type == "evidence":
                sources = _event_sources(event.data)
                if sources:
                    yield _chunk(
                        model_id=model_id,
                        completion_id=completion_id,
                        delta={"content": "", "role": "assistant"},
                        sources=sources,
                    )
            elif event.event_type == "evidence_completed":
                artifact_content = _evidence_completed_markdown(event.data)
                if artifact_content:
                    yield _chunk(
                        model_id=model_id,
                        completion_id=completion_id,
                        delta={"content": artifact_content, "role": "assistant"},
                    )
                sources = _evidence_completed_sources(event.data)
                if sources:
                    yield _chunk(
                        model_id=model_id,
                        completion_id=completion_id,
                        delta={"content": "", "role": "assistant"},
                        sources=sources,
                    )
            elif event.event_type in {"tool_start", "tool_end"} and stream_tool_events:
                text = _tool_event_text(event)
                if text:
                    yield _chunk(
                        model_id=model_id,
                        completion_id=completion_id,
                        delta={"reasoning_content": text, "role": "assistant"},
                    )
        if include_done:
            yield _end_chunk(model_id=model_id, completion_id=completion_id)
            yield "data: [DONE]\n\n"


def _chunk(
    *,
    model_id: str,
    completion_id: str,
    delta: dict[str, Any],
    sources: list[dict[str, Any]] | None = None,
) -> str:
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


def _end_chunk(*, model_id: str, completion_id: str) -> str:
    payload = {
        "id": completion_id,
        "object": "chat.completion.chunk",
        "created": int(time.time()),
        "model": model_id,
        "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
    }
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


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


def _artifact_markdown(data: Any) -> str:
    if not isinstance(data, dict):
        return ""
    artifact_type = str(data.get("type") or "")
    url = str(data.get("url") or data.get("download_url") or "")
    if artifact_type == "image" and url:
        return f"\n\n![可视化画布]({url})\n\n"
    return ""


def _evidence_completed_markdown(data: Any) -> str:
    if not isinstance(data, dict) or data.get("type") != "image_artifact":
        return ""
    payload = data.get("payload")
    if not isinstance(payload, dict):
        return ""
    markdown = str(payload.get("markdown_render") or "").strip()
    if not markdown:
        return ""
    return f"\n\n{markdown}\n\n"


def _evidence_completed_sources(data: Any) -> list[dict[str, Any]]:
    if not isinstance(data, dict) or data.get("type") != "image_artifact":
        return []
    payload = data.get("payload")
    if not isinstance(payload, dict):
        return []
    artifact_id = str(payload.get("artifact_id") or "image_artifact")
    linked_sql_hash = str(payload.get("linked_sql_hash") or "")
    if len(linked_sql_hash) != 64:
        return []
    prompt_used = str(payload.get("prompt_used") or "")
    return [
        {
            "source": {"name": f"图像生成证据：{artifact_id}", "url": ""},
            "document": [f"图像资产绑定 SQL Hash: {linked_sql_hash[:12]}。提示词规约: {prompt_used}"],
            "metadata": [
                {
                    "type": "image_artifact",
                    "artifact_id": artifact_id,
                    "linked_sql_hash": linked_sql_hash,
                }
            ],
        }
    ]


def _tool_event_text(event: SkillEvent) -> str:
    data = event.data if isinstance(event.data, dict) else {}
    tool_name = str(data.get("tool") or data.get("tool_name") or "")
    if not tool_name:
        return ""
    if event.event_type == "tool_start":
        return f"- 正在调用工具: `{tool_name}`\n"
    return f"- 工具完成: `{tool_name}`\n"
