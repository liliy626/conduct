from __future__ import annotations

from datetime import date
from typing import Any

from gateway_core.tools.time_tool import resolve_time_scope


def resolve_policy_time_scope(question: str, *, today: date | None = None) -> dict[str, Any]:
    """Resolve relative time phrases for official policy / notice search."""
    scope = resolve_time_scope(question, today=today)
    if not scope.get("has_time_scope"):
        return {}
    return {
        "scope": scope.get("mode", ""),
        "label": scope.get("resolved_label", ""),
        "reference_date": scope.get("today", ""),
        "timezone": scope.get("timezone", ""),
        "start_date": scope.get("start_date", ""),
        "end_date": scope.get("end_date", ""),
        "instruction": scope.get("instruction", ""),
        "time_expression": scope.get("time_expression", ""),
        "confidence": scope.get("confidence", 0.0),
    }
