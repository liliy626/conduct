from __future__ import annotations

import json
from typing import Any, Iterable

from langchain_core.messages import HumanMessage, SystemMessage

from gateway_core.agents.contracts.models import PerTurnContractPlan


_KNOWN_OUTPUTS = {
    "data_evidence",
    "policy_evidence",
    "web_evidence",
    "chart_artifact",
    "plot_artifact",
    "image_artifact",
    "slide_artifact",
}


class ContractPlanner:
    """Small semantic planner for per-turn tool contracts.

    This replaces keyword routing. It asks the configured chat model for a
    structured contract; if the model cannot produce one, the safe fallback is
    an empty optional-tool contract, not keyword guessing.
    """

    def __init__(self, model: Any):
        self.model = model

    def plan_turn(
        self,
        *,
        question: str,
        conversation_context: str = "",
        available_tools: Iterable[str] = (),
    ) -> PerTurnContractPlan:
        available_tool_names = _dedupe([str(item or "").strip() for item in available_tools if str(item or "").strip()])
        system_prompt = _planner_system_prompt(available_tool_names)
        user_prompt = _planner_user_prompt(
            question=str(question or ""),
            conversation_context=str(conversation_context or ""),
            available_tools=available_tool_names,
        )
        try:
            with_structured_output = getattr(self.model, "with_structured_output", None)
            if callable(with_structured_output):
                structured = with_structured_output(PerTurnContractPlan)
                result = structured.invoke([SystemMessage(content=system_prompt), HumanMessage(content=user_prompt)])
                return _coerce_plan(result, available_tool_names)
            invoke = getattr(self.model, "invoke", None)
            if callable(invoke):
                result = invoke([SystemMessage(content=system_prompt), HumanMessage(content=user_prompt)])
                return _coerce_plan(_extract_json_payload(result), available_tool_names)
        except Exception as exc:
            return PerTurnContractPlan(
                required_outputs=[],
                allowed_tools=[],
                answer_mode="data",
                reason=f"contract planner unavailable: {type(exc).__name__}",
            )
        return PerTurnContractPlan(required_outputs=[], allowed_tools=[], answer_mode="data", reason="contract planner unavailable")


def _planner_system_prompt(available_tools: list[str]) -> str:
    return (
        "你是学校智能问答网关的 Contract Planner。"
        "你的任务不是回答用户，而是判断本轮 ReAct Agent 应该暴露哪些非 SQL 工具，以及最终交付前必须完成哪些产物。"
        "只允许选择当前可用工具列表中的工具；SQL/DDL 查询工具不在这里规划，它们默认由主 Agent 使用。"
        "不要用关键词机械匹配，要根据用户真实语义、上下文追问和可用工具做判断。"
        f"当前可用非 SQL 工具：{', '.join(available_tools) if available_tools else '无'}。"
    )


def _planner_user_prompt(*, question: str, conversation_context: str, available_tools: list[str]) -> str:
    return "\n\n".join(
        [
            "请输出 PerTurnContractPlan。",
            "规划规则：",
            "- 普通学校数据问答：allowed_tools 通常只需要 time、business_prompt_context 或为空；不要暴露图片、PPT、联网、政策工具。",
            "- 需要政策、职称、申报依据、官方规则：允许 official_policy_search；如果用户要最新公开网页或出处链接，可允许 web_search。",
            "- 用户需要真实数据图表且强调 PNG/保存为图片/严谨数据图：允许 plot，并要求 plot_artifact。",
            "- 用户需要交互式/HTML/SVG/可下载图表：允许 chart，并要求 chart_artifact。",
            "- 用户需要视觉图、大屏效果图、海报、宣传图、AI 生成图片、编辑图片：允许 generate_image_tool，并要求 image_artifact。",
            "- 用户需要 PPT/汇报材料/演示文稿：允许 slide，并要求 slide_artifact。",
            "- 如果同时需要多类证据或产物，answer_mode 用 multi。",
            f"可选工具白名单：{available_tools}",
            f"历史上下文：{conversation_context or '无'}",
            f"用户最新问题：{question}",
        ]
    )


def _coerce_plan(value: Any, available_tools: list[str]) -> PerTurnContractPlan:
    if isinstance(value, PerTurnContractPlan):
        plan = value
    elif isinstance(value, dict):
        plan = PerTurnContractPlan.model_validate(value)
    else:
        content = getattr(value, "content", value)
        plan = PerTurnContractPlan.model_validate(_extract_json_payload(content))
    allowed = set(available_tools)
    return PerTurnContractPlan(
        required_outputs=[item for item in _dedupe(plan.required_outputs) if item in _KNOWN_OUTPUTS],
        allowed_tools=[item for item in _dedupe(plan.allowed_tools) if item in allowed],
        answer_mode=plan.answer_mode,
        reason=str(plan.reason or "").strip(),
    )


def _extract_json_payload(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    text = str(getattr(value, "content", value) or "").strip()
    if text.startswith("```"):
        text = text.strip("`")
        text = text.removeprefix("json").strip()
    try:
        loaded = json.loads(text)
        return loaded if isinstance(loaded, dict) else {}
    except Exception:
        return {}


def _dedupe(values: Iterable[Any]) -> list[str]:
    seen: set[str] = set()
    output: list[str] = []
    for value in values:
        text = str(value or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        output.append(text)
    return output

