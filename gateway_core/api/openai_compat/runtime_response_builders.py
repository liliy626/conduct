from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from gateway_core.api.openai_compat.chat_pipeline_parts import response_parts


@dataclass(frozen=True)
class RuntimeResponseFns:
    non_stream: Callable[..., dict[str, Any]]
    stream_chunk: Callable[..., str]
    stream_end: Callable[..., str]


def build_runtime_response_fns(*, now_ts) -> RuntimeResponseFns:
    return RuntimeResponseFns(
        non_stream=lambda model_id, completion_id, text, usage=None, sources=None: runtime_non_stream_response(
            model_id, completion_id, text, usage, sources=sources, now_ts=now_ts
        ),
        stream_chunk=lambda model_id, completion_id, text, sources=None: runtime_stream_chunk(
            model_id, completion_id, text, sources=sources, now_ts=now_ts
        ),
        stream_end=lambda model_id, completion_id, sources=None: runtime_stream_end(
            model_id, completion_id, sources=sources, now_ts=now_ts
        ),
    )


def runtime_non_stream_response(
    model_id: str,
    completion_id: str,
    text: str,
    usage: dict[str, Any] | None = None,
    sources: list[dict[str, Any]] | None = None,
    *,
    now_ts,
) -> dict[str, Any]:
    return response_parts.runtime_non_stream_response(
        model_id=model_id,
        completion_id=completion_id,
        text=text,
        now_ts=now_ts,
        usage=usage,
        sources=sources,
    )


def runtime_stream_chunk(
    model_id: str,
    completion_id: str,
    delta: str,
    sources: list[dict[str, Any]] | None = None,
    *,
    now_ts,
) -> str:
    return response_parts.runtime_stream_chunk(
        model_id=model_id,
        completion_id=completion_id,
        delta=delta,
        now_ts=now_ts,
        sources=sources,
    )

def runtime_stream_end(
    model_id: str,
    completion_id: str,
    sources: list[dict[str, Any]] | None = None,
    *,
    now_ts,
) -> str:
    return response_parts.runtime_stream_end(
        model_id=model_id,
        completion_id=completion_id,
        sources=sources,
        now_ts=now_ts,
    )
