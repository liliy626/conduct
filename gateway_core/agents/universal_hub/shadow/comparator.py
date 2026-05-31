from __future__ import annotations

import time
from typing import Any, AsyncIterator

from gateway_core.agents.universal_hub.models import SkillEvent
from gateway_core.agents.universal_hub.shadow.facts import fact_signature_matches
from gateway_core.agents.universal_hub.shadow.models import LiveShadowMetric, ShadowMetric

CORE_TOOL_CATEGORIES: dict[str, frozenset[str]] = {
    "school_data_query": frozenset({"sql_db_query", "jsonb_recordset_query"}),
    "policy_search": frozenset({"official_policy_search"}),
    "web_search": frozenset({"web_search"}),
    "visual_artifact": frozenset({"chart", "plot", "generate_image_tool", "slide"}),
}


class SkillStreamComparator:
    """Compare legacy stream events with the Skill wrapper stream.

    This is intentionally graph-free and HTTP-free. It only measures whether
    the wrapper preserves observable stream characteristics.
    """

    @staticmethod
    async def compare_streams(
        *,
        user_query: str,
        legacy_stream: AsyncIterator[tuple[str, Any]],
        skill_stream: AsyncIterator[SkillEvent],
    ) -> ShadowMetric:
        legacy = await _collect_legacy(legacy_stream)
        started = time.perf_counter()
        skill = await _collect_skill(skill_stream)
        duration_delta_ms = (time.perf_counter() - started) * 1000
        return _build_shadow_metric(user_query, legacy, skill, duration_delta_ms)

    @staticmethod
    async def compare_live_streams(
        *,
        user_query: str,
        legacy_stream: AsyncIterator[tuple[str, Any]],
        skill_stream: AsyncIterator[SkillEvent],
    ) -> tuple[LiveShadowMetric, ShadowMetric]:
        legacy = await _collect_legacy(legacy_stream)
        started = time.perf_counter()
        skill = await _collect_skill(skill_stream)
        duration_ms = (time.perf_counter() - started) * 1000
        shadow_metric = _build_shadow_metric(user_query, legacy, skill, duration_ms)
        live_metric = LiveShadowMetric(
            process_preserved=skill["has_process"] or not _live_process_required(legacy),
            content_preserved=skill["has_content"],
            sources_preserved=shadow_metric.core_tools_preserved,
            sql_leak_prevented=not skill["sql_leaked"],
            total_live_duration_ms=duration_ms,
        )
        return live_metric, shadow_metric


def _build_shadow_metric(
    user_query: str,
    legacy: dict[str, Any],
    skill: dict[str, Any],
    duration_delta_ms: float,
) -> ShadowMetric:
        core_tools_preserved = _core_tools_preserved(legacy["tools"], skill["tools"])
        tools_semantically_equivalent = _tools_semantically_equivalent(legacy["tools"], skill["tools"])
        fact_preserved = fact_signature_matches(legacy["content_text"], skill["content_text"])

        needs_artifact = _question_mentions_visual(user_query) or legacy["has_artifact"]
        return ShadowMetric(
            process_preserved=legacy["has_process"] == skill["has_process"],
            content_preserved=legacy["has_content"] == skill["has_content"],
            sources_preserved=legacy["has_sources"] == skill["has_sources"],
            sql_leak_prevented=not skill["sql_leaked"],
            artifact_embedded=(skill["has_artifact"] if needs_artifact else True),
            duration_delta_ms=duration_delta_ms,
            legacy_tools_called=legacy["tools"],
            skill_tools_called=skill["tools"],
            core_tools_preserved=core_tools_preserved,
            tools_semantically_equivalent=tools_semantically_equivalent,
            fact_preserved=fact_preserved,
        )


async def _collect_legacy(stream: AsyncIterator[tuple[str, Any]]) -> dict[str, Any]:
    out = {
        "has_process": False,
        "has_content": False,
        "has_sources": False,
        "has_artifact": False,
        "tools": [],
        "content_text": "",
    }
    async for event_type, raw_data in stream:
        if event_type in {"reasoning", "process"}:
            out["has_process"] = True
        elif event_type in {"final_content", "content"}:
            out["has_content"] = True
            out["content_text"] += _event_text(raw_data)
        elif event_type == "sources":
            out["has_sources"] = True
        elif event_type == "artifact":
            out["has_artifact"] = True
        elif event_type == "tool_start":
            tool_name = _tool_name(raw_data)
            if tool_name:
                out["tools"].append(tool_name)
    return out


async def _collect_skill(stream: AsyncIterator[SkillEvent]) -> dict[str, Any]:
    out = {
        "has_process": False,
        "has_content": False,
        "has_sources": False,
        "has_artifact": False,
        "tools": [],
        "sql_leaked": False,
        "content_text": "",
    }
    async for event in stream:
        text = _event_text(event.data)
        tool_name = _tool_name(event.data)
        if event.event_type == "process":
            out["has_process"] = True
            if tool_name:
                out["tools"].append(tool_name)
        elif event.event_type == "content":
            out["has_content"] = True
            out["sql_leaked"] = out["sql_leaked"] or _looks_like_sql(text)
            out["content_text"] += text
        elif event.event_type == "evidence":
            out["has_sources"] = True
        elif event.event_type == "artifact":
            out["has_artifact"] = True
        elif event.event_type == "tool_start":
            if tool_name:
                out["tools"].append(tool_name)
    return out


def _tool_name(data: Any) -> str:
    if not isinstance(data, dict):
        return ""
    return str(data.get("tool") or data.get("tool_name") or "").strip()


def _event_text(data: Any) -> str:
    if isinstance(data, str):
        return data
    if isinstance(data, dict):
        return str(data.get("text") or data.get("content") or "")
    return ""


def _looks_like_sql(text: str) -> bool:
    upper = str(text or "").upper()
    return "SELECT " in upper or " FROM " in upper or "INSERT " in upper or "UPDATE " in upper


def _question_mentions_visual(question: str) -> bool:
    text = str(question or "")
    return any(term in text for term in ("图", "可视化", "大屏", "图片", "画"))


def _core_tools_preserved(legacy_tools: list[str], skill_tools: list[str]) -> bool:
    legacy_categories = _tool_categories(legacy_tools)
    skill_categories = _tool_categories(skill_tools)
    return legacy_categories.issubset(skill_categories)


def _tools_semantically_equivalent(legacy_tools: list[str], skill_tools: list[str]) -> bool:
    if _core_tools_preserved(legacy_tools, skill_tools):
        return True
    return set(legacy_tools).issubset(set(skill_tools))


def _live_process_required(legacy: dict[str, Any]) -> bool:
    return bool(legacy["has_process"] or legacy["tools"])


def _tool_categories(tools: list[str]) -> set[str]:
    categories: set[str] = set()
    for tool in tools:
        for category, names in CORE_TOOL_CATEGORIES.items():
            if tool in names:
                categories.add(category)
    return categories
