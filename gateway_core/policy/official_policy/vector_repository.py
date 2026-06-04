from __future__ import annotations

import json
import logging
import re
from typing import Any, Callable

from gateway_core.infra.db_pool import connect_db
from gateway_core.policy.official_policy.keyword_search import (
    extract_keywords,
    keyword_params,
    keyword_score,
    keyword_sql,
    page_date_filter,
    page_date_params,
    row_to_candidate,
)

LOGGER = logging.getLogger("gateway.official_policy_vector_repository")


def _sanitize_identifier(value: str, fallback: str = "public") -> str:
    candidate = str(value or "").strip() or fallback
    cleaned = candidate.replace('"', "")
    return cleaned if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", cleaned) else fallback


def _quote_ident(value: str, fallback: str) -> str:
    clean = _sanitize_identifier(value, fallback=fallback)
    return f'"{clean}"'


def search_official_policy_chunks(
    *,
    question: str,
    embedding_fn: Callable[[str], list[float] | None] | None,
    dsn: str,
    psycopg_module: Any,
    schema: str = "public",
    table: str = "policy_chunks",
    embedding_table: str = "",
    page_table: str = "",
    limit: int = 5,
    min_similarity: float = 0.55,
    min_relevance: float = 0.35,
    id_column: str = "id",
    title_column: str = "title",
    chunk_column: str = "chunk_text",
    url_column: str = "official_url",
    source_column: str = "source_name",
    embedding_column: str = "embedding",
    chunk_id_column: str = "chunk_id",
    page_id_column: str = "page_id",
    published_start: str = "",
    published_end: str = "",
) -> list[dict[str, Any]]:
    """Search policy chunks with vector, keyword and topic-tag signals."""
    clean_question = str(question or "").strip()
    if not clean_question or not dsn or psycopg_module is None:
        return []
    keywords = extract_keywords(clean_question)
    try:
        vec = embedding_fn(clean_question) if embedding_fn is not None else None
        safe_schema = _quote_ident(schema, "public")
        safe_table = _quote_ident(table, "policy_chunks")
        safe_embedding_table = _quote_ident(embedding_table, "policy_chunk_embeddings_1024") if str(embedding_table or "").strip() else ""
        safe_page_table = _quote_ident(page_table, "policy_pages") if str(page_table or "").strip() else ""
        id_col = _quote_ident(id_column, "id")
        title_col = _quote_ident(title_column, "title")
        chunk_col = _quote_ident(chunk_column, "chunk_text")
        url_col = _quote_ident(url_column, "official_url")
        source_col = _quote_ident(source_column, "source_name")
        embedding_col = _quote_ident(embedding_column, "embedding")
        chunk_id_col = _quote_ident(chunk_id_column, "chunk_id")
        page_id_col = _quote_ident(page_id_column, "page_id")
        limit_value = max(1, min(20, int(limit)))
        min_score = max(0.0, min(1.0, float(min_similarity)))
        min_relevance_score = max(0.0, min(1.0, float(min_relevance)))
        candidate_limit = max(limit_value * 10, 50)
        table_ref = f"{safe_schema}.{safe_table}"
        date_filter = page_date_filter(published_start=published_start, published_end=published_end)
        date_params = page_date_params(published_start=published_start, published_end=published_end)
        rows: list[Any] = []
        with connect_db(psycopg_module, dsn) as conn:
            with conn.cursor() as cur:
                if vec and safe_embedding_table:
                    embedding_ref = f"{safe_schema}.{safe_embedding_table}"
                    page_ref = f"{safe_schema}.{safe_page_table}" if safe_page_table else ""
                    page_join = (
                        f"LEFT JOIN {page_ref} p ON p.{page_id_col} = c.{page_id_col}"
                        if page_ref
                        else ""
                    )
                    cur.execute(
                        f"""
                        SELECT c.{chunk_id_col},
                               COALESCE(p."source_title", c."section_title", '') AS title,
                               c.{chunk_col},
                               COALESCE(p."canonical_url", p."source_url", '') AS official_url,
                               COALESCE(p."org_name", p."source_domain", '') AS source_name,
                               1 - (e.{embedding_col} <=> %s::vector) AS similarity,
                               0::float AS topic_weight,
                               p."published_at" AS published_at
                        FROM {table_ref} c
                        JOIN {embedding_ref} e ON e.{chunk_id_col} = c.{chunk_id_col}
                        {page_join}
                        WHERE e.{embedding_col} IS NOT NULL
                        {date_filter}
                        ORDER BY e.{embedding_col} <=> %s::vector
                        LIMIT %s
                        """,
                        (json.dumps(vec), *date_params, json.dumps(vec), candidate_limit),
                    )
                    rows.extend(cur.fetchall())
                elif vec:
                    cur.execute(
                        f"""
                        SELECT {id_col}, {title_col}, {chunk_col}, {url_col}, {source_col},
                               1 - ({embedding_col} <=> %s::vector) AS similarity,
                               0::float AS topic_weight
                        FROM {table_ref}
                        WHERE {embedding_col} IS NOT NULL
                        ORDER BY {embedding_col} <=> %s::vector
                        LIMIT %s
                        """,
                        (json.dumps(vec), json.dumps(vec), candidate_limit),
                    )
                    rows.extend(cur.fetchall())
                if keywords:
                    cur.execute(
                        keyword_sql(
                            safe_schema=safe_schema,
                            safe_table=safe_table,
                            safe_page_table=safe_page_table,
                            table_ref=table_ref,
                            id_col=id_col,
                            title_col=title_col,
                            chunk_col=chunk_col,
                            url_col=url_col,
                            source_col=source_col,
                            chunk_id_col=chunk_id_col,
                            page_id_col=page_id_col,
                            official_join=bool(safe_embedding_table),
                            keyword_count=len(keywords),
                            limit_value=candidate_limit,
                            page_date_filter=date_filter,
                        ),
                        keyword_params(
                            keywords,
                            official_join=bool(safe_embedding_table),
                            limit_value=candidate_limit,
                            page_date_params=date_params,
                        ),
                    )
                    rows.extend(cur.fetchall())
    except Exception as exc:
        LOGGER.warning("policy hybrid search failed: %s", exc)
        return []

    candidates: dict[str, dict[str, Any]] = {}
    for row in rows:
        item = row_to_candidate(row)
        candidate_id = item["id"] or f"{item['title']}::{item['chunk'][:60]}"
        existing = candidates.get(candidate_id)
        if existing is None:
            candidates[candidate_id] = item
            continue
        existing["similarity"] = max(float(existing.get("similarity") or 0.0), float(item.get("similarity") or 0.0))
        existing["topic_weight"] = max(float(existing.get("topic_weight") or 0.0), float(item.get("topic_weight") or 0.0))

    ranked: list[dict[str, Any]] = []
    for item in candidates.values():
        similarity = float(item.get("similarity") or 0.0)
        keyword_match_score = keyword_score(
            keywords=keywords,
            title=str(item.get("title") or ""),
            chunk=str(item.get("chunk") or ""),
            source_name=str(item.get("source_name") or ""),
            question=clean_question,
        )
        topic_score = min(1.0, float(item.get("topic_weight") or 0.0) / 2.0)
        vector_weight = 0.55 if similarity >= min_score else 0.20
        relevance = round((similarity * vector_weight) + (keyword_match_score * 0.35) + (topic_score * 0.10), 4)
        if similarity < min_score and relevance < min_relevance_score:
            continue
        reasons: list[str] = []
        if similarity >= min_score:
            reasons.append("vector")
        if keyword_match_score > 0:
            reasons.append("keyword")
        if topic_score > 0:
            reasons.append("topic")
        item["keyword_score"] = round(keyword_match_score, 4)
        item["relevance_score"] = relevance
        item["match_reasons"] = reasons or ["vector"]
        ranked.append(item)

    ranked.sort(key=lambda item: (float(item.get("relevance_score") or 0.0), float(item.get("similarity") or 0.0)), reverse=True)
    results: list[dict[str, Any]] = []
    for item in ranked[:limit_value]:
        results.append(
            {
                "id": str(item.get("id") or ""),
                "title": str(item.get("title") or ""),
                "chunk": str(item.get("chunk") or ""),
                "official_url": str(item.get("official_url") or ""),
                "source_name": str(item.get("source_name") or ""),
                "similarity": round(float(item.get("similarity") or 0.0), 4),
                "relevance_score": float(item.get("relevance_score") or 0.0),
                "match_reasons": item.get("match_reasons") or [],
                "published_at": str(item.get("published_at") or ""),
            }
        )
    return results
