from __future__ import annotations

from dataclasses import dataclass, field, is_dataclass, asdict
from datetime import date, datetime
from decimal import Decimal
import re
from typing import Any

from gateway_core.school.schema_index import SchoolDatasetIndex, SchoolSchemaIndex


_KEYWORD_DOMAINS: tuple[tuple[tuple[str, ...], tuple[str, ...]], ...] = (
    (("灯", "电", "水", "空调", "门", "窗", "设施", "设备", "报修", "维修"), ("repair", "asset")),
    (("执勤", "异常"), ("duty", "repair")),
    (("请假", "病假", "发热", "咳嗽", "流感"), ("leave", "morning_check", "policy", "web")),
    (("德育", "行规", "扣分", "纪律", "卫生"), ("moral", "class", "duty")),
    (("积分", "成果", "职称", "荣誉"), ("points", "achievement", "policy")),
)

_DOMAIN_DATASET_TOKENS: dict[str, tuple[str, ...]] = {
    "repair": ("repair", "报修", "维修", "故障"),
    "asset": ("asset", "资产", "设备", "设施", "灯", "电", "水", "空调", "门", "窗"),
    "duty": ("duty", "执勤", "值日", "值班", "异常", "检查"),
    "leave": ("leave", "请假", "病假", "事假"),
    "morning_check": ("morning", "晨检", "午检", "健康", "发热", "咳嗽", "流感"),
    "moral": ("moral", "德育", "行规", "扣分", "纪律"),
    "class": ("class", "班级", "年级"),
    "points": ("points", "积分", "分值"),
    "achievement": ("achievement", "成果", "荣誉", "获奖", "增值", "职称"),
}

_QUESTION_KEYWORDS: tuple[str, ...] = tuple(keyword for keywords, _domains in _KEYWORD_DOMAINS for keyword in keywords)

_PERSON_LABEL_TOKENS = (
    "姓名",
    "人员",
    "申请人",
    "提交人",
    "报修人",
    "负责人",
    "责任人",
    "教师",
    "学生",
    "家长",
    "person",
    "owner",
    "applicant",
    "teacher",
    "student",
)
_TIME_LABEL_TOKENS = ("日期", "时间", "学期", "学年", "date", "time", "created_at", "updated_at")
_LOCATION_LABEL_TOKENS = ("地点", "位置", "场地", "教室", "楼层", "楼", "校区", "区域", "location", "place", "address")
_PROJECT_LABEL_TOKENS = ("项目", "指标", "成果", "荣誉", "活动", "课题", "project", "achievement", "honor")
_OBJECT_LABEL_TOKENS = (
    "报修项目",
    "报修类型",
    "资产",
    "设备",
    "设施",
    "物品",
    "故障",
    "维修",
    "repair_item",
    "repair_type",
    "asset",
    "device",
    "item",
)
_ENTITY_LABEL_TOKENS = ("类型", "类别", "状态", "标准", "原因", "症状", "检查项", "任务")
_EXACT_FIELD_CLUE_TYPES: dict[str, str] = {
    "name": "person",
    "person_name": "person",
    "user_name": "person",
    "teacher_name": "person",
    "student_name": "person",
    "applicant_name": "person",
    "owner_name": "person",
    "date": "time",
    "event_date": "time",
    "created_at": "time",
    "updated_at": "time",
    "created_time": "time",
    "location": "location",
    "place": "location",
    "address": "location",
    "room": "location",
    "campus": "location",
    "project": "project",
    "project_name": "project",
    "activity_name": "project",
    "item": "object",
    "repair_item": "object",
    "repair_type": "object",
    "asset": "object",
    "device": "object",
    "object": "object",
}


@dataclass(frozen=True)
class EvidenceTaskRecord:
    task_id: str
    question: str = ""
    source: str = "school_schema"
    dataset_ids: list[str] = field(default_factory=list)
    row_count: int = 0
    primary_findings: list[Any] = field(default_factory=list)
    rows_sample: list[dict[str, Any]] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def as_payload(self) -> dict[str, Any]:
        return _json_safe(asdict(self))


@dataclass
class BusinessClue:
    clue_type: str
    value: str
    label: str = ""
    field_name: str = ""
    source_task_ids: list[str] = field(default_factory=list)
    dataset_ids: list[str] = field(default_factory=list)
    domains: list[str] = field(default_factory=list)
    confidence: float = 1.0

    @property
    def signature(self) -> str:
        return _clue_signature(self.clue_type, self.value)

    def merge(self, other: "BusinessClue") -> None:
        self.source_task_ids = _dedupe_strings([*self.source_task_ids, *other.source_task_ids])
        self.dataset_ids = _dedupe_strings([*self.dataset_ids, *other.dataset_ids])
        self.domains = _dedupe_strings([*self.domains, *other.domains])
        if not self.label:
            self.label = other.label
        if not self.field_name:
            self.field_name = other.field_name
        self.confidence = max(self.confidence, other.confidence)

    def as_payload(self) -> dict[str, Any]:
        return _json_safe(
            {
                "signature": self.signature,
                "clue_type": self.clue_type,
                "value": self.value,
                "label": self.label,
                "field_name": self.field_name,
                "source_task_ids": self.source_task_ids,
                "dataset_ids": self.dataset_ids,
                "domains": self.domains,
                "confidence": self.confidence,
            }
        )


@dataclass(frozen=True)
class RelatedQuerySuggestion:
    signature: str
    question: str
    source: str
    dataset_ids: list[str] = field(default_factory=list)
    clue_signature: str = ""
    clue_type: str = ""
    clue_value: str = ""
    intent: str = "detail"
    reason: str = ""
    executed: bool = False
    task_id: str = ""

    def as_payload(self) -> dict[str, Any]:
        return _json_safe(asdict(self))


class EvidenceBoard:
    """In-memory evidence board for ReAct-style tenant evidence collection.

    The board is deliberately small and deterministic: it records evidence the
    caller already gathered, extracts obvious business clues from Chinese field
    labels and common field names, and suggests follow-up queries from those
    clues plus school schema semantics. It never calls a model or database.
    """

    def __init__(
        self,
        *,
        question: str = "",
        school_id: str = "",
        school_name: str = "",
        tenant_id: str = "",
        tenant_name: str = "",
    ) -> None:
        self.question = str(question or "").strip()
        self.tenant_id = str(school_id or tenant_id or "").strip()
        self.tenant_name = str(school_name or tenant_name or "").strip()
        self._tasks: dict[str, EvidenceTaskRecord] = {}
        self._clues: dict[str, BusinessClue] = {}
        self._primary_findings: list[Any] = []
        self._primary_finding_keys: set[str] = set()
        self._suggestions: dict[str, RelatedQuerySuggestion] = {}
        self._executed_related_queries: dict[str, RelatedQuerySuggestion] = {}

        self._extract_question_clues(task_id="question", dataset_ids=[])

    @property
    def tasks(self) -> list[EvidenceTaskRecord]:
        return list(self._tasks.values())

    @property
    def business_clues(self) -> list[BusinessClue]:
        return list(self._clues.values())

    def record_task_evidence(
        self,
        *,
        task_id: str,
        question: str = "",
        source: str = "school_schema",
        dataset_ids: list[str] | tuple[str, ...] | None = None,
        row_count: int | None = None,
        primary_findings: Any | None = None,
        rows: list[dict[str, Any]] | tuple[dict[str, Any], ...] | None = None,
        metadata: dict[str, Any] | None = None,
        evidence_payload: dict[str, Any] | None = None,
    ) -> EvidenceTaskRecord:
        clean_task_id = str(task_id or "").strip()
        if not clean_task_id:
            clean_task_id = f"task_{len(self._tasks) + 1}"
        clean_dataset_ids = _dedupe_strings(dataset_ids or [])
        payload_rows = _payload_rows(evidence_payload)
        clean_rows = [_json_safe(row) for row in (rows if rows is not None else payload_rows)]
        clean_findings = _as_list(primary_findings)
        if not clean_findings and isinstance(evidence_payload, dict):
            clean_findings = _as_list(evidence_payload.get("primary_findings") or evidence_payload.get("findings"))
        clean_row_count = int(row_count if row_count is not None else _payload_row_count(evidence_payload, clean_rows))
        record = EvidenceTaskRecord(
            task_id=clean_task_id,
            question=str(question or "").strip(),
            source=str(source or "school_schema").strip() or "school_schema",
            dataset_ids=clean_dataset_ids,
            row_count=clean_row_count,
            primary_findings=[_json_safe(item) for item in clean_findings],
            rows_sample=clean_rows[:20],
            metadata=_json_safe(metadata or {}),
        )
        self._tasks[clean_task_id] = record

        for finding in record.primary_findings:
            key = _stable_key(finding)
            if key not in self._primary_finding_keys:
                self._primary_finding_keys.add(key)
                self._primary_findings.append(finding)

        self._extract_question_clues(task_id=clean_task_id, dataset_ids=clean_dataset_ids, question=record.question)
        for row in record.rows_sample:
            self._extract_row_clues(task_id=clean_task_id, dataset_ids=clean_dataset_ids, row=row)
        return record

    def mark_related_query_executed(
        self,
        *,
        signature: str | None = None,
        question: str = "",
        dataset_ids: list[str] | tuple[str, ...] | None = None,
        source: str = "school_schema",
        task_id: str = "",
    ) -> RelatedQuerySuggestion:
        clean_source = str(source or "school_schema").strip() or "school_schema"
        clean_dataset_ids = _dedupe_strings(dataset_ids or [])
        clean_question = str(question or "").strip()
        clean_signature = str(signature or "").strip() or _related_signature(
            source=clean_source,
            dataset_ids=clean_dataset_ids,
            intent="detail",
            clue_signature="",
            question=clean_question,
        )
        existing = self._executed_related_queries.get(clean_signature)
        if existing is not None:
            return existing
        prior = self._suggestions.get(clean_signature)
        executed = RelatedQuerySuggestion(
            signature=clean_signature,
            question=clean_question or (prior.question if prior else ""),
            source=clean_source,
            dataset_ids=clean_dataset_ids or (prior.dataset_ids if prior else []),
            clue_signature=prior.clue_signature if prior else "",
            clue_type=prior.clue_type if prior else "",
            clue_value=prior.clue_value if prior else "",
            intent=prior.intent if prior else "detail",
            reason=prior.reason if prior else "",
            executed=True,
            task_id=str(task_id or "").strip(),
        )
        self._executed_related_queries[clean_signature] = executed
        self._suggestions.pop(clean_signature, None)
        return executed

    def suggest_related_queries(
        self,
        *,
        package_index: SchoolSchemaIndex | None = None,
        max_suggestions: int = 8,
    ) -> list[dict[str, Any]]:
        limit = max(0, int(max_suggestions or 0))
        for clue in _prioritized_clues(self.business_clues):
            for suggestion in self._suggestions_for_clue(clue=clue, package_index=package_index):
                if suggestion.signature in self._executed_related_queries:
                    continue
                self._suggestions.setdefault(suggestion.signature, suggestion)
                if limit and len(self._suggestions) >= limit:
                    break
            if limit and len(self._suggestions) >= limit:
                break
        suggestions = list(self._suggestions.values())
        if limit:
            suggestions = suggestions[:limit]
        return [item.as_payload() for item in suggestions]

    def to_payload(self, *, include_tasks: bool = False) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "tenant_id": self.tenant_id,
            "tenant_name": self.tenant_name,
            "question": self.question,
            "primary_findings": [_json_safe(item) for item in self._primary_findings],
            "business_clues": [item.as_payload() for item in self.business_clues],
            "related_queries_executed": [item.as_payload() for item in self._executed_related_queries.values()],
            "unexplored_clues": [item.as_payload() for item in self._suggestions.values()],
        }
        if include_tasks:
            payload["tasks"] = [item.as_payload() for item in self.tasks]
        return _json_safe(payload)

    def _extract_question_clues(
        self,
        *,
        task_id: str,
        dataset_ids: list[str],
        question: str | None = None,
    ) -> None:
        text = str(self.question if question is None else question or "").strip()
        for keyword in _QUESTION_KEYWORDS:
            if keyword in text:
                self._add_clue(
                    BusinessClue(
                        clue_type="keyword",
                        value=keyword,
                        label="question",
                        field_name="question",
                        source_task_ids=[task_id],
                        dataset_ids=dataset_ids,
                        domains=_domains_for_text(keyword),
                        confidence=0.7,
                    )
                )

    def _extract_row_clues(self, *, task_id: str, dataset_ids: list[str], row: dict[str, Any]) -> None:
        if not isinstance(row, dict):
            return
        for raw_key, raw_value in row.items():
            value = _clean_value(raw_value)
            if not value:
                continue
            label = str(raw_key or "").strip()
            clue_type = _clue_type_for_label(label)
            if clue_type:
                self._add_clue(
                    BusinessClue(
                        clue_type=clue_type,
                        value=value,
                        label=label,
                        field_name=label,
                        source_task_ids=[task_id],
                        dataset_ids=dataset_ids,
                        domains=_domains_for_text(" ".join([label, value])),
                    )
                )
            for keyword in _QUESTION_KEYWORDS:
                if keyword in value:
                    self._add_clue(
                        BusinessClue(
                            clue_type="keyword",
                            value=keyword,
                            label=label,
                            field_name=label,
                            source_task_ids=[task_id],
                            dataset_ids=dataset_ids,
                            domains=_domains_for_text(keyword),
                            confidence=0.7,
                        )
                    )

    def _add_clue(self, clue: BusinessClue) -> None:
        if not clue.value:
            return
        signature = clue.signature
        existing = self._clues.get(signature)
        if existing is not None:
            existing.merge(clue)
        else:
            clue.source_task_ids = _dedupe_strings(clue.source_task_ids)
            clue.dataset_ids = _dedupe_strings(clue.dataset_ids)
            clue.domains = _dedupe_strings(clue.domains)
            self._clues[signature] = clue

    def _suggestions_for_clue(
        self,
        *,
        clue: BusinessClue,
        package_index: SchoolSchemaIndex | None,
    ) -> list[RelatedQuerySuggestion]:
        domains = clue.domains or _domains_for_text(" ".join([clue.label, clue.value]))
        out: list[RelatedQuerySuggestion] = []
        for domain in domains:
            if domain == "policy":
                out.append(_policy_suggestion(clue))
                continue
            if domain == "web":
                out.append(_web_suggestion(clue))
                continue
            datasets = _matching_dataset_ids(package_index, domain)
            for dataset in datasets:
                out.append(_tenant_suggestion(clue, dataset=dataset, domain=domain))
        if not out and package_index is not None:
            for dataset in _fallback_datasets_for_clue(package_index, clue):
                out.append(_tenant_suggestion(clue, dataset=dataset, domain="related"))
        return out


def _policy_suggestion(clue: BusinessClue) -> RelatedQuerySuggestion:
    question = f"检索“{clue.value}”相关政策、通知和处置要求"
    return RelatedQuerySuggestion(
        signature=_related_signature(
            source="official_policy",
            dataset_ids=[],
            intent="policy_search",
            clue_signature=clue.signature,
            question=question,
        ),
        question=question,
        source="official_policy",
        clue_signature=clue.signature,
        clue_type=clue.clue_type,
        clue_value=clue.value,
        intent="policy_search",
        reason=f"线索“{clue.value}”命中政策/制度语义，需要外部或校内制度依据。",
    )


def _web_suggestion(clue: BusinessClue) -> RelatedQuerySuggestion:
    question = f"检索“{clue.value}”相关公开网页和通知来源"
    return RelatedQuerySuggestion(
        signature=_related_signature(
            source="web",
            dataset_ids=[],
            intent="web_search",
            clue_signature=clue.signature,
            question=question,
        ),
        question=question,
        source="web",
        clue_signature=clue.signature,
        clue_type=clue.clue_type,
        clue_value=clue.value,
        intent="web_search",
        reason=f"线索“{clue.value}”可能需要公开通知、网页来源或外部口径核验。",
    )


def _tenant_suggestion(clue: BusinessClue, *, dataset: SchoolDatasetIndex, domain: str) -> RelatedQuerySuggestion:
    question = f"围绕“{clue.value}”继续查询{dataset.label}相关记录"
    return RelatedQuerySuggestion(
        signature=_related_signature(
            source="school_schema",
            dataset_ids=[dataset.dataset_id],
            intent="detail",
            clue_signature=clue.signature,
            question=question,
        ),
        question=question,
        source="school_schema",
        dataset_ids=[dataset.dataset_id],
        clue_signature=clue.signature,
        clue_type=clue.clue_type,
        clue_value=clue.value,
        intent="detail",
        reason=f"线索“{clue.value}”命中{domain}语义，学校 schema中存在相关数据集“{dataset.label}”。",
    )


def _matching_dataset_ids(package_index: SchoolSchemaIndex | None, domain: str) -> list[SchoolDatasetIndex]:
    if package_index is None:
        return []
    tokens = _DOMAIN_DATASET_TOKENS.get(domain, ())
    if not tokens:
        return []
    matches: list[SchoolDatasetIndex] = []
    for dataset in package_index.datasets:
        haystack = _dataset_search_text(dataset)
        if any(token.lower() in haystack for token in tokens):
            matches.append(dataset)
    return matches


def _fallback_datasets_for_clue(package_index: SchoolSchemaIndex, clue: BusinessClue) -> list[SchoolDatasetIndex]:
    if clue.clue_type == "person":
        return [dataset for dataset in package_index.datasets if dataset.person_fields][:3]
    if clue.clue_type == "time":
        return [dataset for dataset in package_index.datasets if dataset.time_fields][:3]
    if clue.clue_type == "project":
        return [
            dataset
            for dataset in package_index.datasets
            if any("项目" in field.label or "project" in field.field_id.lower() for field in dataset.fields)
        ][:3]
    return []


def _dataset_search_text(dataset: SchoolDatasetIndex) -> str:
    return " ".join(
        str(item or "").lower()
        for item in [
            dataset.dataset_id,
            dataset.label,
            dataset.description,
            dataset.source_view,
            dataset.searchable_text,
            *(field.searchable_text for field in dataset.fields),
        ]
    )


def _domains_for_text(text: str) -> list[str]:
    domains: list[str] = []
    haystack = str(text or "")
    for keywords, mapped_domains in _KEYWORD_DOMAINS:
        if any(keyword in haystack for keyword in keywords):
            domains.extend(mapped_domains)
    return _dedupe_strings(domains)


def _clue_type_for_label(label: str) -> str:
    clean = str(label or "").strip()
    lower = clean.lower()
    if lower in _EXACT_FIELD_CLUE_TYPES:
        return _EXACT_FIELD_CLUE_TYPES[lower]
    if _contains_any(clean, _LOCATION_LABEL_TOKENS, lower=lower):
        return "location"
    if _contains_any(clean, _OBJECT_LABEL_TOKENS, lower=lower):
        return "object"
    if _contains_any(clean, _PROJECT_LABEL_TOKENS, lower=lower):
        return "project"
    if _contains_any(clean, _PERSON_LABEL_TOKENS, lower=lower) and "项目" not in clean and "project" not in lower:
        return "person"
    if _contains_any(clean, _TIME_LABEL_TOKENS, lower=lower):
        return "time"
    if _contains_any(clean, _ENTITY_LABEL_TOKENS, lower=lower):
        return "entity"
    return ""


def _prioritized_clues(clues: list[BusinessClue]) -> list[BusinessClue]:
    return sorted(
        clues,
        key=lambda clue: (
            clue.clue_type == "keyword",
            -len(clue.value),
            clue.clue_type,
            clue.value,
        ),
    )


def _contains_any(text: str, tokens: tuple[str, ...], *, lower: str) -> bool:
    return any((token in text) if _has_cjk(token) else (token.lower() in lower) for token in tokens)


def _has_cjk(text: str) -> bool:
    return any("\u4e00" <= char <= "\u9fff" for char in str(text or ""))


def _clean_value(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (list, tuple, set)):
        return "、".join(_clean_value(item) for item in value if _clean_value(item))
    if isinstance(value, dict):
        return ""
    text = str(value).strip()
    if not text:
        return ""
    if len(text) > 120:
        return text[:117] + "..."
    return text


def _payload_rows(evidence_payload: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not isinstance(evidence_payload, dict):
        return []
    rows = evidence_payload.get("rows") or evidence_payload.get("records") or evidence_payload.get("data") or []
    if isinstance(rows, list):
        return [item for item in rows if isinstance(item, dict)]
    return []


def _payload_row_count(evidence_payload: dict[str, Any] | None, rows: list[dict[str, Any]]) -> int:
    if isinstance(evidence_payload, dict):
        for key in ("row_count", "count", "total"):
            value = evidence_payload.get(key)
            if isinstance(value, int):
                return value
            if isinstance(value, str) and value.isdigit():
                return int(value)
    return len(rows)


def _as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    return [value]


def _dedupe_strings(values: list[str] | tuple[str, ...]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for value in values:
        clean = str(value or "").strip()
        if not clean or clean in seen:
            continue
        seen.add(clean)
        out.append(clean)
    return out


def _normalize_signature_text(value: str) -> str:
    return re.sub(r"\s+", "", str(value or "").strip()).lower()


def _clue_signature(clue_type: str, value: str) -> str:
    return f"{str(clue_type or '').strip()}:{_normalize_signature_text(value)}"


def _related_signature(
    *,
    source: str,
    dataset_ids: list[str],
    intent: str,
    clue_signature: str,
    question: str,
) -> str:
    dataset_part = ",".join(sorted(_dedupe_strings(dataset_ids)))
    clue_part = _normalize_signature_text(clue_signature)
    question_part = _normalize_signature_text(question)
    return f"{source}:{dataset_part}:{intent}:{clue_part}:{question_part}"


def _stable_key(value: Any) -> str:
    safe = _json_safe(value)
    try:
        import json

        return json.dumps(safe, ensure_ascii=False, sort_keys=True)
    except Exception:
        return str(safe)


def _json_safe(value: Any) -> Any:
    if is_dataclass(value):
        return _json_safe(asdict(value))
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    if isinstance(value, set):
        return [_json_safe(item) for item in sorted(value, key=str)]
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


__all__ = [
    "BusinessClue",
    "EvidenceBoard",
    "EvidenceTaskRecord",
    "RelatedQuerySuggestion",
]
