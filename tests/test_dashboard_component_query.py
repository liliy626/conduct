from __future__ import annotations

from fastapi.testclient import TestClient

from app import app


def test_dashboard_component_query_returns_fixed_component_contract() -> None:
    client = TestClient(app)

    response = client.post(
        "/v1/dashboard/component-query",
        json={
            "schema": "zx_mlh",
            "component_name": "趋势",
            "purpose": "按日期展示请假、行规扣分、报修数量变化",
            "time_range": {
                "start": "2026-05-01",
                "end": "2026-06-02",
            },
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload == {
        "status": "ready",
        "component_name": "趋势",
        "sql_queries": [],
        "fields": [],
        "row_count": 0,
        "sample_rows": [],
        "limitations": [
            "组件查询接口已建立；SQL 生成与样本数据执行将在后续接入数据 Agent。"
        ],
    }
