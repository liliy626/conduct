from __future__ import annotations

import hashlib
import json
import os
import re
from pathlib import Path
from typing import Any

from gateway_core.schema_context.ttl_cache import TTLCache


EXPERIENCE_CACHE = TTLCache(max_entries=512, ttl_seconds=300)


def _env_value(primary: str, legacy: str = "", default: str = "") -> str:
    value = os.getenv(primary, "").strip()
    if value:
        return value
    if legacy:
        value = os.getenv(legacy, "").strip()
        if value:
            return value
    return default


def hash_payload(payload: dict[str, Any]) -> str:
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def record_experience_enabled() -> bool:
    return _env_value("SCHOOL_SQL_RECORD_EXPERIENCE_ENABLED", "TENANT_DDL_REACT_RECORD_EXPERIENCE_ENABLED", "1").lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def experience_success_score(*, row_count: int, selected: list[dict[str, str]]) -> float:
    score = 0.55
    if row_count > 0:
        score += 0.25
    if selected:
        score += 0.15
    return min(score, 0.95)


def experience_summary_text(evidence_summary: dict[str, Any]) -> str:
    if not isinstance(evidence_summary, dict):
        return ""
    parts: list[str] = []
    row_count = evidence_summary.get("row_count")
    if row_count is not None:
        parts.append(f"row_count={row_count}")
    columns = evidence_summary.get("columns")
    if isinstance(columns, list) and columns:
        parts.append("columns=" + ",".join(str(item) for item in columns[:12]))
    sample = evidence_summary.get("row_sample")
    if sample:
        parts.append("sample=" + json.dumps(sample[:3], ensure_ascii=False, default=str))
    return " | ".join(parts)[:1000]


def experience_top_k() -> int:
    try:
        return max(1, min(int(_env_value("SQL_HISTORY_TOP_K", "TENANT_QUERY_EXPERIENCE_TOP_K", "5") or "5"), _experience_max_k()))
    except Exception:
        return 5


def experience_top_k_for_question(question: str) -> int:
    text = str(question or "")
    complex_tokens = ["总体", "趋势", "对比", "为什么", "建议", "差距", "适合", "主要", "分布", "排名", "异常"]
    if any(token in text for token in complex_tokens):
        try:
            return max(1, min(int(os.getenv("SQL_HISTORY_COMPLEX_TOP_K", "8") or "8"), _experience_max_k()))
        except Exception:
            return min(8, _experience_max_k())
    return experience_top_k()


def _experience_max_k() -> int:
    try:
        return max(1, min(int(os.getenv("SQL_HISTORY_MAX_K", "10") or "10"), 20))
    except Exception:
        return 10


def experience_schema(source_schema: str = "") -> str:
    mode = _env_value("SCHOOL_QUERY_EXPERIENCE_SCHEMA_MODE", "TENANT_QUERY_EXPERIENCE_SCHEMA_MODE", "source_schema").lower()
    if mode in {"school", "school_schema", "source_schema", "tenant", "tenant_schema"}:
        return str(source_schema or "").strip() or "platform"
    configured = _env_value("SCHOOL_QUERY_EXPERIENCE_SCHEMA", "TENANT_QUERY_EXPERIENCE_SCHEMA")
    if configured in {"__school__", "$school_schema", "__tenant__", "$tenant_schema", "$source_schema"}:
        return str(source_schema or "").strip() or "platform"
    return configured or "platform"


def experience_table() -> str:
    return _env_value("SCHOOL_QUERY_EXPERIENCE_TABLE", "TENANT_QUERY_EXPERIENCE_TABLE", "sql_history_vector_documents") or "sql_history_vector_documents"


def experience_cache_enabled() -> bool:
    return _env_value("SCHOOL_QUERY_EXPERIENCE_CACHE_ENABLED", "TENANT_QUERY_EXPERIENCE_CACHE_ENABLED", "1").lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def experience_hints_for_question(*, question: str, schema_name: str = "") -> list[dict[str, Any]]:
    clean_question = str(question or "").strip()
    if not clean_question:
        return []
    clean_schema = str(schema_name or "").strip()
    out: list[dict[str, Any]] = []
    for item in _load_experience_hints():
        if not isinstance(item, dict) or not _hint_schema_matches(item, clean_schema):
            continue
        if not _hint_question_matches(item, clean_question):
            continue
        out.append(_hint_to_experience(item, fallback_question=clean_question))
    return out


def merge_experience_hints(
    *,
    hints: list[dict[str, Any]],
    experiences: list[dict[str, Any]],
    limit: int,
) -> list[dict[str, Any]]:
    clean_limit = max(1, min(int(limit or 5), _experience_max_k()))
    merged: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in [*(hints or []), *(experiences or [])]:
        if not isinstance(item, dict):
            continue
        key = _experience_identity(item)
        if key in seen:
            continue
        seen.add(key)
        merged.append(item)
        if len(merged) >= clean_limit:
            break
    return merged


def sanitize_experiences_for_question(question: str, experiences: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if _question_has_explicit_school_period(question):
        return experiences
    sanitized: list[dict[str, Any]] = []
    for item in experiences:
        copied = dict(item or {})
        raw_sql = str(copied.get("raw_sql") or "")
        if _sql_has_school_period_filter(raw_sql):
            copied["raw_sql"] = ""
            copied["answer_summary"] = (
                "历史经验仅可参考表选择/字段方向；原 SQL 含学年或学期过滤，"
                "但当前问题未明确限定本学年/本学期/某学年，因此不能沿用该时间条件。"
            )
            copied["time_filter_omitted"] = True
        sanitized.append(copied)
    return sanitized


def _question_has_explicit_school_period(question: str) -> bool:
    text = str(question or "")
    return bool(
        re.search(
            r"\d{4}\s*学年|\d{4}\s*年|本学年|这个学年|本学期|这个学期|上学期|下学期|第一学期|第二学期",
            text,
        )
    )


def _load_experience_hints() -> list[dict[str, Any]]:
    raw_path = _env_value("SCHOOL_QUERY_EXPERIENCE_HINTS_FILE", "TENANT_QUERY_EXPERIENCE_HINTS_FILE", "config/sql_history_hints.json")
    if not raw_path:
        return []
    path = Path(raw_path)
    if not path.is_absolute():
        path = Path.cwd() / path
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return []
    if isinstance(raw, dict):
        raw = raw.get("hints")
    if not isinstance(raw, list):
        return []
    return [item for item in raw if isinstance(item, dict)]


def _hint_schema_matches(item: dict[str, Any], schema_name: str) -> bool:
    raw_schemas = item.get("schemas")
    if not isinstance(raw_schemas, list):
        raw_schemas = [item.get("schema")]
    schemas = {str(value or "").strip() for value in raw_schemas if str(value or "").strip()}
    return not schemas or "*" in schemas or str(schema_name or "").strip() in schemas


def _hint_question_matches(item: dict[str, Any], question: str) -> bool:
    raw_patterns = item.get("question_patterns")
    if not isinstance(raw_patterns, list):
        raw_patterns = [item.get("question")]
    clean_question = str(question or "").strip()
    for raw in raw_patterns:
        pattern = str(raw or "").strip()
        if pattern and (pattern in clean_question or clean_question in pattern):
            return True
    return False


def _hint_to_experience(item: dict[str, Any], *, fallback_question: str) -> dict[str, Any]:
    return {
        "question": str(item.get("question") or fallback_question),
        "raw_sql": str(item.get("raw_sql") or ""),
        "table_refs": _string_list(item.get("table_refs")),
        "column_refs": _string_list(item.get("column_refs")),
        "row_count": _safe_int(item.get("row_count"), 0),
        "used_count": _safe_int(item.get("used_count"), 0),
        "guardrail_version": str(item.get("guardrail_version") or "manual_history_hint_v1"),
        "similarity": float(item.get("similarity") or 1.0),
        "answer_summary": str(item.get("answer_summary") or ""),
        "source": "manual_history_hint",
        "manual_hint": True,
    }


def _experience_identity(item: dict[str, Any]) -> str:
    raw_sql = str(item.get("raw_sql") or "").strip()
    if raw_sql:
        return "sql:" + raw_sql.lower()
    return "question:" + str(item.get("question") or "").strip() + "\n" + str(item.get("answer_summary") or "").strip()


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item) for item in value if str(item or "").strip()]


def _safe_int(value: Any, fallback: int) -> int:
    try:
        return int(value)
    except Exception:
        return fallback


def _sql_has_school_period_filter(sql: str) -> bool:
    text = str(sql or "")
    return bool(
        re.search(
            r'"?(所属学年|所属学期|学年|学期|school_year|school_term|term_name)"?\s*(=|ILIKE|LIKE|IN)',
            text,
            flags=re.IGNORECASE,
        )
    )
