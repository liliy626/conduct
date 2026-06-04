from __future__ import annotations

from typing import AsyncIterator

from gateway_core.agents.universal_hub.models import SkillEvent
from gateway_core.api.openai_compat.response_composer import ResponseComposer


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
        composer = ResponseComposer(model_id=model_id, completion_id=completion_id)
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
            yield _to_openai_done_chunk(composer)
            yield "data: [DONE]\n\n"


def _openai_chunks_for_event(
    event: SkillEvent,
    *,
    model_id: str,
    completion_id: str,
    stream_tool_events: bool,
) -> tuple[str, ...]:
    return _to_openai_chunk(
        ResponseComposer(
            model_id=model_id,
            completion_id=completion_id,
        ),
        event,
        stream_tool_events=stream_tool_events,
    )


def _to_openai_chunk(
    composer: ResponseComposer,
    event: SkillEvent,
    *,
    stream_tool_events: bool,
) -> tuple[str, ...]:
    return composer.compose_skill_event_chunks(event, stream_tool_events=stream_tool_events)


def _to_openai_done_chunk(composer: ResponseComposer) -> str:
    return composer.compose_done_chunk()
