from __future__ import annotations

import os
from typing import Any, Callable

from gateway_core.api.openai_compat.policy_time_scope import resolve_policy_time_scope
from gateway_core.infra.postgres_dsn import postgres_dsn
from gateway_core.policy.vector_store import search_policy_chunks


def build_policy_evidence_search(
    *,
    truthy_env_fn: Callable[[str, str], bool],
    rag_embed_text_fn: Callable[[str], Any],
    psycopg_module: Any,
) -> Callable[[str], list[dict[str, Any]]]:
    def search(question: str) -> list[dict[str, Any]]:
        if not truthy_env_fn("POLICY_VECTOR_ENABLED", "0"):
            return []
        time_scope = resolve_policy_time_scope(question)
        return search_policy_chunks(
            question=question,
            embedding_fn=rag_embed_text_fn,
            dsn=postgres_dsn(),
            psycopg_module=psycopg_module,
            schema=_env("POLICY_VECTOR_SCHEMA", "official_policy"),
            table=_env("POLICY_VECTOR_TABLE", "policy_chunks"),
            embedding_table=os.getenv("POLICY_VECTOR_EMBEDDING_TABLE", "policy_chunk_embeddings_1024").strip(),
            page_table=os.getenv("POLICY_VECTOR_PAGE_TABLE", "policy_pages").strip(),
            limit=_int_env("POLICY_VECTOR_TOP_K", 5, minimum=1, maximum=20),
            min_similarity=_float_env("POLICY_VECTOR_MIN_SIMILARITY", 0.40, minimum=0.0, maximum=1.0),
            min_relevance=_float_env("POLICY_VECTOR_MIN_RELEVANCE", 0.35, minimum=0.0, maximum=1.0),
            id_column=_env("POLICY_VECTOR_ID_COLUMN", "id"),
            title_column=_env("POLICY_VECTOR_TITLE_COLUMN", "title"),
            chunk_column=_env("POLICY_VECTOR_CHUNK_COLUMN", "chunk_text"),
            url_column=_env("POLICY_VECTOR_URL_COLUMN", "official_url"),
            source_column=_env("POLICY_VECTOR_SOURCE_COLUMN", "source_name"),
            embedding_column=_env("POLICY_VECTOR_EMBEDDING_COLUMN", "embedding"),
            chunk_id_column=_env("POLICY_VECTOR_CHUNK_ID_COLUMN", "chunk_id"),
            page_id_column=_env("POLICY_VECTOR_PAGE_ID_COLUMN", "page_id"),
            published_start=str(time_scope.get("start_date") or ""),
            published_end=str(time_scope.get("end_date") or ""),
        )

    return search


def _env(name: str, default: str) -> str:
    return os.getenv(name, default).strip() or default


def _int_env(name: str, default: int, *, minimum: int, maximum: int) -> int:
    try:
        return max(minimum, min(maximum, int(os.getenv(name, str(default)).strip())))
    except Exception:
        return default


def _float_env(name: str, default: float, *, minimum: float, maximum: float) -> float:
    try:
        return max(minimum, min(maximum, float(os.getenv(name, str(default)).strip())))
    except Exception:
        return default
