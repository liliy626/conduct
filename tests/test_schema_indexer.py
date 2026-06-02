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
            [("学生请假_学生请假", "学生姓名", "text", "text", "YES", "学生姓名")],
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


def test_schema_index_loads_fields_in_one_batch_for_vector_tables(monkeypatch) -> None:
    cursor = _FakeCursor(
        fetches=[
            [
                ("教师基础信息", "教师基础信息", "table", "教师基础信息 DDL doc", {"business_description": "教师基础信息"}, 12),
                ("教师职称荣誉", "教师职称荣誉", "table", "教师职称荣誉 DDL doc", {"business_description": "教师职称荣誉"}, 8),
            ],
            [
                ("教师基础信息", "姓名", "text", "text", "YES", "姓名"),
                ("教师职称荣誉", "荣誉名称", "text", "text", "YES", "荣誉名称"),
            ],
        ]
    )
    monkeypatch.setattr(schema_indexer, "connect_db", lambda _psycopg, _dsn: _FakeConnection(cursor))

    datasets = schema_indexer._load_schema_datasets(
        schema_name="zx_mlh",
        dsn="postgresql://example",
        psycopg_module=object(),
    )

    field_queries = [query for query in cursor.queries if "FROM information_schema.columns c" in query]
    assert len(field_queries) == 1
    assert "c.table_name = ANY" in field_queries[0]
    assert [dataset.source_view for dataset in datasets] == ["教师基础信息", "教师职称荣誉"]
    assert datasets[0].fields[0].source_field == "姓名"
    assert datasets[1].fields[0].source_field == "荣誉名称"


def test_schema_index_can_skip_field_loading_for_lightweight_agent_index(monkeypatch) -> None:
    cursor = _FakeCursor(
        fetches=[
            [
                ("教师基础信息", "教师基础信息", "table", "教师基础信息 DDL doc", {"business_description": "教师基础信息"}, 12),
                ("教师职称荣誉", "教师职称荣誉", "table", "教师职称荣誉 DDL doc", {"business_description": "教师职称荣誉"}, 8),
            ],
        ]
    )
    monkeypatch.setattr(schema_indexer, "connect_db", lambda _psycopg, _dsn: _FakeConnection(cursor))

    index = schema_indexer.build_school_schema_index(
        school_id="zx_mlh",
        school_name="美兰湖",
        schema_name="zx_mlh",
        dsn="postgresql://example",
        psycopg_module=object(),
        load_fields=False,
    )

    field_queries = [query for query in cursor.queries if "FROM information_schema.columns c" in query]
    assert field_queries == []
    assert [dataset.source_view for dataset in index.datasets] == ["教师基础信息", "教师职称荣誉"]
    assert index.datasets[0].fields == []
    assert index.datasets[0].raw["field_load_mode"] == "skipped"
