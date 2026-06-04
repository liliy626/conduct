from __future__ import annotations

import json
from typing import Any


OUTPUT_CONTRACT_VERSION = "2026-06-03.1"

ROUTES: tuple[str, ...] = ("data", "chat")
ANSWER_MODES: tuple[str, ...] = ("text", "data", "image", "plot", "chart", "slide", "multi")
OPTIONAL_TOOLS: tuple[str, ...] = (
    "time",
    "official_policy_search",
    "web_search",
    "plot",
    "chart",
    "generate_image_tool",
    "slide",
)
REQUIRED_OUTPUTS: tuple[str, ...] = (
    "data_evidence",
    "policy_evidence",
    "web_evidence",
    "chart_artifact",
    "plot_artifact",
    "image_artifact",
    "slide_artifact",
)
ARTIFACT_OUTPUTS: tuple[str, ...] = ("plot_artifact", "chart_artifact", "image_artifact", "slide_artifact")

OPTIONAL_TOOL_OUTPUTS: dict[str, str] = {
    "official_policy_search": "policy_evidence",
    "web_search": "web_evidence",
    "chart": "chart_artifact",
    "plot": "plot_artifact",
    "generate_image_tool": "image_artifact",
    "slide": "slide_artifact",
}

PER_TURN_CONTRACT_PLAN_FIELDS: tuple[str, ...] = (
    "contract_version",
    "route",
    "answer_focus",
    "allowed_tools",
    "required_outputs",
    "required_artifacts",
    "answer_mode",
    "reason",
)

DIRECT_ANSWER_KEYS: tuple[str, ...] = (
    "pure_business_data_markdown",
    "final_answer",
    "answer",
    "answer_markdown",
    "summary_markdown",
    "summary",
)
HANDOFF_COMPACT_KEYS: tuple[str, ...] = (
    "contract_version",
    "question",
    "status",
    "answer_focus",
    "summary",
    "final_answer",
    "answer",
    "answer_markdown",
    "pure_business_data_markdown",
    "business_domains",
    "items",
    "metrics",
    "key_findings",
    "caveats",
    "source_views",
    "external_evidence",
    "data_evidence",
    "evidence_board",
    "inter_agent_state",
    "tool_contract",
)
EVIDENCE_TASK_KEYS: tuple[str, ...] = ("dataset_label", "intent", "total_row_count", "query_may_have_more", "total_count_error")
EVIDENCE_SUMMARY_KEYS: tuple[str, ...] = ("truth_data_markdown", "notable_findings", "top_items", "row_sample")
EVIDENCE_LINEAGE_KEYS: tuple[str, ...] = ("tables_used", "row_count", "time_range")
EVIDENCE_BOARD_KEYS: tuple[str, ...] = ("business_clues", "caveats", "source_views", "tasks")
FINAL_HANDOFF_FIELDS: tuple[str, ...] = HANDOFF_COMPACT_KEYS


def per_turn_contract_plan_schema_text() -> str:
    """Compact prompt-facing schema generated from the runtime contract."""

    schema: dict[str, Any] = {
        "contract_version": OUTPUT_CONTRACT_VERSION,
        "route": "|".join(ROUTES),
        "answer_focus": [
            {
                "priority": "P0|P1|P2",
                "target_content": "string",
                "trigger_condition": "string",
            }
        ],
        "allowed_tools": list(OPTIONAL_TOOLS),
        "required_outputs": list(REQUIRED_OUTPUTS),
        "required_artifacts": list(ARTIFACT_OUTPUTS),
        "answer_mode": "|".join(ANSWER_MODES),
        "reason": "string",
    }
    return json.dumps(schema, ensure_ascii=False, separators=(",", ":"))


def final_answer_handoff_tool_description() -> str:
    fields = ", ".join(FINAL_HANDOFF_FIELDS)
    return (
        "当你已完成必要数据查询、联网/政策检索和业务证据核验，准备交接证据时调用。"
        "把紧凑 JSON 字符串放入 handoff_json。"
        f"handoff_json 可用字段：{fields}。"
        "不要把完整 Markdown 长答案放入 handoff_json；最终自然语言表达由 final answer 模型完成。"
        "目录盘点类问题优先交接 business_domains/items/source_views/caveats 等结构化字段，"
        "每项只保留名称、数量、代表性表或关键证据短语。"
    )
