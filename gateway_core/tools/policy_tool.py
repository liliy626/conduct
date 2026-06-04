from __future__ import annotations

import time
from typing import Any, Callable

from gateway_core.policy.official_policy.search_service import search_official_policy_evidence

from .tool_core import AgentTool, AgentToolInput, AgentToolOutput, ToolExecutionContext, json_safe


PolicySearchProvider = Callable[[str], list[dict[str, Any]]]


class PolicyTool(AgentTool):
    name = "official_policy_search"
    description = "检索 official_policy 官方政策库。职称、荣誉、申报条件、年限、评审主体、一票否决、继续教育学分等问题必须调用。"

    def __init__(self, *, provider: PolicySearchProvider | None = None) -> None:
        self.provider = provider or search_official_policy_evidence

    def run(self, tool_input: AgentToolInput, context: ToolExecutionContext) -> AgentToolOutput:
        started = time.perf_counter()
        query = str(
            tool_input.arguments.get("query")
            or tool_input.arguments.get("question")
            or tool_input.arguments.get("input")
            or ""
        ).strip()
        if not query:
            return _output(started, ok=False, error="official_policy_search requires query")
        try:
            evidence = self.provider(query)
        except Exception as exc:
            return _output(started, ok=False, error=str(exc))
        return _output(
            started,
            ok=True,
            evidence=[
                {
                    "kind": "official_policy_evidence",
                    "query": query,
                    "chunk_count": len(evidence),
                }
            ],
            artifacts=[
                {
                    "type": "official_policy",
                    "format": "json",
                    "content": {
                        "source": "official_policy",
                        "query": query,
                        "chunk_count": len(evidence),
                        "policy_evidence": json_safe(evidence),
                        "official_urls": [
                            str(item.get("official_url") or "")
                            for item in evidence
                            if isinstance(item, dict) and str(item.get("official_url") or "").strip()
                        ],
                    },
                }
            ],
            sources=[
                {
                    "title": str(item.get("title") or "")[:120],
                    "url": str(item.get("official_url") or ""),
                    "source": str(item.get("source_name") or item.get("source") or "official_policy"),
                }
                for item in evidence
                if isinstance(item, dict) and (item.get("title") or item.get("official_url"))
            ],
        )


def _output(
    started: float,
    *,
    ok: bool,
    evidence: list[dict[str, Any]] | None = None,
    artifacts: list[dict[str, Any]] | None = None,
    sources: list[dict[str, Any]] | None = None,
    warnings: list[str] | None = None,
    error: str | None = None,
) -> AgentToolOutput:
    return AgentToolOutput(
        ok=ok,
        evidence=evidence or [],
        artifacts=artifacts or [],
        sources=sources or [],
        warnings=warnings or [],
        error=error,
        duration_ms=round((time.perf_counter() - started) * 1000, 3),
    )
