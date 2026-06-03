from __future__ import annotations

import json
import re
from typing import Any

from gateway_core.infra.utils import dedupe as dedupe_strings
from gateway_core.infra.utils import env_value
from gateway_core.infra.utils import loads_json_object
from gateway_core.infra.utils import truncate as _truncate


TECHNICAL_FIELD_TOKENS = {
    "id",
    "uuid",
    "tenant_id",
    "__tenant_id",
    "__instance_id",
    "__form_id",
    "__process_instance_id",
    "__creator_id",
    "__updater_id",
    "source_instance_id",
    "instance_id",
    "form_id",
    "process_instance_id",
    "creator_id",
    "updater_id",
    "originator_user_id",
    "originator_userid",
    "owner_user_id",
    "owner_userid",
    "modifier_user_id",
    "modifier_userid",
    "created_at",
    "updated_at",
    "gmt_create",
    "gmt_modified",
    "gmt_create_time",
    "gmt_modified_time",
    "sync_time",
    "__sync_time",
    "deleted_flag",
    "__deleted_flag",
    "raw_json",
    "__raw_json",
    "原始json",
    "实例id",
    "表单id",
    "流程实例id",
    "租户id",
    "同步时间",
    "创建时间",
    "更新时间",
    "删除标记",
    "内部编码",
}

TECHNICAL_FIELD_CONTAINS = (
    "sourceinstanceid",
    "processinstanceid",
    "instanceid",
    "formid",
    "rawjson",
    "userid",
    "user_id",
    "originatoruserid",
    "owneruserid",
    "modifieruserid",
    "tenantid",
    "sync",
    "gmtcreate",
    "gmtmodified",
    "deletedflag",
)

NON_BUSINESS_IDENTIFIER_ALLOWLIST = {
    "__instance_time",
    "__created_time",
    "__modified_time",
    "__title",
    "__status",
}


def normalized_field_token(value: str) -> str:
    return "".join(str(value or "").strip().lower().replace("-", "_").split())


def is_technical_field_name(value: str) -> bool:
    normalized = normalized_field_token(value)
    if normalized in TECHNICAL_FIELD_TOKENS:
        return True
    return any(token in normalized for token in TECHNICAL_FIELD_CONTAINS)


def is_non_business_identifier(value: str) -> bool:
    clean = str(value or "").strip()
    lower = clean.lower()
    if lower in {"__raw_row_json", "__raw_value_json"}:
        return True
    if lower.startswith("__") and lower not in NON_BUSINESS_IDENTIFIER_ALLOWLIST:
        return True
    return any(token in clean for token in ["原始", "raw", "Raw", "RAW", "审批记录"])


def execute_query(*, psycopg_module: Any, dsn: str, sql: str, params: list[Any]) -> list[dict[str, Any]]:
    with psycopg_module.connect(dsn, connect_timeout=3) as conn:
        with conn.cursor() as cur:
            cur.execute("SET statement_timeout = 5000")
            if params:
                cur.execute(sql, params)
            else:
                cur.execute(sql)
            column_names = [column_name(col) for col in (cur.description or [])]
            return [
                {column_names[idx] if idx < len(column_names) else f"col_{idx + 1}": value for idx, value in enumerate(row)}
                for row in cur.fetchall()
            ]


def list_available_tables(
    *,
    psycopg_module: Any,
    dsn: str,
    schema_name: str,
    query: str,
    limit: int,
) -> list[dict[str, Any]]:
    terms = search_terms(query)
    clean_limit = max(1, min(int(limit or 30), 100))
    with psycopg_module.connect(dsn, connect_timeout=3) as conn:
        with conn.cursor() as cur:
            cur.execute("SET statement_timeout = 5000")
            params: list[Any] = [schema_name]
            where = ["table_schema = %s", "table_type IN ('BASE TABLE', 'VIEW')"]
            if terms:
                clauses = []
                for term in terms:
                    clauses.append("table_name ILIKE %s")
                    params.append(f"%{term}%")
                where.append("(" + " OR ".join(clauses) + ")")
            params.append(clean_limit)
            cur.execute(
                f"""
                SELECT table_schema, table_name, table_type
                FROM information_schema.tables
                WHERE {" AND ".join(where)}
                ORDER BY
                  CASE WHEN table_name ILIKE %s THEN 0 ELSE 1 END,
                  table_name
                LIMIT %s
                """,
                [*params[:-1], f"%{str(query or '').strip()}%", params[-1]],
            )
            return [
                {
                    "schema_name": str(row[0] or ""),
                    "table_name": str(row[1] or ""),
                    "table_type": str(row[2] or ""),
                    "table_ref": f"{row[0]}.{row[1]}",
                }
                for row in cur.fetchall()
            ]


def inspect_table_columns(
    *,
    psycopg_module: Any,
    dsn: str,
    schema_name: str,
    table_name: str,
) -> list[dict[str, Any]]:
    with psycopg_module.connect(dsn, connect_timeout=3) as conn:
        with conn.cursor() as cur:
            cur.execute("SET statement_timeout = 5000")
            cur.execute(
                """
                SELECT column_name, data_type, udt_name, is_nullable
                FROM information_schema.columns
                WHERE table_schema = %s AND table_name = %s
                ORDER BY ordinal_position
                """,
                [schema_name, table_name],
            )
            return [
                {
                    "column_name": str(row[0] or ""),
                    "data_type": str(row[1] or ""),
                    "udt_name": str(row[2] or ""),
                    "nullable": str(row[3] or "").upper() == "YES",
                    "is_json_or_array": column_type_is_json_or_array(str(row[1] or ""), str(row[2] or "")),
                }
                for row in cur.fetchall()
            ]


def load_table_ddl_summary(
    *,
    psycopg_module: Any,
    dsn: str,
    schema_name: str,
    table_name: str,
    vector_table: str,
) -> str:
    try:
        with psycopg_module.connect(dsn, connect_timeout=3) as conn:
            with conn.cursor() as cur:
                cur.execute("SET statement_timeout = 5000")
                cur.execute(
                    f"""
                    SELECT COALESCE(content, '')::text
                    FROM {quote_table_ref(schema_name, vector_table)}
                    WHERE table_name = %s OR object_name = %s
                    LIMIT 1
                    """,
                    [table_name, table_name],
                )
                row = cur.fetchone()
                return truncate_text(str(row[0] if row else ""), 2400)
    except Exception:
        return ""


def column_type_is_json_or_array(data_type: str, udt_name: str) -> bool:
    text = f"{data_type} {udt_name}".lower()
    return "json" in text or "array" in text or text.startswith("_")


def requires_json_sample_before_aggregate(sql: str) -> bool:
    return uses_json_or_array_expression(sql) and uses_aggregate(sql) and not is_json_or_array_sample_query(sql)


def is_json_or_array_sample_query(sql: str) -> bool:
    clean = str(sql or "")
    return uses_json_or_array_expression(clean) and not uses_aggregate(clean)


def is_limited_non_aggregate_query(sql: str) -> bool:
    clean = str(sql or "")
    return bool(re.search(r"(?is)\blimit\s+\d+\b", clean)) and not uses_aggregate(clean)


def uses_json_or_array_expression(sql: str) -> bool:
    clean = str(sql or "")
    return bool(
        re.search(
            r"(?is)(->>|->|\bjsonb?_|\bjsonb?_array_elements\b|\bunnest\s*\(|\barray_length\s*\(|\bcardinality\s*\()",
            clean,
        )
    )


def uses_aggregate(sql: str) -> bool:
    return bool(re.search(r"(?is)\b(count|sum|avg|min|max|string_agg|array_agg|jsonb?_agg)\s*\(|\bgroup\s+by\b|\bdistinct\b", sql))


def json_sample_sql_hint(sql: str) -> str:
    refs = re.findall(r'(?is)\bfrom\s+((?:"[^"]+"|[A-Za-z_][A-Za-z0-9_\u4e00-\u9fff]*)(?:\s*\.\s*(?:"[^"]+"|[A-Za-z_][A-Za-z0-9_\u4e00-\u9fff]*))?)', str(sql or ""))
    table_ref = refs[0].strip() if refs else "同一张表"
    fields = []
    for match in re.finditer(r'(?is)("([^"]+)"|[A-Za-z_][A-Za-z0-9_\u4e00-\u9fff]*)\s*(?:->>|->)', str(sql or "")):
        field = match.group(1)
        if field and field not in fields:
            fields.append(field)
    selected = ", ".join(fields[:3]) if fields else "原始 JSON/数组字段"
    return f"SELECT {selected} FROM {table_ref} LIMIT 5"


def school_table_ref(value: str, *, schema_name: str) -> tuple[str, str] | None:
    parts = split_table_ref(value)
    if not parts:
        return None
    if len(parts) == 1:
        schema = str(schema_name or "").strip()
        table = parts[0]
    else:
        schema, table = parts[-2], parts[-1]
    if normalize_ref(schema) != normalize_ref(schema_name):
        return None
    if not valid_identifier(schema) or not valid_identifier(table):
        return None
    return schema, table


tenant_table_ref = school_table_ref


def coerce_tool_text(value: Any, *, keys: list[str]) -> str:
    if isinstance(value, dict):
        for key in keys:
            text = str(value.get(key) or "").strip()
            if text:
                return text
    text = str(value or "").strip()
    if text.startswith("{") and text.endswith("}"):
        try:
            parsed = json.loads(text)
        except Exception:
            parsed = None
        if isinstance(parsed, dict):
            for key in keys:
                found = str(parsed.get(key) or "").strip()
                if found:
                    return found
    return text


def split_table_ref(value: str) -> list[str]:
    raw = str(value or "").strip()
    if not raw:
        return []
    quoted = re.findall(r'"([^"]+)"', raw)
    if quoted:
        return [item.strip() for item in quoted if item.strip()]
    return [part.strip().strip('"') for part in raw.split(".") if part.strip()]


def valid_identifier(value: str) -> bool:
    return bool(re.fullmatch(r"[A-Za-z_\u4e00-\u9fff][A-Za-z0-9_\u4e00-\u9fff]*", str(value or "").strip()))


def quote_ident(value: str) -> str:
    return '"' + str(value or "").replace('"', '""') + '"'


def quote_table_ref(schema_name: str, table_name: str) -> str:
    return f"{quote_ident(schema_name)}.{quote_ident(table_name)}"


def normalize_ref(value: str) -> str:
    return ".".join(part.strip().strip('"').lower() for part in str(value or "").replace(" ", "").split(".") if part.strip())


def search_terms(query: str) -> list[str]:
    text = str(query or "").strip()
    terms = [item.strip() for item in re.split(r"[\s,，。；;、]+", text) if len(item.strip()) >= 2]
    if "请假" in text:
        terms.extend(["请假", "教师请假", "学生请假", "考勤"])
    if any(token in text for token in ["教师", "老师", "教职工"]):
        terms.extend(["教师", "老师", "教职工"])
    if any(token in text for token in ["报修", "维修", "设施"]):
        terms.extend(["报修", "维修", "设施", "资产"])
    if any(token in text for token in ["德育", "行规", "扣分"]):
        terms.extend(["德育", "行规", "扣分"])
    out: list[str] = []
    for term in terms:
        if term and term not in out:
            out.append(term)
    return out[:16]


def truncate_text(text: str, limit: int = 1200) -> str:
    return _truncate(text, limit, strip=True, rstrip=True)


def format_rows(rows: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, str]]:
    labels = {"record_count": "记录数", "day": "日期", "week": "周", "month": "月份"}
    formatted_rows: list[dict[str, Any]] = []
    field_labels: dict[str, str] = {}
    for row in rows:
        formatted_row: dict[str, Any] = {}
        for key, value in row.items():
            label = labels.get(str(key), str(key))
            unique_label = dedupe_label(label, formatted_row)
            formatted_row[unique_label] = value
            field_labels[str(key)] = unique_label
        formatted_rows.append(formatted_row)
    return formatted_rows, field_labels


def dedupe_label(label: str, existing: dict[str, Any]) -> str:
    if label not in existing:
        return label
    index = 2
    while f"{label}{index}" in existing:
        index += 1
    return f"{label}{index}"


def column_name(column: Any) -> str:
    name = getattr(column, "name", None)
    if name:
        return str(name)
    if isinstance(column, (tuple, list)) and column:
        return str(column[0])
    return str(column or "")


def raw_rows_handle(trace: Any, task_id: str) -> str:
    trace_id = str(getattr(trace, "trace_id", "") or "").strip()
    clean_task_id = str(task_id or "").strip() or "task"
    return f"trace://{trace_id}/task/{clean_task_id}/raw_rows" if trace_id else f"trace://current/task/{clean_task_id}/raw_rows"


def related_ddl_query(*, value: str, domains: list[str]) -> str:
    domain_text = " ".join(domains)
    if any(token in domain_text for token in ["repair", "asset"]):
        return f"{value} 报修 维修 资产 设施 设备"
    if "leave" in domain_text:
        return f"{value} 请假 健康 晨午检"
    if "moral" in domain_text:
        return f"{value} 德育 行规 扣分 班级"
    return f"{value} 相关业务记录"


def ddl_vector_table() -> str:
    return env_value("SCHOOL_DDL_VECTOR_TABLE", "TENANT_DDL_VECTOR_TABLE", "ddl_vector_documents") or "ddl_vector_documents"


def table_search_limit() -> int:
    try:
        return max(5, min(int(env_value("SCHOOL_DDL_TABLE_SEARCH_LIMIT", "TENANT_DDL_TABLE_SEARCH_LIMIT", "40") or "40"), 100))
    except Exception:
        return 40


def ddl_top_k() -> int:
    try:
        return max(1, min(int(env_value("SCHOOL_DDL_RETRIEVER_TOP_K", "TENANT_DDL_RETRIEVER_TOP_K", "8") or "8"), 12))
    except Exception:
        return 8


def ddl_max_chars_per_doc() -> int:
    try:
        return max(400, min(int(env_value("SCHOOL_DDL_MAX_CHARS_PER_DOC", "TENANT_DDL_MAX_CHARS_PER_DOC", "2200") or "2200"), 8000))
    except Exception:
        return 2200


def raw_sql_max_rows() -> int:
    try:
        return max(1, min(int(env_value("SCHOOL_RAW_SQL_MAX_ROWS", "TENANT_RAW_SQL_MAX_ROWS", "100") or "100"), 1000))
    except Exception:
        return 100
