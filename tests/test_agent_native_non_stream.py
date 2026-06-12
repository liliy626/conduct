from __future__ import annotations

import asyncio
from types import SimpleNamespace


class _FakeResponseTools:
    def __init__(self) -> None:
        self.events: list[dict] = []

    def elapsed_ms(self) -> float:
        return 12.5

    def log_monitor_event(self, payload: dict, **_: object) -> None:
        self.events.append(dict(payload))

    def build_upstream_error_text(self, exc: Exception) -> str:
        return f"上游模型服务暂时不可用：{type(exc).__name__}"


class _FakeRuntimeResponseFns:
    def non_stream(self, model_id: str, completion_id: str, text: str, usage: dict, sources=None) -> dict:
        payload = {
            "id": completion_id,
            "object": "chat.completion",
            "model": model_id,
            "choices": [{"message": {"role": "assistant", "content": text}}],
            "usage": usage,
        }
        if sources:
            payload["sources"] = sources
            payload["citations"] = sources
        return payload


async def _fake_school_events(**_: object):
    yield {"type": "process", "text": "正在查教师基础信息"}
    yield {
        "type": "sources",
        "sources": [{"source": {"name": "教师基础信息", "url": "db://teacher-base"}, "document": ["汪老师"]}],
    }
    yield {"type": "content", "text": "结论："}
    yield {"type": "content", "text": "汪老师有基础信息记录。"}


def test_school_agent_non_stream_returns_full_chat_completion(monkeypatch) -> None:
    from gateway_core.api.openai_compat import agent_native_non_stream

    monkeypatch.setattr(agent_native_non_stream, "stream_school_sql_agent_native", _fake_school_events)
    monkeypatch.setattr(agent_native_non_stream, "postgres_dsn", lambda: "postgres://example")
    monkeypatch.setattr(agent_native_non_stream, "resolve_agent_final_model", lambda model: model)
    monkeypatch.setattr(agent_native_non_stream.rt, "_current_trace_usage", lambda: {"total_tokens": 3})
    monkeypatch.setattr(agent_native_non_stream, "remember_conversation_turn", lambda *_args, **_kwargs: None)

    response_tools = _FakeResponseTools()

    payload = asyncio.run(
        agent_native_non_stream.run_agent_native_non_stream(
            spec=SimpleNamespace(model_id="yili-deepseek-v3"),
            pipeline_ctx=SimpleNamespace(now_ts_fn=lambda: 1),
            effective_question="汪老师的教师画像",
            token="school-key",
            school_scope="zx_sdfz",
            completion_id="chatcmpl-test",
            openwebui_chat_id="chat-1",
            monitor_base={"route_name": "school_agent_native"},
            response_tools=response_tools,
            runtime_response_fns=_FakeRuntimeResponseFns(),
            model=object(),
            policy_evidence_search_fn=None,
            conversation_context="",
            conversation_memory_key="memory-key",
            monitor_answer_preview_fn=str,
        )
    )

    assert payload["object"] == "chat.completion"
    assert payload["choices"][0]["message"]["content"] == "结论：汪老师有基础信息记录。"
    assert payload["usage"] == {"total_tokens": 3}
    assert payload["sources"][0]["source"]["name"] == "教师基础信息"
    assert response_tools.events[-1]["response_mode"] == "agent_native_non_stream"
    assert response_tools.events[-1]["process_preview"] == "正在查教师基础信息"
