from __future__ import annotations

from typing import Any

from gateway_core.tools.tool_core import AgentToolInput, ToolExecutionContext
from gateway_core.tools.web_search_tool import WebSearchTool


def test_web_search_rejects_empty_query_without_calling_provider() -> None:
    calls: list[tuple[str, dict[str, Any]]] = []

    output = WebSearchTool(enabled=True, provider=_recording_provider(calls)).run(
        AgentToolInput(arguments={}),
        ToolExecutionContext(tenant_id="sch_test"),
    )

    assert output.ok is False
    assert output.error == "invalid_input: web_search requires query"
    assert output.evidence[0]["tool_status"] == "invalid_input"
    assert output.evidence[0]["attempted_external_call"] is False
    assert calls == []


def test_web_search_disabled_is_typed_failure_without_external_call() -> None:
    output = WebSearchTool(enabled=False, provider=_recording_provider([])).run(
        AgentToolInput(arguments={"query": "上海市教师职称政策"}),
        ToolExecutionContext(tenant_id="sch_test"),
    )

    assert output.ok is False
    assert output.error == "disabled: web search is disabled"
    assert output.evidence[0]["tool_status"] == "disabled"
    assert output.evidence[0]["attempted_external_call"] is False


def test_web_search_provider_unavailable_is_typed_failure() -> None:
    output = WebSearchTool(enabled=True, provider=None).run(
        AgentToolInput(arguments={"query": "上海市教师职称政策"}),
        ToolExecutionContext(tenant_id="sch_test"),
    )

    assert output.ok is False
    assert output.error == "provider_unavailable: web search provider unavailable"
    assert output.evidence[0]["tool_status"] == "provider_unavailable"
    assert output.evidence[0]["attempted_external_call"] is False


def test_web_search_provider_error_is_typed_failure() -> None:
    def provider(query: str, context: dict[str, Any]) -> dict[str, Any]:
        del query, context
        raise RuntimeError("upstream timed out")

    output = WebSearchTool(enabled=True, provider=provider).run(
        AgentToolInput(arguments={"query": "上海市教师职称政策"}),
        ToolExecutionContext(tenant_id="sch_test"),
    )

    assert output.ok is False
    assert output.error == "provider_error: upstream timed out"
    assert output.evidence[0]["tool_status"] == "provider_error"
    assert output.evidence[0]["attempted_external_call"] is True


def test_web_search_success_returns_evidence_summary_and_sources() -> None:
    output = WebSearchTool(enabled=True, provider=_recording_provider([])).run(
        AgentToolInput(arguments={"query": "上海市教师职称政策", "locale": "zh-CN"}),
        ToolExecutionContext(tenant_id="sch_test"),
    )

    assert output.ok is True
    assert output.evidence == [
        {
            "kind": "web_search_status",
            "tool_status": "success",
            "query": "上海市教师职称政策",
            "attempted_external_call": True,
            "result_count": 1,
            "reason": "",
        }
    ]
    assert output.artifacts[0]["content"]["tool_status"] == "success"
    assert output.artifacts[0]["content"]["result_count"] == 1
    assert output.sources == [{"title": "政策标题", "url": "https://example.edu/policy", "source": "web"}]


def _recording_provider(calls: list[tuple[str, dict[str, Any]]]):
    def provider(query: str, context: dict[str, Any]) -> dict[str, Any]:
        calls.append((query, context))
        return {
            "results": [
                {
                    "title": "政策标题",
                    "url": "https://example.edu/policy",
                    "content": "政策摘要",
                    "source": "web",
                }
            ]
        }

    return provider
