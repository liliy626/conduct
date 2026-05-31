from __future__ import annotations

import math
from typing import Any


def analyze_trend(
    rows: list[dict[str, Any]],
    *,
    time_field: str = "",
    metric_field: str = "",
) -> dict[str, Any]:
    clean_time_field = str(time_field or "").strip() or pick_field(
        rows,
        [
            "month",
            "week",
            "date",
            "day",
            "term",
            "school_term",
            "月份",
            "周",
            "日期",
            "时间",
            "学期",
        ],
    )
    clean_metric_field = str(metric_field or "").strip() or pick_numeric_field(rows)
    if not rows or not clean_time_field or not clean_metric_field:
        return {
            "ok": False,
            "error": "not_enough_time_series_data",
            "row_count": len(rows),
            "time_field": clean_time_field,
            "metric_field": clean_metric_field,
        }

    points = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        value = number(row.get(clean_metric_field))
        if value is None:
            continue
        points.append({"period": str(row.get(clean_time_field) or ""), "value": value, "row": row})
    points.sort(key=lambda item: item["period"])
    direction = trend_direction([item["value"] for item in points])
    changes = point_changes(points)
    return {
        "ok": True,
        "source": "analysis_tool",
        "analysis_type": "trend",
        "time_field": clean_time_field,
        "metric_field": clean_metric_field,
        "point_count": len(points),
        "direction": direction,
        "first": points[0] if points else {},
        "last": points[-1] if points else {},
        "max_point": max(points, key=lambda item: item["value"]) if points else {},
        "min_point": min(points, key=lambda item: item["value"]) if points else {},
        "changes": changes,
        "summary": trend_summary(direction, points, changes),
    }


def detect_anomalies(
    rows: list[dict[str, Any]],
    *,
    metric_field: str = "",
    label_field: str = "",
) -> dict[str, Any]:
    clean_metric_field = str(metric_field or "").strip() or pick_numeric_field(rows)
    clean_label_field = str(label_field or "").strip() or pick_label_field(rows, exclude={clean_metric_field})
    values = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        value = number(row.get(clean_metric_field))
        if value is None:
            continue
        values.append({"label": str(row.get(clean_label_field) or ""), "value": value, "row": row})
    if len(values) < 3:
        return {
            "ok": False,
            "error": "not_enough_numeric_rows",
            "row_count": len(values),
            "metric_field": clean_metric_field,
            "label_field": clean_label_field,
        }
    nums = [item["value"] for item in values]
    avg = sum(nums) / len(nums)
    variance = sum((item - avg) ** 2 for item in nums) / len(nums)
    std = math.sqrt(variance)
    sorted_nums = sorted(nums)
    med = sorted_nums[len(sorted_nums) // 2]
    anomalies = []
    for item in values:
        z_score = (item["value"] - avg) / std if std > 0 else 0.0
        ratio_to_median = item["value"] / med if med else 0.0
        if abs(z_score) >= 1.5 or ratio_to_median >= 2.0 or ratio_to_median <= 0.35:
            anomalies.append(
                {
                    **item,
                    "z_score": round(z_score, 3),
                    "ratio_to_median": round(ratio_to_median, 3) if med else None,
                    "direction": "high" if item["value"] >= avg else "low",
                }
            )
    anomalies.sort(key=lambda item: abs(float(item.get("z_score") or 0)), reverse=True)
    return {
        "ok": True,
        "source": "analysis_tool",
        "analysis_type": "anomaly",
        "metric_field": clean_metric_field,
        "label_field": clean_label_field,
        "row_count": len(values),
        "average": round(avg, 4),
        "median": med,
        "stddev": round(std, 4),
        "anomalies": anomalies[:10],
        "summary": anomaly_summary(anomalies, metric_field=clean_metric_field),
    }


def compare_cohort(
    rows: list[dict[str, Any]],
    *,
    target_name: str = "",
    name_field: str = "",
    metric_field: str = "",
) -> dict[str, Any]:
    clean_target_name = str(target_name or "").strip()
    clean_metric_field = str(metric_field or "").strip() or pick_numeric_field(rows)
    clean_name_field = str(name_field or "").strip() or pick_label_field(rows, exclude={clean_metric_field})
    scored = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        value = number(row.get(clean_metric_field))
        if value is None:
            continue
        scored.append({"name": str(row.get(clean_name_field) or ""), "value": value, "row": row})
    scored.sort(key=lambda item: item["value"], reverse=True)
    target = None
    if clean_target_name:
        for index, item in enumerate(scored, start=1):
            if clean_target_name in item["name"] or item["name"] in clean_target_name:
                target = {**item, "rank": index}
                break
    if target:
        total = len(scored)
        percentile = round((total - target["rank"] + 1) / total, 4) if total else 0.0
        top_10_percent = target["rank"] <= max(1, math.ceil(total * 0.1)) if total else False
    else:
        percentile = None
        top_10_percent = False
    return {
        "ok": bool(scored),
        "source": "analysis_tool",
        "analysis_type": "cohort_compare",
        "target_name": clean_target_name,
        "name_field": clean_name_field,
        "metric_field": clean_metric_field,
        "cohort_size": len(scored),
        "target": target,
        "percentile": percentile,
        "top_10_percent": top_10_percent,
        "top_items": scored[:10],
        "summary": cohort_summary(
            target=target,
            target_name=clean_target_name,
            total=len(scored),
            percentile=percentile,
            top_10_percent=top_10_percent,
        ),
    }


def pick_field(rows: list[dict[str, Any]], candidates: list[str]) -> str:
    if not rows:
        return ""
    keys = list(rows[0].keys())
    for candidate in candidates:
        clean = str(candidate or "").lower()
        for key in keys:
            if clean and clean in str(key).lower():
                return str(key)
    return ""


def pick_numeric_field(rows: list[dict[str, Any]]) -> str:
    if not rows:
        return ""
    priority = [
        "sum",
        "count",
        "total",
        "score",
        "points",
        "amount",
        "value",
        "record_count",
        "数量",
        "总数",
        "合计",
        "积分",
        "扣分",
        "分值",
        "次数",
        "人次",
    ]
    keys = list(rows[0].keys())
    numeric_keys = []
    for key in keys:
        if any(number(row.get(key)) is not None for row in rows[:20] if isinstance(row, dict)):
            numeric_keys.append(str(key))
    if not numeric_keys:
        return ""
    for token in priority:
        for key in numeric_keys:
            if str(token).lower() in key.lower():
                return key
    return numeric_keys[0]


def pick_label_field(rows: list[dict[str, Any]], *, exclude: set[str] | None = None) -> str:
    if not rows:
        return ""
    excluded = {str(item or "") for item in (exclude or set())}
    priority = [
        "name",
        "teacher",
        "student",
        "class",
        "grade",
        "subject",
        "project",
        "reason",
        "label",
        "姓名",
        "教师",
        "学生",
        "班级",
        "年级",
        "学科",
        "项目",
        "原因",
        "类别",
        "名称",
    ]
    keys = [str(key) for key in rows[0].keys() if str(key) not in excluded]
    for token in priority:
        for key in keys:
            if str(token).lower() in key.lower():
                return key
    for key in keys:
        if any(number(row.get(key)) is None and str(row.get(key) or "").strip() for row in rows[:20]):
            return key
    return keys[0] if keys else ""


def number(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip().replace(",", "")
    if not text:
        return None
    if text.endswith("%"):
        text = text[:-1].strip()
    try:
        return float(text)
    except Exception:
        return None


def trend_direction(values: list[float]) -> str:
    if len(values) < 2:
        return "insufficient"
    diffs = [values[idx] - values[idx - 1] for idx in range(1, len(values))]
    if all(abs(item) < 1e-9 for item in diffs):
        return "stable"
    positive = sum(1 for item in diffs if item > 0)
    negative = sum(1 for item in diffs if item < 0)
    if positive and not negative:
        return "increasing"
    if negative and not positive:
        return "decreasing"
    if positive > negative:
        return "mostly_increasing"
    if negative > positive:
        return "mostly_decreasing"
    return "mixed"


def point_changes(points: list[dict[str, Any]]) -> list[dict[str, Any]]:
    changes: list[dict[str, Any]] = []
    for idx in range(1, len(points)):
        previous = float(points[idx - 1]["value"])
        current = float(points[idx]["value"])
        delta = current - previous
        changes.append(
            {
                "from": points[idx - 1]["period"],
                "to": points[idx]["period"],
                "delta": round(delta, 4),
                "change_rate": round(delta / previous, 4) if previous else None,
            }
        )
    return changes


def trend_summary(direction: str, points: list[dict[str, Any]], changes: list[dict[str, Any]]) -> str:
    if not points:
        return "没有足够的时间序列数据。"
    max_point = max(points, key=lambda item: item["value"])
    min_point = min(points, key=lambda item: item["value"])
    largest_change = max(changes, key=lambda item: abs(float(item.get("delta") or 0)), default={})
    direction_text = {
        "increasing": "整体持续上升",
        "decreasing": "整体持续下降",
        "mostly_increasing": "整体以上升为主，但中间有波动",
        "mostly_decreasing": "整体以下降为主，但中间有波动",
        "stable": "整体基本稳定",
        "mixed": "整体波动明显",
    }.get(direction, "数据点不足，趋势不明确")
    extra = ""
    if largest_change:
        extra = f"；最大变化出现在 {largest_change.get('from')} 到 {largest_change.get('to')}，变化 {largest_change.get('delta')}"
    return f"{direction_text}；最高点是 {max_point.get('period')}（{max_point.get('value')}），最低点是 {min_point.get('period')}（{min_point.get('value')}）{extra}。"


def anomaly_summary(anomalies: list[dict[str, Any]], *, metric_field: str) -> str:
    if not anomalies:
        return f"按 {metric_field} 看，暂未发现明显异常点。"
    top = anomalies[0]
    return (
        f"按 {metric_field} 看，发现 {len(anomalies)} 个可能异常点；"
        f"最明显的是 {top.get('label') or '未命名对象'}，数值 {top.get('value')}，方向为 {top.get('direction')}。"
    )


def cohort_summary(
    *,
    target: dict[str, Any] | None,
    target_name: str,
    total: int,
    percentile: float | None,
    top_10_percent: bool,
) -> str:
    if not total:
        return "没有可用于同类对比的数据。"
    if not target:
        return f"已整理 {total} 个同类对象，但未在结果中匹配到目标对象“{target_name}”。"
    percentile_text = f"{round(float(percentile or 0) * 100, 2)}%"
    top_text = "属于前 10%" if top_10_percent else "不属于前 10%"
    return (
        f"{target.get('name') or target_name} 在 {total} 个同类对象中排名第 {target.get('rank')}，"
        f"分位约 {percentile_text}，{top_text}。"
    )
