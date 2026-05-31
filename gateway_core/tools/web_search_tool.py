from __future__ import annotations

import time
import os
from typing import Any, Callable, Dict, List, Mapping, Optional

from .tool_core import AgentTool, AgentToolInput, AgentToolOutput, ToolExecutionContext, json_safe
from .privacy import STUDENT_NAME_PATTERN, TEACHER_NAME_PATTERN, contains_sensitive_context


WebSearchProvider = Callable[[str, Dict[str, Any]], Any]


class WebSearchTool(AgentTool):
    name = "web_search"
    description = "Runs an optional external web search after privacy sanitization."

    def __init__(self, *, enabled: bool = False, provider: Optional[WebSearchProvider] = None) -> None:
        self.enabled = enabled
        self.provider = provider

    def run(self, tool_input: AgentToolInput, context: ToolExecutionContext) -> AgentToolOutput:
        started = time.perf_counter()
        query = str(tool_input.arguments.get("query") or tool_input.arguments.get("input") or "").strip()
        blocked, reason = _query_has_sensitive_person(query)
        if not blocked:
            blocked, reason = contains_sensitive_context(_privacy_context(tool_input.arguments))
        if blocked:
            return _output(
                started,
                ok=False,
                warnings=[f"privacy block: {reason}; external web search was not called"],
                error="sensitive context cannot be sent to web search",
            )

        if not self.enabled:
            return _output(
                started,
                ok=True,
                warnings=["web search disabled; returning without external lookup"],
            )
        if self.provider is None:
            return _output(
                started,
                ok=True,
                warnings=["web search provider unavailable; returning without external lookup"],
            )

        sanitized_context = _sanitized_context(tool_input.arguments)
        try:
            provider_result = self.provider(query, sanitized_context)
        except Exception as exc:
            return _output(started, ok=False, error=str(exc))
        lean_result = _lean_provider_result(query=query, provider_result=provider_result)

        return _output(
            started,
            ok=True,
            artifacts=[{"type": "web_search_result", "format": "json", "content": json_safe(lean_result)}],
            sources=_extract_sources(lean_result),
        )


def _sanitized_context(arguments: Mapping[str, Any]) -> Dict[str, Any]:
    allowed_keys = {"query", "locale", "date", "topic", "policy_domain"}
    return {key: json_safe(value) for key, value in arguments.items() if key in allowed_keys and key != "query"}


def _privacy_context(arguments: Mapping[str, Any]) -> Dict[str, Any]:
    # The public search query itself may contain normal Chinese phrase lists
    # such as "科学，科技教育", which should not be mistaken for a private
    # person-name list. Sensitive school rows and explicit person fields are
    # still checked through the remaining context.
    return {key: value for key, value in arguments.items() if key not in {"query", "input"}}


def _query_has_sensitive_person(query: str) -> tuple[bool, str]:
    if TEACHER_NAME_PATTERN.search(query):
        return True, "query contains teacher name"
    if STUDENT_NAME_PATTERN.search(query):
        return True, "query contains student name"
    return False, ""


def _extract_sources(provider_result: Any) -> List[Dict[str, Any]]:
    if isinstance(provider_result, Mapping):
        results = provider_result.get("results") or provider_result.get("sources") or []
        if isinstance(results, list):
            sources = []
            for item in results:
                if isinstance(item, Mapping):
                    source = {key: item[key] for key in ("title", "url", "source") if key in item}
                    if source:
                        sources.append(json_safe(source))
            return sources
    return []


def _lean_provider_result(*, query: str, provider_result: Any) -> Dict[str, Any]:
    raw_results: Any = []
    if isinstance(provider_result, Mapping):
        raw_results = provider_result.get("results") or provider_result.get("sources") or []
    elif isinstance(provider_result, list):
        raw_results = provider_result
    if not isinstance(raw_results, list):
        raw_results = []

    results: List[Dict[str, Any]] = []
    seen_urls: set[str] = set()
    for item in raw_results:
        if not isinstance(item, Mapping):
            continue
        url = str(item.get("url") or item.get("link") or item.get("official_url") or "").strip()
        title = _clean_text(item.get("title") or item.get("name") or "", 160)
        if not url and not title:
            continue
        if url and url in seen_urls:
            continue
        if url:
            seen_urls.add(url)
        results.append(
            {
                "title": title,
                "url": url,
                "content": _clean_text(item.get("content") or item.get("snippet") or item.get("description") or "", 360),
                "source": _clean_text(item.get("source") or item.get("engine") or "", 80),
                "published_at": _clean_text(item.get("publishedDate") or item.get("published_at") or item.get("pubdate") or "", 40),
            }
        )
        if len(results) >= _max_results():
            break
    return {"query": query, "result_count": len(results), "results": results}


def _clean_text(value: Any, limit: int) -> str:
    text = " ".join(str(value or "").replace("\x00", " ").split())
    return text[:limit].rstrip()


def _max_results() -> int:
    try:
        return max(1, min(int(os.getenv("GATEWAY_WEB_SEARCH_MAX_RESULTS", "8") or "8"), 20))
    except Exception:
        return 8


def _output(
    started: float,
    *,
    ok: bool,
    artifacts: Optional[List[Dict[str, Any]]] = None,
    sources: Optional[List[Dict[str, Any]]] = None,
    warnings: Optional[List[str]] = None,
    error: Optional[str] = None,
) -> AgentToolOutput:
    return AgentToolOutput(
        ok=ok,
        artifacts=artifacts or [],
        sources=sources or [],
        warnings=warnings or [],
        error=error,
        duration_ms=round((time.perf_counter() - started) * 1000, 3),
    )
