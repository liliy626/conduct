from __future__ import annotations

from typing import Any


def remember_recordset_inspection(owner: Any, table_ref: str, jsonb_column: str, record_schema: dict[str, Any]) -> None:
    # JSONB 子表查询必须复用 inspect_jsonb_recordset 给出的 schema，避免模型自行猜 key。
    state = _state(owner)
    state[_key(table_ref, jsonb_column)] = {str(key): str(value) for key, value in record_schema.items()}


def recordset_inspection_error(
    owner: Any,
    table_ref: str,
    jsonb_column: str,
    record_schema: dict[str, Any],
) -> str:
    expected = _state(owner).get(_key(table_ref, jsonb_column))
    if not expected:
        return "inspect_jsonb_recordset_required_before_jsonb_recordset_query"
    actual = {str(key): str(value) for key, value in record_schema.items()}
    if actual != expected:
        return "record_schema_mismatch_inspect_jsonb_recordset"
    return ""


def recordset_inspection_payload(
    owner: Any,
    table_ref: str,
    jsonb_column: str,
    record_schema: dict[str, Any],
) -> dict[str, object]:
    error = recordset_inspection_error(owner, table_ref, jsonb_column, record_schema)
    if not error:
        return {}
    return {
        "source": "school_schema",
        "allowed": False,
        "error": error,
        "table_ref": table_ref,
        "jsonb_column": jsonb_column,
    }


def _state(owner: Any) -> dict[str, dict[str, str]]:
    state = getattr(owner, "_jsonb_recordset_inspections", None)
    if not isinstance(state, dict):
        state = {}
        setattr(owner, "_jsonb_recordset_inspections", state)
    return state


def _key(table_ref: str, jsonb_column: str) -> str:
    return f"{str(table_ref).strip().lower()}::{str(jsonb_column).strip()}"
