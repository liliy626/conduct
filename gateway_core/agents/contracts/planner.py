from __future__ import annotations

import json
from typing import Any, Iterable

from langchain_core.messages import HumanMessage, SystemMessage

from gateway_core.agents.contracts.models import PerTurnContractPlan
from gateway_core.infra.utils import dedupe as _dedupe
from gateway_core.infra.utils import extract_json_with_fence


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
        "你是学校智能问答网关的单轮契约规划器。"
        "你只负责本轮边界裁决：route、answer_focus、allowed_tools、required_artifacts、answer_mode。"
        "不要生成最终答案，不要规划 SQL/DDL，不要编造事实、数字或结论。"
        "不要扩大用户问题范围，不要因为角色提示词扩大查询范围。"
        "只允许选择当前可用工具列表中的非 SQL 工具。"
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
            "请输出 PerTurnContractPlan，且只输出合法 JSON。",
            "你只做本轮边界裁决，不回答问题，不规划 SQL/DDL。",
            "硬约束：不要编造事实、数字、结论；不要扩大用户问题范围；不要因为角色提示词扩大查询范围。",
            "route 规则：",
            "- route=data：需要学校数据、表目录、历史 SQL、政策/网页/工具证据、图表、PPT、图片等完成回答。开放式学校运营问题也属于 data，例如“最近有什么值得关注”“昨天发生的事情”。",
            "- route=chat：仅限无需学校数据、无需工具、无需政策/网页证据的普通寒暄、通用写作、翻译、泛泛解释，不得编造学校事实。",
            "answer_focus 规则：",
            "- answer_focus 必须是数组。",
            "- P0 必须完整复述用户原问题，只描述本轮回答焦点，不得编造学校事实。",
            "- 如需扩展，只能写 P1/P2；P1/P2 必须写明 trigger_condition；没有明确触发条件时不要扩展。",
            "- 角色提示词不能自动扩大查询范围；它只影响分析角度、总结口径和建议风格。",
            "allowed_tools 规则：",
            "- 普通学校数据问答：allowed_tools 通常为空或仅包含 time。",
            "- 业务提示词、元数据目录、历史 SQL 经验已经预注入规划上下文。",
            "- 问题推理需要政策、职称、申报依据、官方规则：允许 official_policy_search。",
            "- 问题推理需要最新公开网页、出处链接、在线来源：允许 web_search。",
            "artifact 规则：",
            "- PNG/保存为图片/严谨数据图：allowed_tools 包含 plot，required_artifacts 包含 plot_artifact。",
            "- 交互式/HTML/SVG/可下载图表：allowed_tools 包含 chart，required_artifacts 包含 chart_artifact。",
            "- 视觉图/大屏效果图/海报/宣传图/AI 生成图片/编辑图片：allowed_tools 包含 generate_image_tool，required_artifacts 包含 image_artifact。",
            "- PPT/汇报材料/演示文稿：allowed_tools 包含 slide，required_artifacts 包含 slide_artifact。",
            "answer_mode 规则：",
            "- 只需要文本回答：text；需要学校数据或工具证据回答：data；同时需要多类证据或多类产物：multi。",
            '输出 JSON Schema：{"route":"data|chat","answer_focus":[{"priority":"P0|P1|P2","target_content":"string","trigger_condition":"string"}],"allowed_tools":["time|official_policy_search|web_search|plot|chart|generate_image_tool|slide"],"required_artifacts":["plot_artifact|chart_artifact|image_artifact|slide_artifact"],"answer_mode":"text|data|multi"}',
            f"可选工具白名单：{available_tools}",
            f"记忆/会话上下文：{_planner_context_preview(conversation_context, default='无')}",
            f"元数据目录快照：{compact_metadata_catalog_for_contract(metadata_catalog_context)}",
            f"DDL 向量检索配置：{_planner_context_preview(ddl_vector_context, default='未提供')}",
            f"业务提示词：{compact_business_prompt_for_contract(business_prompt_context)}",
            f"历史 SQL 经验检索：{compact_sql_experience_for_contract(sql_experience_context)}",
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
    required_outputs = [
        item
        for item in _dedupe([*list(plan.required_outputs or []), *list(getattr(plan, "required_artifacts", []) or [])])
        if item in _KNOWN_OUTPUTS
    ]
    return PerTurnContractPlan(
        required_outputs=required_outputs,
        allowed_tools=[item for item in _dedupe(plan.allowed_tools) if item in allowed],
        route=plan.route if plan.route in _KNOWN_ROUTES else "data",
        answer_mode=plan.answer_mode,
        answer_focus=_normalize_answer_focus(getattr(plan, "answer_focus", "")).strip()[:500],
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
    sanitized = _sanitize_tools_by_original_question(plan, question=question, available_tools=available_tools)
    return _ensure_priority_focus(sanitized, question=question)


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
                "P1 触发条件：P0 数据为空/明显不完整，P0 结果直接指向该维度。",
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
            "P2 触发条件：分析出用户原问题明确要求政策、依据、官网、链接、出处或公开实时信息时启用。",
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
    required_outputs = payload.get("required_outputs")
    required_artifacts = payload.get("required_artifacts")
    merged_outputs: list[Any] = []
    if isinstance(required_outputs, list):
        merged_outputs.extend(required_outputs)
    if isinstance(required_artifacts, list):
        merged_outputs.extend(required_artifacts)
    if merged_outputs:
        payload["required_outputs"] = _dedupe([str(item or "").strip() for item in merged_outputs if str(item or "").strip()])
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
    if isinstance(value, list):
        lines: list[str] = []
        for item in value:
            if not isinstance(item, dict):
                text = str(item or "").strip()
                if text:
                    lines.append(text)
                continue
            priority = str(item.get("priority") or "").strip().upper() or "P0"
            target = str(item.get("target_content") or item.get("target") or "").strip()
            trigger = str(item.get("trigger_condition") or item.get("trigger") or "").strip()
            if not target:
                continue
            if priority == "P0":
                line = f"P0 原问题：{target}"
            elif priority == "P1":
                line = f"P1 可选扩展：{target}"
            elif priority == "P2":
                line = f"P2 外部证据：{target}"
            else:
                line = f"{priority}：{target}"
            if trigger:
                line = f"{line}\n{priority} 触发条件：{trigger}"
            lines.append(line)
        if lines:
            return "\n".join(lines)
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


def compact_metadata_catalog_for_contract(text: str, *, limit: int = 1200, max_lines: int = 28) -> str:
    clean = str(text or "").strip()
    if not clean:
        return "未提供"
    seen: set[str] = set()
    lines: list[str] = []
    for raw_line in clean.replace("\r\n", "\n").splitlines():
        line = " ".join(str(raw_line or "").split())
        if not line:
            continue
        key = line
        if ": table " in key:
            prefix, _, suffix = key.partition(": table ")
            table_name = suffix.split("；", 1)[0].strip()
            key = f"{prefix}: table {table_name}"
            line = key
        if key in seen:
            continue
        seen.add(key)
        lines.append(line)
        if len(lines) >= max_lines:
            break
    compact = "\n".join(lines).strip()
    if len(seen) >= max_lines:
        compact += "\n...<metadata catalog truncated>"
    return compact[:limit] + ("..." if len(compact) > limit else "")


def compact_sql_experience_for_contract(text: str, *, max_items: int = 3) -> str:
    clean = str(text or "").strip()
    if not clean:
        return "未提供"
    try:
        parsed = json.loads(clean)
    except Exception:
        return _planner_context_preview(clean, default="未提供", limit=700)
    if not isinstance(parsed, dict):
        return _planner_context_preview(clean, default="未提供", limit=700)
    experiences = parsed.get("experiences")
    if not isinstance(experiences, list):
        return _planner_context_preview(clean, default="未提供", limit=700)
    items: list[dict[str, Any]] = []
    for item in experiences[:max_items]:
        if not isinstance(item, dict):
            continue
        compact_item: dict[str, Any] = {
            "question": item.get("question"),
            "table_refs": item.get("table_refs") or [],
            "similarity": _round_float(item.get("similarity")),
            "row_count": item.get("row_count"),
            "answer_summary": item.get("answer_summary"),
        }
        items.append({key: value for key, value in compact_item.items() if value not in (None, "", [])})
    payload = {
        "source": parsed.get("source"),
        "query": parsed.get("query"),
        "experience_count": parsed.get("experience_count") or len(experiences),
        "manual_hint_count": parsed.get("manual_hint_count") or 0,
        "experiences": items,
        "raw_sql_policy": "raw_sql omitted in contract planner; inspect full history only after strong relevance in main Agent.",
    }
    return json.dumps(payload, ensure_ascii=False, default=str)


def compact_business_prompt_for_contract(text: str, *, limit: int = 900) -> str:
    clean = str(text or "").strip()
    if not clean:
        return "未提供"
    try:
        parsed = json.loads(clean)
    except Exception:
        return _planner_context_preview(clean, default="未提供", limit=limit)
    if not isinstance(parsed, dict):
        return _planner_context_preview(clean, default="未提供", limit=limit)
    compact: dict[str, Any] = {
        "ok": parsed.get("ok"),
        "warnings": parsed.get("warnings") or [],
    }
    evidence = parsed.get("evidence")
    if isinstance(evidence, list) and evidence:
        content = evidence[0].get("content") if isinstance(evidence[0], dict) else None
        if isinstance(content, dict):
            role_context = content.get("role_context")
            if isinstance(role_context, dict):
                compact["role_context"] = {
                    key: role_context.get(key)
                    for key in ("role_name", "role_key", "functional_areas", "data_response_logic", "professional_focus")
                    if role_context.get(key)
                }
            boundaries = content.get("evidence_boundaries")
            if isinstance(boundaries, list):
                compact["evidence_boundaries"] = boundaries[:3]
            if content.get("question_focus"):
                compact["question_focus"] = content.get("question_focus")
    return _planner_context_preview(json.dumps(compact, ensure_ascii=False, default=str), default="未提供", limit=limit)


def _round_float(value: Any) -> Any:
    if isinstance(value, (float, int)):
        return round(float(value), 4)
    return value


def _extract_json_payload(value: Any) -> dict[str, Any]:
    return extract_json_with_fence(value)


def _safe_error_text(exc: Exception) -> str:
    text = str(exc or "").strip()
    if not text:
        return ""
    return text[:500]
