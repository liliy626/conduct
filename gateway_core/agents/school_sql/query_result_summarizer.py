from __future__ import annotations

import json
import uuid
from decimal import Decimal
from typing import Any

from gateway_core.agents.school_sql.lineage_route import decide_lineage_route
from gateway_core.agents.school_sql.sql_utils import is_technical_field_name
from gateway_core.infra.utils import env_value as _env_value


BAR_LINE_ROLE_PRESETS = {
    "党政核心层": "证据边界：只使用查询结果中的事实；不得补写未查到的管理判断。",
    "教学线": "证据边界：只使用查询结果中的教学事实；不得推断未查到的课表、代课或教学秩序。",
    "学生与德育线": "证据边界：只使用查询结果中的德育事实；不得把扣分直接归因为主观原因。",
    "人事线": "证据边界：只使用查询结果中的人事事实；不得推断未查到的在岗、调配或考核状态。",
    "后勤保障线": "证据边界：只使用查询结果中的后勤事实；不得推断未查到的维修处置状态。",
    "科研线": "证据边界：只使用查询结果中的科研事实；不得推断未查到的申报资格或政策条件。",
    "群团与监督": "证据边界：只使用查询结果中的监督事实；不得补写未查到的合规结论。",
    "通用智慧校园": "证据边界：只使用查询结果中的事实；不得补写未查到的数据或判断。",
}

DOMAIN_ROLE_PRESETS = BAR_LINE_ROLE_PRESETS


def summarize_query_result(
    *,
    intent: str,
    row_count: int,
    formatted_rows: list[dict[str, Any]],
    field_labels: dict[str, str],
    question: str = "",
    referenced_views: list[str] | None = None,
) -> dict[str, Any]:
    """Pass through cleaned business rows as the primary answer evidence."""
    clean_intent = str(intent or "").strip() or "list"
    all_raw_rows = [_json_safe_row(row) for row in formatted_rows if isinstance(row, dict)]
    total_len = len(all_raw_rows)
    result_id = f"res_idx_{uuid.uuid4().hex[:16]}"

    clean_rows, clean_field_labels = business_project_rows(all_raw_rows, field_labels)
    _cache_lossless_rows_to_vault(result_id, clean_rows)
    numeric_columns = _numeric_columns(clean_rows)
    dimension_columns = _dimension_columns(clean_rows, numeric_columns)
    domain_key = _domain_key(
        question=question,
        referenced_views=referenced_views or [],
        field_labels=clean_field_labels,
        rows=clean_rows,
    )
    one_row_summary = clean_rows[0] if total_len == 1 and clean_rows else {}
    summary = {
        "row_count": int(row_count or total_len),
        "intent": clean_intent,
        "result_shape": _result_shape(clean_intent, total_len),
        "field_labels": clean_field_labels,
        "dimensions": dimension_columns,
        "metrics": numeric_columns,
        "domain_key": domain_key,
        "domain_role_preset": DOMAIN_ROLE_PRESETS[domain_key],
        "truth_data_markdown": _make_truth_markdown(clean_rows, default_rows=50, default_cols=15),
        "one_row_summary": one_row_summary,
        "top_items": clean_rows,
        "row_sample": clean_rows,
        "full_result_ref": {
            "result_id": result_id,
            "is_lossless": True,
            "storage_vault": "active_session_clues",
        },
        "notable_findings": _truth_data_findings(total_len),
    }
    return _drop_empty(summary)


def rows_for_board(summary: dict[str, Any]) -> list[dict[str, Any]]:
    """Rows used only for clue extraction inside EvidenceBoard."""
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for key in ("top_items", "row_sample"):
        value = summary.get(key)
        if isinstance(value, list):
            for item in value:
                if not isinstance(item, dict):
                    continue
                fingerprint = json.dumps(item, ensure_ascii=False, sort_keys=True, default=str)
                if fingerprint in seen:
                    continue
                seen.add(fingerprint)
                rows.append(item)
    one_row = summary.get("one_row_summary")
    if isinstance(one_row, dict) and one_row:
        fingerprint = json.dumps(one_row, ensure_ascii=False, sort_keys=True, default=str)
        if fingerprint not in seen:
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
    rows, _field_labels = business_project_rows(rows, {})
    return {
        "display_rows": rows[:limit],
        "display_row_count": min(len(rows), limit),
        "display_rows_has_more": len(rows) > limit,
    }


def load_lossless_result_rows(result_id: str) -> list[dict[str, Any]]:
    """Load full rows saved behind ``summary.full_result_ref.result_id``."""
    path = _lossless_result_path(result_id)
    payload = json.loads(path.read_text(encoding="utf-8"))
    rows = payload.get("rows", [])
    return [row for row in rows if isinstance(row, dict)]


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
    kept_fields: set[str] = set()
    for row in rows:
        if not isinstance(row, dict):
            continue
        projected_row: dict[str, Any] = {}
        for key, value in row.items():
            label = str(key)
            if _is_technical_field(label, label_values):
                continue
            projected_row[label] = value
            kept_fields.add(label)
        projected_rows.append(projected_row)
    projected_field_labels = {
        str(key): str(label)
        for key, label in (field_labels or {}).items()
        if str(key) in kept_fields and not _is_technical_field(str(key), label_values) and not _is_technical_field(str(label), label_values)
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


def _preferred_metric(columns: list[str]) -> str:
    for token in ("合计", "总", "积分", "分值", "record_count", "记录数"):
        for column in columns:
            if token in column:
                return column
    return columns[0]


def _cache_lossless_rows_to_vault(result_id: str, rows: list[dict[str, Any]]) -> None:
    path = _lossless_result_path(result_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(".tmp")
    tmp_path.write_text(json.dumps({"rows": rows}, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    tmp_path.replace(path)


def _lossless_result_path(result_id: str):
    from gateway_core.tools.artifact_store import artifact_root

    safe_id = "".join(ch for ch in str(result_id or "") if ch.isalnum() or ch in {"_", "-"})
    if not safe_id.startswith("res_idx_"):
        raise ValueError("invalid lossless result id")
    return artifact_root() / "active_session_clues" / f"{safe_id}.json"


def _sort_rows_by_metric(rows: list[dict[str, Any]], metric_col: str | None) -> list[dict[str, Any]]:
    if not metric_col:
        return rows
    try:
        return sorted(
            rows,
            key=lambda row: float(row.get(metric_col) or 0) if _is_number(row.get(metric_col)) else 0,
            reverse=True,
        )
    except Exception:
        return rows


def _tail_summary(tail_rows: list[dict[str, Any]], metric_col: str | None) -> dict[str, Any]:
    if not tail_rows:
        return {"row_count": 0, "affected_departments": []}
    departments = {
        str(dept).strip()
        for row in tail_rows
        for dept in (row.get("部门名称"), row.get("班级"), row.get("部门"), row.get("学科组"))
        if dept and str(dept).strip()
    }
    metrics: list[float] = []
    if metric_col:
        for row in tail_rows:
            value = row.get(metric_col)
            if value is not None:
                try:
                    metrics.append(float(value))
                except (TypeError, ValueError):
                    pass
    total_metric = float(sum(metrics)) if metrics else 0.0
    return {
        "row_count": len(tail_rows),
        "affected_departments": sorted(departments),
        "total_metric_value": total_metric,
        "avg_metric_value": float(total_metric / len(metrics)) if metrics else 0.0,
        "metric_name": metric_col,
    }


def _express_locker_notable_findings(
    *,
    row_count: int,
    result_id: str,
    tail_summary: dict[str, Any],
) -> list[str]:
    if row_count <= 0:
        return ["未查询到符合条件的记录。"]
    findings = [f"本次数据透视共命中 {row_count} 条全量物理流水。"]
    if tail_summary.get("row_count", 0) <= 0:
        return findings

    departments = tail_summary.get("affected_departments") or []
    department_text = "、".join(departments[:5]) or "未识别到明确部门/班级字段"
    if len(departments) > 5:
        department_text += f"等共{len(departments)}个条线"
    metric_text = ""
    if tail_summary.get("metric_name"):
        metric_text = f"，其余长尾项累计贡献{tail_summary['metric_name']}达 {tail_summary['total_metric_value']:.1f}"

    findings.append(
        f"大盘前8条重点明细已就位。其余 {tail_summary['row_count']} 条流水长尾已安全存入快递柜（存根：{result_id}）。"
        f"长尾数据无损穿透点名涉及：{department_text}{metric_text}。大盘基准线维持在 {tail_summary['avg_metric_value']:.2f}。"
        f"系统已锁定完整血缘，支持按该 result_id 执行跨年级/跨学科的二次动态切片调阅。"
    )
    return findings


def _truth_data_findings(row_count: int) -> list[str]:
    if row_count <= 0:
        return ["未查询到符合条件的记录。"]
    return [f"本次查询返回 {row_count} 条记录，已按业务字段清洗后直通展示。"]


def _domain_key(
    *,
    question: str,
    referenced_views: list[str],
    field_labels: dict[str, str],
    rows: list[dict[str, Any]],
) -> str:
    decision = decide_lineage_route(
        question=question,
        table_names=referenced_views,
        columns=[str(key or "") for row in rows[:1] for key in row],
        field_labels=field_labels,
        rows=rows,
    )
    return decision.domain_key


def _make_truth_markdown(
    rows: list[dict[str, Any]],
    *,
    default_rows: int = 50,
    default_cols: int = 15,
    max_rows: int = 50,
    max_cols: int = 30,
) -> str:
    if not rows:
        return ""
    row_limit = _env_int("SCHOOL_REACT_TRUTH_TABLE_ROW_LIMIT", default_rows, min_value=1, max_value=max_rows)
    col_limit = _env_int("SCHOOL_REACT_TRUTH_TABLE_COL_LIMIT", default_cols, min_value=1, max_value=max_cols)
    columns = list(rows[0].keys())[:col_limit]
    if not columns:
        return ""
    lines = [
        "【真实数据快照】",
        "| " + " | ".join(_markdown_cell(column) for column in columns) + " |",
        "| " + " | ".join("---" for _ in columns) + " |",
    ]
    lines.extend(
        "| " + " | ".join(_markdown_cell(row.get(column)) for column in columns) + " |"
        for row in rows[:row_limit]
    )
    if len(rows) > row_limit:
        lines.append(f"（仅展示前 {row_limit} 行，实际返回 {len(rows)} 行。）")
    return "\n".join(lines)


def _markdown_cell(value: Any) -> str:
    return str(value if value is not None else "").replace("|", "\\|").replace("\n", " ").strip()


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
    return is_technical_field_name(label)
