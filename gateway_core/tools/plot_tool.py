from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from .artifact_store import artifact_download_url, safe_artifact_path
from .tool_core import AgentTool, AgentToolInput, AgentToolOutput, ToolExecutionContext, json_safe


SUPPORTED_PLOT_TYPES = {"bar", "line", "scatter", "pie"}


class PlotTool(AgentTool):
    name = "plot"
    description = (
        "Builds a PNG chart image from already queried evidence rows. "
        "Use sql_db_query first, then pass rows/evidence_rows plus chart_type/title/x_field/y_field. "
        "This tool never executes SQL."
    )

    def run(self, tool_input: AgentToolInput, context: ToolExecutionContext) -> AgentToolOutput:
        started = time.perf_counter()
        args = tool_input.arguments
        if args.get("sql") or args.get("sql_query") or args.get("query_sql"):
            return _output(
                started,
                ok=False,
                error="plot tool does not execute SQL; call sql_db_query first and pass evidence_rows/rows.",
            )

        rows = _evidence_rows(tool_input)
        if not rows:
            return _output(started, ok=False, error="plot tool requires evidence rows; refusing to invent chart data.")

        chart_type = str(args.get("chart_type") or args.get("type") or "bar").strip().lower()
        if chart_type not in SUPPORTED_PLOT_TYPES:
            return _output(
                started,
                ok=False,
                error=f"unsupported chart_type: {chart_type}",
                warnings=[f"supported chart types: {', '.join(sorted(SUPPORTED_PLOT_TYPES))}"],
            )

        x_field = str(args.get("x") or args.get("x_field") or "").strip()
        y_field = str(args.get("y") or args.get("y_field") or "").strip()
        x_field = x_field or _guess_label_field(rows)
        y_field = y_field or _guess_value_field(rows, exclude=x_field)
        if not x_field or not y_field:
            return _output(started, ok=False, error="plot tool requires x_field and y_field or inferable rows.")

        title = str(args.get("title") or "数据图表").strip() or "数据图表"
        try:
            png_path = _render_matplotlib_png(
                rows=rows,
                chart_type=chart_type,
                title=title,
                x_field=x_field,
                y_field=y_field,
                context=context,
            )
        except ImportError:
            return _output(
                started,
                ok=False,
                error="matplotlib is not installed; install requirements or use chart tool for SVG/HTML charts.",
            )
        except Exception as exc:
            return _output(started, ok=False, error=f"plot render failed: {exc}")

        json_path = png_path.with_suffix(".json")
        json_path.write_text(
            json.dumps(
                json_safe(
                    {
                        "title": title,
                        "chart_type": chart_type,
                        "x_field": x_field,
                        "y_field": y_field,
                        "rows": rows,
                    }
                ),
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        image_url = artifact_download_url(png_path)
        data_url = artifact_download_url(json_path)
        return _output(
            started,
            ok=True,
            evidence=[{"kind": "plot_evidence", "row_count": len(rows), "x_field": x_field, "y_field": y_field}],
            artifacts=[
                {
                    "type": "plot",
                    "format": "png",
                    "title": title,
                    "path": str(png_path),
                    "image_url": image_url,
                    "download_url": image_url,
                    "data_url": data_url,
                    "content": {
                        "chart_type": chart_type,
                        "x_field": x_field,
                        "y_field": y_field,
                        "rows": rows,
                    },
                }
            ],
        )


def _render_matplotlib_png(
    *,
    rows: list[dict[str, Any]],
    chart_type: str,
    title: str,
    x_field: str,
    y_field: str,
    context: ToolExecutionContext,
) -> Path:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    plt.rcParams["font.sans-serif"] = ["PingFang SC", "SimHei", "Microsoft YaHei", "Arial Unicode MS", "DejaVu Sans"]
    plt.rcParams["axes.unicode_minus"] = False

    labels = [str(row.get(x_field) or "") for row in rows]
    values = [_to_float(row.get(y_field)) for row in rows]
    png_path = safe_artifact_path(tenant_id=context.tenant_id, tool_name="plot", suffix=".png")

    fig, ax = plt.subplots(figsize=(10, 6), dpi=160)
    fig.patch.set_facecolor("#ffffff")
    ax.set_facecolor("#ffffff")
    accent = "#2563eb"
    if chart_type == "bar":
        ax.bar(labels, values, color=accent, alpha=0.86)
    elif chart_type == "line":
        ax.plot(labels, values, marker="o", linewidth=2.4, color=accent)
    elif chart_type == "scatter":
        ax.scatter(labels, values, s=72, color=accent, alpha=0.88)
    elif chart_type == "pie":
        ax.pie(values, labels=labels, autopct="%1.1f%%", startangle=140)
        ax.axis("equal")

    ax.set_title(title, fontsize=16, fontweight="bold", pad=16)
    if chart_type != "pie":
        ax.set_xlabel(x_field)
        ax.set_ylabel(y_field)
        ax.grid(axis="y", color="#e5e7eb", linewidth=0.8)
        ax.tick_params(axis="x", rotation=35)
        for spine in ("top", "right"):
            ax.spines[spine].set_visible(False)
    fig.tight_layout()
    fig.savefig(png_path, bbox_inches="tight")
    plt.close(fig)
    return png_path


def _evidence_rows(tool_input: AgentToolInput) -> list[dict[str, Any]]:
    rows = tool_input.arguments.get("evidence_rows") or tool_input.arguments.get("rows") or tool_input.evidence
    if not isinstance(rows, list):
        return []
    return [dict(row) for row in rows if isinstance(row, dict)]


def _guess_label_field(rows: list[dict[str, Any]]) -> str:
    if not rows:
        return ""
    candidates = list(rows[0].keys())
    for key in candidates:
        if not _is_number(rows[0].get(key)):
            return str(key)
    return str(candidates[0]) if candidates else ""


def _guess_value_field(rows: list[dict[str, Any]], *, exclude: str = "") -> str:
    if not rows:
        return ""
    for key in rows[0].keys():
        if str(key) != exclude and _is_number(rows[0].get(key)):
            return str(key)
    return ""


def _is_number(value: Any) -> bool:
    try:
        float(value)
        return value is not None and value != ""
    except Exception:
        return False


def _to_float(value: Any) -> float:
    try:
        return float(value)
    except Exception:
        return 0.0


def _output(
    started: float,
    *,
    ok: bool,
    evidence: list[dict[str, Any]] | None = None,
    artifacts: list[dict[str, Any]] | None = None,
    warnings: list[str] | None = None,
    error: str | None = None,
) -> AgentToolOutput:
    return AgentToolOutput(
        ok=ok,
        evidence=evidence or [],
        artifacts=artifacts or [],
        warnings=warnings or [],
        error=error,
        duration_ms=round((time.perf_counter() - started) * 1000, 3),
    )
