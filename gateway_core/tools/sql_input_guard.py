from __future__ import annotations

from typing import Any, Mapping


SQL_ARGUMENT_KEYS = frozenset({"sql", "sql_query", "query_sql", "query", "input"})
SQL_TOKENS = (
    "select ",
    "with ",
    "insert ",
    "update ",
    "delete ",
    "drop ",
    "alter ",
    "from ",
)


def contains_sql_like_input(arguments: Mapping[str, Any]) -> bool:
    # 图表类工具只消费已查询证据，不能绕过 sql_db_query 直接执行 SQL。
    for key in SQL_ARGUMENT_KEYS:
        value = arguments.get(key)
        if not isinstance(value, str):
            continue
        normalized = f" {value.strip().lower()} "
        if any(token in normalized for token in SQL_TOKENS):
            return True
    return False
