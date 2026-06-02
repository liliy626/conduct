from __future__ import annotations

import time

from gateway_core.agents.streaming.langgraph_event_stream import record_langgraph_event_as_trace_step
from gateway_core.school.trace import SchoolTrace


def test_record_langgraph_event_tracks_llm_events() -> None:
    trace = SchoolTrace(trace_id="trace_llm", question="学校最近有什么异常")
    run_id = "run-1"

    record_langgraph_event_as_trace_step(
        trace,
        {
            "event": "on_llm_start",
            "run_id": run_id,
            "name": "deepseek-v4-flash",
            "metadata": {"ls_model_name": "deepseek-v4-flash", "ls_provider": "deepseek"},
            "data": {"input": {"messages": [["human", "学校最近有什么异常"]]}},
        },
        prefix="agent_native.langgraph",
    )
    time.sleep(0.001)
    record_langgraph_event_as_trace_step(
        trace,
        {"event": "on_llm_stream", "run_id": run_id, "data": {"chunk": "查"}},
        prefix="agent_native.langgraph",
    )
    time.sleep(0.001)
    record_langgraph_event_as_trace_step(
        trace,
        {
            "event": "on_llm_end",
            "run_id": run_id,
            "data": {"output": {"usage": {"prompt_tokens": 3, "completion_tokens": 2, "total_tokens": 5}}},
        },
        prefix="agent_native.langgraph",
    )

    assert len(trace.steps) == 1
    step = trace.steps[0]
    assert step.name == "agent_native.langgraph.llm"
    assert step.status == "ok"
    assert step.duration_ms >= 1
    assert step.output["first_token_ms"] is not None
    assert step.output["stream_chunk_count"] == 1
    assert step.output["usage"]["total_tokens"] == 5
    assert step.input["event"] == "on_llm_start"
