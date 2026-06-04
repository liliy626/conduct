from __future__ import annotations

from gateway_core.tools import policy_tool
from gateway_core.tools.policy_tool import PolicyTool
from gateway_core.tools.tool_core import AgentToolInput, ToolExecutionContext


def test_policy_tool_defaults_to_official_policy_database_search(monkeypatch) -> None:
    calls: list[str] = []

    def fake_search(query: str):
        calls.append(query)
        return [
            {
                "title": "上海市教师职称评审通知",
                "chunk": "继续教育学时应符合申报要求。",
                "official_url": "https://example.edu/policy",
                "source_name": "official_policy.policy_pages",
            }
        ]

    monkeypatch.setattr(policy_tool, "search_official_policy_evidence", fake_search)

    output = PolicyTool().run(
        AgentToolInput(arguments={"query": "继续教育学时影响职称吗"}),
        ToolExecutionContext(),
    )

    assert calls == ["继续教育学时影响职称吗"]
    assert output.ok is True
    assert output.artifacts[0]["type"] == "official_policy"
    assert output.artifacts[0]["content"]["policy_evidence"][0]["title"] == "上海市教师职称评审通知"
    assert output.sources[0]["url"] == "https://example.edu/policy"
