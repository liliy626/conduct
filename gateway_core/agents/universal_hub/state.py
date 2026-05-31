from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

from langgraph.graph import MessagesState


@dataclass(frozen=True)
class ImageArtifact:
    """Immutable image artifact reference kept outside chat history."""

    artifact_id: str
    prompt_used: str
    cdn_url: str
    linked_sql_hash: str
    status: str


class UniversalAgentState(MessagesState, total=False):
    """Lightweight state for the future universal hub.

    Large rows, documents, images, and trace payloads must stay outside this
    state. Store only references here so LangGraph checkpointing stays cheap
    and serializable.
    """

    session_context: dict[str, Any]
    active_skill: Optional[str]
    required_outputs: list[str]
    completed_outputs: list[str]
    evidence_board_id: str
    evidence_refs: list[str]
    artifact_refs: list[str]
    multimodal_artifacts: dict[str, ImageArtifact]
    meta_context: dict[str, Any]
    visited_skills: list[str]
    skill_call_count: int
    max_skill_calls: int
    candidate_skills: list[str]
    hub_error: str
