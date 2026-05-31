from __future__ import annotations

import contextvars


_AGENT_STREAM_PROCESS_REQUESTED: contextvars.ContextVar[bool] = contextvars.ContextVar(
    "agent_stream_process_requested",
    default=False,
)


def set_agent_stream_process_requested(value: bool) -> None:
    _AGENT_STREAM_PROCESS_REQUESTED.set(bool(value))


def agent_stream_process_requested() -> bool:
    return bool(_AGENT_STREAM_PROCESS_REQUESTED.get())
