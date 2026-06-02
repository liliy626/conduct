from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class DashboardComponentTimeRange(BaseModel):
    start: str
    end: str


class DashboardComponentQueryRequest(BaseModel):
    schema_name: str = Field(alias="schema")
    component_name: str
    purpose: str
    time_range: DashboardComponentTimeRange


class DashboardComponentQueryResponse(BaseModel):
    status: str
    component_name: str
    sql_queries: list[dict[str, Any]]
    fields: list[dict[str, Any]]
    row_count: int
    sample_rows: list[dict[str, Any]]
    limitations: list[str]


def route_dashboard_component_query(
    request: DashboardComponentQueryRequest,
) -> dict[str, Any]:
    """Return the stable dashboard component data contract.

    The endpoint is intentionally isolated so the future Data Agent integration
    can replace this placeholder without changing the FastAPI entrypoint.
    """
    return DashboardComponentQueryResponse(
        status="ready",
        component_name=request.component_name,
        sql_queries=[],
        fields=[],
        row_count=0,
        sample_rows=[],
        limitations=[
            "组件查询接口已建立；SQL 生成与样本数据执行将在后续接入数据 Agent。"
        ],
    ).model_dump(mode="json")
