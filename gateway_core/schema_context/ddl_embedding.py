from __future__ import annotations

import os
from typing import Any

from gateway_core.infra.china_llm_defaults import (
    DEFAULT_CHINA_EMBED_API_KEY_ENV,
    DEFAULT_CHINA_EMBED_BASE_URL,
    DEFAULT_CHINA_EMBED_DIM,
    DEFAULT_CHINA_EMBED_MODEL,
    build_embedding_request_kwargs,
)

try:
    from openai import OpenAI
except Exception:
    OpenAI = None


_DDL_EMBED_CLIENTS: dict[tuple[str, str, str, int], Any] = {}


def _env_value(primary: str, legacy: str = "", default: str = "") -> str:
    value = os.getenv(primary, "").strip()
    if value:
        return value
    if legacy:
        value = os.getenv(legacy, "").strip()
        if value:
            return value
    return default


def _ddl_embed_client(*, dimensions: int | None = None) -> Any:
    if OpenAI is None:
        return None
    clean_dimensions = int(dimensions or 0)
    api_key_env = _ddl_api_key_env_for_dim(clean_dimensions)
    api_key = _env_value("SCHOOL_DDL_EMBED_API_KEY", "TENANT_DDL_EMBED_API_KEY") or os.getenv(api_key_env, "").strip()
    if not api_key:
        return None
    base_url = _ddl_base_url_for_dim(clean_dimensions)
    model = _ddl_model_for_dim(clean_dimensions)
    cache_key = (api_key_env, base_url, model, clean_dimensions)
    if cache_key in _DDL_EMBED_CLIENTS:
        return _DDL_EMBED_CLIENTS[cache_key]
    timeout_raw = _env_value("SCHOOL_DDL_EMBED_TIMEOUT_SEC", "TENANT_DDL_EMBED_TIMEOUT_SEC", "8")
    try:
        timeout = max(2, min(60, int(timeout_raw)))
    except Exception:
        timeout = 8
    kwargs: dict[str, Any] = {"api_key": api_key, "timeout": timeout}
    if base_url:
        kwargs["base_url"] = base_url
    try:
        client = OpenAI(**kwargs)
    except Exception:
        return None
    _DDL_EMBED_CLIENTS[cache_key] = client
    return client


def ddl_embed_text(text: str, *, dimensions: int | None = None) -> list[float] | None:
    clean_text = str(text or "").strip()
    if not clean_text:
        return None
    client = _ddl_embed_client(dimensions=dimensions)
    if client is None:
        return None
    clean_dimensions = int(dimensions or 0)
    model = _ddl_model_for_dim(clean_dimensions)
    base_url = _ddl_base_url_for_dim(clean_dimensions)
    size_raw = str(dimensions or "").strip() or _env_value(
        "SCHOOL_DDL_EMBEDDING_SIZE",
        "TENANT_DDL_EMBEDDING_SIZE",
        str(DEFAULT_CHINA_EMBED_DIM),
    )
    try:
        resp = client.embeddings.create(
            **build_embedding_request_kwargs(
                model=model,
                input_data=[clean_text],
                base_url=base_url,
                embedding_size_raw=size_raw,
            )
        )
    except Exception:
        return None
    data = getattr(resp, "data", None)
    if not data:
        return None
    item = next(iter(data), None)
    if item is None:
        return None
    vector = list(getattr(item, "embedding", []) or [])
    return vector or None


def _ddl_model_for_dim(dimensions: int) -> str:
    if dimensions == 1536:
        return _env_value("SCHOOL_DDL_EMBED_MODEL_1536", "TENANT_DDL_EMBED_MODEL_1536", "text-embedding-3-small") or "text-embedding-3-small"
    return _env_value("SCHOOL_DDL_EMBED_MODEL", "TENANT_DDL_EMBED_MODEL", DEFAULT_CHINA_EMBED_MODEL) or DEFAULT_CHINA_EMBED_MODEL


def _ddl_base_url_for_dim(dimensions: int) -> str:
    if dimensions == 1536:
        return _env_value("SCHOOL_DDL_EMBED_BASE_URL_1536", "TENANT_DDL_EMBED_BASE_URL_1536", "https://api.openai.com/v1").rstrip("/") or "https://api.openai.com/v1"
    return _env_value("SCHOOL_DDL_EMBED_BASE_URL", "TENANT_DDL_EMBED_BASE_URL", DEFAULT_CHINA_EMBED_BASE_URL).rstrip("/") or DEFAULT_CHINA_EMBED_BASE_URL


def _ddl_api_key_env_for_dim(dimensions: int) -> str:
    if dimensions == 1536:
        return _env_value("SCHOOL_DDL_EMBED_API_KEY_ENV_1536", "TENANT_DDL_EMBED_API_KEY_ENV_1536", "OPENAI_API_KEY") or "OPENAI_API_KEY"
    return _env_value("SCHOOL_DDL_EMBED_API_KEY_ENV", "TENANT_DDL_EMBED_API_KEY_ENV", DEFAULT_CHINA_EMBED_API_KEY_ENV) or DEFAULT_CHINA_EMBED_API_KEY_ENV
