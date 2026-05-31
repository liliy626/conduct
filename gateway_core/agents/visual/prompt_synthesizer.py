from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from langchain_core.messages import BaseMessage

from gateway_core.prompts.prompt_domains import (
    IMAGE_ENTITY_CONTEXTS,
    IMAGE_MASTER_TEMPLATE,
    IMAGE_STYLE_THEMES,
)


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
        return IMAGE_MASTER_TEMPLATE.format(
            style_theme=style_axis,
            entity_context=entity_axis,
            data_signal=data_axis,
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
        return IMAGE_STYLE_THEMES["warning"]
    if any(token in text for token in ("对比", "趋势", "变化", "环比", "同比")):
        return IMAGE_STYLE_THEMES["analytical"]
    if any(token in text for token in ("汇报", "领导", "校长", "大屏", "驾驶舱")):
        return IMAGE_STYLE_THEMES["executive"]
    return IMAGE_STYLE_THEMES["default"]


def _entity_axis(*, text: str, purpose: str) -> str:
    combined = f"{text} {purpose}".lower()
    if any(token in combined for token in ("眼保健操", "违纪", "扣分", "行规", "纪律", "德育")):
        return IMAGE_ENTITY_CONTEXTS["student_discipline"]
    if any(token in combined for token in ("老师", "教师", "教研组", "请假", "销假", "假勤")):
        return IMAGE_ENTITY_CONTEXTS["faculty"]
    if any(token in combined for token in ("年级", "班级", "学生")):
        return IMAGE_ENTITY_CONTEXTS["student_grade"]
    return IMAGE_ENTITY_CONTEXTS["default"].format(purpose=purpose)


def _data_axis(*, tables: list[str], row_count: int) -> str:
    table_text = ", ".join(tables[:3]) if tables else "audited school data"
    return f"accurately visualizing {row_count} real-time data records registered in table '{table_text}'"
