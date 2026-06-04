from __future__ import annotations

import json
import os
from typing import Any
from urllib import parse as urllib_parse
from urllib import request as urllib_request


def build_web_search_provider():
    if not _truthy_env("GATEWAY_WEB_SEARCH_ENABLED", "0"):
        return None
    provider = str(os.getenv("GATEWAY_WEB_SEARCH_PROVIDER", "searxng") or "searxng").strip().lower()
    endpoint = os.getenv("GATEWAY_WEB_SEARCH_ENDPOINT", "").strip()
    if provider == "bocha":
        endpoint = endpoint or os.getenv("BOCHA_WEB_SEARCH_ENDPOINT", "").strip() or "https://api.bochaai.com/v1/web-search"
    if not endpoint:
        return None
    timeout = _float_env("GATEWAY_WEB_SEARCH_TIMEOUT_SEC", 5.0)

    def _provider(query: str, context: dict[str, Any]) -> Any:
        if provider == "bocha":
            body = json.dumps(
                {
                    "query": query,
                    "freshness": str(context.get("freshness") or os.getenv("BOCHA_FRESHNESS", "noLimit") or "noLimit"),
                    "summary": _truthy_env("BOCHA_SUMMARY", "1"),
                    "count": _positive_int(os.getenv("BOCHA_COUNT", os.getenv("GATEWAY_WEB_SEARCH_MAX_RESULTS", "8")), 8),
                },
                ensure_ascii=False,
            ).encode("utf-8")
            api_key = os.getenv("BOCHA_API_KEY", "").strip() or os.getenv("GATEWAY_WEB_SEARCH_API_KEY", "").strip()
            if not api_key:
                raise RuntimeError("missing env var: BOCHA_API_KEY")
            req = urllib_request.Request(
                endpoint,
                data=body,
                headers={"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"},
                method="POST",
            )
        elif provider == "json_post":
            body = json.dumps({"query": query, **context}, ensure_ascii=False).encode("utf-8")
            headers = {"Content-Type": "application/json"}
            api_key = os.getenv("GATEWAY_WEB_SEARCH_API_KEY", "").strip()
            if api_key:
                headers["Authorization"] = f"Bearer {api_key}"
            req = urllib_request.Request(endpoint, data=body, headers=headers, method="POST")
        else:
            sep = "&" if "?" in endpoint else "?"
            url = endpoint + sep + urllib_parse.urlencode({"q": query, "format": "json"})
            req = urllib_request.Request(url, headers={"Accept": "application/json"}, method="GET")
        with urllib_request.urlopen(req, timeout=timeout) as resp:  # nosec - endpoint is operator configured.
            text = resp.read().decode("utf-8")
        try:
            payload = json.loads(text)
        except Exception:
            return {"results": [{"title": "web_search_response", "content": text[:2000]}]}
        if provider == "bocha":
            return _normalize_bocha_search_payload(payload)
        return payload

    return _provider


def _normalize_bocha_search_payload(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return {"results": []}
    web_pages = ((payload.get("data") or {}).get("webPages") or {}) if isinstance(payload.get("data"), dict) else {}
    raw_items = web_pages.get("value") if isinstance(web_pages, dict) else None
    if not isinstance(raw_items, list):
        raw_items = payload.get("results") if isinstance(payload.get("results"), list) else []
    results: list[dict[str, Any]] = []
    for item in raw_items:
        if not isinstance(item, dict):
            continue
        results.append(
            {
                "title": item.get("name") or item.get("title") or "",
                "url": item.get("url") or "",
                "content": item.get("summary") or item.get("snippet") or item.get("content") or "",
                "source": item.get("siteName") or item.get("source") or "bocha",
                "published_at": item.get("datePublished") or item.get("published_at") or "",
            }
        )
    return {"results": results, "provider": "bocha"}


def _truthy_env(name: str, default: str = "0") -> bool:
    return (os.getenv(name, default).strip().lower() or default) in {"1", "true", "yes", "on"}


def _positive_int(raw: str, default: int) -> int:
    try:
        return max(1, int(str(raw or default).strip()))
    except Exception:
        return default


def _float_env(name: str, default: float) -> float:
    try:
        return max(0.5, float(os.getenv(name, str(default)).strip()))
    except Exception:
        return default
