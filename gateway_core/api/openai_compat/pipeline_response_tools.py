from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, AsyncGenerator, Callable

from fastapi.responses import JSONResponse, StreamingResponse

from gateway_core.api.openai_compat.chat_pipeline_parts import request_parts
from gateway_core.api.openai_compat.early_response import build_single_text_response


@dataclass(frozen=True)
class PipelineResponseTools:
    elapsed_ms: Callable[[], float]
    log_monitor_event: Callable[..., None]
    build_upstream_error_text: Callable[[Exception], str]
    text_response: Callable[..., Any]


def build_pipeline_response_tools(
    *,
    request_started: float,
    model_id: str,
    completion_id: str,
    stream: bool,
    runtime_stream_chunk_fn: Callable[[str, str, str], str],
    runtime_stream_end_fn: Callable[[str, str], str],
    runtime_non_stream_response_fn: Callable[..., dict[str, Any]],
    current_context_stage_trace_fn: Callable[[], dict[str, Any]],
    write_question_monitor_event_fn: Callable[[dict[str, Any]], Any],
) -> PipelineResponseTools:
    def elapsed_ms() -> float:
        return round(max((time.perf_counter() - request_started) * 1000.0, 0.0), 1)

    def log_monitor_event(
        payload: dict[str, Any],
        *,
        first_token_ms: float | None = None,
        stream_done: bool = False,
    ) -> None:
        data = dict(payload or {})
        if "e2e_ms" not in data:
            data["e2e_ms"] = elapsed_ms()
        if first_token_ms is not None:
            data["first_token_ms"] = round(max(float(first_token_ms), 0.0), 1)
        mode = str(data.get("response_mode") or "").strip().lower()
        if stream_done:
            data["stream_done_ms"] = round(float(data.get("e2e_ms") or elapsed_ms()), 1)
        elif mode == "stream_final" and "stream_done_ms" not in data:
            data["stream_done_ms"] = round(float(data.get("e2e_ms") or elapsed_ms()), 1)
        if "context_stage_trace" not in data:
            stage_trace = current_context_stage_trace_fn()
            if stage_trace.get("stages"):
                data["context_stage_trace"] = stage_trace
        write_question_monitor_event_fn(data)

    def single_chunk_stream_response(
        text: str,
        *,
        final_payload: dict[str, Any] | None = None,
    ) -> StreamingResponse:
        async def _stream() -> AsyncGenerator[str, None]:
            first_token_ms = elapsed_ms()
            yield runtime_stream_chunk_fn(model_id, completion_id, text)
            if final_payload is not None:
                log_monitor_event(final_payload, first_token_ms=first_token_ms, stream_done=True)
            yield runtime_stream_end_fn(model_id, completion_id)

        return StreamingResponse(_stream(), media_type="text/event-stream")

    def text_response(
        *,
        text: str,
        usage: dict[str, Any] | None,
        monitor_payload: dict[str, Any],
        final_monitor_payload: dict[str, Any],
    ) -> Any:
        return build_single_text_response(
            text=text,
            stream=stream,
            model_id=model_id,
            completion_id=completion_id,
            usage=usage,
            monitor_payload=monitor_payload,
            final_monitor_payload=final_monitor_payload,
            log_monitor_event_fn=log_monitor_event,
            runtime_non_stream_response_fn=lambda model_id, completion_id, text, usage: JSONResponse(
                runtime_non_stream_response_fn(model_id, completion_id, text, usage)
            ),
            single_chunk_stream_response_fn=single_chunk_stream_response,
        )

    return PipelineResponseTools(
        elapsed_ms=elapsed_ms,
        log_monitor_event=log_monitor_event,
        build_upstream_error_text=request_parts.build_upstream_error_text,
        text_response=text_response,
    )
