from __future__ import annotations

import json
import os
from typing import Any

from gateway_core.infra.db_pool import connect_db
from gateway_core.schema_context.ttl_cache import TTLCache
from gateway_core.school.schema_index import SchoolDatasetIndex, SchoolFieldIndex, SchoolSchemaIndex


_SCHEMA_INDEX_CACHE = TTLCache(max_entries=64, ttl_seconds=300)


def build_school_schema_index(
    *,
    school_id: str,
    school_name: str = "",
    schema_name: str,
    dsn: str,
    psycopg_module: Any,
    load_fields: bool = True,
) -> SchoolSchemaIndex:
    """Build the Agent schema index directly from PostgreSQL metadata.

    This is the schema-only path used by API-key configured schools. It keeps
    the rest of the SQL Agent on the same safe interface while removing the
    runtime dependency on school-specific YAML files.
    """
    clean_schema = _clean_identifier(schema_name)
    clean_school = str(school_id or clean_schema).strip() or clean_schema
    if not clean_schema:
        raise ValueError("schema_name is required for schema-only school index")
    if not str(dsn or "").strip() or psycopg_module is None:
        raise ValueError("database dsn and psycopg_module are required for schema-only school index")

    cache_key = f"{clean_school}:{clean_schema}:{_max_tables()}:{_max_fields_per_table()}:{int(bool(load_fields))}"
    if _cache_enabled():
        hit = _SCHEMA_INDEX_CACHE.get(cache_key)
        if hit is not None and isinstance(hit.value, SchoolSchemaIndex):
            return hit.value

    datasets = _load_schema_datasets(
        schema_name=clean_schema,
        dsn=dsn,
        psycopg_module=psycopg_module,
        load_fields=load_fields,
    )
    index = SchoolSchemaIndex(
        school_id=clean_school,
        school_name=str(school_name or clean_school).strip(),
        source_schema=clean_schema,
        datasets=datasets,
    )
    if _cache_enabled():
        _SCHEMA_INDEX_CACHE.set(cache_key, index)
    return index


def clear_schema_index_cache() -> None:
    _SCHEMA_INDEX_CACHE.clear()


def _load_schema_datasets(
    *,
    schema_name: str,
    dsn: str,
    psycopg_module: Any,
    load_fields: bool = True,
) -> list[SchoolDatasetIndex]:
    with connect_db(psycopg_module, dsn) as conn:
        with conn.cursor() as cur:
            cur.execute("SET statement_timeout = 5000", [])
            vector_datasets = _load_ddl_vector_datasets(cur, schema_name=schema_name, load_fields=load_fields)
            if vector_datasets:
                return vector_datasets
            return _load_information_schema_datasets(cur, schema_name=schema_name, load_fields=load_fields)


def _load_ddl_vector_datasets(cur: Any, *, schema_name: str, load_fields: bool = True) -> list[SchoolDatasetIndex]:
    vector_table = _clean_identifier(os.getenv("SCHOOL_DDL_VECTOR_TABLE") or os.getenv("TENANT_DDL_VECTOR_TABLE") or "ddl_vector_documents")
    if not vector_table:
        return []
    try:
        cur.execute(
            f"""
            SELECT table_name, object_name, object_type, content, metadata, column_count
            FROM {_quote_table_ref(schema_name, vector_table)}
            WHERE schema_name = %s
              AND COALESCE(table_name, object_name, '') <> ''
            ORDER BY table_name, object_name
            LIMIT %s
            """,
            [schema_name, _max_tables()],
        )
        rows = cur.fetchall()
    except Exception:
        return []

    datasets: list[SchoolDatasetIndex] = []
    seen: set[str] = set()
    candidates: list[tuple[str, str, str, dict[str, Any], str]] = []
    for table_name_raw, object_name_raw, object_type_raw, content_raw, metadata_raw, _column_count_raw in rows:
        table_name = str(table_name_raw or object_name_raw or "").strip()
        if not table_name or table_name in seen or _is_metadata_table(table_name):
            continue
        seen.add(table_name)
        metadata = _metadata_dict(metadata_raw)
        description = _ddl_vector_description(
            table_name=table_name,
            object_type=str(object_type_raw or ""),
            content=str(content_raw or ""),
            metadata=metadata,
        )
        candidates.append((table_name, str(object_type_raw or "TABLE"), description, metadata, str(content_raw or "")))
    fields_by_table = (
        _load_fields_many(
            cur,
            schema_name=schema_name,
            table_names=[item[0] for item in candidates],
        )
        if load_fields
        else {}
    )
    for table_name, object_type, description, _metadata, _content in candidates:
        datasets.append(
            _dataset_from_table(
                schema_name=schema_name,
                table_name=table_name,
                table_type=object_type,
                comment=description,
                fields=fields_by_table.get(table_name, []),
                raw_source="ddl_vector_documents",
                field_load_mode="loaded" if load_fields else "skipped",
            )
        )
    return datasets


def _load_information_schema_datasets(cur: Any, *, schema_name: str, load_fields: bool = True) -> list[SchoolDatasetIndex]:
    cur.execute(
        """
        SELECT t.table_name, t.table_type,
               COALESCE(obj_description(c.oid), '') AS table_comment
        FROM information_schema.tables t
        LEFT JOIN pg_namespace n
          ON n.nspname = t.table_schema
        LEFT JOIN pg_class c
          ON c.relname = t.table_name
         AND c.relnamespace = n.oid
        WHERE t.table_schema = %s
          AND t.table_type IN ('BASE TABLE', 'VIEW')
        ORDER BY t.table_name
        LIMIT %s
        """,
        [schema_name, _max_tables()],
    )
    table_rows = cur.fetchall()
    datasets: list[SchoolDatasetIndex] = []
    candidates: list[tuple[str, str, str]] = []
    for table_name_raw, table_type_raw, comment_raw in table_rows:
        table_name = str(table_name_raw or "").strip()
        if not table_name or _is_metadata_table(table_name):
            continue
        candidates.append((table_name, str(table_type_raw or ""), str(comment_raw or "")))
    fields_by_table = (
        _load_fields_many(
            cur,
            schema_name=schema_name,
            table_names=[item[0] for item in candidates],
        )
        if load_fields
        else {}
    )
    for table_name, table_type, comment in candidates:
        datasets.append(
            _dataset_from_table(
                schema_name=schema_name,
                table_name=table_name,
                table_type=table_type,
                comment=comment,
                fields=fields_by_table.get(table_name, []),
                raw_source="information_schema",
                field_load_mode="loaded" if load_fields else "skipped",
            )
        )
    return datasets


def _load_fields(cur: Any, *, schema_name: str, table_name: str) -> list[SchoolFieldIndex]:
    return _load_fields_many(cur, schema_name=schema_name, table_names=[table_name]).get(table_name, [])


def _load_fields_many(cur: Any, *, schema_name: str, table_names: list[str]) -> dict[str, list[SchoolFieldIndex]]:
    clean_names = [str(item or "").strip() for item in table_names if str(item or "").strip()]
    if not clean_names:
        return {}
    cur.execute(
        """
        SELECT c.table_name,
               c.column_name,
               c.data_type,
               c.udt_name,
               c.is_nullable,
               COALESCE(col_description(pc.oid, c.ordinal_position), '') AS column_comment
        FROM information_schema.columns c
        LEFT JOIN pg_class pc
          ON pc.relname = c.table_name
        LEFT JOIN pg_namespace pn
          ON pn.oid = pc.relnamespace
         AND pn.nspname = c.table_schema
        WHERE c.table_schema = %s
          AND c.table_name = ANY(%s)
        ORDER BY c.table_name, c.ordinal_position
        LIMIT %s
        """,
        [schema_name, clean_names, _max_fields_per_table() * max(1, len(clean_names))],
    )
    output: dict[str, list[SchoolFieldIndex]] = {name: [] for name in clean_names}
    for table_name_raw, column_name_raw, data_type_raw, udt_name_raw, _nullable_raw, comment_raw in cur.fetchall():
        table_name = str(table_name_raw or "").strip()
        if not table_name or table_name not in output:
            continue
        if len(output[table_name]) >= _max_fields_per_table():
            continue
        column_name = str(column_name_raw or "").strip()
        if not column_name:
            continue
        dataset_id = _dataset_id(table_name)
        data_type = str(data_type_raw or udt_name_raw or "").strip()
        label = str(comment_raw or "").strip() or column_name
        role = _infer_role(column_name=column_name, data_type=data_type)
        sensitive = _is_sensitive_field(column_name, label)
        output[table_name].append(
            SchoolFieldIndex(
                field_id=f"{dataset_id}.{column_name}",
                source_field=column_name,
                label=label,
                role=role,
                field_type=_field_type(data_type),
                searchable_text=" ".join([column_name, label, role, data_type]),
                filterable=role != "metric" and not sensitive,
                groupable=role in {"dimension", "status", "display_name", "attribute"} and not sensitive,
                sensitive=sensitive,
            )
        )
    return output


def _dataset_from_table(
    *,
    schema_name: str,
    table_name: str,
    table_type: str,
    comment: str,
    fields: list[SchoolFieldIndex],
    raw_source: str = "information_schema",
    field_load_mode: str = "loaded",
) -> SchoolDatasetIndex:
    dataset_id = _dataset_id(table_name)
    time_fields = [field.field_id for field in fields if field.role == "date"]
    person_fields = [
        field.field_id
        for field in fields
        if any(token in f"{field.source_field} {field.label}" for token in ["姓名", "教师", "学生", "家长", "name"])
        and not field.sensitive
    ]
    metric_fields = [field.field_id for field in fields if field.role == "metric"]
    status_fields = [field.field_id for field in fields if field.role == "status"]
    sensitive_fields = [field.field_id for field in fields if field.sensitive]
    description = comment or f"{table_type} {schema_name}.{table_name}"
    return SchoolDatasetIndex(
        dataset_id=dataset_id,
        label=comment or table_name,
        description=description,
        source_schema=schema_name,
        source_view=table_name,
        searchable_text=" ".join([table_name, comment, " ".join(field.searchable_text for field in fields[:40])]),
        fields=fields,
        default_field_ids=[field.field_id for field in fields[:8] if not field.sensitive],
        time_fields=time_fields,
        person_fields=person_fields,
        organization_fields=[],
        metric_fields=metric_fields,
        status_fields=status_fields,
        sensitive_fields=sensitive_fields,
        raw={"source": raw_source, "table_type": table_type, "field_load_mode": field_load_mode},
    )


def _metadata_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except Exception:
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def _ddl_vector_description(*, table_name: str, object_type: str, content: str, metadata: dict[str, Any]) -> str:
    for key in ("business_description", "description", "comment", "table_comment"):
        value = str(metadata.get(key) or "").strip()
        if value:
            return value
    content_desc = _content_business_description(content)
    if content_desc:
        return content_desc
    return f"{object_type or 'TABLE'} {table_name}".strip()


def _content_business_description(content: str) -> str:
    marker = "[BUSINESS DESCRIPTION]"
    text = str(content or "")
    if marker not in text:
        return ""
    tail = text.split(marker, 1)[1]
    stop_markers = ["[DDL SUMMARY]", "[COLUMNS]", "[SAMPLE VALUES]"]
    for stop in stop_markers:
        if stop in tail:
            tail = tail.split(stop, 1)[0]
            break
    lines = [line.strip(" -\t\r") for line in tail.splitlines()]
    clean = "；".join(line for line in lines if line and "暂无显式业务描述" not in line)
    return clean[:500]


def _infer_role(*, column_name: str, data_type: str) -> str:
    text = f"{column_name} {data_type}".lower()
    if any(token in text for token in ["date", "time", "日期", "时间", "created_at", "updated_at"]):
        return "date"
    if any(token in text for token in ["status", "state", "状态", "结果"]):
        return "status"
    if any(token in text for token in ["name", "姓名", "名称", "标题"]):
        return "display_name"
    if _field_type(data_type) == "number":
        return "metric"
    return "dimension" if _field_type(data_type) == "string" else "attribute"


def _field_type(data_type: str) -> str:
    text = str(data_type or "").lower()
    if any(token in text for token in ["int", "numeric", "decimal", "double", "real", "money"]):
        return "number"
    if any(token in text for token in ["date", "time"]):
        return "datetime"
    if any(token in text for token in ["json", "array"]):
        return "json"
    if any(token in text for token in ["bool"]):
        return "boolean"
    return "string"


def _is_sensitive_field(column_name: str, label: str) -> bool:
    text = f"{column_name} {label}".lower()
    return any(
        token in text
        for token in [
            "password",
            "token",
            "secret",
            "身份证",
            "证件号",
            "手机号",
            "手机号码",
            "电话",
            "住址",
            "家庭地址",
            "address",
            "phone",
            "mobile",
            "id_card",
        ]
    )


def _is_metadata_table(table_name: str) -> bool:
    clean = str(table_name or "").strip().lower()
    metadata_names = {
        "ddl_vector_documents",
        "sql_history_vector_documents",
        "app_detail",
        "yida_form_field_label_map",
        "ai五育管理平台_角色配置表",
    }
    metadata_tokens = ["field_label", "字段映射", "字段标签", "metadata", "schema"]
    return clean in metadata_names or clean.startswith("_") or any(token in clean for token in metadata_tokens)


def _dataset_id(table_name: str) -> str:
    clean = str(table_name or "").strip()
    if not clean:
        return "table"
    return "table_" + "".join(ch if ch.isalnum() or ch == "_" else "_" for ch in clean)


def _clean_identifier(value: str | None) -> str:
    clean = str(value or "").strip()
    if not clean:
        return ""
    return "".join(ch for ch in clean if ch.isalnum() or ch == "_")


def _quote_ident(value: str) -> str:
    return '"' + str(value or "").replace('"', '""') + '"'


def _quote_table_ref(schema_name: str, table_name: str) -> str:
    return f"{_quote_ident(schema_name)}.{_quote_ident(table_name)}"


def _cache_enabled() -> bool:
    return str(os.getenv("SCHOOL_SCHEMA_INDEX_CACHE_ENABLED", "1") or "1").strip().lower() in {"1", "true", "yes", "on"}


def _max_tables() -> int:
    try:
        return max(1, min(int(os.getenv("SCHOOL_SCHEMA_INDEX_MAX_TABLES", "400") or "400"), 1000))
    except Exception:
        return 400


def _max_fields_per_table() -> int:
    try:
        return max(1, min(int(os.getenv("SCHOOL_SCHEMA_INDEX_MAX_FIELDS_PER_TABLE", "160") or "160"), 500))
    except Exception:
        return 160
