from __future__ import annotations

import inspect


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
