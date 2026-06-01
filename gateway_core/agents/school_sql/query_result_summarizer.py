from __future__ import annotations

import json
import os
import uuid
from decimal import Decimal
from typing import Any

from gateway_core.agents.school_sql.lineage_route import decide_lineage_route


BAR_LINE_ROLE_PRESETS = {
    "党政核心层": (
        "【当前语境：党支部/校长室/校办 - 宏观治理与学校大盘】\n"
        "提示词风格：说话像是一位经验丰富的校办主任。语气沉稳、大局观强、懂得把握势态。"
        "请看着下方的客观数据，围绕咱们学校的发展规划、日常党政会务、公章合规或者校园安全总责来聊。"
        "你得一眼看出数据背后折射出的整体风向，说话要懂行、透彻。第一句话把最核心的行政结论告诉你，"
        "接下来顺理成章地帮校长联想并点出哪些细节可能存在管理盲区，有哪些微调和优化抓手，既专业又有人情味。"
    ),
    "教学线": (
        "【当前语境：教务处/课程教学部/研究生院 - 教务常规与运行大盘】\n"
        "提示词风格：说话像是一位深谙教务门道、雷厉风行的老教导主任。语气严谨、务实、直切痛点。"
        "分析数据时，请聚焦于咱们的课表排程、教材学籍、教学常规和教研组的日常运转。"
        "看到数据后，结论先行，别摆大厂的格盘。你要凭经验帮大家揪出那些偏离常规的细节，"
        "从咱们日常教学、排课代课备勤的实际血缘出发，多往后联想半步，给出接地气、能解决实际教务秩序的调控好建议。"
    ),
    "学生与德育线": (
        "【当前语境：德育处/学工部/研工部/少先队 - 学生行为与校纪校规】\n"
        "提示词风格：说话像是一位威严与慈爱并存的德育处长。语气客观、切中要害，但绝非冷冰冰的机器。"
        "请围绕咱们的班主任工作、学生行为规范、行规红黑榜加减分分布、或者是家校安全法治来进行提炼。"
        "盯着扣分事件的离散度和加分频次。结合咱们学校最近的管理窗口期，自然地联想、诊断一下："
        "到底这是因为某些特定群体习惯性反弹，还是咱们近期行规宣导细节上有些脱节？"
        "字字要切中要害，给出充满教育智慧的精准约谈或行规加固建议。"
    ),
    "人事线": (
        "【当前语境：人事处/教师工作部 - 师资效能与调配大盘】\n"
        "提示词风格：说话像是一位精明、负责、懂人心的人事处长。语气专业、信息密度高、切合实际。"
        "结合客观数据，多去关注咱们的师资结构、近期请假频率或教师工作量的分布细节。"
        "帮你梳理出不同团队、不同时段里的人效偏离情况。允许你从跨年级代课备勤、教师职称评审、"
        "教师培训学分的实际细节出发，做一些合理的能效连带联想，给学校领导提供高保真、有实际参考价值的人事调配数据抓手。"
    ),
    "后勤保障线": (
        "【当前语境：总务处/后勤保障中心/国资处 - 资产运维与财务对账】\n"
        "提示词风格：说话像是一位勤勉、心细、账本门清的总务老处长。语气极度精准、踏实。"
        "请围绕咱们学校的资产采购、食堂餐饮流水、基建维修或者门禁消防巡查进行盘点。"
        "结论先行，一眼帮模型盯住关键的拐点、极值或预算死锁交叉点。说话不要用花哨的修辞，"
        "结合物业运维和应急处置的后勤细节，展开合理的损耗或隐患联想，像写汇报公文一样专业且接地气。"
    ),
    "科研线": (
        "【当前语境：教科室/教师发展部/科研处 - 学术成果与课题孵化】\n"
        "提示词风格：说话像是一位学术作风严谨、懂得引领教师发展的科研老校长。语气专业、克制。"
        "聚焦在老师们的课题申报、论文发表、科研经费分配或职称评定进度上。"
        "细节上点出目前成果产出的盲区或表现断层。多联想一下咱们学校当下的学术梯队建设、"
        "中青年骨干教师的成长势态，给出能够驱动科研并轨、实实落地的靶向建议。"
    ),
    "群团与监督": (
        "【当前语境：工会/纪委/监察处/学术委员会 - 权益保障与合规监督】\n"
        "提示词风格：说话像是一位中立客观、讲原则、同时关心职工福利的工会主席/纪委老书记。口吻公道、合规。"
        "围绕教代会决议、职工福利分配、退管女工、或纪检统战的合规度量来展开。"
        "细节上锚定监督事件、福利离散度或职称评审争议的核心势态，用白盒公文口吻踏实、直接地总结出来。"
    ),
    "通用智慧校园": (
        "【当前语境：智慧校园首席教务数据智囊】\n"
        "提示词风格：结论先行，口吻懂行、现代、带有人情味。别原样翻译表格，"
        "帮用户点出数据大盘里的异常极值和潜在发展趋势即可。"
    ),
}

DOMAIN_ROLE_PRESETS = BAR_LINE_ROLE_PRESETS


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
    question: str = "",
    referenced_views: list[str] | None = None,
) -> dict[str, Any]:
    """Compress query rows into a small, LLM-friendly evidence summary.

    The ReAct agent should reason over business evidence, not re-process a
    large row dump. Raw rows stay in trace/debug logs; this payload keeps only
    aggregates, top breakdown items, and a small representative sample.
    """
    clean_intent = str(intent or "").strip() or "list"
    all_raw_rows = [_json_safe_row(row) for row in formatted_rows if isinstance(row, dict)]
    total_len = len(all_raw_rows)
    result_id = f"res_idx_{uuid.uuid4().hex[:16]}"
    _cache_lossless_rows_to_vault(result_id, all_raw_rows)
    numeric_columns = _numeric_columns(all_raw_rows)
    dimension_columns = _dimension_columns(all_raw_rows, numeric_columns)
    metric_col = _preferred_metric(numeric_columns) if numeric_columns else None
    sorted_rows = _sort_rows_by_metric(all_raw_rows, metric_col)
    domain_key = _domain_key(question=question, referenced_views=referenced_views or [], field_labels=field_labels, rows=all_raw_rows)
    one_row_summary = sorted_rows[0] if total_len == 1 else {}
    top_items = sorted_rows[:8]
    tail_summary = _tail_summary(sorted_rows[8:], metric_col)
    row_sample = sorted_rows[:5] if clean_intent in {"list", "detail"} and total_len <= 8 else []
    summary = {
        "row_count": int(row_count or total_len),
        "intent": clean_intent,
        "result_shape": _result_shape(clean_intent, total_len),
        "field_labels": field_labels,
        "dimensions": dimension_columns,
        "metrics": numeric_columns,
        "domain_key": domain_key,
        "domain_role_preset": DOMAIN_ROLE_PRESETS[domain_key],
        "truth_data_markdown": _truth_data_markdown(all_raw_rows),
        "one_row_summary": one_row_summary,
        "top_items": top_items,
        "tail_summary": tail_summary,
        "row_sample": row_sample,
        "full_result_ref": {
            "result_id": result_id,
            "is_lossless": True,
            "storage_vault": "active_session_clues",
        },
        "notable_findings": _express_locker_notable_findings(
            row_count=total_len,
            result_id=result_id,
            tail_summary=tail_summary,
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


def _truth_data_markdown(rows: list[dict[str, Any]]) -> str:
    if not rows:
        return ""
    row_limit = _env_int("SCHOOL_REACT_TRUTH_TABLE_ROW_LIMIT", 12, min_value=1, max_value=30)
    col_limit = _env_int("SCHOOL_REACT_TRUTH_TABLE_COL_LIMIT", 8, min_value=1, max_value=20)
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
