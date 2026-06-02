from __future__ import annotations

import json

from gateway_core.agents.universal_hub.models import SkillEvent
from gateway_core.api.openai_compat.response_composer import (
    OutputEventType,
    OutputPolicy,
    ResponseComposer,
)


def _payload(chunk: str) -> dict:
    assert chunk.startswith("data: ")
    return json.loads(chunk.removeprefix("data: ").strip())


def test_output_policy_routes_visible_nodes_to_content() -> None:
    policy = OutputPolicy()

    assert policy.decide("content", current_node="final_answer") == OutputEventType.ANSWER_DELTA
    assert policy.decide("content", current_node="supervisor") == OutputEventType.REASONING_DELTA
    assert policy.decide("process", current_node="final_answer") == OutputEventType.REASONING_DELTA


def test_response_composer_maps_skill_event_to_content_chunk() -> None:
    composer = ResponseComposer(model_id="yili-model", completion_id="chatcmpl-test", now_ts=lambda: 123)
    event = SkillEvent(
        event_type="content",
        data={"text": "今天全校请假 8 人。"},
        metadata={"langgraph_node": "final_answer"},
    )

    chunks = composer.compose_stream_chunk(event)

    assert len(chunks) == 1
    payload = _payload(chunks[0])
    assert payload["created"] == 123
    assert payload["choices"][0]["delta"] == {"content": "今天全校请假 8 人。", "role": "assistant"}


def test_response_composer_maps_internal_content_to_reasoning_chunk() -> None:
    composer = ResponseComposer(model_id="yili-model", completion_id="chatcmpl-test", now_ts=lambda: 123)
    event = SkillEvent(
        event_type="content",
        data={"text": "正在选择数据表。"},
        metadata={"langgraph_node": "supervisor"},
    )

    chunks = composer.compose_stream_chunk(event)

    assert len(chunks) == 1
    payload = _payload(chunks[0])
    assert payload["choices"][0]["delta"] == {"reasoning_content": "正在选择数据表。", "role": "assistant"}


def test_response_composer_maps_sources_to_citations_chunk() -> None:
    composer = ResponseComposer(model_id="yili-model", completion_id="chatcmpl-test", now_ts=lambda: 123)
    sources = [{"url": "https://example.edu/policy", "title": "政策"}]
    event = SkillEvent(event_type="evidence", data={"sources": sources})

    chunks = composer.compose_stream_chunk(event)

    assert len(chunks) == 1
    payload = _payload(chunks[0])
    assert payload["choices"][0]["delta"] == {"content": "", "role": "assistant"}
    assert payload["sources"] == sources
    assert payload["citations"] == sources


def test_response_composer_done_chunk_preserves_openai_stop_shape() -> None:
    composer = ResponseComposer(model_id="yili-model", completion_id="chatcmpl-test", now_ts=lambda: 123)

    chunk = composer.compose_done_chunk()

    payload = _payload(chunk)
    assert payload["choices"][0]["delta"] == {}
    assert payload["choices"][0]["finish_reason"] == "stop"
