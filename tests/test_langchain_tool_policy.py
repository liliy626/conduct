from __future__ import annotations

from types import SimpleNamespace

from gateway_core.tools.langchain_tools import _tool_policy_for_contract


def test_tool_policy_does_not_expose_business_prompt_context_by_default() -> None:
    contract = SimpleNamespace(allowed_tools=set(), required_outputs=set())

    policy = _tool_policy_for_contract(contract)

    assert "time" in policy
    assert "business_prompt_context" not in policy


def test_tool_policy_exposes_business_prompt_context_when_contract_allows_it() -> None:
    contract = SimpleNamespace(allowed_tools={"business_prompt_context"}, required_outputs=set())

    policy = _tool_policy_for_contract(contract)

    assert "business_prompt_context" in policy
