from __future__ import annotations

import json
import os
import re
from typing import Any, Iterable


def env_value(primary: str, legacy: str = "", default: str = "") -> str:
    value = os.getenv(primary, "").strip()
    if value:
        return value
    if legacy:
        value = os.getenv(legacy, "").strip()
        if value:
            return value
    return default


def truncate(
    text: str,
    limit: int,
    *,
    suffix: str = "...[truncated]",
    strip: bool = False,
    rstrip: bool = False,
    reserve: int = 0,
) -> str:
    clean = str(text or "")
    if strip:
        clean = clean.strip()
    if len(clean) <= limit:
        return clean
    cut = max(0, int(limit or 0) - max(0, int(reserve or 0))) if reserve else max(0, int(limit or 0))
    head = clean[:cut]
    if rstrip:
        head = head.rstrip()
    return head + suffix


def loads_json_object(text: Any) -> dict[str, Any]:
    try:
        parsed = json.loads(str(text or "").strip())
    except Exception:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def extract_json_with_fence(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    text = str(getattr(value, "content", value) or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE)
        text = re.sub(r"\s*```$", "", text)
    parsed = loads_json_object(text)
    if parsed:
        return parsed
    try:
        value, _ = json.JSONDecoder().raw_decode(text)
        return value if isinstance(value, dict) else {}
    except Exception:
        pass
    start = text.find("{")
    end = text.rfind("}")
    if start >= 0 and end > start:
        return loads_json_object(text[start : end + 1])
    return {}


def dedupe(values: Iterable[Any]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for value in values:
        clean = str(value or "").strip()
        if not clean or clean in seen:
            continue
        seen.add(clean)
        out.append(clean)
    return out


def chunk_text(chunk: Any) -> str:
    content = getattr(chunk, "content", "")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        pieces: list[str] = []
        for item in content:
            if isinstance(item, dict):
                pieces.append(str(item.get("text") or item.get("content") or ""))
            else:
                pieces.append(str(item or ""))
        return "".join(pieces)
    return str(content or "")


def safe_int(value: Any, default: int = 0, *, minimum: int | None = 0) -> int:
    try:
        parsed = int(value if value is not None else default)
    except Exception:
        parsed = int(default or 0)
    if minimum is not None:
        parsed = max(int(minimum), parsed)
    return parsed


def extract_chunk_usage(chunk: Any) -> dict[str, int]:
    metadata: dict[str, Any] = {}
    usage_metadata = getattr(chunk, "usage_metadata", None)
    response_metadata = getattr(chunk, "response_metadata", None)
    if isinstance(usage_metadata, dict):
        metadata = usage_metadata
    if not metadata and isinstance(response_metadata, dict):
        raw = response_metadata.get("token_usage") or response_metadata.get("usage") or response_metadata
        metadata = raw if isinstance(raw, dict) else {}
    prompt_tokens = safe_int(metadata.get("prompt_tokens") or metadata.get("input_tokens"))
    completion_tokens = safe_int(metadata.get("completion_tokens") or metadata.get("output_tokens"))
    total_tokens = safe_int(metadata.get("total_tokens"))
    if total_tokens <= 0:
        total_tokens = prompt_tokens + completion_tokens
    return {
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_tokens": total_tokens,
    }


def model_name(model: Any) -> str:
    for attr in ("model_name", "model", "deployment_name", "name"):
        value = str(getattr(model, attr, "") or "").strip()
        if value:
            return value
    return type(model).__name__
