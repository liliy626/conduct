from __future__ import annotations

import json
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage

from gateway_core.infra.utils import model_name as _model_name
from gateway_core.runtime.runtime_trace_context import _add_trace_usage
from gateway_core.school.trace import set_step_output, trace_preview, trace_step


def handoff_text(handoff_payload: dict[str, Any]) -> str:
    return json.dumps(handoff_payload, ensure_ascii=False, default=str)


def final_answer_messages(*, system_prompt: str, prompt: str) -> list[Any]:
    return [
        SystemMessage(content=system_prompt),
        HumanMessage(content=prompt),
    ]


def record_scripted_final_answer_context(
    *,
    trace: Any,
    question: str,
    source_views: list[str],
    handoff_payload: dict[str, Any],
    scripted_answer: str,
) -> None:
    text = handoff_text(handoff_payload)
    with trace_step(trace, "final_answer_context", {"question": question}) as context_step:
        set_step_output(
            context_step,
            {
                "input": {
                    "question": question,
                    "source_views": source_views,
                    "handoff_chars": len(text),
                },
                "decision": {
                    "model_name": "",
                    "scripted_handoff": True,
                    "format_policy": "scripted_business_markdown",
                },
                "output": {
                    "answer_chars": len(scripted_answer),
                    "handoff_json": trace_preview(text),
                },
                "error": None,
            },
        )


def record_final_answer_context(
    *,
    trace: Any,
    final_model: Any,
    question: str,
    source_views: list[str],
    handoff_text: str,
    business_prompt_context: str,
    system_prompt: str,
    prompt: str,
) -> None:
    with trace_step(trace, "final_answer_context", {"question": question}) as context_step:
        set_step_output(
            context_step,
            {
                "input": {
                    "question": question,
                    "source_views": source_views,
                    "handoff_chars": len(handoff_text),
                    "business_prompt_chars": len(str(business_prompt_context or "")),
                },
                "decision": {
                    "model_name": _model_name(final_model),
                    "format_policy": "free",
                },
                "output": {
                    "handoff_json": trace_preview(handoff_text),
                    "business_prompt_context": trace_preview(business_prompt_context),
                    "system_prompt": trace_preview(system_prompt),
                    "final_prompt": trace_preview(prompt),
                },
                "error": None,
            },
        )


def final_llm_trace_input(
    *,
    final_model: Any,
    question: str,
    handoff_text: str,
    source_views: list[str],
    business_prompt_context: str,
    prompt: str,
) -> dict[str, Any]:
    return {
        "model_name": _model_name(final_model),
        "question": question,
        "handoff_chars": len(handoff_text),
        "source_views": source_views,
        "handoff_json": trace_preview(handoff_text),
        "business_prompt_context": trace_preview(business_prompt_context),
        "final_prompt": trace_preview(prompt),
    }


def record_final_llm_output(
    step: Any,
    *,
    final_model: Any,
    first_token_ms: int | None,
    chunk_count: int,
    usage: dict[str, int],
    prompt: str,
) -> None:
    set_step_output(
        step,
        {
            "model_name": _model_name(final_model),
            "first_token_ms": first_token_ms,
            "stream_chunk_count": chunk_count,
            "usage": usage,
            "final_prompt": trace_preview(prompt),
        },
    )
    if usage["total_tokens"] > 0:
        _add_trace_usage(usage)
