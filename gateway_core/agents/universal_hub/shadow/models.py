from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage


@dataclass(frozen=True)
class ShadowMetric:
    process_preserved: bool
    content_preserved: bool
    sources_preserved: bool
    sql_leak_prevented: bool
    artifact_embedded: bool
    duration_delta_ms: float
    legacy_tools_called: list[str]
    skill_tools_called: list[str]
    core_tools_preserved: bool = True
    tools_semantically_equivalent: bool = True
    fact_preserved: bool = True


@dataclass(frozen=True)
class ShadowRunRecord:
    test_case_id: str
    user_query: str
    contract_snapshot: dict[str, Any]
    legacy_tools_called: list[str]
    skill_tools_called: list[str]
    metrics: ShadowMetric
    audit_passed: bool


@dataclass(frozen=True)
class LiveShadowMetric:
    """Audit metrics for real LLM/DB live replay.

    Unlike fixture replay, this measures end-to-end live runtime. It must not
    reuse the 50ms wrapper-overhead gate used by ``ShadowMetric``.
    """

    process_preserved: bool
    content_preserved: bool
    sources_preserved: bool
    sql_leak_prevented: bool
    total_live_duration_ms: float


@dataclass(frozen=True)
class LiveShadowRunRecord:
    trace_id: str
    user_query: str
    metrics: LiveShadowMetric
    live_audit_passed: bool


@dataclass(frozen=True)
class LegacyTraceSnapshot:
    trace_id: str
    user_query: str
    history_messages_snapshot: list[dict[str, Any]]
    contract_snapshot: dict[str, Any]
    trace_payload: dict[str, Any] | None = None

    def to_langchain_messages(self) -> list[BaseMessage]:
        messages: list[BaseMessage] = []
        for item in self.history_messages_snapshot:
            role = str(item.get("role") or "user")
            content = str(item.get("content") or "")
            if role == "assistant":
                messages.append(AIMessage(content=content))
            elif role == "system":
                messages.append(SystemMessage(content=content))
            else:
                messages.append(HumanMessage(content=content))
        return messages
