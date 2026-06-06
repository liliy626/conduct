from __future__ import annotations

from gateway_core.tools.chart_tool import ChartTool
from gateway_core.tools.plot_tool import PlotTool
from gateway_core.tools.tool_core import AgentToolInput, ToolExecutionContext


def test_chart_tool_rejects_sql_like_input() -> None:
    output = ChartTool().run(
        AgentToolInput(
            arguments={
                "query": "select count(*) from student_leave",
                "rows": [{"grade": "六年级", "count": 3}],
            }
        ),
        ToolExecutionContext(tenant_id="sch_test"),
    )

    assert output.ok is False
    assert output.error == "chart tool does not execute SQL; call sql_db_query first and pass evidence_rows/rows."
    assert output.artifacts == []


def test_plot_tool_rejects_sql_like_input_aliases() -> None:
    output = PlotTool().run(
        AgentToolInput(
            arguments={
                "input": "WITH base AS (select * from student_leave) select * from base",
                "rows": [{"grade": "六年级", "count": 3}],
            }
        ),
        ToolExecutionContext(tenant_id="sch_test"),
    )

    assert output.ok is False
    assert output.error == "plot tool does not execute SQL; call sql_db_query first and pass evidence_rows/rows."
    assert output.artifacts == []


def test_chart_tool_rejects_non_table_chart_without_numeric_field() -> None:
    output = ChartTool().run(
        AgentToolInput(
            arguments={
                "chart_type": "bar",
                "rows": [
                    {"grade": "六年级", "status": "正常"},
                    {"grade": "七年级", "status": "异常"},
                ],
            }
        ),
        ToolExecutionContext(tenant_id="sch_test"),
    )

    assert output.ok is False
    assert output.error == "chart tool requires an inferable numeric value field for non-table charts."
    assert output.artifacts == []
