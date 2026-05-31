from __future__ import annotations

import time
import json
from html import escape
from typing import Any, Dict, Iterable, List, Optional

from .artifact_store import artifact_download_url, safe_artifact_path
from .tool_core import AgentTool, AgentToolInput, AgentToolOutput, ToolExecutionContext, json_safe


SUPPORTED_CHART_TYPES = {"line", "bar", "stacked_bar", "pie", "table"}
LABEL_FIELD_HINTS = (
    "月份",
    "日期",
    "时间",
    "学期",
    "学年",
    "学科",
    "年级",
    "班级",
    "类别",
    "类型",
    "原因",
    "项目",
    "名称",
    "姓名",
    "month",
    "date",
    "time",
    "subject",
    "grade",
    "class",
    "category",
    "type",
    "reason",
    "name",
    "label",
)
VALUE_FIELD_HINTS = (
    "次数",
    "人次",
    "人数",
    "条数",
    "数量",
    "总数",
    "合计",
    "总量",
    "总天数",
    "小时",
    "积分",
    "扣分",
    "分数",
    "占比",
    "count",
    "total",
    "sum",
    "value",
    "hours",
    "days",
    "score",
    "rate",
)


class ChartTool(AgentTool):
    name = "chart"
    description = "Builds a JSON chart artifact from local evidence rows."

    def run(self, tool_input: AgentToolInput, context: ToolExecutionContext) -> AgentToolOutput:
        started = time.perf_counter()
        args = tool_input.arguments
        rows = _evidence_rows(tool_input)
        if not rows:
            return _output(
                started,
                ok=False,
                error="chart tool requires evidence rows; refusing to invent chart data",
            )

        chart_type = str(args.get("chart_type") or "table")
        if chart_type not in SUPPORTED_CHART_TYPES:
            return _output(
                started,
                ok=False,
                error=f"unsupported chart_type: {chart_type}",
                warnings=[f"supported chart types: {', '.join(sorted(SUPPORTED_CHART_TYPES))}"],
            )

        title = str(args.get("title") or "")
        x_field = str(args.get("x") or args.get("x_field") or "")
        y_field = str(args.get("y") or args.get("y_field") or _first_item(args.get("y_fields")) or "")
        series_field = str(args.get("series") or "")
        chart_json = _build_chart_json(
            rows=rows,
            chart_type=chart_type,
            title=title,
            x_field=x_field,
            y_field=y_field,
            series_field=series_field,
        )
        html_path = safe_artifact_path(tenant_id=context.tenant_id, tool_name="chart", suffix=".html")
        json_path = html_path.with_suffix(".json")
        svg_path = html_path.with_suffix(".svg")
        html_path.write_text(_chart_preview_html(chart_json), encoding="utf-8")
        json_path.write_text(json.dumps(json_safe(chart_json), ensure_ascii=False, indent=2), encoding="utf-8")
        if chart_json.get("chart_type") != "table":
            svg_path.write_text(_standalone_svg(chart_json), encoding="utf-8")
        preview_url = artifact_download_url(html_path)
        data_url = artifact_download_url(json_path)
        image_url = artifact_download_url(svg_path) if svg_path.is_file() else ""
        return _output(
            started,
            ok=True,
            evidence=[{"kind": "chart_evidence", "row_count": len(rows)}],
            artifacts=[
                {
                    "type": "chart",
                    "format": "html",
                    "title": title,
                    "source_task_id": str(args.get("source_task_id") or ""),
                    "path": str(html_path),
                    "preview_url": preview_url,
                    "image_url": image_url,
                    "svg_url": image_url,
                    "download_url": preview_url,
                    "data_url": data_url,
                    "content": chart_json,
                }
            ],
        )


def _evidence_rows(tool_input: AgentToolInput) -> List[Dict[str, Any]]:
    rows = (
        tool_input.arguments.get("evidence_rows")
        or tool_input.arguments.get("rows")
        or tool_input.evidence
    )
    if not isinstance(rows, list):
        return []
    return [row for row in rows if isinstance(row, dict)]


def _first_item(value: Any) -> Any:
    return value[0] if isinstance(value, list) and value else ""


def _build_chart_json(
    *,
    rows: List[Dict[str, Any]],
    chart_type: str,
    title: str,
    x_field: str,
    y_field: str,
    series_field: str,
) -> Dict[str, Any]:
    if chart_type == "table":
        columns = _ordered_columns(rows)
        return {
            "chart_type": "table",
            "title": title,
            "columns": columns,
            "rows": json_safe(rows),
        }

    resolved_x = _valid_field(rows, x_field) or _infer_label_field(rows)
    resolved_y = _valid_field(rows, y_field) or _infer_value_field(
        rows,
        exclude={field for field in (resolved_x, series_field) if field},
    )
    labels = [_label(row, resolved_x, index) for index, row in enumerate(rows)]
    chart_title = title or _default_title(chart_type, resolved_x, resolved_y)
    if chart_type == "pie":
        return {
            "chart_type": "pie",
            "title": chart_title,
            "resolved_fields": {"x": resolved_x, "y": resolved_y, "series": series_field},
            "data": {
                "labels": labels,
                "values": [_value(row, resolved_y) for row in rows],
            },
        }

    return {
        "chart_type": chart_type,
        "title": chart_title,
        "resolved_fields": {"x": resolved_x, "y": resolved_y, "series": series_field},
        "data": {
            "labels": labels,
            "datasets": _datasets(rows, resolved_y, series_field),
        },
    }


def _ordered_columns(rows: Iterable[Dict[str, Any]]) -> List[str]:
    columns: List[str] = []
    for row in rows:
        for key in row:
            if key not in columns:
                columns.append(key)
    return columns


def _label(row: Dict[str, Any], x_field: str, index: int) -> Any:
    if x_field and x_field in row:
        return json_safe(row[x_field])
    return json_safe(row.get("label", index + 1))


def _value(row: Dict[str, Any], y_field: str) -> Any:
    if y_field and y_field in row:
        return json_safe(_number(row[y_field]))
    for value in row.values():
        number = _number_or_none(value)
        if number is not None:
            return number
    return 0


def _datasets(rows: List[Dict[str, Any]], y_field: str, series_field: str) -> List[Dict[str, Any]]:
    if not series_field:
        return [{"name": _display_field_name(y_field) or "数值", "field": y_field, "values": [_value(row, y_field) for row in rows]}]

    grouped: Dict[str, List[Any]] = {}
    for row in rows:
        name = str(row.get(series_field, "value"))
        grouped.setdefault(name, []).append(_value(row, y_field))
    return [{"name": name, "values": values} for name, values in grouped.items()]


def _valid_field(rows: List[Dict[str, Any]], field: str) -> str:
    if not field:
        return ""
    return field if any(field in row for row in rows) else ""


def _infer_label_field(rows: List[Dict[str, Any]]) -> str:
    fields = _ordered_columns(rows)
    if not fields:
        return ""
    non_numeric = [field for field in fields if any(_number_or_none(row.get(field)) is None for row in rows if field in row)]
    candidates = non_numeric or fields
    return max(candidates, key=lambda field: _field_hint_score(field, LABEL_FIELD_HINTS))


def _infer_value_field(rows: List[Dict[str, Any]], *, exclude: set[str]) -> str:
    fields = [field for field in _ordered_columns(rows) if field not in exclude]
    numeric_fields = []
    for field in fields:
        values = [_number_or_none(row.get(field)) for row in rows if field in row]
        numeric = [value for value in values if value is not None]
        if numeric:
            numeric_fields.append((field, len(numeric), sum(abs(value) for value in numeric)))
    if not numeric_fields:
        return ""
    return max(
        numeric_fields,
        key=lambda item: (_field_hint_score(item[0], VALUE_FIELD_HINTS), item[1], item[2]),
    )[0]


def _field_hint_score(field: str, hints: tuple[str, ...]) -> int:
    lowered = field.lower()
    score = 0
    for index, hint in enumerate(hints):
        hint_lower = hint.lower()
        if hint_lower == lowered:
            score += 1000 - index
        elif hint_lower in lowered:
            score += 500 - index
    return score


def _display_field_name(field: str) -> str:
    if not field:
        return ""
    return field.split(".")[-1].replace("_", " ")


def _default_title(chart_type: str, x_field: str, y_field: str) -> str:
    if chart_type == "line":
        return f"{_display_field_name(y_field) or '数据'}趋势"
    if chart_type == "pie":
        return f"{_display_field_name(y_field) or '数据'}构成"
    if x_field and y_field:
        return f"{_display_field_name(x_field)}按{_display_field_name(y_field)}分布"
    return "数据图表"


def _chart_preview_html(chart: Dict[str, Any]) -> str:
    title = escape(str(chart.get("title") or "数据图表"))
    chart_type = str(chart.get("chart_type") or "table")
    body = _table_html(chart) if chart_type == "table" else _svg_chart_html(chart)
    summary = "" if chart_type == "table" else _summary_html(chart)
    data_json = escape(json.dumps(json_safe(chart), ensure_ascii=False), quote=False)
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>{title}</title>
  <style>
    :root {{ --ink:#0f172a; --muted:#64748b; --line:#dbe4f0; --blue:#1464a5; --teal:#12a594; --cyan:#54b7d3; --amber:#f59e0b; }}
    * {{ box-sizing:border-box; }}
    body {{ margin:0; padding:34px; font-family:-apple-system,BlinkMacSystemFont,"Segoe UI","Microsoft YaHei",sans-serif; background:linear-gradient(135deg,#f8fbff 0%,#eef6fb 100%); color:var(--ink); }}
    .wrap {{ max-width:1180px; margin:0 auto; background:#fff; border:1px solid var(--line); box-shadow:0 18px 50px rgba(15,23,42,.10); padding:34px 38px 30px; }}
    .eyebrow {{ color:var(--blue); font-weight:700; letter-spacing:.08em; font-size:13px; margin-bottom:8px; }}
    h1 {{ margin:0 0 8px; font-size:38px; line-height:1.16; color:#083b66; }}
    .sub {{ margin:0 0 26px; color:var(--muted); font-size:15px; }}
    .summary {{ display:grid; grid-template-columns:repeat(3,minmax(0,1fr)); gap:14px; margin:22px 0 26px; }}
    .metric {{ background:#f7fbff; border:1px solid #d9e8f5; padding:16px 18px; min-height:92px; }}
    .metric .k {{ color:var(--muted); font-size:13px; margin-bottom:8px; }}
    .metric .v {{ color:#0b4f82; font-size:26px; font-weight:800; line-height:1.2; }}
    .metric .d {{ color:var(--muted); font-size:13px; margin-top:6px; }}
    .chart-card {{ border:1px solid var(--line); padding:18px 20px 12px; background:#ffffff; }}
    table {{ width:100%; border-collapse:collapse; font-size:15px; }}
    th,td {{ border-bottom:1px solid #e5e7eb; padding:12px 10px; text-align:left; }}
    th {{ background:#f8fafc; color:#334155; }}
    svg {{ width:100%; height:auto; display:block; }}
    .axis {{ stroke:#94a3b8; stroke-width:1; }}
    .grid {{ stroke:#e5edf6; stroke-width:1; }}
    .bar {{ fill:url(#barGradient); }}
    .bar-alt {{ fill:#e64b4b; }}
    .line {{ fill:none; stroke:#1464a5; stroke-width:4; }}
    .area {{ fill:#1464a5; opacity:.08; }}
    .dot {{ fill:#1464a5; stroke:#fff; stroke-width:3; }}
    .label {{ fill:#334155; font-size:14px; }}
    .value {{ fill:#0f172a; font-size:14px; font-weight:700; }}
    .small {{ fill:#64748b; font-size:12px; }}
    .foot {{ margin-top:18px; padding-top:14px; border-top:1px solid #e5e7eb; color:var(--muted); font-size:13px; }}
    details {{ margin-top:18px; color:#64748b; }}
    pre {{ white-space:pre-wrap; background:#f8fafc; padding:14px; border:1px solid #e5e7eb; overflow:auto; }}
    @media (max-width:760px) {{ body {{ padding:14px; }} .wrap {{ padding:22px; }} h1 {{ font-size:28px; }} .summary {{ grid-template-columns:1fr; }} }}
  </style>
</head>
<body>
  <div class="wrap">
    <div class="eyebrow">DATA VISUALIZATION</div>
    <h1>{title}</h1>
    <p class="sub">基于本轮已查询到的数据自动生成，用于快速查看分布、趋势和异常点。</p>
    {summary}
    <div class="chart-card">{body}</div>
    <div class="foot">字段由系统根据数据语义自动识别；如需更换维度，可继续要求“按月份 / 按班级 / 按学科重新画”。</div>
    <details><summary>查看图表 JSON</summary><pre>{data_json}</pre></details>
  </div>
</body>
</html>"""


def _standalone_svg(chart: Dict[str, Any]) -> str:
    svg = _svg_chart_html(chart)
    if not svg.strip().startswith("<svg"):
        title = escape(str(chart.get("title") or "数据图表"))
        svg = f'<svg viewBox="0 0 1060 430" xmlns="http://www.w3.org/2000/svg"><text x="40" y="70" font-size="28" font-family="Microsoft YaHei">{title}</text></svg>'
    style = """<style>
      .axis{stroke:#94a3b8;stroke-width:1}.grid{stroke:#e5edf6;stroke-width:1}
      .bar{fill:url(#barGradient)}.bar-alt{fill:#e64b4b}
      .line{fill:none;stroke:#1464a5;stroke-width:4}.area{fill:#1464a5;opacity:.08}
      .dot{fill:#1464a5;stroke:#fff;stroke-width:3}.label{fill:#334155;font-size:14px;font-family:Microsoft YaHei,Arial,sans-serif}
      .value{fill:#0f172a;font-size:14px;font-weight:700;font-family:Microsoft YaHei,Arial,sans-serif}
      .small{fill:#64748b;font-size:12px;font-family:Microsoft YaHei,Arial,sans-serif}
    </style>"""
    if "xmlns=" not in svg[:120]:
        svg = svg.replace("<svg ", '<svg xmlns="http://www.w3.org/2000/svg" ', 1)
    return svg.replace(">", f">{style}", 1)


def _table_html(chart: Dict[str, Any]) -> str:
    columns = chart.get("columns") if isinstance(chart.get("columns"), list) else []
    rows = chart.get("rows") if isinstance(chart.get("rows"), list) else []
    header = "".join(f"<th>{escape(str(col))}</th>" for col in columns)
    body_rows = []
    for row in rows[:200]:
        if not isinstance(row, dict):
            continue
        body_rows.append("<tr>" + "".join(f"<td>{escape(str(row.get(col, '')))}</td>" for col in columns) + "</tr>")
    return f"<table><thead><tr>{header}</tr></thead><tbody>{''.join(body_rows)}</tbody></table>"


def _svg_chart_html(chart: Dict[str, Any]) -> str:
    data = chart.get("data") if isinstance(chart.get("data"), dict) else {}
    labels = data.get("labels") if isinstance(data.get("labels"), list) else []
    datasets = data.get("datasets") if isinstance(data.get("datasets"), list) else []
    values = []
    if datasets and isinstance(datasets[0], dict) and isinstance(datasets[0].get("values"), list):
        values = datasets[0]["values"]
    if chart.get("chart_type") == "pie":
        values = data.get("values") if isinstance(data.get("values"), list) else []
    points = [(str(label), _number(value)) for label, value in zip(labels[:24], values[:24])]
    if not points:
        return "<p>没有可绘制的数据。</p>"
    if chart.get("chart_type") == "line":
        return _line_svg(points)
    if chart.get("chart_type") == "pie":
        return _donut_svg(points)
    return _bar_svg(points)


def _bar_svg(points: List[tuple[str, float]]) -> str:
    width = 1060
    row_h = 52
    height = max(430, 120 + row_h * len(points))
    left, right, top, bottom = 210, 90, 38, 50
    plot_w = width - left - right
    plot_h = height - top - bottom
    max_v = max([value for _, value in points] + [1.0])
    rows = []
    ticks = []
    for i in range(5):
        x = left + plot_w * i / 4
        ticks.append(f'<line class="grid" x1="{x:.1f}" y1="{top}" x2="{x:.1f}" y2="{height-bottom}"/>')
        ticks.append(f'<text class="small" x="{x:.1f}" y="{height-bottom+24}" text-anchor="middle">{max_v * i / 4:g}</text>')
    bars = []
    max_index = max(range(len(points)), key=lambda idx: points[idx][1]) if points else -1
    for i, (label, value) in enumerate(points):
        y = top + 18 + i * row_h
        bar_w = 0 if max_v <= 0 else value / max_v * plot_w
        fill_class = "bar-alt" if i == max_index and len(points) > 1 else "bar"
        rows.append(f'<text class="label" x="{left-18}" y="{y+21}" text-anchor="end">{escape(_truncate_label(label, 16))}</text>')
        bars.append(f'<rect class="{fill_class}" x="{left}" y="{y}" width="{bar_w:.1f}" height="26" rx="8"/>')
        bars.append(f'<text class="value" x="{min(width-right-4, left+bar_w+12):.1f}" y="{y+19}">{value:g}</text>')
    return f'''<svg viewBox="0 0 {width} {height}" role="img">
      <defs><linearGradient id="barGradient" x1="0" x2="1"><stop offset="0%" stop-color="#1464a5"/><stop offset="100%" stop-color="#33b7c8"/></linearGradient></defs>
      {"".join(ticks)}
      <line class="axis" x1="{left}" y1="{height-bottom}" x2="{width-right}" y2="{height-bottom}"/>
      {"".join(rows)}{"".join(bars)}
    </svg>'''


def _line_svg(points: List[tuple[str, float]]) -> str:
    width, height = 980, 520
    left, right, top, bottom = 90, 40, 30, 90
    plot_w = width - left - right
    plot_h = height - top - bottom
    max_v = max([value for _, value in points] + [1.0])
    min_v = min([value for _, value in points] + [0.0])
    span = max(max_v - min_v, 1.0)
    coords = []
    for i, (_, value) in enumerate(points):
        x = left + (plot_w * i / max(1, len(points) - 1))
        y = top + plot_h - ((value - min_v) / span * plot_h)
        coords.append((x, y, value))
    polyline = " ".join(f"{x:.1f},{y:.1f}" for x, y, _ in coords)
    area = f"{left},{top + plot_h} {polyline} {width - right},{top + plot_h}"
    dots = []
    for (x, y, value), (label, _) in zip(coords, points):
        dots.append(f'<circle class="dot" cx="{x:.1f}" cy="{y:.1f}" r="5"/><text class="value" x="{x:.1f}" y="{y - 10:.1f}" text-anchor="middle">{value:g}</text><text class="label" x="{x:.1f}" y="{height - 42}" text-anchor="middle" transform="rotate(-25 {x:.1f},{height - 42})">{escape(label[:12])}</text>')
    grids = "".join(
        f'<line class="grid" x1="{left}" y1="{top + plot_h * i / 4:.1f}" x2="{width-right}" y2="{top + plot_h * i / 4:.1f}"/>'
        for i in range(5)
    )
    return f'<svg viewBox="0 0 {width} {height}" role="img">{grids}<line class="axis" x1="{left}" y1="{top + plot_h}" x2="{width - right}" y2="{top + plot_h}"/><line class="axis" x1="{left}" y1="{top}" x2="{left}" y2="{top + plot_h}"/><polygon class="area" points="{area}"/><polyline class="line" points="{polyline}"/>{"".join(dots)}</svg>'


def _donut_svg(points: List[tuple[str, float]]) -> str:
    width, height = 980, 520
    total = sum(max(value, 0) for _, value in points) or 1
    colors = ["#1464a5", "#12a594", "#33b7c8", "#f59e0b", "#e64b4b", "#7c3aed"]
    legend = []
    y = 80
    for index, (label, value) in enumerate(points[:12]):
        pct = value / total * 100
        color = colors[index % len(colors)]
        legend.append(f'<rect x="600" y="{y-14}" width="16" height="16" rx="4" fill="{color}"/><text class="label" x="628" y="{y}">{escape(_truncate_label(label, 18))}：{value:g} ({pct:.1f}%)</text>')
        y += 32
    return f'<svg viewBox="0 0 {width} {height}" role="img"><circle cx="280" cy="250" r="155" fill="#1464a5" opacity=".18"/><circle cx="280" cy="250" r="108" fill="#fff"/><text class="value" x="280" y="244" text-anchor="middle">{total:g}</text><text class="small" x="280" y="274" text-anchor="middle">总量</text>{"".join(legend)}</svg>'


def _summary_html(chart: Dict[str, Any]) -> str:
    points = _chart_points(chart)
    if not points:
        return ""
    values = [value for _, value in points]
    total = sum(values)
    max_label, max_value = max(points, key=lambda item: item[1])
    avg = total / len(values)
    return f'''<div class="summary">
      <div class="metric"><div class="k">数据总量</div><div class="v">{total:g}</div><div class="d">共 {len(points)} 个维度</div></div>
      <div class="metric"><div class="k">最高项</div><div class="v">{escape(_truncate_label(max_label, 12))}</div><div class="d">{max_value:g}</div></div>
      <div class="metric"><div class="k">平均值</div><div class="v">{avg:.1f}</div><div class="d">用于判断集中度</div></div>
    </div>'''


def _chart_points(chart: Dict[str, Any]) -> List[tuple[str, float]]:
    data = chart.get("data") if isinstance(chart.get("data"), dict) else {}
    labels = data.get("labels") if isinstance(data.get("labels"), list) else []
    if chart.get("chart_type") == "pie":
        raw_values = data.get("values") if isinstance(data.get("values"), list) else []
    else:
        datasets = data.get("datasets") if isinstance(data.get("datasets"), list) else []
        raw_values = datasets[0].get("values", []) if datasets and isinstance(datasets[0], dict) else []
    return [(str(label), _number(value)) for label, value in zip(labels[:24], raw_values[:24])]


def _truncate_label(label: str, max_len: int) -> str:
    return label if len(label) <= max_len else f"{label[:max_len - 1]}…"


def _number(value: Any) -> float:
    number = _number_or_none(value)
    return number if number is not None else 0.0


def _number_or_none(value: Any) -> Optional[float]:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    try:
        text = str(value).strip().replace(",", "")
        if not text:
            return None
        if text.endswith("%"):
            return float(text[:-1])
        return float(text)
    except Exception:
        return None


def _output(
    started: float,
    *,
    ok: bool,
    evidence: Optional[List[Dict[str, Any]]] = None,
    artifacts: Optional[List[Dict[str, Any]]] = None,
    warnings: Optional[List[str]] = None,
    error: Optional[str] = None,
) -> AgentToolOutput:
    return AgentToolOutput(
        ok=ok,
        evidence=evidence or [],
        artifacts=artifacts or [],
        warnings=warnings or [],
        error=error,
        duration_ms=round((time.perf_counter() - started) * 1000, 3),
    )
