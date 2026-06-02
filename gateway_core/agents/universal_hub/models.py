from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from pydantic import BaseModel, Field


class SkillEvent(BaseModel):
    """A normalized streaming event emitted by a vertical skill."""

    event_type: Literal[
        "process",
        "tool_start",
        "tool_end",
        "artifact",
        "evidence",
        "evidence_completed",
        "content",
    ]
    data: Any
    metadata: dict[str, Any] = Field(default_factory=dict)


@dataclass(frozen=True)
class SkillSpec:
    """Static registry metadata for a skill.

    This is deliberately a dataclass rather than a Pydantic model because it
    stores Python classes and immutable metadata, not user-facing data.
    """

    name: str
    skill_cls: type
    outputs: frozenset[str]
    tools: frozenset[str]
    supports_stream: bool
    default_model_role: str


@dataclass(frozen=True)
class MultimodalOutputContract:
    """Typed handoff contract for multimodal artifact-producing skills."""

    artifact_type: str
    artifact_id: str
    cdn_url: str
    crypto_proof: str
    meta_payload: dict[str, Any]
