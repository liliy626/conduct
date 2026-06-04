from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Any, Callable

from gateway_core.agents.universal_hub.models import SkillEvent
from gateway_core.api.openai_compat.chat_pipeline_parts import response_parts
from gateway_core.api.openai_compat.chat_pipeline_parts.response_parts import sanitize_sources
from gateway_core.prompts import prompt_domains


class OutputEventType:
    ANSWER_DELTA = "answer_delta"
    REASONING_DELTA = "reasoning_delta"
    SOURCES = "sources"
    DONE = "done"


def visible_content_nodes() -> set[str]:
    import os

    return {
        node.strip()
        for node in os.getenv("VISIBLE_CONTENT_NODES", "answer_summarizer,responder,final_answer").split(",")
        if node.strip()
    }


@dataclass(frozen=True)
class OutputPolicy:
    def decide(
        self,
        event_type: str,
        *,
        current_node: str = "",
        stream_tool_events: bool = False,
    ) -> str | None:
        clean_type = str(event_type or "").strip()
        if clean_type == "process":
            return OutputEventType.REASONING_DELTA
        if clean_type in {"tool_start", "tool_end"}:
            return OutputEventType.REASONING_DELTA if stream_tool_events else None
        if clean_type == "content":
            if str(current_node or "").strip() in visible_content_nodes():
                return OutputEventType.ANSWER_DELTA
            return OutputEventType.REASONING_DELTA
        if clean_type in {"sources", "evidence"}:
            return OutputEventType.SOURCES
        if clean_type == "done":
            return OutputEventType.DONE
        return None


class ResponseComposer:
    """Central policy and mapping layer for OpenAI-compatible stream chunks."""

    def __init__(
        self,
        *,
        model_id: str,
        completion_id: str,
        now_ts: Callable[[], int] | None = None,
        policy: OutputPolicy | None = None,
        include_role: bool = True,
    ) -> None:
        self.model_id = model_id
        self.completion_id = completion_id
        self.now_ts = now_ts or (lambda: int(time.time()))
        self.policy = policy or OutputPolicy()
        self.include_role = include_role

    def compose_stream_chunk(
        self,
        event: SkillEvent,
        *,
        stream_tool_events: bool = False,
    ) -> tuple[str, ...]:
        return self.compose_skill_event_chunks(event, stream_tool_events=stream_tool_events)

    def compose_skill_event_chunks(
        self,
        event: SkillEvent,
        *,
        stream_tool_events: bool = False,
    ) -> tuple[str, ...]:
        current_node = _event_langgraph_node(event)
        output_type = self.policy.decide(
            event.event_type,
            current_node=current_node,
            stream_tool_events=stream_tool_events,
        )
        if output_type == OutputEventType.ANSWER_DELTA:
            text = _tool_event_text(event) if event.event_type in {"tool_start", "tool_end"} else _event_text(event.data)
            return self.compose_text_delta(text, field="content")
        if output_type == OutputEventType.REASONING_DELTA:
            text = _tool_event_text(event) if event.event_type in {"tool_start", "tool_end"} else _event_text(event.data)
            return self.compose_text_delta(text, field="reasoning_content")
        if output_type == OutputEventType.SOURCES:
            return self.compose_sources_delta(_event_sources(event.data))
        if event.event_type == "artifact":
            return self.compose_text_delta(_rendered_artifact_markdown(event.data), field="content")
        if event.event_type == "evidence_completed":
            return (
                *self.compose_text_delta(_evidence_completed_markdown(event.data), field="content"),
                *self.compose_sources_delta(_evidence_completed_sources(event.data)),
            )
        return ()

    def compose_text_delta(self, text: str, *, field: str = "content") -> tuple[str, ...]:
        if not text:
            return ()
        return (
            self._to_openai_chunk(
                field=field,
                text=text,
            ),
        )

    def compose_sources_delta(self, sources: list[dict[str, Any]]) -> tuple[str, ...]:
        if not sources:
            return ()
        return (
            self._to_openai_chunk(
                sources=sources,
            ),
        )

    def compose_done_chunk(self) -> str:
        return response_parts.runtime_stream_end(
            model_id=self.model_id,
            completion_id=self.completion_id,
            now_ts=self.now_ts,
        ).split("data: [DONE]\n\n", 1)[0]

    def compose_done(self) -> str:
        return response_parts.runtime_stream_end(
            model_id=self.model_id,
            completion_id=self.completion_id,
            now_ts=self.now_ts,
        )

    def _to_openai_chunk(
        self,
        *,
        field: str = "content",
        text: str = "",
        sources: list[dict[str, Any]] | None = None,
    ) -> str:
        if text or not sources:
            delta = {field: text}
        else:
            delta = {"content": ""}
        if self.include_role:
            delta["role"] = "assistant"
        payload: dict[str, Any] = {
            "id": self.completion_id,
            "object": "chat.completion.chunk",
            "created": self.now_ts(),
            "model": self.model_id,
            "choices": [{"index": 0, "delta": delta, "finish_reason": None}],
        }
        if sources:
            payload["sources"] = sanitize_sources(sources)
        return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


def _event_langgraph_node(event: SkillEvent) -> str:
    metadata = getattr(event, "metadata", None)
    if isinstance(metadata, dict):
        node = metadata.get("langgraph_node")
        if node:
            return str(node)
    if isinstance(event.data, dict):
        data_metadata = event.data.get("metadata")
        if isinstance(data_metadata, dict):
            node = data_metadata.get("langgraph_node")
            if node:
                return str(node)
    return ""


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
