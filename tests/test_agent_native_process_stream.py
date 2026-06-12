from __future__ import annotations

import json

from gateway_core.agents.streaming.context import set_agent_stream_process_requested
from gateway_core.api.openai_compat.agent_native_flow import build_agent_process_stream_chunk


def _content_chunk(text: str) -> str:
    payload = {
        "choices": [{"delta": {"content": text}, "finish_reason": None}],
    }
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


def _chunk_payload(chunk: str) -> dict:
    return json.loads(chunk.removeprefix("data: ").strip())


def test_process_stream_is_hidden_unless_requested(monkeypatch) -> None:
    set_agent_stream_process_requested(False)
    monkeypatch.delenv("SCHOOL_AGENT_NATIVE_PROCESS_DELTA_MODE", raising=False)
    monkeypatch.delenv("TENANT_AGENT_NATIVE_PROCESS_DELTA_MODE", raising=False)

    chunk = build_agent_process_stream_chunk(
        model_id="m",
        completion_id="c",
        created=1,
        text="正在查数据",
        content_chunk_fn=_content_chunk,
    )

    assert chunk == ""


def test_process_stream_uses_reasoning_content_when_requested(monkeypatch) -> None:
    set_agent_stream_process_requested(True)
    monkeypatch.delenv("SCHOOL_AGENT_NATIVE_PROCESS_DELTA_MODE", raising=False)
    monkeypatch.delenv("TENANT_AGENT_NATIVE_PROCESS_DELTA_MODE", raising=False)

    chunk = build_agent_process_stream_chunk(
        model_id="m",
        completion_id="c",
        created=1,
        text="正在查数据",
        content_chunk_fn=_content_chunk,
    )

    delta = _chunk_payload(chunk)["choices"][0]["delta"]
    assert delta == {"reasoning_content": "正在查数据"}
    set_agent_stream_process_requested(False)


def test_process_stream_can_be_sent_as_content_when_requested(monkeypatch) -> None:
    set_agent_stream_process_requested(True)
    monkeypatch.setenv("SCHOOL_AGENT_NATIVE_PROCESS_DELTA_MODE", "content")

    chunk = build_agent_process_stream_chunk(
        model_id="m",
        completion_id="c",
        created=1,
        text="正在查数据",
        content_chunk_fn=_content_chunk,
    )

    delta = _chunk_payload(chunk)["choices"][0]["delta"]
    assert delta == {"content": "正在查数据"}
    set_agent_stream_process_requested(False)
