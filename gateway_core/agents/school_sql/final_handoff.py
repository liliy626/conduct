from __future__ import annotations

from typing import Any


def has_handoff_evidence(handoff_payload: dict[str, Any], source_views: list[str] | None = None) -> bool:
    if source_views:
        return True
    if _has_value(handoff_payload.get("source_views")):
        return True
    for key in ("data_evidence", "evidence_board", "external_evidence"):
        if _has_value(handoff_payload.get(key)):
            return True
    return False


def should_emit_missing_handoff_draft(
    *,
    draft_text: str,
    handoff_payload: dict[str, Any],
    source_views: list[str] | None = None,
) -> bool:
    if has_handoff_evidence(handoff_payload, source_views):
        return False
    clean = _clean_draft_text(draft_text)
    if not clean:
        return False
    if "FINAL_ANSWER_HANDOFF_" in clean:
        return False
    return not _looks_like_pending_tool_plan(clean)


def _has_value(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, dict):
        return any(_has_value(item) for item in value.values())
    if isinstance(value, (list, tuple, set)):
        return any(_has_value(item) for item in value)
    if isinstance(value, str):
        return bool(value.strip())
    return True


def _clean_draft_text(text: str) -> str:
    return " ".join(str(text or "").replace("\x00", " ").split())


def _looks_like_pending_tool_plan(text: str) -> bool:
    clean = _clean_draft_text(text)
    if not clean:
        return True
    lowered = clean.lower()
    pending_markers = [
        "我先查询",
        "先查询",
        "我需要查询",
        "需要先查",
        "接下来我会",
        "准备调用",
        "我要调用",
        "let me check",
        "i need to query",
    ]
    return any(marker in lowered for marker in pending_markers)
