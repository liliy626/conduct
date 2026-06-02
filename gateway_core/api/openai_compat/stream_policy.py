from __future__ import annotations

import os


def visible_content_nodes() -> set[str]:
    return {
        node.strip()
        for node in os.getenv("VISIBLE_CONTENT_NODES", "answer_summarizer,responder,final_answer").split(",")
        if node.strip()
    }


def route_text_visibility(
    event_type: str,
    *,
    current_node: str = "",
    stream_tool_events: bool = False,
) -> str | None:
    clean_type = str(event_type or "").strip()
    if clean_type == "process":
        return "reasoning_content"
    if clean_type in {"tool_start", "tool_end"}:
        return "reasoning_content" if stream_tool_events else None
    if clean_type == "content":
        return "content" if str(current_node or "").strip() in visible_content_nodes() else "reasoning_content"
    return None
