from __future__ import annotations

from gateway_core.agents.contracts.tool_contract import ToolContract


def test_policy_search_result_completes_required_policy_evidence_even_when_empty() -> None:
    contract = ToolContract(question="继续教育学时影响职称吗", required_outputs={"policy_evidence"})

    contract.record_tool_result(
        "official_policy_search",
        {
            "ok": True,
            "sources": [],
            "artifacts": [
                {
                    "type": "official_policy",
                    "content": {"policy_evidence": [], "official_urls": []},
                }
            ],
        },
    )

    assert contract.is_completed("policy_evidence")
    assert contract.handoff_block_payload() is None


def test_web_search_result_completes_required_web_evidence_and_keeps_artifacts() -> None:
    contract = ToolContract(question="官网最新链接", required_outputs={"web_evidence"})
    artifact = {
        "type": "web_search_result",
        "content": {"results": [{"title": "上海市教委", "url": "https://edu.sh.gov.cn/"}]},
    }

    contract.record_tool_result(
        "web_search",
        {
            "ok": True,
            "sources": [{"title": "上海市教委", "url": "https://edu.sh.gov.cn/"}],
            "artifacts": [artifact],
        },
    )

    assert contract.is_completed("web_evidence")
    assert contract.handoff_block_payload() is None
    assert contract.artifacts == [artifact]
