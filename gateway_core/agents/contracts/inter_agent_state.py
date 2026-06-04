from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, Field

from gateway_core.agents.contracts.output_contracts import OUTPUT_CONTRACT_VERSION


SENSITIVE_FIELD_TOKENS = (
    "姓名",
    "学生",
    "教师",
    "人员",
    "身份证",
    "手机号",
    "电话",
    "联系方式",
    "userid",
    "user_id",
    "mobile",
    "phone",
)


class EvidenceRef(BaseModel):
    """Reference to full raw evidence kept outside prompt context."""

    id: str = ""
    type: str = "evidence_ref"
    storage: str = "runtime_trace"
    evidence_ref_id: str = ""


class RawDataPolicy(BaseModel):
    """How much raw data may be embedded into agent context."""

    has_more: bool = False
    contains_sensitive_fields: bool = False
    max_embedded_rows: int = 20
    embedded_row_count: int = 0


class DataEvidenceTask(BaseModel):
    """Normalized data evidence passed between agents."""

    task_id: str
    intent: str = ""
    dataset_label: str = ""
    row_count: int = 0
    total_row_count: Optional[int] = None
    ref: EvidenceRef = Field(default_factory=EvidenceRef)
    sample: list[dict[str, Any]] = Field(default_factory=list)
    lineage: dict[str, Any] = Field(default_factory=dict)
    evidence_summary: dict[str, Any] = Field(default_factory=dict)
    raw_data_policy: RawDataPolicy = Field(default_factory=RawDataPolicy)
    caveats: list[str] = Field(default_factory=list)


class InterAgentState(BaseModel):
    """Shared compact state envelope for agent handoffs."""

    contract_version: str = OUTPUT_CONTRACT_VERSION
    question: str = ""
    required_outputs: list[str] = Field(default_factory=list)
    completed_outputs: list[str] = Field(default_factory=list)
    data_evidence: dict[str, DataEvidenceTask] = Field(default_factory=dict)
    evidence_board: dict[str, Any] = Field(default_factory=dict)
    source_views: list[str] = Field(default_factory=list)
    tool_contract: dict[str, Any] = Field(default_factory=dict)
    external_evidence: list[dict[str, Any]] = Field(default_factory=list)
    artifacts: list[dict[str, Any]] = Field(default_factory=list)
    caveats: list[str] = Field(default_factory=list)


def build_inter_agent_state(
    *,
    question: str,
    data_evidence: dict[str, Any] | None = None,
    evidence_board: dict[str, Any] | None = None,
    source_views: list[str] | None = None,
    tool_contract: dict[str, Any] | None = None,
    required_outputs: list[str] | None = None,
    completed_outputs: list[str] | None = None,
    external_evidence: list[dict[str, Any]] | None = None,
    artifacts: list[dict[str, Any]] | None = None,
    caveats: list[str] | None = None,
    sample_limit: int = 20,
) -> InterAgentState:
    tasks: dict[str, DataEvidenceTask] = {}
    for task_id, payload in (data_evidence or {}).items():
        if isinstance(payload, dict):
            tasks[str(task_id)] = _data_evidence_task(str(task_id), payload, sample_limit=sample_limit)
    return InterAgentState(
        question=str(question or ""),
        required_outputs=_clean_list(required_outputs),
        completed_outputs=_clean_list(completed_outputs),
        data_evidence=tasks,
        evidence_board=dict(evidence_board or {}),
        source_views=_clean_list(source_views),
        tool_contract=dict(tool_contract or {}),
        external_evidence=[item for item in (external_evidence or []) if isinstance(item, dict)],
        artifacts=[item for item in (artifacts or []) if isinstance(item, dict)],
        caveats=_clean_list(caveats),
    )


def _data_evidence_task(task_id: str, payload: dict[str, Any], *, sample_limit: int) -> DataEvidenceTask:
    summary = payload.get("evidence_summary") if isinstance(payload.get("evidence_summary"), dict) else {}
    lineage = payload.get("sql_lineage") if isinstance(payload.get("sql_lineage"), dict) else {}
    sample = _sample_rows(payload, summary=summary, limit=sample_limit)
    row_count = _int_value(payload.get("row_count"), default=len(sample))
    total_row_count = _optional_int(payload.get("total_row_count") or summary.get("total_row_count"))
    caveats = _clean_list(payload.get("caveats") or summary.get("caveats"))
    return DataEvidenceTask(
        task_id=task_id,
        intent=str(payload.get("intent") or summary.get("intent") or ""),
        dataset_label=str(payload.get("dataset_label") or summary.get("dataset_label") or ""),
        row_count=row_count,
        total_row_count=total_row_count,
        ref=_evidence_ref(task_id, payload=payload, lineage=lineage),
        sample=sample,
        lineage=dict(lineage),
        evidence_summary=dict(summary),
        raw_data_policy=RawDataPolicy(
            has_more=_bool_value(
                payload.get("query_may_have_more")
                or payload.get("display_rows_has_more")
                or summary.get("query_may_have_more")
            ),
            contains_sensitive_fields=_contains_sensitive_fields(sample, payload=payload),
            max_embedded_rows=max(0, int(sample_limit or 0)),
            embedded_row_count=len(sample),
        ),
        caveats=caveats,
    )


def _evidence_ref(task_id: str, *, payload: dict[str, Any], lineage: dict[str, Any]) -> EvidenceRef:
    ref_id = str(
        payload.get("raw_sql_handle")
        or payload.get("raw_data_ref")
        or payload.get("evidence_ref")
        or lineage.get("evidence_ref_id")
        or f"task:{task_id}:raw_rows"
    ).strip()
    evidence_ref_id = str(lineage.get("evidence_ref_id") or "").strip()
    storage = "runtime_trace" if ref_id.startswith("trace://") or ref_id.startswith("task:") else "external"
    return EvidenceRef(id=ref_id, storage=storage, evidence_ref_id=evidence_ref_id)


def _sample_rows(payload: dict[str, Any], *, summary: dict[str, Any], limit: int) -> list[dict[str, Any]]:
    candidates = (
        payload.get("row_sample"),
        summary.get("row_sample"),
        summary.get("top_items"),
        payload.get("display_rows"),
        payload.get("items"),
    )
    for value in candidates:
        rows = [dict(item) for item in value if isinstance(item, dict)] if isinstance(value, list) else []
        if rows:
            return rows[: max(0, int(limit or 0))]
    return []


def _contains_sensitive_fields(sample: list[dict[str, Any]], *, payload: dict[str, Any]) -> bool:
    keys: set[str] = set()
    for row in sample:
        keys.update(str(key) for key in row)
    field_labels = payload.get("field_labels")
    if isinstance(field_labels, dict):
        keys.update(str(key) for key in field_labels)
        keys.update(str(value) for value in field_labels.values())
    haystack = " ".join(keys).lower()
    return any(token.lower() in haystack for token in SENSITIVE_FIELD_TOKENS)


def _clean_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item or "").strip()]


def _int_value(value: Any, *, default: int = 0) -> int:
    try:
        return int(value)
    except Exception:
        return default


def _optional_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except Exception:
        return None


def _bool_value(value: Any) -> bool:
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)
