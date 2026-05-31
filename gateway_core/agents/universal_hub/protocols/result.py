from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal


SkillStatus = Literal["success", "partial", "failed"]
SkillErrorCode = Literal["TIMEOUT", "EMPTY_RESULT", "TOOL_ERROR", "PERMISSION_DENIED", "AST_VIOLATION"]


@dataclass(frozen=True)
class SkillErrorDetail:
    code: SkillErrorCode
    message: str
    recoverable: bool
    failed_tool: str | None = None


@dataclass(frozen=True)
class SkillResult:
    status: SkillStatus
    delivered_outputs: frozenset[str]
    evidence_refs: tuple[str, ...] = ()
    artifact_refs: tuple[str, ...] = ()
    errors: tuple[SkillErrorDetail, ...] = field(default_factory=tuple)
