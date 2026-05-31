from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from langchain_core.messages import BaseMessage


class TripleAxisPromptSynthesizer:
    """Build image prompts from style intent, entity context, and data evidence."""

    @classmethod
    def synthesize(
        cls,
        *,
        history_messages: Sequence[BaseMessage],
        purpose: str,
        tables: list[str],
        row_count: int,
    ) -> str:
        text = _recent_text(history_messages)
        style_axis = _style_axis(text)
        entity_axis = _entity_axis(text=text, purpose=purpose)
        data_axis = _data_axis(tables=tables, row_count=row_count)
        return (
            f"{style_axis}, {entity_axis}, {data_axis}, "
            "clean executive campus dashboard composition, crisp vector-like UI, "
            "no invented numbers, no fake people portraits, no dark sci-fi style."
        )


def _recent_text(history_messages: Sequence[BaseMessage]) -> str:
    parts: list[str] = []
    for message in list(history_messages)[-6:]:
        content = getattr(message, "content", "")
        if isinstance(content, str):
            parts.append(content)
        elif isinstance(content, list):
            parts.extend(str(item) for item in content)
        else:
            parts.append(str(content))
    return " ".join(parts).lower()


def _style_axis(text: str) -> str:
    if any(token in text for token in ("警示", "严重", "最差", "问题", "风险", "橙色", "预警")):
        return "Warning tone administrative infographic, amber and deep orange alert style"
    if any(token in text for token in ("对比", "趋势", "变化", "环比", "同比")):
        return "Analytical data trend presentation graphic, blue and cyan comparison style"
    if any(token in text for token in ("汇报", "领导", "校长", "大屏", "驾驶舱")):
        return "Executive school operations dashboard illustration, polished light BI style"
    return "Professional school management dashboard illustration, clean light operations style"


def _entity_axis(*, text: str, purpose: str) -> str:
    combined = f"{text} {purpose}".lower()
    if any(token in combined for token in ("眼保健操", "违纪", "扣分", "行规", "纪律", "德育")):
        return "student behavior discipline and routine inspection dashboard"
    if any(token in combined for token in ("老师", "教师", "教研组", "请假", "销假", "假勤")):
        return "faculty attendance and leave analytics dashboard"
    if any(token in combined for token in ("年级", "班级", "学生")):
        return "student grade and class operations dashboard"
    return f"campus analytics dashboard about {purpose}"


def _data_axis(*, tables: list[str], row_count: int) -> str:
    table_text = ", ".join(tables[:3]) if tables else "audited school data"
    return f"accurately visualizing {row_count} real-time data records registered in table '{table_text}'"
