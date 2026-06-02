from __future__ import annotations

from gateway_core.school import schema_indexer


class _FakeCursor:
    def __init__(self, fetches: list[list[tuple]] | None = None) -> None:
        self.queries: list[str] = []
        self._fetches: list[list[tuple]] = fetches or [
            [("学生请假_学生请假", "BASE TABLE", "学生请假")],
            [],
        ]

    def execute(self, query: str, _params: list[object]) -> None:
        self.queries.append(query)

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False

    def fetchall(self) -> list[tuple]:
        return self._fetches.pop(0) if self._fetches else []


class _FakeConnection:
    def __init__(self, cursor: _FakeCursor) -> None:
        self.cursor_obj = cursor

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False

    def cursor(self) -> _FakeCursor:
        return self.cursor_obj


def test_schema_index_table_query_joins_pg_class_by_namespace(monkeypatch) -> None:
    cursor = _FakeCursor(fetches=[[], [("学生请假_学生请假", "BASE TABLE", "学生请假")], []])
    monkeypatch.setattr(schema_indexer, "connect_db", lambda _psycopg, _dsn: _FakeConnection(cursor))

    schema_indexer._load_schema_datasets(schema_name="zx_mlh", dsn="postgresql://example", psycopg_module=object())

    table_query = next(query for query in cursor.queries if "FROM information_schema.tables t" in query)
    assert "LEFT JOIN pg_namespace n" in table_query
    assert "LEFT JOIN pg_class c" in table_query
    assert "c.relname = t.table_name" in table_query
    assert "c.relnamespace = n.oid" in table_query


def test_schema_index_prefers_ddl_vector_documents_and_filters_metadata(monkeypatch) -> None:
    cursor = _FakeCursor(
        fetches=[
            [
                ("app_detail", "app_detail", "table", "metadata doc", {}, 5),
                ("学生请假_学生请假", "学生请假_学生请假", "table", "学生请假 DDL doc", {"business_description": "学生请假"}, 12),
            ],
            [("学生姓名", "text", "text", "YES", "学生姓名")],
        ]
    )
    monkeypatch.setattr(schema_indexer, "connect_db", lambda _psycopg, _dsn: _FakeConnection(cursor))

    datasets = schema_indexer._load_schema_datasets(
        schema_name="zx_mlh",
        dsn="postgresql://example",
        psycopg_module=object(),
    )

    assert [dataset.source_view for dataset in datasets] == ["学生请假_学生请假"]
    assert datasets[0].raw["source"] == "ddl_vector_documents"
    assert datasets[0].description == "学生请假"
