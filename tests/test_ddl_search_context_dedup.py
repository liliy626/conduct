from __future__ import annotations

from gateway_core.agents.school_sql.sql_tools import _ddl_search_context_plan, _inspect_schema_reuse_payload


def test_ddl_search_context_plan_marks_new_refs_first() -> None:
    seen_refs: set[str] = set()
    seen_signatures: dict[str, int] = {}

    plan = _ddl_search_context_plan(
        ["zx_mlh.教师销假_请假明细", "zx_mlh.教师获奖_获奖上报"],
        seen_table_refs=seen_refs,
        seen_result_signatures=seen_signatures,
    )

    assert plan["duplicate_result"] is False
    assert plan["new_table_refs"] == ["zx_mlh.教师销假_请假明细", "zx_mlh.教师获奖_获奖上报"]
    assert plan["known_table_refs"] == []


def test_ddl_search_context_plan_detects_duplicate_result() -> None:
    seen_refs = {"zx_mlh.教师销假_请假明细", "zx_mlh.教师获奖_获奖上报"}
    seen_signatures: dict[str, int] = {}
    refs = ["zx_mlh.教师销假_请假明细", "zx_mlh.教师获奖_获奖上报"]

    first = _ddl_search_context_plan(refs, seen_table_refs=seen_refs, seen_result_signatures=seen_signatures)
    second = _ddl_search_context_plan(refs, seen_table_refs=seen_refs, seen_result_signatures=seen_signatures)

    assert first["duplicate_result"] is False
    assert first["new_table_refs"] == []
    assert first["known_table_refs"] == refs
    assert second["duplicate_result"] is True
    assert second["new_table_refs"] == []
    assert second["known_table_refs"] == refs


def test_ddl_search_context_plan_keeps_only_unseen_refs_as_new() -> None:
    seen_refs = {"zx_mlh.教师销假_请假明细"}
    seen_signatures: dict[str, int] = {}

    plan = _ddl_search_context_plan(
        ["zx_mlh.教师销假_请假明细", "zx_mlh.作业公示_新_教师数据"],
        seen_table_refs=seen_refs,
        seen_result_signatures=seen_signatures,
    )

    assert plan["duplicate_result"] is False
    assert plan["known_table_refs"] == ["zx_mlh.教师销假_请假明细"]
    assert plan["new_table_refs"] == ["zx_mlh.作业公示_新_教师数据"]


def test_inspect_schema_reuse_payload_omits_full_columns_and_ddl() -> None:
    payload = _inspect_schema_reuse_payload(
        {
            "allowed": True,
            "schema_name": "zx_mlh",
            "table_name": "教师销假_请假明细",
            "table_ref": "zx_mlh.教师销假_请假明细",
            "column_count": 2,
            "columns": [
                {"column_name": "教师姓名", "data_type": "text"},
                {"column_name": "请假开始时间", "data_type": "timestamp"},
            ],
            "ddl_summary": "很长的 DDL 摘要",
        }
    )

    assert payload["schema_reused"] is True
    assert payload["table_ref"] == "zx_mlh.教师销假_请假明细"
    assert payload["known_column_names"] == ["教师姓名", "请假开始时间"]
    assert "columns" not in payload
    assert "ddl_summary" not in payload
