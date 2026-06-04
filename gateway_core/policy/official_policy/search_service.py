from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Callable

from gateway_core.infra.postgres_dsn import postgres_dsn
from gateway_core.policy.official_policy.time_scope import resolve_policy_time_scope
from gateway_core.policy.official_policy.vector_repository import search_official_policy_chunks
from gateway_core.runtime.runtime_rag_embedding import _rag_embed_text


PolicySearchProvider = Callable[[str], list[dict[str, Any]]]


@dataclass(frozen=True)
class OfficialPolicySearchConfig:
    schema: str = "official_policy"
    table: str = "policy_chunks"
    embedding_table: str = "policy_chunk_embeddings_1024"
    page_table: str = "policy_pages"
    limit: int = 5
    min_similarity: float = 0.40
    min_relevance: float = 0.35
    id_column: str = "id"
    title_column: str = "title"
    chunk_column: str = "chunk_text"
    url_column: str = "official_url"
    source_column: str = "source_name"
    embedding_column: str = "embedding"
    chunk_id_column: str = "chunk_id"
    page_id_column: str = "page_id"

    @classmethod
    def from_env(cls) -> "OfficialPolicySearchConfig":
        return cls(
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
        )


def search_official_policy_evidence(
    question: str,
    *,
    config: OfficialPolicySearchConfig | None = None,
    embedding_fn: Callable[[str], Any] | None = None,
    psycopg_module: Any | None = None,
    dsn: str | None = None,
) -> list[dict[str, Any]]:
    """Search the official policy database schema and return evidence chunks."""
    clean_question = str(question or "").strip()
    if not clean_question or not _truthy_env("POLICY_VECTOR_ENABLED", "0"):
        return []
    resolved_config = config or OfficialPolicySearchConfig.from_env()
    time_scope = resolve_policy_time_scope(clean_question)
    return search_official_policy_chunks(
        question=clean_question,
        embedding_fn=embedding_fn or _rag_embed_text,
        dsn=dsn or postgres_dsn(),
        psycopg_module=psycopg_module if psycopg_module is not None else _psycopg_module(),
        schema=resolved_config.schema,
        table=resolved_config.table,
        embedding_table=resolved_config.embedding_table,
        page_table=resolved_config.page_table,
        limit=resolved_config.limit,
        min_similarity=resolved_config.min_similarity,
        min_relevance=resolved_config.min_relevance,
        id_column=resolved_config.id_column,
        title_column=resolved_config.title_column,
        chunk_column=resolved_config.chunk_column,
        url_column=resolved_config.url_column,
        source_column=resolved_config.source_column,
        embedding_column=resolved_config.embedding_column,
        chunk_id_column=resolved_config.chunk_id_column,
        page_id_column=resolved_config.page_id_column,
        published_start=str(time_scope.get("start_date") or ""),
        published_end=str(time_scope.get("end_date") or ""),
    )


def build_official_policy_search(
    *,
    config: OfficialPolicySearchConfig | None = None,
    embedding_fn: Callable[[str], Any] | None = None,
    psycopg_module: Any | None = None,
    dsn: str | None = None,
) -> PolicySearchProvider:
    def search(question: str) -> list[dict[str, Any]]:
        return search_official_policy_evidence(
            question,
            config=config,
            embedding_fn=embedding_fn,
            psycopg_module=psycopg_module,
            dsn=dsn,
        )

    return search


def _psycopg_module() -> Any:
    try:
        import psycopg
    except Exception:
        return None
    return psycopg


def _truthy_env(name: str, default: str = "0") -> bool:
    return (os.getenv(name, default).strip().lower() or default) in {"1", "true", "yes", "on"}


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
