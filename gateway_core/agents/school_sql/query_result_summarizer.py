from __future__ import annotations

import os
from decimal import Decimal
from typing import Any


def _env_value(primary: str, legacy: str = "", default: str = "") -> str:
    value = os.getenv(primary, "").strip()
    if value:
        return value
    if legacy:
        value = os.getenv(legacy, "").strip()
        if value:
            return value
    return default


def summarize_query_result(
    *,
    intent: str,
    row_count: int,
    formatted_rows: list[dict[str, Any]],
    field_labels: dict[str, str],
) -> dict[str, Any]:
    """Compress query rows into a small, LLM-friendly evidence summary.

    The ReAct agent should reason over business evidence, not re-process a
    large row dump. Raw rows stay in trace/debug logs; this payload keeps only
    aggregates, top breakdown items, and a small representative sample.
    """
    clean_intent = str(intent or "").strip() or "list"
    rows = [_json_safe_row(row) for row in formatted_rows if isinstance(row, dict)]
    sample_limit = _env_int("SCHOOL_REACT_ROW_SAMPLE_LIMIT", 5, min_value=0, max_value=20, legacy="TENANT_REACT_ROW_SAMPLE_LIMIT")
    top_limit = _env_int("SCHOOL_REACT_TOP_ITEM_LIMIT", 10, min_value=1, max_value=30, legacy="TENANT_REACT_TOP_ITEM_LIMIT")
    numeric_columns = _numeric_columns(rows)
    dimension_columns = _dimension_columns(rows, numeric_columns)
    one_row_summary = rows[0] if len(rows) == 1 else {}
    top_items = rows[:top_limit] if clean_intent in {"group_count", "group_sum", "rank", "trend"} else []
    row_sample = rows[:sample_limit] if clean_intent in {"list", "detail"} else []
    summary = {
        "row_count": int(row_count or 0),
        "intent": clean_intent,
        "result_shape": _result_shape(clean_intent, len(rows)),
        "field_labels": field_labels,
        "dimensions": dimension_columns,
        "metrics": numeric_columns,
        "one_row_summary": one_row_summary,
        "top_items": top_items,
        "row_sample": row_sample,
        "notable_findings": _notable_findings(
            intent=clean_intent,
            row_count=int(row_count or 0),
            rows=rows,
            numeric_columns=numeric_columns,
            dimension_columns=dimension_columns,
        ),
    }
    return _drop_empty(summary)


def rows_for_board(summary: dict[str, Any]) -> list[dict[str, Any]]:
    """Rows used only for clue extraction inside EvidenceBoard."""
    rows: list[dict[str, Any]] = []
    for key in ("top_items", "row_sample"):
        value = summary.get(key)
        if isinstance(value, list):
            rows.extend([item for item in value if isinstance(item, dict)])
    one_row = summary.get("one_row_summary")
    if isinstance(one_row, dict) and one_row:
        rows.append(one_row)
    return rows[:20]


def display_rows_for_shape(
    *,
    evidence_shape: str,
    formatted_rows: list[dict[str, Any]],
) -> dict[str, Any]:
    clean_shape = str(evidence_shape or "").strip().lower()
    if clean_shape not in {"display", "export"}:
        return {}
    limit = _display_row_budget()
    rows = [_json_safe_row(row) for row in formatted_rows if isinstance(row, dict)]
    return {
        "display_rows": rows[:limit],
        "display_row_count": min(len(rows), limit),
        "display_rows_has_more": len(rows) > limit,
    }


def infer_evidence_shape(*, question: str, intent: str, result_mode: str = "") -> str:
    mode = str(result_mode or "").strip().lower()
    if mode in {"detail", "details", "full", "rows", "display_rows", "完整", "明细", "名单"}:
        return "display"
    if mode in {"summary", "summarize", "aggregate", "overview", "摘要", "汇总"}:
        return "analysis"
    text = "".join(str(question or "").split())
    if any(token in text for token in ["导出", "全部", "完整", "全量"]):
        return "export"
    if any(token in text for token in ["最新", "最近", "最后一次", "明细", "名单", "有哪些", "看一下", "列出", "详情", "记录"]):
        return "display"
    if any(token in text for token in ["总体", "趋势", "分析", "主要", "为什么", "建议", "概况", "情况如何"]):
        return "analysis"
    clean_intent = str(intent or "").strip().lower()
    if clean_intent in {"list", "detail"}:
        return "display"
    return "analysis"


def business_project_rows(
    rows: list[dict[str, Any]],
    field_labels: dict[str, str] | None = None,
) -> tuple[list[dict[str, Any]], dict[str, str]]:
    label_values = {str(value) for value in (field_labels or {}).values()}
    projected_rows: list[dict[str, Any]] = []
    kept_labels: set[str] = set()
    for row in rows:
        if not isinstance(row, dict):
            continue
        projected_row: dict[str, Any] = {}
        for key, value in row.items():
            label = str(key)
            if _is_technical_field(label, label_values):
                continue
            projected_row[label] = value
            kept_labels.add(label)
        projected_rows.append(projected_row)
    projected_field_labels = {
        str(key): str(label)
        for key, label in (field_labels or {}).items()
        if str(label) in kept_labels and not _is_technical_field(str(key), label_values) and not _is_technical_field(str(label), label_values)
    }
    return projected_rows, projected_field_labels


def _result_shape(intent: str, length: int) -> str:
    if length == 0:
        return "empty"
    if intent in {"count", "sum"} or length == 1:
        return "single_summary"
    if intent == "trend":
        return "time_series"
    if intent in {"group_count", "group_sum", "rank"}:
        return "breakdown"
    return "sampled_rows"


def _numeric_columns(rows: list[dict[str, Any]]) -> list[str]:
    if not rows:
        return []
    columns: list[str] = []
    keys = list(rows[0].keys())
    for key in keys:
        values = [row.get(key) for row in rows if row.get(key) is not None]
        if values and all(_is_number(value) for value in values):
            columns.append(str(key))
    return columns


def _dimension_columns(rows: list[dict[str, Any]], numeric_columns: list[str]) -> list[str]:
    numeric = set(numeric_columns)
    columns: list[str] = []
    for row in rows[:1]:
        for key in row:
            if key not in numeric:
                columns.append(str(key))
    return columns


def _notable_findings(
    *,
    intent: str,
    row_count: int,
    rows: list[dict[str, Any]],
    numeric_columns: list[str],
    dimension_columns: list[str],
) -> list[str]:
    if row_count <= 0 or not rows:
        return ["未查询到符合条件的记录。"]
    findings = [f"本次查询返回 {row_count} 条结果。"]
    if intent in {"group_count", "group_sum", "rank", "trend"} and numeric_columns:
        metric = _preferred_metric(numeric_columns)
        top = rows[0]
        label = _row_label(top, dimension_columns)
        value = top.get(metric)
        if label and value is not None:
            findings.append(f"最高项是 {label}，{metric} 为 {value}。")
    if intent in {"count", "sum"} and len(rows) == 1:
        findings.append("该结果是单项汇总，可直接作为回答依据。")
    return findings


def _preferred_metric(columns: list[str]) -> str:
    for token in ("合计", "总", "积分", "分值", "record_count", "记录数"):
        for column in columns:
            if token in column:
                return column
    return columns[0]


def _row_label(row: dict[str, Any], dimension_columns: list[str]) -> str:
    for key in dimension_columns:
        value = row.get(key)
        if value not in {None, ""}:
            return str(value)
    return ""


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float, Decimal)) and not isinstance(value, bool)


def _json_safe_row(row: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in row.items():
        if isinstance(value, Decimal):
            out[str(key)] = float(value)
        else:
            out[str(key)] = value
    return out


def _drop_empty(value: dict[str, Any]) -> dict[str, Any]:
    return {key: item for key, item in value.items() if item not in ({}, [], None, "")}


def _env_int(name: str, default: int, *, min_value: int, max_value: int, legacy: str = "") -> int:
    try:
        value = int(_env_value(name, legacy, str(default)) or str(default))
    except Exception:
        value = default
    return max(min_value, min(max_value, value))


def _display_row_budget() -> int:
    return _env_int("SCHOOL_REACT_DISPLAY_ROW_LIMIT", 100, min_value=1, max_value=500, legacy="TENANT_REACT_DISPLAY_ROW_LIMIT")


def _is_technical_field(label: str, all_labels: set[str]) -> bool:
    del all_labels
    clean = str(label or "").strip()
    normalized = "".join(clean.lower().replace("-", "_").split())
    technical_tokens = {
        "id",
        "uuid",
        "tenant_id",
        "source_instance_id",
        "instance_id",
        "form_id",
        "process_instance_id",
        "creator_id",
        "updater_id",
        "created_at",
        "updated_at",
        "sync_time",
        "deleted_flag",
        "raw_json",
        "原始json",
        "实例id",
        "表单id",
        "流程实例id",
        "租户id",
        "同步时间",
        "创建时间",
        "更新时间",
        "删除标记",
        "内部编码",
    }
    if normalized in technical_tokens:
        return True
    return any(
        token in normalized
        for token in [
            "sourceinstanceid",
            "processinstanceid",
            "instanceid",
            "formid",
            "rawjson",
            "tenantid",
            "sync",
            "deletedflag",
        ]
    )
