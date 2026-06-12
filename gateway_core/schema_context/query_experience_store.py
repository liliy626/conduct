from __future__ import annotations

import hashlib
import json
import os
import re
import time
from typing import Any, Callable

from gateway_core.infra.china_llm_defaults import resolve_embedding_dimension
from gateway_core.infra.db_pool import connect_db


CREATE_EXPERIENCE_TABLE_TEMPLATE = """
CREATE TABLE IF NOT EXISTS "{schema}"."{table}" (
    id                TEXT PRIMARY KEY,
    question_text     TEXT NOT NULL,
    sql_text          TEXT NOT NULL,
    sql_fingerprint   TEXT NOT NULL,
    table_refs        JSONB NOT NULL DEFAULT '[]'::jsonb,
    column_refs       JSONB NOT NULL DEFAULT '[]'::jsonb,
    row_count         INTEGER NOT NULL DEFAULT 0,
    used_count        INTEGER NOT NULL DEFAULT 1,
    last_used_at      DOUBLE PRECISION NOT NULL DEFAULT 0,
    created_at        DOUBLE PRECISION NOT NULL DEFAULT 0,
    embedding_text    TEXT NOT NULL DEFAULT '',
    embedding         vector({vector_dim}),
    is_active         BOOLEAN NOT NULL DEFAULT TRUE,
    guardrail_version TEXT NOT NULL DEFAULT ''
)
"""


def init_experience_table(
    *,
    dsn: str,
    psycopg_module: Any,
    schema: str = "platform",
    table: str = "sql_history_vector_documents",
    vector_dim: int | None = None,
) -> bool:
    if not str(dsn or "").strip() or psycopg_module is None:
        return False
    safe_schema = _sanitize_identifier(schema)
    safe_table = _sanitize_identifier(table, fallback="sql_history_vector_documents")
    dim = int(vector_dim or _resolve_vector_dim())
    try:
        with connect_db(psycopg_module, dsn) as conn:
            with conn.cursor() as cur:
                cur.execute("CREATE EXTENSION IF NOT EXISTS vector")
                cur.execute(f'CREATE SCHEMA IF NOT EXISTS "{safe_schema}"')
                cur.execute(CREATE_EXPERIENCE_TABLE_TEMPLATE.format(schema=safe_schema, table=safe_table, vector_dim=dim))
                cur.execute(
                    f"""
                    CREATE UNIQUE INDEX IF NOT EXISTS idx_{safe_table}_fingerprint
                    ON "{safe_schema}"."{safe_table}" (sql_fingerprint)
                    """
                )
                cur.execute(
                    f"""
                    CREATE INDEX IF NOT EXISTS idx_{safe_table}_active_last_used
                    ON "{safe_schema}"."{safe_table}" (is_active, last_used_at DESC)
                    """
                )
                _create_vector_index(cur, schema=safe_schema, table=safe_table)
                conn.commit()
        return True
    except Exception:
        return False


def search_query_experiences(
    *,
    question: str,
    tenant_id: str,
    dsn: str,
    psycopg_module: Any,
    embedding_fn: Callable[[str], list[float] | None] | None = None,
    schema: str = "platform",
    table: str = "sql_history_vector_documents",
    limit: int = 5,
) -> list[dict[str, Any]]:
    if not str(question or "").strip() or not str(dsn or "").strip() or psycopg_module is None:
        return []
    safe_schema = _sanitize_identifier(schema)
    safe_table = _sanitize_identifier(table, fallback="sql_history_vector_documents")
    clean_limit = max(1, min(int(limit or 5), 10))
    vector = embedding_fn(str(question).strip()) if embedding_fn is not None else None
    if not init_experience_table(dsn=dsn, psycopg_module=psycopg_module, schema=safe_schema, table=safe_table):
        return []
    try:
        with connect_db(psycopg_module, dsn) as conn:
            with conn.cursor() as cur:
                if vector:
                    cur.execute(
                        f"""
                        SELECT question_text, sql_text, table_refs, column_refs, row_count,
                               used_count, guardrail_version,
                               1 - (embedding <=> %s::vector) AS similarity
                        FROM "{safe_schema}"."{safe_table}"
                        WHERE is_active = TRUE AND embedding IS NOT NULL
                        ORDER BY embedding <=> %s::vector
                        LIMIT %s
                        """,
                        [json.dumps(vector), json.dumps(vector), clean_limit],
                    )
                else:
                    cur.execute(
                        f"""
                        SELECT question_text, sql_text, table_refs, column_refs, row_count,
                               used_count, guardrail_version, 0 AS similarity
                        FROM "{safe_schema}"."{safe_table}"
                        WHERE is_active = TRUE AND question_text ILIKE %s
                        ORDER BY last_used_at DESC
                        LIMIT %s
                        """,
                        [f"%{_keyword_probe(question)}%", clean_limit],
                    )
                rows = cur.fetchall()
    except Exception:
        return []
    return [_experience_row_to_dict(row) for row in rows]


def record_query_experience(
    *,
    tenant_id: str,
    question: str,
    selected_path: str = "",
    used_datasets: list[dict[str, Any]] | list[str] | None = None,
    raw_sql: str = "",
    json_plan: dict[str, Any] | None = None,
    row_count: int = 0,
    answer_summary: str = "",
    success_score: float = 0.0,
    dsn: str,
    psycopg_module: Any,
    embedding_fn: Callable[[str], list[float] | None] | None = None,
    schema: str = "platform",
    table: str = "sql_history_vector_documents",
    table_refs: list[str] | None = None,
    column_refs: list[str] | None = None,
    guardrail_version: str = "",
) -> bool:
    del tenant_id, selected_path, json_plan, answer_summary, success_score
    clean_sql = str(raw_sql or "").strip()
    clean_question = str(question or "").strip()
    if not clean_question or not clean_sql or not str(dsn or "").strip() or psycopg_module is None:
        return False
    if not should_record_sql_history(sql=clean_sql, row_count=row_count):
        return False
    safe_schema = _sanitize_identifier(schema)
    safe_table = _sanitize_identifier(table, fallback="sql_history_vector_documents")
    refs = table_refs if table_refs is not None else _table_refs_from_used_datasets(used_datasets)
    cols = column_refs if column_refs is not None else extract_column_refs(clean_sql)
    if not refs:
        return False
    if not init_experience_table(dsn=dsn, psycopg_module=psycopg_module, schema=safe_schema, table=safe_table):
        return False
    fingerprint = sql_fingerprint(clean_sql)
    embedding_text = build_embedding_text(question=clean_question, table_refs=refs, column_refs=cols)
    vector = embedding_fn(embedding_text) if embedding_fn is not None else None
    now = time.time()
    item_id = hashlib.sha256(f"{safe_schema}:{fingerprint}".encode("utf-8")).hexdigest()[:32]
    try:
        with connect_db(psycopg_module, dsn) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    INSERT INTO "{safe_schema}"."{safe_table}"
                        (id, question_text, sql_text, sql_fingerprint, table_refs, column_refs,
                         row_count, used_count, last_used_at, created_at, embedding_text,
                         embedding, is_active, guardrail_version)
                    VALUES (%s, %s, %s, %s, %s::jsonb, %s::jsonb, %s, 1, %s, %s, %s, %s::vector, TRUE, %s)
                    ON CONFLICT (sql_fingerprint) DO UPDATE SET
                        used_count = "{safe_table}".used_count + 1,
                        last_used_at = EXCLUDED.last_used_at,
                        row_count = EXCLUDED.row_count,
                        question_text = EXCLUDED.question_text
                    """,
                    [
                        item_id,
                        clean_question,
                        clean_sql,
                        fingerprint,
                        json.dumps(refs, ensure_ascii=False),
                        json.dumps(cols, ensure_ascii=False),
                        int(row_count or 0),
                        now,
                        now,
                        embedding_text,
                        json.dumps(vector) if vector else None,
                        guardrail_version or "v1",
                    ],
                )
                conn.commit()
        return True
    except Exception:
        return False


def should_record_sql_history(*, sql: str, row_count: int) -> bool:
    clean = str(sql or "").strip()
    if not clean or row_count < 0:
        return False
    if re.search(r"(?is)^\s*select\s+\*\s+from\b", clean) and re.search(r"(?is)\blimit\s+([1-9]|10)\b", clean):
        return False
    if re.search(r"(?is)(->>|->|\bjsonb?_|\bunnest\s*\()", clean) and not re.search(
        r"(?is)\b(count|sum|avg|min|max|string_agg|array_agg|jsonb?_agg)\s*\(|\bgroup\s+by\b", clean
    ):
        return False
    return bool(re.match(r"(?is)^\s*select\b", clean))


def sql_fingerprint(sql: str) -> str:
    normalized = normalize_sql_for_fingerprint(sql)
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def normalize_sql_for_fingerprint(sql: str) -> str:
    text = str(sql or "").strip().lower()
    text = re.sub(r"'(?:''|[^'])*'", "?", text)
    text = re.sub(r'"(?:[^"]|"")*"', lambda m: m.group(0).lower(), text)
    text = re.sub(r"\b\d{4}-\d{1,2}-\d{1,2}\b", "?", text)
    text = re.sub(r"\b\d+(?:\.\d+)?\b", "?", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip().rstrip(";")


def build_embedding_text(*, question: str, table_refs: list[str], column_refs: list[str]) -> str:
    return "\n".join(
        [
            str(question or "").strip(),
            "tables: " + ", ".join(str(item) for item in table_refs[:8]),
            "columns: " + ", ".join(str(item) for item in column_refs[:20]),
        ]
    ).strip()


def extract_column_refs(sql: str) -> list[str]:
    identifiers = re.findall(r'"([^"]+)"', str(sql or ""))
    out: list[str] = []
    for ident in identifiers:
        if ident and ident not in out:
            out.append(ident)
    return out[:40]


def _experience_row_to_dict(row: Any) -> dict[str, Any]:
    if len(row) >= 9 and not _is_int_like(row[4]):
        return {
            "question": str(row[0] or ""),
            "raw_sql": _clip_sql(row[3]),
            "table_refs": _loads_json(row[2], fallback=[]),
            "column_refs": extract_column_refs(str(row[3] or ""))[:20],
            "row_count": int(row[5] or 0),
            "used_count": 0,
            "guardrail_version": "",
            "similarity": float(row[8] or 0.0),
            "answer_summary": str(row[6] or "") or "历史 SQL 仅供参考；必须按当前问题、当前 DDL 和当前时间口径重写。",
        }
    return {
        "question": str(row[0] or ""),
        "raw_sql": _clip_sql(row[1]),
        "table_refs": _loads_json(row[2], fallback=[]),
        "column_refs": _loads_json(row[3], fallback=[])[:20],
        "row_count": int(row[4] or 0),
        "used_count": int(row[5] or 0),
        "guardrail_version": str(row[6] or ""),
        "similarity": float(row[7] or 0.0),
        "answer_summary": "历史 SQL 仅供参考；必须按当前问题、当前 DDL 和当前时间口径重写。",
    }


def _is_int_like(value: Any) -> bool:
    try:
        int(value or 0)
        return True
    except Exception:
        return False


def _clip_sql(value: Any) -> str:
    clean = str(value or "").strip()
    try:
        limit = max(300, min(int(os.getenv("SQL_HISTORY_RECALL_SQL_MAX_CHARS", "900") or "900"), 3000))
    except Exception:
        limit = 900
    if len(clean) <= limit:
        return clean
    return clean[:limit].rstrip() + " ...[truncated]"


def _table_refs_from_used_datasets(used_datasets: list[dict[str, Any]] | list[str] | None) -> list[str]:
    refs: list[str] = []
    for item in used_datasets or []:
        if isinstance(item, str):
            ref = item
        elif isinstance(item, dict):
            schema = str(item.get("source_schema") or "").strip()
            view = str(item.get("source_view") or "").strip()
            ref = f"{schema}.{view}" if schema and view else view
        else:
            ref = ""
        if ref and ref not in refs:
            refs.append(ref)
    return refs


def _create_vector_index(cur: Any, *, schema: str, table: str) -> None:
    index_name = f"idx_{table}_embedding"
    cur.execute("SAVEPOINT sql_history_vector_index")
    try:
        cur.execute(
            f"""
            CREATE INDEX IF NOT EXISTS {index_name}_hnsw
            ON "{schema}"."{table}" USING hnsw (embedding vector_cosine_ops)
            """
        )
        cur.execute("RELEASE SAVEPOINT sql_history_vector_index")
    except Exception:
        cur.execute("ROLLBACK TO SAVEPOINT sql_history_vector_index")
        cur.execute(
            f"""
            CREATE INDEX IF NOT EXISTS {index_name}_ivfflat
            ON "{schema}"."{table}"
                USING ivfflat (embedding vector_cosine_ops) WITH (lists = 100)
            """
        )
        cur.execute("RELEASE SAVEPOINT sql_history_vector_index")


def _keyword_probe(question: str) -> str:
    tokens = re.findall(r"[\w\u4e00-\u9fff]{2,}", str(question or ""))
    return tokens[0] if tokens else str(question or "")[:12]


def _loads_json(value: Any, *, fallback: Any) -> Any:
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(str(value or ""))
    except Exception:
        return fallback


def _resolve_vector_dim(default_dim: int = 1024) -> int:
    raw = (
        os.getenv("SQL_HISTORY_VECTOR_DIM", "").strip()
        or os.getenv("SCHOOL_QUERY_EXPERIENCE_VECTOR_DIM", "").strip()
        or os.getenv("TENANT_QUERY_EXPERIENCE_VECTOR_DIM", "").strip()
        or os.getenv("RAG_EMBEDDING_SIZE", "").strip()
    )
    return resolve_embedding_dimension(
        raw,
        model=os.getenv("RAG_EMBED_MODEL", "embedding-3").strip(),
        base_url=os.getenv("RAG_EMBED_BASE_URL", "").strip(),
        default_embedding_size=default_dim,
    )


def _sanitize_identifier(value: str, fallback: str = "platform") -> str:
    candidate = str(value or "").strip() or fallback
    cleaned = candidate.replace('"', "")
    return cleaned if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", cleaned) else fallback
