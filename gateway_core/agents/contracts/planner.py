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
_KNOWN_ANSWER_MODES = {"text", "data", "image", "plot", "chart", "slide", "multi"}
_KNOWN_ROUTES = {"chat", "data"}


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
        metadata_catalog_context: str = "",
        ddl_vector_context: str = "",
        business_prompt_context: str = "",
        sql_experience_context: str = "",
        available_tools: Iterable[str] = (),
    ) -> PerTurnContractPlan:
        available_tool_names = _dedupe([str(item or "").strip() for item in available_tools if str(item or "").strip()])
        system_prompt = _planner_system_prompt(available_tool_names)
        user_prompt = _planner_user_prompt(
            question=str(question or ""),
            conversation_context=str(conversation_context or ""),
            metadata_catalog_context=str(metadata_catalog_context or ""),
            ddl_vector_context=str(ddl_vector_context or ""),
            business_prompt_context=str(business_prompt_context or ""),
            sql_experience_context=str(sql_experience_context or ""),
            available_tools=available_tool_names,
        )
        structured_error = ""
        try:
            with_structured_output = getattr(self.model, "with_structured_output", None)
            if callable(with_structured_output):
                try:
                    structured = with_structured_output(PerTurnContractPlan)
                    result = structured.invoke([SystemMessage(content=system_prompt), HumanMessage(content=user_prompt)])
                    return _enrich_plan_with_business_context(
                        _coerce_plan(result, available_tool_names),
                        question=question,
                        business_prompt_context=business_prompt_context,
                        available_tools=available_tool_names,
                    )
                except Exception as exc:
                    structured_error = f"{type(exc).__name__}: {_safe_error_text(exc)}"
            invoke = getattr(self.model, "invoke", None)
            if callable(invoke):
                result = invoke([SystemMessage(content=system_prompt), HumanMessage(content=user_prompt)])
                plan = _coerce_plan(_extract_json_payload(result), available_tool_names)
                if structured_error and not plan.reason:
                    return _enrich_plan_with_business_context(
                        PerTurnContractPlan(
                            required_outputs=plan.required_outputs,
                            allowed_tools=plan.allowed_tools,
                            route=plan.route,
                            answer_mode=plan.answer_mode,
                            answer_focus=plan.answer_focus,
                            reason=f"plain_json_fallback_after_structured_error: {structured_error}",
                        ),
                        question=question,
                        business_prompt_context=business_prompt_context,
                        available_tools=available_tool_names,
                    )
                return _enrich_plan_with_business_context(
                    plan,
                    question=question,
                    business_prompt_context=business_prompt_context,
                    available_tools=available_tool_names,
                )
        except Exception as exc:
            reason = f"contract planner unavailable: {type(exc).__name__}: {_safe_error_text(exc)}"
            if structured_error:
                reason = f"{reason}; structured_error={structured_error}"
            return _enrich_plan_with_business_context(
                PerTurnContractPlan(
                    required_outputs=[],
                    allowed_tools=[],
                    route="data",
                    answer_mode="data",
                    answer_focus="",
                    reason=reason,
                ),
                question=question,
                business_prompt_context=business_prompt_context,
                available_tools=available_tool_names,
            )
        return _enrich_plan_with_business_context(
            PerTurnContractPlan(
                required_outputs=[],
                allowed_tools=[],
                route="data",
                answer_mode="data",
                answer_focus="",
                reason="contract planner unavailable",
            ),
            question=question,
            business_prompt_context=business_prompt_context,
            available_tools=available_tool_names,
        )


def _planner_system_prompt(available_tools: list[str]) -> str:
    return (
        "你是学校智能问答网关的 Contract Planner。"
        "你的任务不是回答用户，而是判断本轮应该普通聊天还是进入学校数据 Agent；"
        "如果进入数据 Agent，再判断本轮 ReAct Agent 应该暴露哪些非 SQL 工具，以及最终交付前必须完成哪些产物。"
        "只允许选择当前可用工具列表中的工具；SQL/DDL 查询工具不在这里规划，它们默认由主 Agent 使用。"
        "不要用关键词机械匹配，要根据用户真实语义、上下文追问和可用工具做判断。"
        f"当前可用非 SQL 工具：{', '.join(available_tools) if available_tools else '无'}。"
    )


def _planner_user_prompt(
    *,
    question: str,
    conversation_context: str,
    metadata_catalog_context: str,
    ddl_vector_context: str,
    business_prompt_context: str,
    sql_experience_context: str,
    available_tools: list[str],
) -> str:
    return "\n\n".join(
        [
            "请输出 PerTurnContractPlan。",
            "规划规则：",
            "- route=data：用户需要基于学校数据、表目录、历史 SQL 经验、政策/网页/工具证据、图表/PPT/图片等完成回答；"
            "开放式运营提问也属于数据问题，例如“有什么是我能关心的”“最近有什么值得关注”。",
            "- route=chat：仅限无需学校数据、无需工具、无需政策或网页证据的普通寒暄、通用写作、翻译、编程或泛泛解释。",
            "- 先基于元数据目录快照、业务提示词、历史 SQL 经验和记忆/会话上下文，判断本轮应该回答什么内容，并写入 answer_focus。",
            "- answer_focus 只描述回答焦点，不直接编造事实数字；事实仍必须由后续工具证据确认。",
            "- answer_focus 必须先复述用户原问题作为 P0；如需扩展，写成 P1/P2，并为每个扩展项写明触发条件。",
            "- 角色提示词只能影响分析角度、总结口径和建议风格，不能自动扩大查询范围。",
            "- 普通学校数据问答：allowed_tools 通常只需要 time 或为空；业务提示词已经预注入到本次规划和最终回答，不需要再暴露 business_prompt_context。",
            "- 只有用户明确要求查看/解释证据边界、业务提示词或系统如何约束回答时，才允许 business_prompt_context。",
            "- 需要政策、职称、申报依据、官方规则：允许 official_policy_search；如果用户要最新公开网页或出处链接，可允许 web_search。",
            "- 用户问“本周/本月/今天学校有哪些工作安排/重点工作/日程安排”时，按校内工作安排/每周安排/日程表数据回答；不要扩展成全员导师、AI五育、值班、公文等多业务域全量扫描，除非用户明确点名这些领域。",
            "- 用户需要真实数据图表且强调 PNG/保存为图片/严谨数据图：允许 plot，并要求 plot_artifact。",
            "- 用户需要交互式/HTML/SVG/可下载图表：允许 chart，并要求 chart_artifact。",
            "- 用户需要视觉图、大屏效果图、海报、宣传图、AI 生成图片、编辑图片：允许 generate_image_tool，并要求 image_artifact。",
            "- 用户需要 PPT/汇报材料/演示文稿：允许 slide，并要求 slide_artifact。",
            "- 如果同时需要多类证据或产物，answer_mode 用 multi。",
            f"可选工具白名单：{available_tools}",
            f"记忆/会话上下文：{_planner_context_preview(conversation_context, default='无')}",
            f"元数据目录快照：{_planner_context_preview(metadata_catalog_context, default='未提供')}",
            f"DDL 向量检索配置：{_planner_context_preview(ddl_vector_context, default='未提供')}",
            f"业务提示词：{_planner_context_preview(business_prompt_context, default='未提供')}",
            f"历史 SQL 经验检索：{_planner_context_preview(sql_experience_context, default='未提供')}",
            f"用户最新问题：{question}",
        ]
    )


def _coerce_plan(value: Any, available_tools: list[str]) -> PerTurnContractPlan:
    if isinstance(value, PerTurnContractPlan):
        plan = value
    elif isinstance(value, dict):
        plan = PerTurnContractPlan.model_validate(_normalize_plan_payload(value))
    else:
        content = getattr(value, "content", value)
        plan = PerTurnContractPlan.model_validate(_normalize_plan_payload(_extract_json_payload(content)))
    allowed = set(available_tools)
    return PerTurnContractPlan(
        required_outputs=[item for item in _dedupe(plan.required_outputs) if item in _KNOWN_OUTPUTS],
        allowed_tools=[item for item in _dedupe(plan.allowed_tools) if item in allowed],
        route=plan.route if plan.route in _KNOWN_ROUTES else "data",
        answer_mode=plan.answer_mode,
        answer_focus=str(getattr(plan, "answer_focus", "") or "").strip()[:300],
        reason=str(plan.reason or "").strip(),
    )


def _enrich_plan_with_business_context(
    plan: PerTurnContractPlan,
    *,
    question: str,
    business_prompt_context: str,
    available_tools: list[str],
) -> PerTurnContractPlan:
    allowed_set = set(available_tools)
    required_outputs = list(plan.required_outputs)
    allowed_tools = list(plan.allowed_tools)
    hints = _business_contract_hints(question=question, business_prompt_context=business_prompt_context)
    added: list[str] = []

    if hints["policy"] and "official_policy_search" in allowed_set:
        if "official_policy_search" not in allowed_tools:
            allowed_tools.append("official_policy_search")
            added.append("official_policy_search")
        if hints["require_policy"] and "policy_evidence" not in required_outputs:
            required_outputs.append("policy_evidence")
            added.append("policy_evidence")

    if hints["web"] and "web_search" in allowed_set:
        if "web_search" not in allowed_tools:
            allowed_tools.append("web_search")
            added.append("web_search")
        if hints["require_web"] and "web_evidence" not in required_outputs:
            required_outputs.append("web_evidence")
            added.append("web_evidence")

    enriched = plan
    if added:
        reason = str(plan.reason or "").strip()
        suffix = "业务提示词/用户问题触发补证工具: " + ", ".join(_dedupe(added))
        enriched = PerTurnContractPlan(
            required_outputs=[item for item in _dedupe(required_outputs) if item in _KNOWN_OUTPUTS],
            allowed_tools=[item for item in _dedupe(allowed_tools) if item in allowed_set],
            route=plan.route,
            answer_mode=plan.answer_mode,
            answer_focus=plan.answer_focus,
            reason=f"{reason}; {suffix}" if reason else suffix,
        )
    return _prioritize_contract_plan(enriched, question=question, available_tools=available_tools)


def _prioritize_contract_plan(
    plan: PerTurnContractPlan,
    *,
    question: str,
    available_tools: list[str],
) -> PerTurnContractPlan:
    if not _is_plain_work_schedule_query(question):
        sanitized = _sanitize_tools_by_original_question(plan, question=question, available_tools=available_tools)
        return _ensure_priority_focus(sanitized, question=question)
    sanitized = _sanitize_tools_by_original_question(plan, question=question, available_tools=available_tools)
    reason = str(plan.reason or "").strip()
    suffix = "按原问题优先级整理：P0 先回答本周工作安排；扩展项仅在触发条件满足后补查"
    return PerTurnContractPlan(
        required_outputs=[],
        allowed_tools=sanitized.allowed_tools,
        route="data",
        answer_mode="data",
        answer_focus=_work_schedule_priority_focus(question=question, model_focus=plan.answer_focus),
        reason=f"{reason}; {suffix}" if reason else suffix,
    )


def _ensure_priority_focus(plan: PerTurnContractPlan, *, question: str) -> PerTurnContractPlan:
    if plan.route != "data":
        return plan
    focus = str(plan.answer_focus or "").strip()
    if focus.startswith("P0 原问题："):
        return plan
    return PerTurnContractPlan(
        required_outputs=plan.required_outputs,
        allowed_tools=plan.allowed_tools,
        route=plan.route,
        answer_mode=plan.answer_mode,
        answer_focus=_generic_priority_focus(question=question, model_focus=focus),
        reason=plan.reason,
    )


def _generic_priority_focus(*, question: str, model_focus: str) -> str:
    expansion = _strip_original_question_from_focus(question=question, focus=model_focus)
    lines = [
        f"P0 原问题：{str(question or '').strip()}",
        "P0 首轮策略：Agent 首轮只围绕原问题选择 DDL/SQL 查询；优先使用元数据目录、DDL 召回和历史 SQL 中与原问题直接相关的表。",
    ]
    if expansion:
        lines.extend(
            [
                f"P1 可选扩展：{expansion}",
                "P1 触发条件：P0 数据为空/明显不完整，P0 结果直接指向该维度，或用户继续追问该管理维度时才补查。",
            ]
        )
    else:
        lines.extend(
            [
                "P1 可选扩展：角色提示词建议的管理维度、趋势、风险和优化建议。",
                "P1 触发条件：P0 已有数据支撑后，仅作为总结口径或后续追问方向，不得抢先扩大首轮查询范围。",
            ]
        )
    lines.extend(
        [
            "P2 外部证据：政策、官网、网页、公开链接。",
            "P2 触发条件：仅当用户原问题明确要求政策、依据、官网、链接、出处或公开实时信息时启用。",
            "角色提示词：只影响分析角度、总结口径和建议风格，不能自动扩大查询范围。",
        ]
    )
    return "\n".join(lines)


def _strip_original_question_from_focus(*, question: str, focus: str) -> str:
    text = str(focus or "").strip()
    if not text:
        return ""
    q = str(question or "").strip()
    replacements = [
        f"P0: 用户问题：{q}",
        f"P0：用户问题：{q}",
        f"P0: {q}",
        f"P0：{q}",
        f"用户问题：{q}",
        q,
    ]
    for item in replacements:
        text = text.replace(item, "")
    text = text.replace("P1:", "").replace("P1：", "").replace("P2:", "").replace("P2：", "")
    return " ".join(text.split()).strip("；;，,。 ")


def _sanitize_tools_by_original_question(
    plan: PerTurnContractPlan,
    *,
    question: str,
    available_tools: list[str],
) -> PerTurnContractPlan:
    allowed_set = set(available_tools)
    q = str(question or "")
    filtered_tools: list[str] = []
    for tool_name in _dedupe(plan.allowed_tools):
        if tool_name not in allowed_set:
            continue
        if tool_name == "official_policy_search" and not _has_policy_intent(q):
            continue
        if tool_name == "web_search" and not _has_web_intent(q):
            continue
        filtered_tools.append(tool_name)
    filtered_outputs: list[str] = []
    for output_name in _dedupe(plan.required_outputs):
        if output_name == "policy_evidence" and not _has_policy_intent(q):
            continue
        if output_name == "web_evidence" and not _has_web_intent(q):
            continue
        filtered_outputs.append(output_name)
    return PerTurnContractPlan(
        required_outputs=[item for item in filtered_outputs if item in _KNOWN_OUTPUTS],
        allowed_tools=filtered_tools,
        route=plan.route,
        answer_mode=plan.answer_mode,
        answer_focus=plan.answer_focus,
        reason=plan.reason,
    )


def _work_schedule_priority_focus(*, question: str, model_focus: str) -> str:
    p1_items = _work_schedule_expansion_items(model_focus)
    p1_text = "、".join(p1_items) if p1_items else "公文通知、值班记录、活动发布等相关安排来源"
    return "\n".join(
        [
            f"P0 原问题：{str(question or '').strip()}",
            "P0 首查范围：工作安排、每周安排、日程录入、周计划等直接承载校内工作安排的数据表。",
            "P0 首轮策略：没有触发条件时，Agent 首轮只查 P0；先汇总事项、时间、责任部门/人员、地点和状态。",
            f"P1 可选扩展：{p1_text}。",
            "P1 触发条件：P0 结果为空/明显不完整，P0 记录明确引用相关活动或部门，或用户明确要求全校统筹、跨部门协同、督办、活动来源。",
            "P2 外部证据：政策、官网、网页、公开链接。",
            "P2 触发条件：仅当用户原问题明确要求政策、依据、官网、链接、出处或公开实时信息时启用。",
            "角色提示词：只影响总结口径和管理建议角度，不能自动扩大查询范围。",
        ]
    )


def _work_schedule_expansion_items(model_focus: str) -> list[str]:
    text = str(model_focus or "")
    candidates = [
        "全员导师活动",
        "AI五育平台活动",
        "公文通知",
        "值班重点事项",
        "跨部门协同",
        "教育局督办",
    ]
    return [item for item in candidates if item in text]


def _is_plain_work_schedule_query(question: str) -> bool:
    q = str(question or "")
    if not q.strip():
        return False
    schedule_tokens = ["工作安排", "重点工作", "日程安排", "周安排", "每周安排", "本周安排"]
    time_tokens = ["本周", "这周", "本月", "今天", "明天", "近期", "最近"]
    external_or_policy_tokens = ["政策", "依据", "官网", "链接", "出处", "文件", "制度", "标准", "规则", "规范"]
    return (
        any(token in q for token in schedule_tokens)
        and any(token in q for token in time_tokens)
        and not any(token in q for token in external_or_policy_tokens)
    )


def _has_policy_intent(question: str) -> bool:
    policy_question_tokens = ["政策", "依据", "要求", "规则", "规范", "条例", "评审", "申报", "文件", "制度", "标准"]
    return any(token in str(question or "") for token in policy_question_tokens)


def _has_web_intent(question: str) -> bool:
    web_question_tokens = ["最新", "官网", "网站", "网页", "链接", "出处", "公开", "实时", "查网站", "查官网", "联网"]
    return any(token in str(question or "") for token in web_question_tokens)


def _business_contract_hints(*, question: str, business_prompt_context: str) -> dict[str, bool]:
    q = str(question or "")
    context = str(business_prompt_context or "")
    policy_intent = _has_policy_intent(q)
    web_intent = _has_web_intent(q)
    return {
        "policy": policy_intent,
        "require_policy": policy_intent,
        "web": web_intent,
        "require_web": web_intent,
    }


def _normalize_plan_payload(value: dict[str, Any]) -> dict[str, Any]:
    payload = dict(value or {})
    payload["answer_focus"] = _normalize_answer_focus(payload.get("answer_focus"))
    answer_mode = str(payload.get("answer_mode") or "data").strip().lower()
    if answer_mode not in _KNOWN_ANSWER_MODES:
        answer_mode = "data"
    payload["answer_mode"] = answer_mode
    route = str(payload.get("route") or "data").strip().lower()
    if route not in _KNOWN_ROUTES:
        route = "data"
    payload["route"] = route
    return payload


def _normalize_answer_focus(value: Any) -> str:
    if isinstance(value, dict):
        lines: list[str] = []
        for key in ("P0", "p0", "P1", "p1", "P2", "p2"):
            item = value.get(key)
            if item is None:
                continue
            text = str(item or "").strip()
            if text:
                lines.append(f"{str(key).upper()}: {text}")
        if lines:
            return "\n".join(lines)
    return str(value or "").strip()


def _planner_context_preview(text: str, *, default: str, limit: int = 1800) -> str:
    clean = str(text or "").strip()
    if not clean:
        return default
    clean = clean.replace("\r\n", "\n")
    return clean[:limit] + ("..." if len(clean) > limit else "")


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


def _safe_error_text(exc: Exception) -> str:
    text = str(exc or "").strip()
    if not text:
        return ""
    return text[:500]


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
