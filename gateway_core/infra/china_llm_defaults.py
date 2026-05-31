from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict

import yaml


def _model_config_path() -> Path:
    return Path(os.getenv("MODEL_CONFIG_PATH", Path(__file__).resolve().parents[2] / "model_config.yaml"))


def _load_china_embedding_defaults() -> dict[str, Any]:
    raw = yaml.safe_load(_model_config_path().read_text(encoding="utf-8")) or {}
    values = ((raw.get("embedding_defaults") or {}).get("china") or {})
    return {
        "model": str(values["model"]).strip(),
        "api_key_env": str(values["api_key_env"]).strip(),
        "base_url": str(values["base_url"]).strip().rstrip("/"),
        "dimensions": int(values["dimensions"]),
    }


_CHINA_EMBEDDING_DEFAULTS = _load_china_embedding_defaults()

DEFAULT_CHINA_EMBED_MODEL = _CHINA_EMBEDDING_DEFAULTS["model"]
DEFAULT_CHINA_EMBED_API_KEY_ENV = _CHINA_EMBEDDING_DEFAULTS["api_key_env"]
DEFAULT_CHINA_EMBED_BASE_URL = _CHINA_EMBEDDING_DEFAULTS["base_url"]
DEFAULT_CHINA_EMBED_DIM = _CHINA_EMBEDDING_DEFAULTS["dimensions"]


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
