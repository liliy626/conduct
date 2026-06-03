from __future__ import annotations

from types import SimpleNamespace

from gateway_core.tools.langchain_tools import _tool_policy_for_contract, build_langchain_agent_tools


def test_tool_policy_does_not_expose_business_prompt_context_by_default() -> None:
    contract = SimpleNamespace(allowed_tools=set(), required_outputs=set())

    policy = _tool_policy_for_contract(contract)

    assert "time" in policy
    assert "business_prompt_context" not in policy


def test_tool_policy_does_not_expose_business_prompt_context_even_when_contract_allows_it() -> None:
    contract = SimpleNamespace(allowed_tools={"business_prompt_context"}, required_outputs=set())

    policy = _tool_policy_for_contract(contract)

    assert "time" in policy
    assert "business_prompt_context" not in policy


def test_langchain_tools_do_not_build_business_prompt_context_tool() -> None:
    contract = SimpleNamespace(allowed_tools={"business_prompt_context", "time"}, required_outputs=set())

    tools = build_langchain_agent_tools(trace=None, tool_contract=contract)
    names = {tool.name for tool in tools}

    assert "time" in names
    assert "business_prompt_context" not in names
