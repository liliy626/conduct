from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import json
import os
import re
from typing import Any, Callable

from gateway_core.infra.db_pool import connect_db
from gateway_core.schema_context.ttl_cache import TTLCache


_DDL_CACHE = TTLCache(max_entries=256, ttl_seconds=300)


def _env_value(primary: str, legacy: str = "", default: str = "") -> str:
    value = os.getenv(primary, "").strip()
    if value:
        return value
    if legacy:
        value = os.getenv(legacy, "").strip()
        if value:
            return value
    return default


@dataclass(frozen=True)
class RetrievedDDLDocument:
    table_name: str
    business_description: str = ""
    ddl_context: str = ""
    similarity: float | None = None


@dataclass(frozen=True)
class RetrievedDDLContext:
    ddl: str = ""
    source: str = "none"
    schema_name: str = ""
    table_refs: list[str] = field(default_factory=list)
    documents: list[RetrievedDDLDocument] = field(default_factory=list)
    from_cache: bool = False
    cache_age_seconds: float = 0.0
    error: str = ""


def retrieve_lean_ddl_context(
    *,
    question: str,
    schema_name: str,
    dsn: str,
    psycopg_module: Any,
    embedding_fn: Callable[[str], list[float] | None] | None,
    vector_table: str = "ddl_vector_documents",
    top_k: int = 3,
    max_chars_per_doc: int = 2200,
) -> RetrievedDDLContext:
    clean_question = str(question or "").strip()
    clean_schema = _sanitize_identifier(schema_name)
    clean_vector_table = _sanitize_identifier(vector_table, fallback="ddl_vector_documents")
    clean_top_k = max(1, min(int(top_k or 3), 12))
    clean_max_chars = max(400, min(int(max_chars_per_doc or 2200), 8000))
    if not clean_question:
        return RetrievedDDLContext(source="schema_ddl_vector", schema_name=clean_schema, error="empty_question")
    if not clean_schema:
        return RetrievedDDLContext(source="schema_ddl_vector", error="empty_schema")
    if not str(dsn or "").strip() or psycopg_module is None or embedding_fn is None:
        return RetrievedDDLContext(source="schema_ddl_vector", schema_name=clean_schema, error="missing_db_or_embedding")

    cache_key = _cache_key(clean_schema, clean_vector_table, clean_top_k, clean_max_chars, clean_question)
    if _ddl_cache_enabled():
        hit = _DDL_CACHE.get(cache_key)
        if hit is not None and isinstance(hit.value, RetrievedDDLContext):
            return RetrievedDDLContext(
                ddl=hit.value.ddl,
                source=hit.value.source,
                schema_name=hit.value.schema_name,
                table_refs=hit.value.table_refs,
                documents=hit.value.documents,
                from_cache=True,
                cache_age_seconds=hit.age_seconds,
                error=hit.value.error,
            )

    try:
        with connect_db(psycopg_module, dsn) as conn:
            with conn.cursor() as cur:
                cur.execute("SET statement_timeout = 3000", [])
                columns = _load_vector_table_columns(cur, schema_name=clean_schema, table_name=clean_vector_table)
                embedding_col = _choose_column(columns, ["embedding", "vector", "content_embedding", "ddl_embedding"])
                vector_dim = _load_vector_column_dimension(
                    cur,
                    schema_name=clean_schema,
                    table_name=clean_vector_table,
                    column_name=embedding_col,
                )
                vector = _call_embedding_fn(embedding_fn, clean_question, dimensions=vector_dim)
                if not vector:
                    return RetrievedDDLContext(source="schema_ddl_vector", schema_name=clean_schema, error="embedding_empty")
                if vector_dim and len(vector) != vector_dim:
                    return RetrievedDDLContext(
                        source="schema_ddl_vector",
                        schema_name=clean_schema,
                        error=f"ddl_embedding_dimension_mismatch: query={len(vector)} table={vector_dim}",
                    )
                candidate_limit = _candidate_fetch_limit(clean_top_k)
                query_sql, params = _build_vector_search_sql(
                    schema_name=clean_schema,
                    table_name=clean_vector_table,
                    columns=columns,
                    vector=vector,
                    limit=candidate_limit,
                )
                if not query_sql:
                    context = _retrieve_information_schema_ddl_context(
                        cur,
                        question=clean_question,
                        schema_name=clean_schema,
                        top_k=clean_top_k,
                        max_chars=clean_max_chars,
                        reason="ddl_vector_documents_missing_required_columns",
                    )
                    if _ddl_cache_enabled():
                        _DDL_CACHE.set(cache_key, context)
                    return context
                cur.execute(query_sql, params)
                rows = cur.fetchall()
                keyword_sql, keyword_params = _build_keyword_search_sql(
                    schema_name=clean_schema,
                    table_name=clean_vector_table,
                    columns=columns,
                    question=clean_question,
                    limit=candidate_limit,
                )
                if keyword_sql:
                    cur.execute(keyword_sql, keyword_params)
                    rows = _merge_retrieved_rows(
                        rows,
                        cur.fetchall(),
                        question=clean_question,
                        schema_name=clean_schema,
                        limit=max(clean_top_k, 1),
                    )
                else:
                    rows = _merge_retrieved_rows(
                        rows,
                        [],
                        question=clean_question,
                        schema_name=clean_schema,
                        limit=max(clean_top_k, 1),
                    )
    except Exception as exc:
        try:
            with connect_db(psycopg_module, dsn) as conn:
                with conn.cursor() as cur:
                    cur.execute("SET statement_timeout = 3000", [])
                    context = _retrieve_information_schema_ddl_context(
                        cur,
                        question=clean_question,
                        schema_name=clean_schema,
                        top_k=clean_top_k,
                        max_chars=clean_max_chars,
                        reason=str(exc),
                    )
                    if _ddl_cache_enabled():
                        _DDL_CACHE.set(cache_key, context)
                    return context
        except Exception:
            return RetrievedDDLContext(source="schema_ddl_vector", schema_name=clean_schema, error=str(exc))

    documents: list[RetrievedDDLDocument] = []
    pieces: list[str] = []
    table_refs: list[str] = []
    for row in rows:
        table_name = str(row[0] if len(row) > 0 else "").strip()
        content = str(row[2] if len(row) > 2 else "")
        metadata = _loads_json(row[4] if len(row) > 4 else None)
        if not table_name:
            table_name = _table_name_from_metadata(metadata) or _table_name_from_text(content, schema_name=clean_schema)
        if not table_name:
            continue
        desc = str(row[1] if len(row) > 1 else "").strip()
        if not desc:
            desc = _description_from_metadata(metadata)
        lean = _lean_ddl(content, max_chars=clean_max_chars)
        similarity = _float_or_none(row[3] if len(row) > 3 else None)
        doc = RetrievedDDLDocument(
            table_name=table_name,
            business_description=desc,
            ddl_context=lean,
            similarity=similarity,
        )
        documents.append(doc)
        table_refs.append(f"{clean_schema}.{table_name}")
        pieces.append(_format_doc(schema_name=clean_schema, doc=doc))

    context = RetrievedDDLContext(
        ddl="\n---\n".join(pieces),
        source="schema_ddl_vector" if pieces else "schema_ddl_vector_empty",
        schema_name=clean_schema,
        table_refs=table_refs,
        documents=documents,
    )
    if _ddl_cache_enabled():
        _DDL_CACHE.set(cache_key, context)
    return context


def clear_ddl_retriever_cache() -> None:
    _DDL_CACHE.clear()


def _load_vector_table_columns(cur: Any, *, schema_name: str, table_name: str) -> set[str]:
    cur.execute(
        """
        SELECT column_name
        FROM information_schema.columns
        WHERE table_schema = %s AND table_name = %s
        """,
        [schema_name, table_name],
    )
    return {str(row[0] or "") for row in cur.fetchall()}


def _retrieve_information_schema_ddl_context(
    cur: Any,
    *,
    question: str,
    schema_name: str,
    top_k: int,
    max_chars: int,
    reason: str,
) -> RetrievedDDLContext:
    candidates = _search_information_schema_tables(
        cur,
        question=question,
        schema_name=schema_name,
        limit=max(int(top_k or 3) * 8, 24),
    )
    if not candidates:
        return RetrievedDDLContext(
            source="information_schema_empty",
            schema_name=schema_name,
            error=f"ddl_vector_unavailable: {reason}",
        )
    documents: list[RetrievedDDLDocument] = []
    pieces: list[str] = []
    table_refs: list[str] = []
    for table in candidates[: max(1, int(top_k or 3))]:
        table_name = str(table.get("table_name") or "").strip()
        if not table_name:
            continue
        columns = _load_information_schema_columns(cur, schema_name=schema_name, table_name=table_name)
        if not columns:
            continue
        lean = _format_information_schema_columns(columns, max_chars=max_chars)
        doc = RetrievedDDLDocument(
            table_name=table_name,
            business_description=str(table.get("description") or ""),
            ddl_context=lean,
            similarity=_float_or_none(table.get("score")),
        )
        documents.append(doc)
        table_refs.append(f"{schema_name}.{table_name}")
        pieces.append(_format_doc(schema_name=schema_name, doc=doc))
    return RetrievedDDLContext(
        ddl="\n---\n".join(pieces),
        source="information_schema_fallback" if pieces else "information_schema_empty",
        schema_name=schema_name,
        table_refs=table_refs,
        documents=documents,
        error="" if pieces else f"ddl_vector_unavailable: {reason}",
    )


def _search_information_schema_tables(
    cur: Any,
    *,
    question: str,
    schema_name: str,
    limit: int,
) -> list[dict[str, Any]]:
    terms = _keyword_terms(question)
    params: list[Any] = [schema_name]
    table_clauses: list[str] = []
    column_clauses: list[str] = []
    for term in terms:
        table_clauses.append("t.table_name ILIKE %s")
        params.append(f"%{term}%")
        column_clauses.append("c.column_name ILIKE %s")
        params.append(f"%{term}%")
    where_terms = ""
    if table_clauses or column_clauses:
        where_terms = "AND (" + " OR ".join([*table_clauses, *column_clauses]) + ")"
    params.append(max(1, min(int(limit or 24), 100)))
    cur.execute(
        f"""
        SELECT t.table_name,
               t.table_type,
               array_agg(c.column_name ORDER BY c.ordinal_position) AS column_names
        FROM information_schema.tables t
        JOIN information_schema.columns c
          ON c.table_schema = t.table_schema
         AND c.table_name = t.table_name
        WHERE t.table_schema = %s
          AND t.table_type IN ('BASE TABLE', 'VIEW')
          {where_terms}
        GROUP BY t.table_name, t.table_type
        LIMIT %s
        """,
        params,
    )
    rows = cur.fetchall()
    scored: list[dict[str, Any]] = []
    for row in rows:
        table_name = str(row[0] or "")
        table_type = str(row[1] or "")
        column_names = [str(item or "") for item in (row[2] or [])]
        score = _information_schema_match_score(table_name=table_name, column_names=column_names, question=question)
        scored.append(
            {
                "table_name": table_name,
                "table_type": table_type,
                "description": f"{table_type} from information_schema fallback",
                "score": score,
            }
        )
    return sorted(scored, key=lambda item: float(item.get("score") or 0.0), reverse=True)


def _load_information_schema_columns(cur: Any, *, schema_name: str, table_name: str) -> list[dict[str, str]]:
    cur.execute(
        """
        SELECT column_name, data_type, is_nullable
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
            "is_nullable": str(row[2] or ""),
        }
        for row in cur.fetchall()
    ]


def _format_information_schema_columns(columns: list[dict[str, str]], *, max_chars: int) -> str:
    lines = ["[STRUCTURE & SAMPLES]"]
    for column in columns:
        name = str(column.get("column_name") or "")
        dtype = str(column.get("data_type") or "")
        nullable = str(column.get("is_nullable") or "")
        lines.append(f'- "{name}" ({dtype}, nullable={nullable})')
    return _lean_ddl("\n".join(lines), max_chars=max_chars)


def _information_schema_match_score(*, table_name: str, column_names: list[str], question: str) -> float:
    haystack = f"{table_name} {' '.join(column_names)}".lower()
    score = 0.0
    for term in _keyword_terms(question):
        lowered = term.lower()
        if not lowered:
            continue
        if lowered in table_name.lower():
            score += 2.0
        if lowered in haystack:
            score += 0.7
    if any(token in question for token in ["成果", "申报", "学术", "发表", "论文", "亮点", "评优", "业绩"]):
        if "专业发展成果申报" in table_name:
            score += 5.0
        if all(token in haystack for token in ["名称", "申报人", "级别", "等第"]):
            score += 2.0
    if any(token in question for token in ["积分", "排名", "总分"]):
        if "积分" in table_name:
            score += 4.0
    return score


def _load_vector_column_dimension(cur: Any, *, schema_name: str, table_name: str, column_name: str) -> int | None:
    if not column_name:
        return None
    cur.execute(
        """
        SELECT format_type(a.atttypid, a.atttypmod)
        FROM pg_attribute a
        JOIN pg_class c ON c.oid = a.attrelid
        JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname = %s
          AND c.relname = %s
          AND a.attname = %s
          AND a.attnum > 0
          AND NOT a.attisdropped
        """,
        [schema_name, table_name, column_name],
    )
    row = cur.fetchone()
    type_text = str(row[0] if row else "")
    match = re.search(r"vector\s*\(\s*(\d+)\s*\)", type_text)
    if not match:
        return None
    try:
        return int(match.group(1))
    except Exception:
        return None


def _call_embedding_fn(embedding_fn: Callable[..., list[float] | None], text: str, *, dimensions: int | None) -> list[float] | None:
    try:
        return embedding_fn(text, dimensions=dimensions)
    except TypeError:
        return embedding_fn(text)


def _build_vector_search_sql(
    *,
    schema_name: str,
    table_name: str,
    columns: set[str],
    vector: list[float],
    limit: int,
) -> tuple[str, list[Any]]:
    embedding_col = _choose_column(columns, ["embedding", "vector", "content_embedding", "ddl_embedding"])
    content_col = _choose_column(columns, ["ddl_content", "content", "document", "page_content", "text", "ddl", "markdown"])
    if not embedding_col or not content_col:
        return "", []
    table_col = _choose_column(
        columns,
        ["table_name", "relation_name", "object_name", "source_table", "physical_table", "name"],
    )
    desc_col = _choose_column(
        columns,
        ["business_description", "description", "table_description", "summary", "comment", "title"],
    )
    metadata_col = _choose_column(columns, ["metadata", "meta", "extra", "payload"])
    select_table = f'"{table_col}"' if table_col else "''"
    select_desc = f'COALESCE("{desc_col}"::text, \'\')' if desc_col else "''"
    select_metadata = f'"{metadata_col}"' if metadata_col else "'{}'::jsonb"
    sql = f"""
        SELECT {select_table} AS table_name,
               {select_desc} AS business_description,
               "{content_col}"::text AS ddl_content,
               1 - ("{embedding_col}" <=> %s::vector) AS similarity,
               {select_metadata} AS metadata
        FROM "{schema_name}"."{table_name}"
        WHERE "{embedding_col}" IS NOT NULL
        ORDER BY "{embedding_col}" <=> %s::vector
        LIMIT %s
    """
    return sql, [json.dumps(vector), json.dumps(vector), limit]


def _build_keyword_search_sql(
    *,
    schema_name: str,
    table_name: str,
    columns: set[str],
    question: str,
    limit: int,
) -> tuple[str, list[Any]]:
    terms = _keyword_terms(question)
    if not terms:
        return "", []
    content_col = _choose_column(columns, ["ddl_content", "content", "document", "page_content", "text", "ddl", "markdown"])
    if not content_col:
        return "", []
    table_col = _choose_column(
        columns,
        ["table_name", "relation_name", "object_name", "source_table", "physical_table", "name"],
    )
    desc_col = _choose_column(
        columns,
        ["business_description", "description", "table_description", "summary", "comment", "title"],
    )
    metadata_col = _choose_column(columns, ["metadata", "meta", "extra", "payload"])
    select_table = f'"{table_col}"' if table_col else "''"
    select_desc = f'COALESCE("{desc_col}"::text, \'\')' if desc_col else "''"
    select_metadata = f'"{metadata_col}"' if metadata_col else "'{}'::jsonb"
    searchable = [f'COALESCE("{content_col}"::text, \'\')']
    if table_col:
        searchable.append(f'COALESCE("{table_col}"::text, \'\')')
    if desc_col:
        searchable.append(f'COALESCE("{desc_col}"::text, \'\')')
    haystack = " || ' ' || ".join(searchable)
    clauses = [f"({haystack}) ILIKE %s" for _term in terms]
    sql = f"""
        SELECT {select_table} AS table_name,
               {select_desc} AS business_description,
               "{content_col}"::text AS ddl_content,
               NULL::double precision AS similarity,
               {select_metadata} AS metadata
        FROM "{schema_name}"."{table_name}"
        WHERE {" OR ".join(clauses)}
        LIMIT %s
    """
    return sql, [f"%{term}%" for term in terms] + [limit]


def _merge_retrieved_rows(
    vector_rows: list[tuple[Any, ...]],
    keyword_rows: list[tuple[Any, ...]],
    *,
    question: str,
    schema_name: str,
    limit: int,
) -> list[tuple[Any, ...]]:
    by_table: dict[str, tuple[Any, ...]] = {}
    scores: dict[str, float] = {}
    for idx, row in enumerate(vector_rows or []):
        key = _row_key(row, schema_name=schema_name)
        if not key:
            continue
        by_table[key] = row
        similarity = _float_or_none(row[3] if len(row) > 3 else None) or 0.0
        scores[key] = max(
            scores.get(key, 0.0),
            similarity
            + max(0.0, 0.2 - idx * 0.01)
            + _keyword_match_score(row, question=question) * 0.25
            - _metadata_table_penalty(row, question=question, schema_name=schema_name)
            - _domain_mismatch_penalty(row, question=question, schema_name=schema_name),
        )
    for idx, row in enumerate(keyword_rows or []):
        key = _row_key(row, schema_name=schema_name)
        if not key:
            continue
        by_table[key] = row
        scores[key] = max(
            scores.get(key, 0.0),
            1.0
            + _keyword_match_score(row, question=question)
            - idx * 0.01
            - _metadata_table_penalty(row, question=question, schema_name=schema_name)
            - _domain_mismatch_penalty(row, question=question, schema_name=schema_name),
        )
    return [
        by_table[key]
        for key in sorted(by_table, key=lambda item: scores.get(item, 0.0), reverse=True)
    ][: max(1, int(limit or 1))]


def _row_key(row: tuple[Any, ...], *, schema_name: str) -> str:
    table_name = str(row[0] if len(row) > 0 else "").strip()
    metadata = _loads_json(row[4] if len(row) > 4 else None)
    content = str(row[2] if len(row) > 2 else "")
    return table_name or _table_name_from_metadata(metadata) or _table_name_from_text(content, schema_name=schema_name)


def _keyword_match_score(row: tuple[Any, ...], *, question: str) -> float:
    table_text = str(row[0] if len(row) > 0 else "").lower()
    desc_text = str(row[1] if len(row) > 1 else "").lower()
    content_text = str(row[2] if len(row) > 2 else "").lower()
    score = 0.0
    for term in _keyword_terms(question):
        lowered = term.lower()
        if not lowered:
            continue
        if lowered in table_text:
            score += 1.2
        if lowered in desc_text:
            score += 0.8
        if lowered in content_text:
            score += 0.15
    return score


def _domain_mismatch_penalty(row: tuple[Any, ...], *, question: str, schema_name: str) -> float:
    text = str(question or "")
    table_text = _row_key(row, schema_name=schema_name)
    desc_text = str(row[1] if len(row) > 1 else "")
    haystack = f"{table_text} {desc_text}".lower()
    asks_teacher = any(token in text for token in ["教师", "老师", "教职工"])
    asks_student = any(token in text for token in ["学生", "同学", "学生姓名"])
    penalty = 0.0
    if asks_teacher and "学生" in haystack and "教师" not in haystack and "教职工" not in haystack:
        penalty += 1.2
    if asks_student and ("教师" in haystack or "教职工" in haystack) and "学生" not in haystack:
        penalty += 1.2
    if asks_teacher and any(token in haystack for token in ["作品", "创作", "心理学生"]):
        penalty += 0.8
    return penalty


def _metadata_table_penalty(row: tuple[Any, ...], *, question: str, schema_name: str) -> float:
    if _metadata_question(question):
        return 0.0
    table_name = _row_key(row, schema_name=schema_name).lower()
    if not table_name:
        return 0.0
    metadata_tables = _metadata_table_names()
    if table_name in metadata_tables or table_name.rsplit(".", 1)[-1] in metadata_tables:
        return 2.0
    metadata_tokens = [
        "app_detail",
        "field_label",
        "字段映射",
        "字段标签",
        "配置",
        "角色配置",
        "metadata",
        "schema",
    ]
    return 1.25 if any(token in table_name for token in metadata_tokens) else 0.0


def _metadata_question(question: str) -> bool:
    text = str(question or "")
    return any(token in text for token in ["表结构", "字段", "DDL", "数据库结构", "有哪些表", "元数据", "schema"])


def _metadata_table_names() -> set[str]:
    default = "app_detail,yida_form_field_label_map,AI五育管理平台_角色配置表"
    raw = _env_value("SCHOOL_DDL_RETRIEVER_METADATA_TABLES", "TENANT_DDL_RETRIEVER_METADATA_TABLES", default)
    return {item.strip().lower() for item in raw.split(",") if item.strip()}


def _keyword_terms(question: str) -> list[str]:
    text = str(question or "").strip()
    raw_terms = [term.strip() for term in re.split(r"[\s,，。；;、]+", text) if len(term.strip()) >= 2]
    domain_terms: list[str] = []
    asks_teacher = any(token in text for token in ["教师", "老师", "教职工"])
    asks_student = any(token in text for token in ["学生", "同学", "学生姓名"])
    if "请假" in text:
        if asks_teacher and not asks_student:
            domain_terms.extend(["教师请假", "考勤管理_教师请假", "教师销假", "教职工", "老师", "考勤", "请假"])
        elif asks_student and not asks_teacher:
            domain_terms.extend(["学生请假", "学生考勤", "考勤", "请假"])
        else:
            domain_terms.extend(["请假", "教师请假", "学生请假", "考勤"])
    domain_rules = {
        "教师": ["教师", "老师"],
        "老师": ["教师", "老师"],
        "教职工": ["教师", "教职工", "老师", "教师请假", "考勤管理_教师请假"],
        "成果": ["成果", "成果申报", "专业发展成果申报", "申报", "名称", "级别", "等第", "主办单位"],
        "申报": ["申报", "成果申报", "专业发展成果申报", "申报人", "名称"],
        "学术发表": ["学术发表", "论文", "发表", "成果申报", "专业发展成果申报"],
        "学术": ["学术发表", "论文", "发表", "成果申报"],
        "发表": ["学术发表", "论文", "发表", "成果申报"],
        "论文": ["学术发表", "论文", "发表", "成果申报"],
        "亮点": ["成果", "荣誉", "级别", "等第", "积分", "主办单位"],
        "评优": ["成果", "荣誉", "级别", "等第", "主办单位"],
        "业绩": ["成果", "业绩", "荣誉", "级别", "等第", "主办单位"],
        "荣誉": ["荣誉", "成果", "级别", "等第", "主办单位"],
        "积分": ["积分", "可获得积分", "获得积分"],
        "学生": ["学生"],
        "报修": ["报修", "维修", "资产", "设施"],
        "维修": ["维修", "报修", "资产", "设施"],
        "德育": ["德育", "行规", "扣分"],
        "行规": ["行规", "德育", "扣分"],
        "文印": ["文印", "打印", "复印"],
        "执勤": ["执勤", "巡查", "异常"],
        "晨午检": ["晨午检", "健康"],
    }
    for marker, terms in domain_rules.items():
        if marker in text:
            domain_terms.extend(terms)
    out: list[str] = []
    for term in [*domain_terms, *raw_terms]:
        clean = term.strip()
        if clean and clean not in out:
            out.append(clean)
    return out[:14]


def _choose_column(columns: set[str], candidates: list[str]) -> str:
    lowered = {item.lower(): item for item in columns}
    for candidate in candidates:
        found = lowered.get(candidate.lower())
        if found:
            return found
    return ""


def _format_doc(*, schema_name: str, doc: RetrievedDDLDocument) -> str:
    lines = [
        f'Table: "{schema_name}"."{doc.table_name}"',
    ]
    if doc.business_description:
        lines.append(f"Desc: {doc.business_description}")
    if doc.similarity is not None:
        lines.append(f"Similarity: {doc.similarity:.4f}")
    if doc.ddl_context:
        lines.append(doc.ddl_context)
    return "\n".join(lines).strip()


def _lean_ddl(content: str, *, max_chars: int) -> str:
    text = str(content or "").strip()
    if not text:
        return ""
    marker_match = re.search(r"(?is)\[(?:STRUCTURE|COLUMNS|FIELDS|STRUCTURE\s*&\s*SAMPLES)[^\]]*\]", text)
    if marker_match:
        text = text[marker_match.end() :].strip()
    text = _drop_low_value_lines(text)
    if len(text) <= max_chars:
        return text
    return text[:max_chars].rstrip() + "\n... [DDL truncated]"


def _drop_low_value_lines(text: str) -> str:
    low_value_tokens = [
        "created_at",
        "updated_at",
        "create_time",
        "update_time",
        "gmt_create",
        "gmt_modified",
    ]
    kept: list[str] = []
    for line in str(text or "").splitlines():
        clean = line.strip()
        if not clean:
            continue
        lowered = clean.lower()
        if any(token in lowered for token in low_value_tokens) and "--" not in clean:
            continue
        kept.append(clean)
    return "\n".join(kept)


def _loads_json(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if value is None:
        return {}
    try:
        parsed = json.loads(str(value))
    except Exception:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _table_name_from_metadata(metadata: dict[str, Any]) -> str:
    for key in ["table_name", "relation_name", "object_name", "source_table", "physical_table", "name"]:
        value = str(metadata.get(key) or "").strip()
        if value:
            return value.rsplit(".", 1)[-1].strip('"')
    return ""


def _description_from_metadata(metadata: dict[str, Any]) -> str:
    for key in ["business_description", "description", "table_description", "summary", "comment", "title"]:
        value = str(metadata.get(key) or "").strip()
        if value:
            return value
    return ""


def _table_name_from_text(text: str, *, schema_name: str) -> str:
    raw = str(text or "")
    patterns = [
        rf'(?is)\b(?:CREATE\s+(?:TABLE|VIEW)|Table:)\s+"?{re.escape(schema_name)}"?\s*\.\s*"([^"]+)"',
        rf"(?is)\b(?:CREATE\s+(?:TABLE|VIEW)|Table:)\s+{re.escape(schema_name)}\.([A-Za-z_][A-Za-z0-9_\u4e00-\u9fff]*)",
        r'(?is)\btable_name\s*[:=]\s*"?([A-Za-z_][A-Za-z0-9_\u4e00-\u9fff]*)"?',
        r'(?is)\b表名\s*[:：]\s*"?([A-Za-z_][A-Za-z0-9_\u4e00-\u9fff]*)"?',
    ]
    for pattern in patterns:
        match = re.search(pattern, raw)
        if match:
            return str(match.group(1) or "").strip()
    return ""


def _cache_key(schema_name: str, vector_table: str, top_k: int, max_chars: int, question: str) -> str:
    raw = json.dumps(
        {
            "schema": schema_name,
            "table": vector_table,
            "top_k": top_k,
            "max_chars": max_chars,
            "question": question,
        },
        ensure_ascii=False,
        sort_keys=True,
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _candidate_fetch_limit(top_k: int) -> int:
    try:
        base = int(top_k or 3)
    except Exception:
        base = 3
    return max(base, min(max(base * 80, 500), 1000))


def _ddl_cache_enabled() -> bool:
    return _env_value("SCHOOL_DDL_RETRIEVER_CACHE_ENABLED", "TENANT_DDL_RETRIEVER_CACHE_ENABLED", "1").lower() in {"1", "true", "yes", "on"}


def _sanitize_identifier(value: str, *, fallback: str = "") -> str:
    clean = str(value or "").strip()
    if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", clean):
        return clean
    return fallback


def _float_or_none(value: Any) -> float | None:
    try:
        return float(value)
    except Exception:
        return None
