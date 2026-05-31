from __future__ import annotations

import os
import time
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo
from typing import Any, Optional

from gateway_core.domain.time_filter import get_reference_today, parse_time_range

from .tool_core import AgentTool, AgentToolInput, AgentToolOutput, ToolExecutionContext, json_safe


DEFAULT_TIMEZONE = "Asia/Shanghai"


def current_time_context(*, timezone: str = DEFAULT_TIMEZONE, today: date | None = None) -> dict[str, Any]:
    """Return the gateway's authoritative current time context."""
    tz_name = str(timezone or DEFAULT_TIMEZONE).strip() or DEFAULT_TIMEZONE
    try:
        tz = ZoneInfo(tz_name)
    except Exception:
        tz_name = DEFAULT_TIMEZONE
        tz = ZoneInfo(DEFAULT_TIMEZONE)
    now = datetime.now(tz)
    reference_day = today or get_reference_today()
    return {
        "timezone": tz_name,
        "today": reference_day.isoformat(),
        "now": now.isoformat(timespec="seconds"),
    }


def resolve_time_scope(
    question: str,
    *,
    today: date | None = None,
    timezone: str = DEFAULT_TIMEZONE,
    default_recent_days: int = 30,
) -> dict[str, Any]:
    """Resolve natural-language time expressions into a standard JSON scope."""
    q = str(question or "").strip()
    if not q:
        return _empty_scope(timezone=timezone, today=today)

    reference_day = today or get_reference_today()
    current = current_time_context(timezone=timezone, today=reference_day)

    recent = _resolve_recent_scope(q, reference_day=reference_day, default_recent_days=default_recent_days)
    if recent:
        recent.update(current)
        return recent

    parsed = parse_time_range(q, today=reference_day)
    start = parsed.start.isoformat() if parsed.start else ""
    end = parsed.end.isoformat() if parsed.end else ""
    has_scope = bool(start and end and parsed.mode != "all")
    return {
        **current,
        "has_time_scope": has_scope,
        "time_expression": _matched_expression(q) if has_scope else "",
        "resolved_label": _label_for_mode(parsed.mode),
        "mode": parsed.mode,
        "start_date": start,
        "end_date": end,
        "granularity": "day" if has_scope else "",
        "confidence": 0.85 if has_scope else 0.0,
        "instruction": _instruction(_label_for_mode(parsed.mode), start, end) if has_scope else "",
    }


class TimeTool(AgentTool):
    name = "time"
    description = "Resolves current time and natural-language time ranges into standard JSON."

    def run(self, tool_input: AgentToolInput, context: ToolExecutionContext) -> AgentToolOutput:
        started = time.perf_counter()
        args = tool_input.arguments or {}
        question = str(args.get("question") or args.get("text") or "").strip()
        timezone = str(args.get("timezone") or context.metadata.get("timezone") or os.getenv("GATEWAY_TIMEZONE", DEFAULT_TIMEZONE))
        reference_date = _parse_date(args.get("reference_date"))
        scope = resolve_time_scope(question, today=reference_date, timezone=timezone)
        return AgentToolOutput(
            ok=True,
            evidence=[{"kind": "time_context", "time_context": json_safe(scope)}],
            artifacts=[{"type": "time_context", "format": "json", "content": json_safe(scope)}],
            duration_ms=round((time.perf_counter() - started) * 1000, 3),
        )


def _resolve_recent_scope(q: str, *, reference_day: date, default_recent_days: int) -> dict[str, Any]:
    recent_tokens = (
        "最近",
        "近三年",
        "最近三年",
        "近一年",
        "最近一年",
        "近7天",
        "最近7天",
        "近一周",
        "最近一周",
        "近一个月",
        "最近一个月",
        "近一月",
        "最近一月",
    )
    if not any(token in q for token in recent_tokens):
        return {}
    if any(token in q for token in ("近三年", "最近三年")):
        start = reference_day.replace(year=reference_day.year - 3)
        return _scope(
            expression="近三年",
            label="近三年",
            mode="last_3_years",
            start=start,
            end=reference_day,
            confidence=0.9,
        )
    if any(token in q for token in ("近一年", "最近一年")):
        start = reference_day - timedelta(days=365)
        return _scope("近一年", "近一年", "last_365_days", start, reference_day, 0.9)
    if any(token in q for token in ("近7天", "最近7天", "近一周", "最近一周")):
        start = reference_day - timedelta(days=6)
        return _scope("最近7天", "最近7天", "last_7_days", start, reference_day, 0.9)
    if any(token in q for token in ("近一个月", "最近一个月", "近一月", "最近一月")) or "最近" in q:
        days = max(1, int(default_recent_days or 30))
        start = reference_day - timedelta(days=days)
        return _scope("最近一个月", "最近一个月", f"last_{days}_calendar_days", start, reference_day, 0.9)
    return {}


def _scope(
    expression: str,
    label: str,
    mode: str,
    start: date,
    end: date,
    confidence: float,
) -> dict[str, Any]:
    return {
        "has_time_scope": True,
        "time_expression": expression,
        "resolved_label": label,
        "mode": mode,
        "start_date": start.isoformat(),
        "end_date": end.isoformat(),
        "granularity": "day",
        "confidence": confidence,
        "instruction": _instruction(label, start.isoformat(), end.isoformat()),
    }


def _empty_scope(*, timezone: str, today: date | None) -> dict[str, Any]:
    current = current_time_context(timezone=timezone, today=today)
    return {
        **current,
        "has_time_scope": False,
        "time_expression": "",
        "resolved_label": "",
        "mode": "none",
        "start_date": "",
        "end_date": "",
        "granularity": "",
        "confidence": 0.0,
        "instruction": "",
    }


def _instruction(label: str, start: str, end: str) -> str:
    return f"{label}按 {start} 至 {end} 口径理解。"


def _matched_expression(q: str) -> str:
    for token in ("今天", "昨天", "本周", "上周", "本月", "上个月", "本学期", "近三年", "近一年", "最近一个月"):
        if token in q:
            return token
    return ""


def _label_for_mode(mode: str) -> str:
    labels = {
        "today": "今天",
        "yesterday": "昨天",
        "this_week": "本周",
        "last_week": "上周",
        "this_month": "本月",
        "last_month": "上个月",
        "this_semester": "本学期",
        "current_semester": "本学期",
        "last_30_days": "最近30天",
    }
    return labels.get(str(mode or ""), str(mode or ""))


def _parse_date(value: Any) -> Optional[date]:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return datetime.strptime(text, "%Y-%m-%d").date()
    except Exception:
        return None
