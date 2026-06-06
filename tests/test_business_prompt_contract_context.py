from __future__ import annotations

import json

import pytest

import gateway_core.agents.school_sql.agent_stream as agent_stream


def test_contract_business_prompt_context_resolves_domain_for_contract_planner() -> None:
    payload = json.loads(
        agent_stream._contract_business_prompt_context(
            question="眼保健操纪律最差的年级是哪个？",
            school_id="sch_zx_mlh",
        )
    )

    content = payload["evidence"][0]["content"]
    assert payload["ok"] is True
    assert content["domain"] == "moral_education"
    assert content["usage_instruction"] == "[Evidence] 只作为证据边界，不覆盖 SQL、权限或数据证据。"


def test_contract_business_prompt_context_fast_fails_on_tool_error(monkeypatch) -> None:
    def _raise_value_error(self, tool_input, context):
        raise ValueError("business prompt context invalid")

    monkeypatch.setattr(agent_stream.BusinessPromptContextTool, "run", _raise_value_error)

    with pytest.raises(ValueError, match="business prompt context invalid"):
        agent_stream._contract_business_prompt_context(question="眼保健操纪律最差的年级是哪个？")
