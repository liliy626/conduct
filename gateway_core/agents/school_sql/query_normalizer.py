from __future__ import annotations

import hashlib
from typing import Any


class QueryNormalizer:
    """Map common school questions to deterministic execution slots."""

    @staticmethod
    def extract_tenant_id(payload_or_context: dict[str, Any] | None) -> str:
        data = payload_or_context or {}
        session_context = data.get("session_context") if isinstance(data.get("session_context"), dict) else data
        tenant_id = str(session_context.get("school_id") or session_context.get("tenant_id") or "default").strip()
        return tenant_id or "default"

    @classmethod
    def to_canonical_slot(cls, query: str) -> str:
        q = str(query or "").strip().lower()
        if not q:
            return "raw_slot:empty"
        if any(term in q for term in ("请假", "销假", "假勤")) and any(
            term in q for term in ("最多", "排名", "排行", "top", "前三", "前3")
        ):
            return "slot:teacher_leave_ranking"
        if any(term in q for term in ("德育", "扣分", "行规", "纪律对比")):
            return "slot:moral_score_comparison"
        if "眼保健操" in q:
            return "slot:eye_exercise_discipline"
        digest = hashlib.sha256(q.encode("utf-8")).hexdigest()[:16]
        return f"raw_slot:{digest}"
