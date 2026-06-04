from __future__ import annotations

import json
import asyncio
from types import SimpleNamespace

from gateway_core.api.openai_compat.agent_native_flow import run_agent_native_stream


class _ResponseTools:
    def elapsed_ms(self) -> int:
        return 0

    def log_monitor_event(self, *_args, **_kwargs) -> None:
        return None

    def build_upstream_error_text(self, exc: Exception) -> str:
        return str(exc)


class _RuntimeResponseFns:
    def stream_chunk(self, model_id: str, completion_id: str, text: str, sources=None) -> str:
        del sources
        payload = {
            "id": completion_id,
            "object": "chat.completion.chunk",
            "created": 1,
            "model": model_id,
            "choices": [{"index": 0, "delta": {"content": text}, "finish_reason": None}],
        }
        return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"

    def stream_end(self, model_id: str, completion_id: str) -> str:
        return ""


def test_agent_native_stream_sends_initial_reasoning_heartbeat(monkeypatch):
    monkeypatch.setenv("SCHOOL_AGENT_NATIVE_PROCESS_DELTA_MODE", "reasoning_content")

    async def _first_chunk() -> str:
        stream = run_agent_native_stream(
            spec=SimpleNamespace(model_id="yili-model"),
            pipeline_ctx=SimpleNamespace(now_ts_fn=lambda: 1),
            effective_question="学校师资团队怎么样?",
            token="key_a",
            school_scope="mlh",
            completion_id="chatcmpl-test",
            openwebui_chat_id="",
            monitor_base={},
            response_tools=_ResponseTools(),
            runtime_response_fns=_RuntimeResponseFns(),
            model=object(),
            monitor_answer_preview_fn=lambda text: str(text or "")[:20],
        )
        try:
            return await stream.__anext__()
        finally:
            await stream.aclose()

    first_chunk = asyncio.run(_first_chunk())

    assert "reasoning_content" in first_chunk
    assert "正在理解问题" in first_chunk
