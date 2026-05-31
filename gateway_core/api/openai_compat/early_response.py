from __future__ import annotations

from typing import Any, Callable


def build_single_text_response(
    *,
    text: str,
    stream: bool,
    model_id: str,
    completion_id: str,
    usage: dict[str, Any] | None,
    monitor_payload: dict[str, Any],
    final_monitor_payload: dict[str, Any],
    log_monitor_event_fn: Callable[..., Any],
    runtime_non_stream_response_fn: Callable[..., Any],
    single_chunk_stream_response_fn: Callable[..., Any],
) -> Any:
    if not stream:
        log_monitor_event_fn(monitor_payload)
        return runtime_non_stream_response_fn(model_id, completion_id, text, usage)
    return single_chunk_stream_response_fn(text, final_payload=final_monitor_payload)
