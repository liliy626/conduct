from __future__ import annotations

import json
import time
from typing import Any, Callable, Dict, List, Optional

from langchain_core.messages import BaseMessage


def runtime_non_stream_response(
    *,
    model_id: str,
    completion_id: str,
    text: str,
    now_ts: Callable[[], int],
    usage: Optional[Dict[str, Any]] = None,
    sources: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    payload: Dict[str, Any] = {
        "id": completion_id,
        "object": "chat.completion",
        "created": now_ts(),
        "model": model_id,
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": text},
                "finish_reason": "stop",
            }
        ],
        "usage": usage or {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
    }
    if sources:
        payload["sources"] = sanitize_sources(sources)
    return payload


def resolve_effective_stream_mode(
    *,
    requested_stream: Optional[bool],
    raw_last_question: str,
    last_question: str,
    is_meta_instruction_prompt,
) -> tuple[bool, bool]:
    is_meta_request = bool(
        is_meta_instruction_prompt(raw_last_question or "") or is_meta_instruction_prompt(last_question or "")
    )
    effective_stream = requested_stream if requested_stream is not None else (not is_meta_request)
    return bool(effective_stream), False


def runtime_stream_chunk(
    *,
    model_id: str,
    completion_id: str,
    delta: str,
    now_ts: Callable[[], int],
    sources: Optional[List[Dict[str, Any]]] = None,
) -> str:
    payload = {
        "id": completion_id,
        "object": "chat.completion.chunk",
        "created": now_ts(),
        "model": model_id,
        "choices": [{"index": 0, "delta": {"content": delta}, "finish_reason": None}],
    }
    if sources:
        payload["sources"] = sanitize_sources(sources)
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


def runtime_stream_end(
    *,
    model_id: str,
    completion_id: str,
    now_ts: Callable[[], int],
    sources: Optional[List[Dict[str, Any]]] = None,
) -> str:
    payload = {
        "id": completion_id,
        "object": "chat.completion.chunk",
        "created": now_ts(),
        "model": model_id,
        "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
    }
    if sources:
        payload["sources"] = sanitize_sources(sources)
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n" + "data: [DONE]\n\n"


def sanitize_sources(sources: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    clean_sources: List[Dict[str, Any]] = []
    for item in sources:
        if not isinstance(item, dict):
            continue
        clean_item = dict(item)
        source = clean_item.get("source")
        if isinstance(source, dict):
            clean_source = dict(source)
            if not str(clean_source.get("url") or "").strip():
                clean_source.pop("url", None)
            clean_item["source"] = clean_source
        clean_sources.append(clean_item)
    return clean_sources


def extract_usage(result: Any) -> Dict[str, int]:
    usage_meta: Dict[str, Any] = {}
    if hasattr(result, "response_metadata") and isinstance(result.response_metadata, dict):
        usage_meta = result.response_metadata.get("token_usage", {}) or result.response_metadata.get("usage", {}) or {}
    if not usage_meta and hasattr(result, "usage_metadata") and isinstance(result.usage_metadata, dict):
        usage_meta = result.usage_metadata
    prompt_tokens = int(usage_meta.get("prompt_tokens", usage_meta.get("input_tokens", 0)) or 0)
    completion_tokens = int(usage_meta.get("completion_tokens", usage_meta.get("output_tokens", 0)) or 0)
    total_tokens = int(usage_meta.get("total_tokens", 0) or 0)
    if total_tokens <= 0:
        total_tokens = prompt_tokens + completion_tokens
    return {
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_tokens": total_tokens,
    }


async def collect_stream_text_and_usage(
    *,
    client: Any,
    lc_messages: List[BaseMessage],
    stringify_content,
    elapsed_ms,
) -> tuple[str, Dict[str, int], Optional[float], int]:
    started = time.perf_counter()
    chunks: List[str] = []
    usage: Dict[str, int] = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
    first_token_ms: Optional[float] = None
    async for chunk in client.astream(lc_messages):
        text = stringify_content(getattr(chunk, "content", ""))
        if text:
            if first_token_ms is None:
                first_token_ms = elapsed_ms()
            chunks.append(text)
        chunk_usage = extract_usage(chunk)
        if chunk_usage["total_tokens"] > 0:
            usage = chunk_usage
    model_call_ms = int((time.perf_counter() - started) * 1000)
    return "".join(chunks), usage, first_token_ms, max(0, model_call_ms)
