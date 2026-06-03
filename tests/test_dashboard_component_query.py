from __future__ import annotations

import asyncio

from fastapi.testclient import TestClient

from app import app
from gateway_core.dashboard.component_query import (
    DashboardComponentQueryRequest,
    run_dashboard_component_data_agent,
)


def test_dashboard_component_query_delegates_to_data_agent(monkeypatch) -> None:
    async def _fake_runner(request, *, authorization, x_school_scope):
        assert authorization == "Bearer key_a"
        assert x_school_scope == "mlh"
        return {
            "status": "ready",
            "component_name": request.component_name,
            "sql_queries": [{"task_id": "q1", "sql": "select 1"}],
            "fields": [{"name": "日期", "type": "date"}],
            "row_count": 1,
            "sample_rows": [{"日期": "2026-05-01"}],
            "limitations": [],
        }

    monkeypatch.setattr(
        "gateway_core.dashboard.component_query.run_dashboard_component_data_agent",
        _fake_runner,
    )
    client = TestClient(app)

    response = client.post(
        "/v1/dashboard/component-query",
        headers={"Authorization": "Bearer key_a", "X-School-Scope": "mlh"},
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
    assert response.json() == {
        "status": "ready",
        "component_name": "趋势",
        "sql_queries": [{"task_id": "q1", "sql": "select 1"}],
        "fields": [{"name": "日期", "type": "date"}],
        "row_count": 1,
        "sample_rows": [{"日期": "2026-05-01"}],
        "limitations": [],
    }


def test_dashboard_component_query_accepts_dashboard_agent_question_payload(monkeypatch) -> None:
    seen = {}

    async def _fake_runner(request, *, authorization, x_school_scope):
        seen["request"] = request
        return {
            "status": "ready",
            "component_name": request.component_name,
            "sql_queries": [],
            "fields": [],
            "row_count": 0,
            "sample_rows": [],
            "limitations": [],
        }

    monkeypatch.setattr(
        "gateway_core.dashboard.component_query.run_dashboard_component_data_agent",
        _fake_runner,
    )
    client = TestClient(app)

    response = client.post(
        "/v1/dashboard/component-query",
        json={
            "question": {
                "dashboardNeed": (
                    "你是大屏 Agent。目标是制作“美兰湖中学近期异常情况”大屏。\n"
                    "时间范围优先：2026-05-01 至 2026-06-02\n"
                    "我要做组件【KPI】。用途是【近期异常总量、重点风险数、待处理事项数、涉及班级数】。"
                ),
                "targetSchema": "zx_mlh",
            }
        },
    )

    assert response.status_code == 200
    request = seen["request"]
    assert request.schema_name == "zx_mlh"
    assert request.component_name == "KPI"
    assert request.purpose == "近期异常总量、重点风险数、待处理事项数、涉及班级数"
    assert request.time_range.start == "2026-05-01"
    assert request.time_range.end == "2026-06-02"


def test_dashboard_component_query_accepts_nested_component_request_payload(monkeypatch) -> None:
    seen = {}

    async def _fake_runner(request, *, authorization, x_school_scope):
        seen["request"] = request
        return {
            "status": "ready",
            "component_name": request.component_name,
            "sql_queries": [],
            "fields": [],
            "row_count": 0,
            "sample_rows": [],
            "limitations": [],
        }

    monkeypatch.setattr(
        "gateway_core.dashboard.component_query.run_dashboard_component_data_agent",
        _fake_runner,
    )
    client = TestClient(app)

    response = client.post(
        "/v1/dashboard/component-query",
        json={
            "question": {
                "dashboardNeed": "学校最近有什么异常？请生成一个近期异常情况大屏，重点看请假、行规、心理健康、设备报修和待处理事项。",
                "taskContext": {
                    "goal": "制作“美兰湖中学近期异常情况”大屏",
                    "targetSchema": "zx_mlh",
                    "timeRange": {
                        "start": "2026-05-01",
                        "end": "2026-06-02",
                    },
                },
                "componentRequest": {
                    "componentName": "KPI",
                    "purpose": "近期异常总量、重点风险数、待处理事项数、涉及班级数",
                },
            }
        },
    )

    assert response.status_code == 200
    request = seen["request"]
    assert request.schema_name == "zx_mlh"
    assert request.component_name == "KPI"
    assert request.purpose == "近期异常总量、重点风险数、待处理事项数、涉及班级数"
    assert request.time_range.start == "2026-05-01"
    assert request.time_range.end == "2026-06-02"


def test_dashboard_component_data_agent_disables_final_fast_llm() -> None:
    request = DashboardComponentQueryRequest.model_validate(
        {
            "schema": "zx_mlh",
            "component_name": "趋势",
            "purpose": "按日期展示请假、行规扣分、报修数量变化",
            "time_range": {"start": "2026-05-01", "end": "2026-06-02"},
        }
    )
    captured = {}

    async def _fake_stream(**kwargs):
        captured.update(kwargs)
        yield {
            "type": "content",
            "text": (
                '{"status":"ready","component_name":"趋势",'
                '"sql_queries":[{"task_id":"trend","sql":"select 1"}],'
                '"fields":[{"name":"日期","type":"date"}],'
                '"row_count":1,'
                '"sample_rows":[{"日期":"2026-05-01"}],'
                '"limitations":[]}'
            ),
        }

    result = asyncio.run(
        run_dashboard_component_data_agent(
            request,
            token="key_a",
            school_scope="mlh",
            model=object(),
            dsn="postgres://example",
            psycopg_module=object(),
            embedding_fn=lambda _text: None,
            stream_fn=_fake_stream,
            validate_sql=False,
        )
    )

    assert captured["final_model"] is None
    assert "final_answer_handoff" in captured["disabled_tool_names"]
    assert captured["question"].startswith("你是大屏组件查数 Agent")
    assert result["sql_queries"] == [{"task_id": "trend", "sql": "select 1"}]


def test_dashboard_component_data_agent_filters_unusable_sql() -> None:
    request = DashboardComponentQueryRequest.model_validate(
        {
            "schema": "zx_mlh",
            "component_name": "KPI",
            "purpose": "近期异常总量",
            "time_range": {"start": "2026-05-01", "end": "2026-06-02"},
        }
    )
    executed = []

    class FakeCursor:
        def __enter__(self):
            return self

        def __exit__(self, *_exc):
            return False

        def execute(self, sql, _params=None):
            executed.append(sql)
            if "missing_column" in sql:
                raise RuntimeError("column does not exist")

    class FakeConnection:
        def __enter__(self):
            return self

        def __exit__(self, *_exc):
            return False

        def cursor(self):
            return FakeCursor()

    class FakePsycopg:
        @staticmethod
        def connect(_dsn):
            return FakeConnection()

    async def _fake_stream(**_kwargs):
        yield {
            "type": "content",
            "text": (
                '{"status":"ready","component_name":"KPI",'
                '"sql_queries":['
                '{"task_id":"ok","sql":"select 1 as value"},'
                '{"task_id":"write","sql":"update zx_mlh.foo set x = 1"},'
                '{"task_id":"bad","sql":"select missing_column from zx_mlh.foo"}'
                '],'
                '"fields":[],"row_count":1,"sample_rows":[{"value":1}],'
                '"limitations":[]}'
            ),
        }

    result = asyncio.run(
        run_dashboard_component_data_agent(
            request,
            token="key_a",
            school_scope="mlh",
            model=object(),
            dsn="postgres://example",
            psycopg_module=FakePsycopg,
            embedding_fn=lambda _text: None,
            stream_fn=_fake_stream,
        )
    )

    assert result["sql_queries"] == [
        {
            "task_id": "ok",
            "sql": "select 1 as value",
            "validation_report": {"ok": True, "status": "passed"},
        }
    ]
    assert any("write" in item for item in result["limitations"])
    assert any("bad" in item for item in result["limitations"])
    assert any("select * from (select 1 as value)" in sql.lower() for sql in executed)
