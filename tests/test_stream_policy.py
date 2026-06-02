from __future__ import annotations

from gateway_core.api.openai_compat.stream_policy import route_text_visibility, visible_content_nodes


def test_stream_policy_routes_final_nodes_to_content() -> None:
    assert "answer_summarizer" in visible_content_nodes()
    assert route_text_visibility("content", current_node="answer_summarizer") == "content"
    assert route_text_visibility("content", current_node="final_answer") == "content"


def test_stream_policy_routes_upstream_text_to_reasoning() -> None:
    assert route_text_visibility("content", current_node="supervisor") == "reasoning_content"
    assert route_text_visibility("content", current_node="") == "reasoning_content"
    assert route_text_visibility("process", current_node="answer_summarizer") == "reasoning_content"


def test_stream_policy_respects_tool_event_visibility_flag() -> None:
    assert route_text_visibility("tool_start", current_node="", stream_tool_events=False) is None
    assert route_text_visibility("tool_start", current_node="", stream_tool_events=True) == "reasoning_content"
    assert route_text_visibility("tool_end", current_node="", stream_tool_events=True) == "reasoning_content"
