from __future__ import annotations

import json
from types import SimpleNamespace
import threading


def test_inspect_table_schema_does_not_expand_sql_allowlist_without_ddl_search(monkeypatch) -> None:
    from gateway_core.agents.school_sql.sql_tools import DDLReactTools
    import gateway_core.agents.school_sql.sql_tools as sql_tools

    tool = DDLReactTools.__new__(DDLReactTools)
    tool.tenant_id = "sch_zx_mlh"
    tool.package_index = SimpleNamespace(source_schema="zx_mlh")
    tool.trace = None
    tool.allowed_table_refs = []
    tool._post_ddl_inspect_refs = set()

    def fail_inspect(**_kwargs):
        raise AssertionError("inspect_table_schema must not inspect a table before ddl_search authorizes it")

    monkeypatch.setattr(sql_tools, "_inspect_table_columns", fail_inspect)

    payload = json.loads(tool.inspect_table_schema("学生请假_学生请假"))

    assert payload == {
        "source": "information_schema",
        "allowed": False,
        "error": "ddl_search_required_before_inspect_table_schema",
        "table_ref": "zx_mlh.学生请假_学生请假",
    }
    assert tool.allowed_table_refs == []
    assert tool._post_ddl_inspect_refs == set()


def test_sample_table_rows_does_not_expand_sql_allowlist_without_ddl_search(monkeypatch) -> None:
    from gateway_core.agents.school_sql.sql_tools import DDLReactTools
    import gateway_core.agents.school_sql.sql_tools as sql_tools

    tool = DDLReactTools.__new__(DDLReactTools)
    tool.tenant_id = "sch_zx_mlh"
    tool.package_index = SimpleNamespace(source_schema="zx_mlh")
    tool.psycopg_module = object()
    tool.dsn = "postgres://fake"
    tool.trace = None
    tool.allowed_table_refs = []
    tool._post_ddl_sample_refs = set()

    def fail_sample_columns(**_kwargs):
        raise AssertionError("sample_table_rows must not sample a table before ddl_search authorizes it")

    monkeypatch.setattr(sql_tools, "_sample_select_columns", fail_sample_columns)

    payload = json.loads(tool.sample_table_rows("学生请假_学生请假"))

    assert payload == {
        "source": "school_schema",
        "allowed": False,
        "error": "ddl_search_required_before_sample_table_rows",
        "table_ref": "zx_mlh.学生请假_学生请假",
    }
    assert tool.allowed_table_refs == []
    assert tool._post_ddl_sample_refs == set()


def test_inspect_table_schema_enriches_only_already_allowed_table(monkeypatch) -> None:
    from gateway_core.agents.school_sql.sql_tools import DDLReactTools
    import gateway_core.agents.school_sql.sql_tools as sql_tools

    tool = DDLReactTools.__new__(DDLReactTools)
    tool.tenant_id = "sch_zx_mlh"
    tool.package_index = SimpleNamespace(source_schema="zx_mlh")
    tool.psycopg_module = object()
    tool.dsn = "postgres://fake"
    tool.trace = None
    tool.allowed_table_refs = ["zx_mlh.学生请假_学生请假"]
    tool._post_ddl_inspect_refs = set()
    tool._known_columns_by_ref = {}

    monkeypatch.setattr(
        sql_tools,
        "_inspect_table_columns",
        lambda **_kwargs: [{"column_name": "请假类型", "data_type": "text"}],
    )
    monkeypatch.setattr(sql_tools, "_load_table_ddl_summary", lambda **_kwargs: "学生请假 DDL")

    payload = json.loads(tool.inspect_table_schema("学生请假_学生请假"))

    assert payload["allowed"] is True
    assert payload["table_ref"] == "zx_mlh.学生请假_学生请假"
    assert payload["column_count"] == 1
    assert tool.allowed_table_refs == ["zx_mlh.学生请假_学生请假"]
    assert tool._post_ddl_inspect_refs == {"zx_mlh.学生请假_学生请假"}
    assert tool._known_columns_by_ref == {"zx_mlh.学生请假_学生请假": {"请假类型"}}


def test_jsonb_recordset_query_requires_prior_jsonb_inspect(monkeypatch) -> None:
    from gateway_core.agents.school_sql.sql_tools import DDLReactTools
    import gateway_core.agents.school_sql.sql_tools as sql_tools

    tool = DDLReactTools.__new__(DDLReactTools)
    tool.question = "今天行政执勤子表记录"
    tool.tenant_id = "sch_zx_mlh"
    tool.package_index = SimpleNamespace(source_schema="zx_mlh")
    tool.psycopg_module = object()
    tool.dsn = "postgres://fake"
    tool.trace = None
    tool.allowed_table_refs = ["zx_mlh.每日执勤_行政执勤记录表"]
    tool._sql_query_counter = 0
    tool._sql_query_counter_lock = threading.Lock()

    monkeypatch.setattr(
        sql_tools,
        "_inspect_table_columns",
        lambda **_kwargs: [{"column_name": "值班安排", "is_json_or_array": True}],
    )

    def fail_execute(**_kwargs):
        raise AssertionError("jsonb_recordset_query must not execute before inspect_jsonb_recordset")

    monkeypatch.setattr(sql_tools, "_execute_query", fail_execute)

    payload = json.loads(
        tool.jsonb_recordset_query(
            json.dumps(
                {
                    "table_name": "每日执勤_行政执勤记录表",
                    "jsonb_column": "值班安排",
                    "record_schema": {"星期": "text"},
                },
                ensure_ascii=False,
            )
        )
    )

    assert payload == {
        "source": "school_schema",
        "allowed": False,
        "error": "inspect_jsonb_recordset_required_before_jsonb_recordset_query",
        "table_ref": "zx_mlh.每日执勤_行政执勤记录表",
        "jsonb_column": "值班安排",
    }


def test_analysis_tools_ignore_forged_input_rows_without_evidence() -> None:
    from gateway_core.agents.school_sql.sql_tools import DDLReactTools

    tool = DDLReactTools.__new__(DDLReactTools)
    tool.tenant_id = "sch_zx_mlh"
    tool.trace = None
    tool.evidence_by_task = {}

    payload = json.loads(
        tool.trend_analysis(
            json.dumps(
                {
                    "rows": [
                        {"月份": "2026-04", "数量": 1},
                        {"月份": "2026-05", "数量": 99},
                    ],
                    "time_field": "月份",
                    "metric_field": "数量",
                },
                ensure_ascii=False,
            )
        )
    )

    assert payload["ok"] is False
    assert payload["error"] == "not_enough_time_series_data"
    assert payload["row_count"] == 0
