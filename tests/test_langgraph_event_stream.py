from __future__ import annotations

import time

from gateway_core.agents.streaming.langgraph_event_stream import (
    flush_active_langgraph_llm_runs,
    record_langgraph_event_as_trace_step,
)
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


def test_record_langgraph_event_attaches_trace_context_to_tool_steps() -> None:
    trace = SchoolTrace(trace_id="trace_tool_context", question="查请假")
    trace_context = {"workflow_step": {"step_id": "school_sql.react_execute"}}

    record_langgraph_event_as_trace_step(
        trace,
        {
            "event": "on_tool_end",
            "name": "sql_db_query",
            "data": {"output": '{"row_count": 2, "referenced_views": ["teacher_leave"]}'},
        },
        prefix="agent_native.langgraph",
        trace_context=trace_context,
    )

    assert len(trace.steps) == 1
    step = trace.steps[0]
    assert step.input["workflow_step"]["step_id"] == "school_sql.react_execute"
    assert step.output["workflow_step"]["step_id"] == "school_sql.react_execute"
    assert step.output["row_count"] == 2


def test_record_langgraph_event_attaches_trace_context_to_llm_steps() -> None:
    trace = SchoolTrace(trace_id="trace_llm_context", question="查请假")
    run_id = "run-context"
    trace_context = {"workflow_step": {"step_id": "school_sql.react_execute"}}

    record_langgraph_event_as_trace_step(
        trace,
        {
            "event": "on_chat_model_start",
            "run_id": run_id,
            "metadata": {"ls_model_name": "deepseek-v4-flash"},
            "data": {"input": {"messages": [["human", "查请假"]]}},
        },
        prefix="agent_native.langgraph",
        trace_context=trace_context,
    )
    record_langgraph_event_as_trace_step(
        trace,
        {"event": "on_chat_model_end", "run_id": run_id, "data": {"output": {}}},
        prefix="agent_native.langgraph",
        trace_context=trace_context,
    )

    assert len(trace.steps) == 1
    step = trace.steps[0]
    assert step.input["workflow_step"]["step_id"] == "school_sql.react_execute"
    assert step.output["workflow_step"]["step_id"] == "school_sql.react_execute"


def test_final_model_filter_keeps_agent_llm_events_when_model_name_matches() -> None:
    from gateway_core.agents.school_sql.agent_stream import _is_final_model_langgraph_event

    class FinalModel:
        model_name = "deepseek-v4-flash"

    event = {
        "event": "on_chat_model_start",
        "metadata": {"ls_model_name": "deepseek-v4-flash"},
    }

    assert _is_final_model_langgraph_event(event, final_model=FinalModel(), final_handoff_enabled=True) is False


def test_flush_active_langgraph_llm_runs_records_missing_end_event() -> None:
    trace = SchoolTrace(trace_id="trace_llm_flush", question="学校最近有什么异常")
    run_id = "run-flush"

    record_langgraph_event_as_trace_step(
        trace,
        {
            "event": "on_chat_model_start",
            "run_id": run_id,
            "metadata": {"ls_model_name": "deepseek-v4-flash"},
            "data": {"input": {"messages": [["human", "学校最近有什么异常"]]}},
        },
        prefix="agent_native.langgraph",
    )
    time.sleep(0.001)
    record_langgraph_event_as_trace_step(
        trace,
        {"event": "on_chat_model_stream", "run_id": run_id, "data": {"chunk": "查"}},
        prefix="agent_native.langgraph",
    )

    flush_active_langgraph_llm_runs(trace, prefix="agent_native.langgraph")

    assert len(trace.steps) == 1
    step = trace.steps[0]
    assert step.name == "agent_native.langgraph.llm"
    assert step.output["event"] == "flushed_without_end_event"
    assert step.output["run_id"] == run_id
    assert step.output["stream_chunk_count"] == 1


def test_flush_active_langgraph_llm_runs_preserves_trace_context() -> None:
    trace = SchoolTrace(trace_id="trace_llm_flush_context", question="学校最近有什么异常")
    run_id = "run-flush-context"
    trace_context = {"workflow_step": {"step_id": "school_sql.react_execute"}}

    record_langgraph_event_as_trace_step(
        trace,
        {
            "event": "on_chat_model_start",
            "run_id": run_id,
            "metadata": {"ls_model_name": "deepseek-v4-flash"},
            "data": {"input": {"messages": [["human", "学校最近有什么异常"]]}},
        },
        prefix="agent_native.langgraph",
        trace_context=trace_context,
    )

    flush_active_langgraph_llm_runs(trace, prefix="agent_native.langgraph")

    assert len(trace.steps) == 1
    assert trace.steps[0].input["workflow_step"]["step_id"] == "school_sql.react_execute"
    assert trace.steps[0].output["workflow_step"]["step_id"] == "school_sql.react_execute"
