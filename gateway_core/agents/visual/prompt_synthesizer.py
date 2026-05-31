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
    """Build image prompts from matrix-routed style, entity, and data axes."""

    @classmethod
    def synthesize(
        cls,
        *,
        history_messages: Sequence[BaseMessage],
        purpose: str,
        tables: list[str],
        row_count: int,
    ) -> str:
        full_text = " ".join(
            str(item)
            for content in (getattr(message, "content", "") for message in list(history_messages)[-6:])
            for item in (content if isinstance(content, list) else [content])
        ).lower()
        style_key = max(
            [(0, "default")]
            + [
                (len(str(word)), key)
                for key, words in STYLE_ROUTER_MATRIX.items()
                for word in words
                if str(word) and str(word) in full_text
            ],
            key=lambda item: item[0],
        )[1]
        entity_text = f"{full_text} {purpose}".lower()
        entity_key = max(
            [(0, "default")]
            + [
                (len(str(word)), key)
                for key, words in ENTITY_ROUTER_MATRIX.items()
                for word in words
                if str(word) and str(word) in entity_text
            ],
            key=lambda item: item[0],
        )[1]
        table_text = (", ".join(tables[:3]), "audited school data")[not bool(tables)]
        return IMAGE_MASTER_TEMPLATE.format(
            style_theme=IMAGE_STYLE_THEMES[style_key],
            entity_context=IMAGE_ENTITY_CONTEXTS[entity_key].format(purpose=purpose),
            data_signal=f"accurately visualizing {row_count} real-time data records registered in table '{table_text}'",
        )
