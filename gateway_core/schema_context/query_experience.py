from __future__ import annotations

import hashlib
import json
import os
import re
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
        requested = int(_env_value("SQL_HISTORY_TOP_K", "TENANT_QUERY_EXPERIENCE_TOP_K", "3") or "3")
        return max(1, min(requested, _experience_normal_hard_max_k()))
    except Exception:
        return 3


def experience_top_k_for_question(question: str) -> int:
    text = str(question or "")
    complex_tokens = ["总体", "趋势", "对比", "为什么", "建议", "差距", "适合", "主要", "分布", "排名", "异常"]
    if any(token in text for token in complex_tokens):
        try:
            requested = int(os.getenv("SQL_HISTORY_COMPLEX_TOP_K", "4") or "4")
            return max(1, min(requested, _experience_complex_hard_max_k()))
        except Exception:
            return _experience_complex_hard_max_k()
    return experience_top_k()


def _experience_max_k() -> int:
    try:
        return max(1, min(int(os.getenv("SQL_HISTORY_MAX_K", "6") or "6"), 6))
    except Exception:
        return 6


def _experience_normal_hard_max_k() -> int:
    try:
        return max(1, min(int(os.getenv("SQL_HISTORY_TOP_K_HARD_MAX", "3") or "3"), _experience_max_k()))
    except Exception:
        return 3


def _experience_complex_hard_max_k() -> int:
    try:
        return max(1, min(int(os.getenv("SQL_HISTORY_COMPLEX_TOP_K_HARD_MAX", "4") or "4"), _experience_max_k()))
    except Exception:
        return 4


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


def _sql_has_school_period_filter(sql: str) -> bool:
    text = str(sql or "")
    return bool(
        re.search(
            r'"?(所属学年|所属学期|学年|学期|school_year|school_term|term_name)"?\s*(=|ILIKE|LIKE|IN)',
            text,
            flags=re.IGNORECASE,
        )
    )
