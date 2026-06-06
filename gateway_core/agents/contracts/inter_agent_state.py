from __future__ import annotations

from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, NonNegativeInt, StringConstraints, ValidationError, model_validator
from typing_extensions import Annotated

from gateway_core.agents.contracts.output_contracts import OUTPUT_CONTRACT_VERSION


NonEmptyStr = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, strict=True)]
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
    id: NonEmptyStr
    type: str = "evidence_ref"
    storage: str = "runtime_trace"
    evidence_ref_id: str = ""


class RawDataPolicy(BaseModel):
    original_count: int
    included_count: int
    truncated: bool
    has_more: bool = False
    contains_sensitive_fields: bool = False
    max_embedded_rows: int = 20
    embedded_row_count: int = 0


class DataEvidenceTask(BaseModel):
    task_id: NonEmptyStr
    intent: NonEmptyStr
    dataset_label: NonEmptyStr
    row_count: int
    total_row_count: Optional[int] = None
    ref: EvidenceRef
    sample: list[dict[str, Any]]
    lineage: dict[str, Any]
    evidence_summary: dict[str, Any]
    raw_data_policy: RawDataPolicy
    caveats: list[str] = Field(default_factory=list)


class InterAgentState(BaseModel):
    contract_version: str = OUTPUT_CONTRACT_VERSION
    question: str = ""
    required_outputs: list[NonEmptyStr] = Field(default_factory=list)
    completed_outputs: list[NonEmptyStr] = Field(default_factory=list)
    data_evidence: dict[str, DataEvidenceTask] = Field(default_factory=dict)
    evidence_board: dict[str, Any] = Field(default_factory=dict)
    source_views: list[NonEmptyStr] = Field(default_factory=list)
    tool_contract: dict[str, Any] = Field(default_factory=dict)
    external_evidence: list[dict[str, Any]] = Field(default_factory=list)
    artifacts: list[dict[str, Any]] = Field(default_factory=list)
    caveats: list[NonEmptyStr] = Field(default_factory=list)


class RawDataEvidencePayload(BaseModel):
    model_config = ConfigDict(strict=True)

    task_id: NonEmptyStr
    allowed: Literal[True]
    intent: NonEmptyStr
    dataset_label: NonEmptyStr
    row_count: NonNegativeInt
    sql_lineage: dict[str, Any]
    evidence_summary: dict[str, Any]
    raw_sql_handle: NonEmptyStr
    total_row_count: Optional[NonNegativeInt] = None
    row_sample: Optional[list[dict[str, Any]]] = None
    display_rows: Optional[list[dict[str, Any]]] = None
    field_labels: dict[str, Any] = Field(default_factory=dict)
    caveats: list[NonEmptyStr] = Field(default_factory=list)
    query_may_have_more: bool = False
    display_rows_has_more: bool = False

    @model_validator(mode="after")
    def validate_contract(self) -> RawDataEvidencePayload:
        if self.row_count > 0 and not self.sample_source:
            raise ValueError("row_sample/display_rows is required when row_count > 0")
        evidence_ref_id = self.sql_lineage.get("evidence_ref_id", "")
        if evidence_ref_id != "" and not isinstance(evidence_ref_id, str):
            raise ValueError("sql_lineage.evidence_ref_id must be a string")
        return self

    @property
    def sample_source(self) -> list[dict[str, Any]]:
        if self.row_sample is not None:
            return self.row_sample
        if self.display_rows is not None:
            return self.display_rows
        raise KeyError("row_sample")


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
    if sample_limit < 0:
        raise ValueError("sample_limit must be non-negative")
    return InterAgentState(
        question=question,
        required_outputs=required_outputs or [],
        completed_outputs=completed_outputs or [],
        data_evidence={
            str(task_id): validate_data_evidence_payload(payload, task_id=str(task_id), sample_limit=sample_limit)
            for task_id, payload in (data_evidence or {}).items()
        },
        evidence_board=dict(evidence_board or {}),
        source_views=source_views or [],
        tool_contract=dict(tool_contract or {}),
        external_evidence=external_evidence or [],
        artifacts=artifacts or [],
        caveats=caveats or [],
    )


def validate_data_evidence_payload(
    payload: Any,
    *,
    task_id: str | None = None,
    sample_limit: int = 20,
) -> DataEvidenceTask:
    if not isinstance(payload, dict):
        raise ValueError("data evidence payload must be a dict")
    raw_payload = dict(payload)
    if task_id is not None:
        raw_payload["task_id"] = task_id
    try:
        raw = RawDataEvidencePayload.model_validate(raw_payload)
    except ValidationError as exc:
        raise ValueError(str(exc)) from exc
    sample = raw.sample_source[:sample_limit]
    return DataEvidenceTask(
        task_id=raw.task_id,
        intent=raw.intent,
        dataset_label=raw.dataset_label,
        row_count=raw.row_count,
        total_row_count=raw.total_row_count,
        ref=_evidence_ref(raw),
        sample=sample,
        lineage=dict(raw.sql_lineage),
        evidence_summary=dict(raw.evidence_summary),
        raw_data_policy=RawDataPolicy(
            original_count=raw.row_count,
            included_count=len(sample),
            truncated=raw.row_count > len(sample),
            has_more=raw.query_may_have_more or raw.display_rows_has_more,
            contains_sensitive_fields=_contains_sensitive_fields(sample, raw.field_labels),
            max_embedded_rows=sample_limit,
            embedded_row_count=len(sample),
        ),
        caveats=raw.caveats,
    )

def _evidence_ref(raw: RawDataEvidencePayload) -> EvidenceRef:
    ref_id = raw.raw_sql_handle
    evidence_ref_id = raw.sql_lineage.get("evidence_ref_id", "")
    storage = "runtime_trace" if ref_id.startswith("trace://") else "external"
    return EvidenceRef(id=ref_id, storage=storage, evidence_ref_id=evidence_ref_id)


def _contains_sensitive_fields(sample: list[dict[str, Any]], field_labels: dict[str, Any]) -> bool:
    keys = {str(key) for row in sample for key in row}
    keys.update(str(key) for key in field_labels)
    keys.update(str(value) for value in field_labels.values())
    haystack = " ".join(keys).lower()
    return any(token.lower() in haystack for token in SENSITIVE_FIELD_TOKENS)
