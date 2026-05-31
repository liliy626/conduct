from __future__ import annotations

"""网关运行时配置与守卫能力。

本模块集中处理：
1) 模型配置加载、清洗与校验（model_config.yaml -> ModelSpec）。
2) ChatOpenAI client 构造与复用（默认参数下按 model_id 复用）。
3) 网关鉴权、访问者角色/权限上下文（contextvars）。
4) 请求级结果缓存（按请求关键维度生成稳定 cache key）。
"""

import os
import re
import json
import hashlib
import contextvars
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, Optional

import yaml
from fastapi import HTTPException
from langchain_openai import ChatOpenAI
from gateway_core.infra.api_keys import (
    api_key_db_enabled,
    current_api_key_record,
    is_admin_record,
    lookup_gateway_api_key,
    set_current_api_key_record,
)
from gateway_core.infra.gateway_auth import extract_bearer_token, get_gateway_keys, require_gateway_auth


def truthy_env(name: str, default: str = "1") -> bool:
    """统一解析布尔环境变量。"""
    return os.getenv(name, default).strip().lower() not in {"0", "false", "no", "off"}


_ZERO_WIDTH_CHARS_RE = re.compile(r"[\u200b-\u200f\u202a-\u202e\u2060-\u206f\ufeff]")


def _sanitize_endpoint_text(raw: Any) -> str:
    """清洗 endpoint 文本，避免零宽字符/空白导致的隐蔽配置错误。"""
    text = str(raw or "")
    text = _ZERO_WIDTH_CHARS_RE.sub("", text)
    text = re.sub(r"\s+", "", text)
    return text.strip()


def _model_max_retries(item: dict[str, Any]) -> int:
    raw = item.get("max_retries")
    if raw is None:
        raw = "2"
    try:
        return max(0, min(int(raw), 8))
    except Exception:
        return 2


@dataclass
class ModelSpec:
    model_id: str
    provider: str
    remote_model: str
    base_url: str
    api_key_env: str
    temperature: float = 0.2
    max_tokens: int = 2048
    timeout: int = 60
    max_retries: int = 2


@dataclass
class RequestCacheEntry:
    text: str
    usage: Dict[str, Any]
    expires_at: float


class RequestResultCache:
    """线程安全的轻量请求结果缓存（TTL + 容量上限 + 命中统计）。"""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._entries: "OrderedDict[str, RequestCacheEntry]" = OrderedDict()
        self._hits = 0
        self._misses = 0

    def enabled(self) -> bool:
        return truthy_env("REQUEST_CACHE_ENABLED", "1")

    def ttl_seconds(self) -> int:
        raw = os.getenv("REQUEST_CACHE_TTL_SEC", "300").strip()
        try:
            return max(0, int(raw))
        except Exception:
            return 300

    def max_entries(self) -> int:
        raw = os.getenv("REQUEST_CACHE_MAX_ENTRIES", "256").strip()
        try:
            return max(1, int(raw))
        except Exception:
            return 256

    def max_cache_temperature(self) -> float:
        raw = os.getenv("REQUEST_CACHE_MAX_TEMPERATURE", "0.3").strip()
        try:
            return max(0.0, float(raw))
        except Exception:
            return 0.3

    def _purge_expired_locked(self, now: float) -> None:
        expired_keys = [key for key, entry in self._entries.items() if entry.expires_at <= now]
        for key in expired_keys:
            self._entries.pop(key, None)

    def get(self, key: str) -> Optional[RequestCacheEntry]:
        if not self.enabled() or not key:
            return None
        now = time.time()
        with self._lock:
            self._purge_expired_locked(now)
            entry = self._entries.pop(key, None)
            if entry is None:
                self._misses += 1
                return None
            if entry.expires_at <= now:
                self._misses += 1
                return None
            self._entries[key] = entry
            self._hits += 1
            return entry

    def set(self, key: str, text: str, usage: Dict[str, Any]) -> None:
        if not self.enabled() or not key:
            return
        ttl = self.ttl_seconds()
        if ttl <= 0:
            return
        entry = RequestCacheEntry(text=text, usage=dict(usage or {}), expires_at=time.time() + ttl)
        with self._lock:
            self._purge_expired_locked(time.time())
            self._entries.pop(key, None)
            self._entries[key] = entry
            while len(self._entries) > self.max_entries():
                self._entries.popitem(last=False)

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()
            self._hits = 0
            self._misses = 0

    def stats(self) -> Dict[str, Any]:
        with self._lock:
            self._purge_expired_locked(time.time())
            return {
                "enabled": self.enabled(),
                "ttl_sec": self.ttl_seconds(),
                "max_entries": self.max_entries(),
                "max_temperature": self.max_cache_temperature(),
                "size": len(self._entries),
                "hits": self._hits,
                "misses": self._misses,
            }


class Gateway:
    """模型网关配置加载器与客户端工厂。"""

    def __init__(self, config_path: Path) -> None:
        self.config_path = config_path
        self.default_model = ""
        self.models: Dict[str, ModelSpec] = {}
        self._clients: Dict[str, ChatOpenAI] = {}
        self.reload_config()

    def reload_config(self) -> None:
        """重载模型配置并做强校验，失败时直接抛错阻断启动。"""
        if not self.config_path.exists():
            raise RuntimeError(
                f"config file not found: {self.config_path}. Copy model_config.example.yaml to model_config.yaml first."
            )
        raw = yaml.safe_load(self.config_path.read_text(encoding="utf-8")) or {}
        model_items = raw.get("models", [])
        if not model_items:
            raise RuntimeError("models is empty in config")

        models: Dict[str, ModelSpec] = {}
        for item in model_items:
            mid = str(item.get("id", "")).strip()
            if not mid:
                raise RuntimeError("model id is required")
            spec = ModelSpec(
                model_id=mid,
                provider=str(item.get("provider", "openai_compatible")).strip(),
                remote_model=_sanitize_endpoint_text(item.get("model", mid)),
                base_url=_sanitize_endpoint_text(item.get("base_url", "")).rstrip("/"),
                api_key_env=_sanitize_endpoint_text(item.get("api_key_env", "DEEPSEEK_API_KEY")),
                temperature=float(item.get("temperature", 0.2)),
                max_tokens=int(item.get("max_tokens", 2048)),
                timeout=int(item.get("timeout", 60)),
                max_retries=_model_max_retries(item),
            )
            if not spec.base_url:
                raise RuntimeError(f"model {mid} missing base_url")
            models[mid] = spec

        self.models = models
        self.default_model = str(raw.get("default_model", "")).strip() or next(iter(models.keys()))
        if self.default_model not in self.models:
            raise RuntimeError(f"default_model {self.default_model} not in models")

        # 清理已下线模型对应的 client，避免误用旧配置。
        stale = set(self._clients.keys()) - set(self.models.keys())
        for key in stale:
            del self._clients[key]

    def resolve_model(self, model_id: Optional[str]) -> ModelSpec:
        m = (model_id or "").strip() or self.default_model
        if m not in self.models:
            raise HTTPException(status_code=400, detail=f"unsupported model: {m}")
        return self.models[m]

    def _build_client(self, spec: ModelSpec, temperature: Optional[float], max_tokens: Optional[int]) -> ChatOpenAI:
        """按模型规格构造 ChatOpenAI，支持请求级参数覆盖。"""
        api_key = os.getenv(spec.api_key_env, "")
        if not api_key:
            raise HTTPException(status_code=500, detail=f"missing env var: {spec.api_key_env}")

        temp = spec.temperature if temperature is None else temperature
        mtok = spec.max_tokens if max_tokens is None else max_tokens

        return ChatOpenAI(
            model=spec.remote_model,
            api_key=api_key,
            base_url=spec.base_url,
            temperature=temp,
            max_tokens=mtok,
            timeout=spec.timeout,
            max_retries=spec.max_retries,
            stream_usage=truthy_env("GATEWAY_STREAM_USAGE_ENABLED", "1"),
        )

    def get_client(self, spec: ModelSpec, temperature: Optional[float], max_tokens: Optional[int]) -> ChatOpenAI:
        """默认参数命中缓存 client；覆写参数时返回一次性 client。"""
        if temperature is not None or max_tokens is not None:
            return self._build_client(spec, temperature, max_tokens)
        if spec.model_id not in self._clients:
            self._clients[spec.model_id] = self._build_client(spec, None, None)
        return self._clients[spec.model_id]


def _normalize_viewer_role(raw: Optional[str]) -> str:
    return re.sub(r"\s+", " ", str(raw or "").strip()).lower()


def _normalize_user_id(raw: Optional[str]) -> str:
    return re.sub(r"\s+", " ", str(raw or "").strip())


def _parse_viewer_permissions(raw: Optional[str]) -> tuple[str, ...]:
    if not raw:
        return ()
    out: list[str] = []
    seen = set()
    for item in str(raw).replace(";", ",").split(","):
        value = re.sub(r"\s+", " ", str(item or "").strip()).lower()
        if not value or value in seen:
            continue
        seen.add(value)
        out.append(value)
    return tuple(out)


VIEWER_ROLE_CTX: contextvars.ContextVar[str] = contextvars.ContextVar("viewer_role", default="")
VIEWER_PERMISSIONS_CTX: contextvars.ContextVar[tuple[str, ...]] = contextvars.ContextVar(
    "viewer_permissions",
    default=(),
)
USER_ID_CTX: contextvars.ContextVar[str] = contextvars.ContextVar("user_id", default="")


def _set_viewer_access(role: Optional[str], permissions: Optional[str], user_id: Optional[str] = None) -> None:
    """将当前请求的访问者角色/权限/用户ID写入 contextvars。"""
    VIEWER_ROLE_CTX.set(_normalize_viewer_role(role))
    VIEWER_PERMISSIONS_CTX.set(_parse_viewer_permissions(permissions))
    USER_ID_CTX.set(_normalize_user_id(user_id))


def _current_viewer_role() -> str:
    return (VIEWER_ROLE_CTX.get() or "").strip()


def _current_viewer_permissions() -> tuple[str, ...]:
    return tuple(VIEWER_PERMISSIONS_CTX.get() or ())


def _current_user_id() -> str:
    return (USER_ID_CTX.get() or "").strip()


def _strict_school_isolation_enabled() -> bool:
    return truthy_env("STRICT_SCHOOL_ISOLATION", "0")


def _get_gateway_keys() -> list[str]:
    raw = os.getenv("GATEWAY_API_KEYS", "").strip() or os.getenv("GATEWAY_API_KEY", "").strip()
    return get_gateway_keys(raw)


def _gateway_auth_enabled() -> bool:
    return truthy_env("GATEWAY_AUTH_ENABLED", "1")


def _require_gateway_auth(authorization: Optional[str]) -> str:
    """按开关决定是否执行网关鉴权，返回解析后的 key 标识。"""
    set_current_api_key_record(None)
    if not _gateway_auth_enabled():
        return ""
    token = extract_bearer_token(authorization)
    if api_key_db_enabled():
        if not token:
            raise HTTPException(status_code=401, detail="missing bearer token")
        from gateway_core.infra.postgres_dsn import gateway_api_key_postgres_dsn

        record = lookup_gateway_api_key(
            token=token,
            dsn=gateway_api_key_postgres_dsn(),
            psycopg_module=_psycopg_module(),
            initialize=True,
        )
        if record is None:
            keys = _get_gateway_keys()
            if keys and token in keys:
                return token
            raise HTTPException(status_code=401, detail="invalid gateway api key")
        if not record.enabled:
            raise HTTPException(status_code=401, detail="disabled gateway api key")
        set_current_api_key_record(record)
        return token
    return require_gateway_auth(authorization, _get_gateway_keys())


def _current_gateway_api_key_record():
    return current_api_key_record()


def _current_gateway_key_is_admin() -> bool:
    return is_admin_record(current_api_key_record())


def _psycopg_module():
    try:
        import psycopg

        return psycopg
    except Exception:
        return None


def _request_cache_key(
    req: Any,
    spec: ModelSpec,
    school_scope: str,
    *,
    stringify_content: Optional[Callable[[Any], str]] = None,
) -> str:
    """构建稳定缓存键。

    关键维度包含：模型、采样参数、消息内容、school_scope、viewer 权限上下文与隔离开关。
    目的是避免跨租户/跨权限场景复用缓存结果。
    """
    if stringify_content is None:
        stringify_content = lambda value: "" if value is None else str(value)

    def _extract_payload_message(item: Any) -> Dict[str, str]:
        return {
            "role": str(getattr(item, "role", "") or ""),
            "content": stringify_content(getattr(item, "content", "")),
        }

    payload = {
        "model_id": spec.model_id,
        "remote_model": spec.remote_model,
        "school_scope": school_scope or "",
        "viewer_role": _current_viewer_role(),
        "viewer_permissions": list(_current_viewer_permissions()),
        "user_id": _current_user_id(),
        "strict_school_isolation": _strict_school_isolation_enabled(),
        "temperature": req.temperature if req.temperature is not None else spec.temperature,
        "max_tokens": req.max_tokens if req.max_tokens is not None else spec.max_tokens,
        "messages": [_extract_payload_message(m) for m in getattr(req, "messages", [])],
    }
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()
