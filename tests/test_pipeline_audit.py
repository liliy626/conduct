from __future__ import annotations

import inspect
import asyncio

from gateway_core.api.openai_compat.pipeline_audit import trace_pipeline_audit


def test_trace_pipeline_audit_preserves_async_result() -> None:
    @trace_pipeline_audit(route_name="plain_chat")
    async def endpoint(value: int) -> int:
        return value + 1

    assert asyncio.run(endpoint(2)) == 3
    assert getattr(endpoint, "__pipeline_route_name__") == "plain_chat"


def test_run_chat_completions_is_trace_decorated() -> None:
    import gateway_core.api.openai_compat.chat_pipeline as chat_pipeline

    assert getattr(chat_pipeline.run_chat_completions, "__pipeline_route_name__", "") == "chat_completions"
    assert "trace_pipeline_audit" in inspect.getsource(chat_pipeline)
