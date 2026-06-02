from __future__ import annotations

from gateway_core.api.openai_compat.response_composer import OutputEventType, OutputPolicy, visible_content_nodes


def route_text_visibility(
    event_type: str,
    *,
    current_node: str = "",
    stream_tool_events: bool = False,
) -> str | None:
    output_type = OutputPolicy().decide(
        event_type,
        current_node=current_node,
        stream_tool_events=stream_tool_events,
    )
    if output_type == OutputEventType.ANSWER_DELTA:
        return "content"
    if output_type == OutputEventType.REASONING_DELTA:
        return "reasoning_content"
    return None
