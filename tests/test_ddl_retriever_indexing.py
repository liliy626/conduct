from __future__ import annotations

import inspect
from types import SimpleNamespace
import threading
import time


def test_ddl_retriever_has_no_static_schema_list_scan() -> None:
    import gateway_core.schema_context.ddl_retriever as ddl_retriever

    source = inspect.getsource(ddl_retriever)

    assert "_static_schema_cache" not in source
    assert "cached_table" not in source
    assert "for cached_table in" not in source


def test_merge_retrieved_rows_uses_hash_indexes_for_deduping() -> None:
    import gateway_core.schema_context.ddl_retriever as ddl_retriever

    source = inspect.getsource(ddl_retriever._merge_retrieved_rows)

    assert "by_table: dict" in source
    assert "scores: dict" in source
    assert "by_table[key]" in source


def test_ddl_retriever_runs_keyword_recall_while_embedding_is_in_flight(monkeypatch) -> None:
    import gateway_core.schema_context.ddl_retriever as ddl_retriever

    embedding_finished = threading.Event()
    keyword_started_during_embedding = threading.Event()

    class FakeCursor:
        def __init__(self) -> None:
            self.rows = []

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def execute(self, sql, params):
            text = str(sql)
            if "information_schema.columns" in text:
                self.rows = [
                    ("embedding",),
                    ("ddl_content",),
                    ("table_name",),
                    ("business_description",),
                    ("metadata",),
                ]
            elif "pg_attribute" in text:
                self.rows = [("vector(2)",)]
            elif "<=>" in text:
                self.rows = [("向量召回表", "", "Table: 向量召回表", 0.9, {})]
            elif "ILIKE" in text:
                if not embedding_finished.is_set():
                    keyword_started_during_embedding.set()
                self.rows = [("关键词召回表", "", "Table: 关键词召回表", None, {})]
            else:
                self.rows = []

        def fetchone(self):
            return self.rows[0] if self.rows else None

        def fetchall(self):
            return list(self.rows)

    class FakeConnection:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def cursor(self):
            return FakeCursor()

    def fake_connect_db(psycopg_module, dsn):
        return FakeConnection()

    def fake_embedding(text, *, dimensions=None):
        time.sleep(0.05)
        embedding_finished.set()
        return [0.1, 0.2]

    monkeypatch.setattr(ddl_retriever, "connect_db", fake_connect_db)
    ddl_retriever.clear_ddl_retriever_cache()

    context = ddl_retriever.retrieve_lean_ddl_context(
        question="教师请假",
        schema_name="zx_mlh",
        dsn="postgres://fake",
        psycopg_module=object(),
        embedding_fn=fake_embedding,
        top_k=2,
    )

    assert keyword_started_during_embedding.is_set()
    assert set(context.timings_ms) >= {"embedding_ms", "vector_sql_ms", "keyword_sql_ms", "merge_ms"}
    assert context.recall_compare["vector_count"] == 1
    assert context.recall_compare["keyword_count"] == 1
    assert context.recall_compare["only_vector"] == ["向量召回表"]
    assert context.recall_compare["only_keyword"] == ["关键词召回表"]


def test_coverage_probe_runs_candidate_tables_concurrently_and_preserves_order(monkeypatch) -> None:
    from gateway_core.agents.school_sql.sql_tools import DDLReactTools
    import gateway_core.agents.school_sql.sql_tools as sql_tools

    first_probe_started = threading.Event()
    second_probe_started = threading.Event()
    calls: list[str] = []

    tool = DDLReactTools.__new__(DDLReactTools)

    def fake_probe(ref: str, *, query: str) -> dict:
        calls.append(ref)
        if ref == "a":
            first_probe_started.set()
            second_probe_started.wait(0.2)
        elif ref == "b":
            second_probe_started.set()
        return {"table_ref": ref, "query": query}

    monkeypatch.setattr(sql_tools, "_coverage_probe_enabled", lambda: True)
    monkeypatch.setattr(sql_tools, "_coverage_probe_max_tables", lambda: 3)
    monkeypatch.setattr(sql_tools, "_coverage_probe_max_workers", lambda: 3)
    tool._probe_single_candidate_table = fake_probe

    result = tool._probe_candidate_evidence_map(["a", "b", "c"], query="师资")

    assert first_probe_started.is_set()
    assert second_probe_started.is_set()
    assert [item["table_ref"] for item in result] == ["a", "b", "c"]
    assert set(calls) == {"a", "b", "c"}


def test_coverage_probe_uses_lightweight_presence_and_latest_time_queries(monkeypatch) -> None:
    from gateway_core.agents.school_sql.sql_tools import DDLReactTools
    import gateway_core.agents.school_sql.sql_tools as sql_tools

    executed_sql: list[str] = []
    tool = DDLReactTools.__new__(DDLReactTools)
    tool.package_index = SimpleNamespace(source_schema="zx_mlh")
    tool.psycopg_module = object()
    tool.dsn = "postgres://fake"

    monkeypatch.setattr(
        sql_tools,
        "_inspect_table_columns",
        lambda **kwargs: [
            {
                "column_name": "__instance_time",
                "data_type": "timestamp with time zone",
                "udt_name": "timestamptz",
            }
        ],
    )

    def fake_execute_query(*, psycopg_module, dsn, sql, params):
        executed_sql.append(sql)
        if "LIMIT 1" in sql and "ORDER BY" not in sql:
            return [{"exists": 1}]
        if "ORDER BY" in sql:
            return [{"latest_time": "2026-05-31 23:11:00+00:00"}]
        raise AssertionError(f"unexpected probe SQL: {sql}")

    monkeypatch.setattr(sql_tools, "_execute_query", fake_execute_query)

    result = tool._probe_single_candidate_table("zx_mlh.学生请假_学生请假", query="今天请假的同学有哪些？")

    assert result["status"] == "stale"
    assert result["has_rows"] is True
    assert result["time_field"] == "__instance_time"
    assert result["latest_time"] == "2026-05-31 23:11:00+00:00"
    assert result["current_period"] == "today"
    assert result["has_current_period_data"] is False
    assert "total_count" not in result
    assert "current_period_count" not in result
    assert executed_sql
    assert not any("COUNT(*)" in sql or "COUNT(" in sql for sql in executed_sql)
