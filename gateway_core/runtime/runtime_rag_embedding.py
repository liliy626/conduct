from __future__ import annotations

import logging
import os
import time
from typing import Any, List, Optional

from gateway_core.infra.china_llm_defaults import (
    DEFAULT_CHINA_EMBED_API_KEY_ENV,
    DEFAULT_CHINA_EMBED_BASE_URL,
    DEFAULT_CHINA_EMBED_DIM,
    DEFAULT_CHINA_EMBED_MODEL,
    build_embedding_request_kwargs,
    resolve_embedding_dimension,
)

try:
    from openai import OpenAI
except Exception:
    OpenAI = None

LOGGER = logging.getLogger("gateway.app")
_RAG_EMBED_CLIENT: Optional[Any] = None
_RAG_EMBED_FAIL_STREAK = 0
_RAG_EMBED_DISABLED_UNTIL_TS = 0.0
_RAG_EMBED_LAST_WARN_TS = 0.0

def _resolve_rag_embedding_dim(default_embedding_size: int = DEFAULT_CHINA_EMBED_DIM) -> int:
    """确定 RAG/向量检索实际应使用的 embedding 维度。"""
    raw = (
        os.getenv("RAG_EMBEDDING_SIZE", "").strip()
        or os.getenv("SCHEMA_METADATA_EMBEDDING_SIZE", "").strip()
        or str(int(default_embedding_size))
    )
    model = (
        os.getenv("RAG_EMBED_MODEL", "").strip()
        or os.getenv("SCHEMA_METADATA_EMBED_MODEL", "").strip()
        or DEFAULT_CHINA_EMBED_MODEL
    )
    base_url = (
        os.getenv("RAG_EMBED_BASE_URL", "").strip()
        or os.getenv("SCHEMA_METADATA_EMBED_BASE_URL", "").strip()
        or os.getenv("OPENAI_BASE_URL", "").strip()
        or os.getenv("OPENAI_API_BASE", "").strip()
        or DEFAULT_CHINA_EMBED_BASE_URL
    )
    embedding_size = resolve_embedding_dimension(raw, model=model, base_url=base_url, default_embedding_size=default_embedding_size)
    if not str(raw or "").strip():
        return embedding_size
    try:
        int(float(str(raw).strip()))
    except Exception:
        LOGGER.warning(
            "invalid embedding embedding_size (RAG_EMBEDDING_SIZE/SCHEMA_METADATA_EMBEDDING_SIZE)=%r, fallback=%s",
            raw,
            embedding_size,
        )
    return embedding_size


def _rag_embed_cooldown_sec() -> int:
    """读取 embedding 连续失败后的冷却时间。"""
    raw = os.getenv("RAG_EMBED_COOLDOWN_SEC", "").strip() or "60"
    try:
        return max(5, min(600, int(raw)))
    except Exception:
        return 60


def _rag_embed_temporarily_disabled() -> bool:
    """判断 embedding 功能是否处于临时熔断状态。"""
    return _RAG_EMBED_DISABLED_UNTIL_TS > time.time()


def _mark_rag_embed_success() -> None:
    """记录一次 embedding 成功，并清空失败计数。"""
    global _RAG_EMBED_FAIL_STREAK, _RAG_EMBED_DISABLED_UNTIL_TS
    _RAG_EMBED_FAIL_STREAK = 0
    _RAG_EMBED_DISABLED_UNTIL_TS = 0.0


def _mark_rag_embed_failure() -> int:
    """记录一次 embedding 失败，并触发短暂冷却。"""
    global _RAG_EMBED_FAIL_STREAK, _RAG_EMBED_DISABLED_UNTIL_TS
    _RAG_EMBED_FAIL_STREAK += 1
    _RAG_EMBED_DISABLED_UNTIL_TS = time.time() + float(_rag_embed_cooldown_sec())
    return _RAG_EMBED_FAIL_STREAK


def _should_log_rag_embed_failure() -> bool:
    """控制 embedding 报错日志不要刷屏。"""
    global _RAG_EMBED_LAST_WARN_TS
    now = time.time()
    if (now - float(_RAG_EMBED_LAST_WARN_TS)) < float(_rag_embed_cooldown_sec()):
        return False
    _RAG_EMBED_LAST_WARN_TS = now
    return True


def _get_rag_embed_client() -> Optional[Any]:
    """懒加载一个用于做 embedding 的 OpenAI 兼容客户端。

    虽然名字叫 OpenAI 客户端，但这里接的是兼容 OpenAI 协议的中国模型接口。
    """
    global _RAG_EMBED_CLIENT
    if _rag_embed_temporarily_disabled():
        return None
    if OpenAI is None:
        return None
    if _RAG_EMBED_CLIENT is not None:
        return _RAG_EMBED_CLIENT

    model_key_env = (
        os.getenv("RAG_EMBED_API_KEY_ENV", "").strip()
        or os.getenv("SCHEMA_METADATA_EMBED_API_KEY_ENV", "").strip()
        or DEFAULT_CHINA_EMBED_API_KEY_ENV
    )
    api_key = (
        os.getenv("RAG_EMBED_API_KEY", "").strip()
        or os.getenv("SCHEMA_METADATA_EMBED_API_KEY", "").strip()
        or os.getenv(model_key_env, "").strip()
    )
    if not api_key:
        return None

    model = (
        os.getenv("RAG_EMBED_MODEL", "").strip()
        or os.getenv("SCHEMA_METADATA_EMBED_MODEL", "").strip()
        or DEFAULT_CHINA_EMBED_MODEL
    )
    if not model:
        return None

    timeout_raw = (
        os.getenv("RAG_EMBED_TIMEOUT", "").strip()
        or os.getenv("RAG_EMBED_TIMEOUT_SEC", "").strip()
        or os.getenv("SCHEMA_METADATA_EMBED_TIMEOUT_SEC", "").strip()
        or "8"
    )
    try:
        timeout = max(2, min(60, int(timeout_raw)))
    except Exception:
        timeout = 8

    kwargs: Dict[str, Any] = {"api_key": api_key, "timeout": timeout}
    base_url = (
        os.getenv("RAG_EMBED_BASE_URL", "").strip()
        or os.getenv("SCHEMA_METADATA_EMBED_BASE_URL", "").strip()
        or os.getenv("OPENAI_BASE_URL", "").strip()
        or os.getenv("OPENAI_API_BASE", "").strip()
        or DEFAULT_CHINA_EMBED_BASE_URL
    ).rstrip("/")
    if base_url:
        kwargs["base_url"] = base_url
    try:
        _RAG_EMBED_CLIENT = OpenAI(**kwargs)
    except Exception:
        _RAG_EMBED_CLIENT = None
    return _RAG_EMBED_CLIENT


def _rag_embed_text(text: str) -> Optional[List[float]]:
    """把一段文本转成向量。

    RAG、schema metadata、人名向量检索都会依赖这个能力。
    """
    if not text:
        return None
    if _rag_embed_temporarily_disabled():
        return None
    client = _get_rag_embed_client()
    if client is None:
        return None
    model = (
        os.getenv("RAG_EMBED_MODEL", "").strip()
        or os.getenv("SCHEMA_METADATA_EMBED_MODEL", "").strip()
        or DEFAULT_CHINA_EMBED_MODEL
    )
    if not model:
        return None
    try:
        resp = client.embeddings.create(
            **build_embedding_request_kwargs(
                model=model,
                input_data=[str(text).strip()],
                base_url=getattr(getattr(client, "base_url", None), "host", "") or "",
                embedding_size_raw=os.getenv("RAG_EMBEDDING_SIZE", "").strip()
                or os.getenv("SCHEMA_METADATA_EMBEDDING_SIZE", "").strip(),
            )
        )
    except Exception as exc:
        fail_streak = _mark_rag_embed_failure()
        if _should_log_rag_embed_failure():
            LOGGER.warning(
                "rag embed failed: %s (streak=%s, cooldown=%ss)",
                exc,
                fail_streak,
                _rag_embed_cooldown_sec(),
            )
        return None

    data = getattr(resp, "data", None)
    if not data:
        return None
    item = next(iter(data), None)
    if item is None:
        return None
    vec = list(getattr(item, "embedding", []) or [])
    _mark_rag_embed_success()
    return vec if vec else None
