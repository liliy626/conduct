from __future__ import annotations

from collections.abc import Sequence
from langchain_core.messages import BaseMessage

from gateway_core.prompts.prompt_domains import (
    ENTITY_ROUTER_MATRIX,
    IMAGE_ENTITY_CONTEXTS,
    IMAGE_MASTER_TEMPLATE,
    IMAGE_STYLE_THEMES,
    STYLE_ROUTER_MATRIX,
    render_triple_axis_prompt,
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
        answer_context: str = "",
    ) -> str:
        return render_triple_axis_prompt(
            history_messages=history_messages,
            purpose=purpose,
            tables=tables,
            row_count=row_count,
            answer_context=answer_context,
            style_themes=IMAGE_STYLE_THEMES,
            style_router_matrix=STYLE_ROUTER_MATRIX,
            entity_contexts=IMAGE_ENTITY_CONTEXTS,
            entity_router_matrix=ENTITY_ROUTER_MATRIX,
            master_template=IMAGE_MASTER_TEMPLATE,
        )
