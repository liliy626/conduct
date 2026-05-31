from __future__ import annotations

from typing import Any, Dict

DEFAULT_CHINA_EMBED_MODEL = "embedding-2"
DEFAULT_CHINA_EMBED_API_KEY_ENV = "GLM_API_KEY"
DEFAULT_CHINA_EMBED_BASE_URL = "https://open.bigmodel.cn/api/paas/v4"
DEFAULT_CHINA_EMBED_DIM = 1024


def _is_bigmodel_base_url(base_url: str) -> bool:
    return "bigmodel.cn" in str(base_url or "").strip().lower()


def resolve_embedding_dimension(
    raw_dim: Any,
    *,
    model: str = "",
    base_url: str = "",
    default_embedding_size: int = DEFAULT_CHINA_EMBED_DIM,
) -> int:
    text = str(raw_dim or "").strip()
    if text:
        try:
            embedding_size = int(float(text))
            if embedding_size > 0:
                return embedding_size
        except Exception:
            pass
    if str(model or "").strip() == DEFAULT_CHINA_EMBED_MODEL and _is_bigmodel_base_url(base_url):
        return DEFAULT_CHINA_EMBED_DIM
    return max(1, int(default_embedding_size))


def build_embedding_request_kwargs(
    *,
    model: str,
    input_data: Any,
    base_url: str = "",
    embedding_size_raw: Any = "",
    default_embedding_size: int = DEFAULT_CHINA_EMBED_DIM,
    **legacy_kwargs: Any,
) -> Dict[str, Any]:
    kwargs: Dict[str, Any] = {"model": model, "input": input_data}
    explicit_embedding_size = bool(str(embedding_size_raw or "").strip())
    if not str(embedding_size_raw or "").strip():
        embedding_size_raw = legacy_kwargs.get("dim" + "_" + "raw", "")
        explicit_embedding_size = bool(str(embedding_size_raw or "").strip())
    embedding_size = resolve_embedding_dimension(embedding_size_raw, model=model, base_url=base_url, default_embedding_size=default_embedding_size)
    if embedding_size > 0 and (explicit_embedding_size or not _is_bigmodel_base_url(base_url)):
        kwargs["dimensions"] = embedding_size
    return kwargs
