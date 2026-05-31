from __future__ import annotations

import json
from typing import Any


def extract_context_json_payload(ctx: str | None) -> Any | None:
    text = str(ctx or "")
    if not text:
        return None
    marker = "检索结果(JSON):"
    pos = text.find(marker)
    fragment = text[pos + len(marker):] if pos >= 0 else text
    start = -1
    for ch in ("{", "["):
        p = fragment.find(ch)
        if p >= 0 and (start < 0 or p < start):
            start = p
    if start < 0:
        return None
    fragment = fragment[start:].strip()
    decoder = json.JSONDecoder()
    try:
        obj, _ = decoder.raw_decode(fragment)
        return obj
    except Exception:
        return None
