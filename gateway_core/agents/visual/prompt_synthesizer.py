from __future__ import annotations

from collections.abc import Sequence

from langchain_core.messages import BaseMessage

from gateway_core.prompts.prompt_domains import (
    ENTITY_ROUTER_MATRIX,
    IMAGE_ENTITY_CONTEXTS,
    IMAGE_MASTER_TEMPLATE,
    IMAGE_STYLE_THEMES,
    STYLE_ROUTER_MATRIX,
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
    key = _route_key(text, STYLE_ROUTER_MATRIX, default_key="default")
    return IMAGE_STYLE_THEMES[key]


def _entity_axis(*, text: str, purpose: str) -> str:
    combined = f"{text} {purpose}".lower()
    key = _route_key(combined, ENTITY_ROUTER_MATRIX, default_key="default")
    return IMAGE_ENTITY_CONTEXTS[key].format(purpose=purpose)


def _route_key(text: str, matrix: dict[str, Sequence[str]], *, default_key: str) -> str:
    matches = [
        (len(str(word)), key)
        for key, words in matrix.items()
        for word in words
        if str(word) and str(word) in text
    ]
    if not matches:
        return default_key
    return max(matches, key=lambda item: item[0])[1]


def _data_axis(*, tables: list[str], row_count: int) -> str:
    table_text = ", ".join(tables[:3]) if tables else "audited school data"
    return f"accurately visualizing {row_count} real-time data records registered in table '{table_text}'"
