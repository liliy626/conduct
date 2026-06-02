from __future__ import annotations

import json
import re
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Optional

from langchain_core.messages import BaseMessage, SystemMessage

from gateway_core.api.openai_compat.citation_formatter import citation_sources_from_payload
from gateway_core.prompts.data_format_guard import build_data_format_guard
from gateway_core.prompts.prompt_domains import (
    CROSS_DOMAIN_ANALYSIS_LAYER,
    DOMAIN_AGENT_LABEL,
    DOMAIN_CONTEXT_LAYER,
    expert_playbook_for_domain,
    final_answer_guide_for_domain,
    resolve_domain_id,
)

_GLOBAL_CONTEXT_BASE = (
    "请严格基于“检索结果(JSON)”作答，不得编造数据。"
    "先给结论，再给数据支撑。"
    "面向用户输出时禁止出现数据库字段名、数据库表名或 snake_case 技术标识。"
    "当检索结果中存在记录、统计值、名单、时间明细或任一可见数据时，严禁回答“暂无数据”“没有相关数据”“未检索到数据”“未找到记录”等表述。"
    "如果数据不完整或仅返回部分结果，应如实展示当前可见范围与限制，不得以“无数据”替代。"
)

_FINAL_REQUEST_LAYER_PROTOCOL = (
    "最终请求层级说明：最终发给模型的请求由“用户问题、数据上下文、system prompt、LLM messages”组成。"
    "用户问题是本轮唯一需要回答的目标；数据上下文是回答数据问题时的唯一事实依据，必须优先于历史消息中的旧结论；"
    "system prompt 规定安全边界，必须持续遵守；"
    "LLM messages 是原始对话历史，历史消息只作为对话上下文，不得用历史消息覆盖当前检索结果。"
    "如果历史消息与当前数据上下文冲突，以当前数据上下文为准，并用简短自然语言说明口径。"
)

_GLOBAL_ANSWER_STYLE_WITH_DATA = (
    "数据回答要求：所有数字、名单和判断必须来自当前数据上下文。"
    "风险、遗漏或流程问题只能按证据说明。"
)

_GLOBAL_ANSWER_STYLE_NO_DATA = (
    "无数据回答要求：不要编造数据。"
)

_GLOBAL_ANSWER_STYLE_JSON = (
    "JSON 要求：用户明确要求结构化结果时，直接输出合法 JSON。"
    "JSON之外不要补充任何自然语言。"
)

_POLICY_EVIDENCE_ANSWER_LAYER = (
    "政策证据回答要求：当检索结果(JSON)包含 policy_evidence 或 policy_evidence_required=true 时，"
    "回答必须区分“数据事实、政策依据、综合判断/建议”。"
    "当检索结果(JSON)包含 policy_time_context 时，必须按其中 start_date/end_date/reference_date 解释“最近、近一个月、最近发布”等相对时间，"
    "不得自行改用模型当前时间或历史消息里的旧时间口径；"
    "政策依据只能来自 policy_evidence 中的政策 chunks，并尽量带出政策标题、来源和官网链接；"
    "如果 policy_evidence 为空，但 evidence_by_task 中存在 web_search/web_evidence 证据，允许基于联网公开检索结果回答，"
    "并说明本地政策库未命中、以下依据来自公开网页检索；"
    "当存在“引用来源索引(JSON)”时，正文中涉及政策、通知、官网信息的句子必须使用编号引用，如 [1]、[2]；"
    "正文不要直接输出 http/https 明文网址，也不要把网址写成 Markdown 链接；"
    "不要在答案末尾手写“引用来源”清单，系统会用 OpenWebUI 原生引用组件展示来源；"
    "没有官网链接时说明“本条政策证据未提供官网链接”。"
    "不得编造政策依据、申报条件、评分标准或链接；政策证据不足时，必须明确说明还需要补充哪些政策文件。"
)

_COMPLEX_EVIDENCE_ANSWER_LAYER = (
    "复杂问题的回答要求：先按 evidence_by_task 中每个 sub_question 的结果提取可用证据，再进行结构化整合给出最终结论，"
    "避免直接逐条复述 SQL 明细。"
    "对于总体情况、概况、趋势、分布、排行、原因分析、建议类问题，输出必须有清晰分段："
    "1）简短结论：用1段说明总体判断和最重要数字；"
    "2）关键指标：列出记录数、合计值、占比或排名等核心指标；"
    "3）结构分析：说明主要类别、年级、班级、人群或项目来源；"
    "4）趋势/异常：如有时间序列或集中项，指出变化和需要关注的点；"
    "5）下一步建议：最多3条，必须基于数据且可执行。"
    "如果某一部分没有对应证据，可以省略该部分，不要编造。"
)

_EVIDENCE_BOARD_ANSWER_LAYER = (
    "Evidence Board 回答要求：当检索结果(JSON)包含 evidence_board 时，回答必须区分三类信息："
    "1）直接事实：只能来自主问题已执行查询和 evidence_by_task 的结果；"
    "2）由事实触发的相关发现：只能来自 related_queries 或相关查询已经返回的证据；"
    "3）未探索线索：只能作为可继续核查的方向，不得把未探索线索写成事实。"
    "不得根据 business_clues 或 unexplored_clues 自行补全事实、名单、原因或结论。"
)

_QUESTION_TYPE_FINAL_LAYER = (
    "轻量回答提示：请由模型根据用户问题、证据类型和上下文自主决定回答结构，不要机械套固定模板。"
    "可以参考问题类型选择表达重点：明细问题重对象/时间/状态；概况问题重总量/结构；"
    "趋势问题重变化方向；对比问题重差距和相对位置；诊断/建议问题要区分已证实事实、相关线索和待核查因素。"
    "材料整理或报告生成可以组织成可直接使用的文字，但不得编造不存在的经历、荣誉或政策条件。"
)


def _payload_requires_policy_evidence(payload: Any) -> bool:
    if not isinstance(payload, dict):
        return False
    if bool(payload.get("policy_evidence_required")):
        return True
    evidence = payload.get("policy_evidence")
    return isinstance(evidence, list) and bool(evidence)


def _payload_has_evidence_board(payload: Any) -> bool:
    return isinstance(payload, dict) and isinstance(payload.get("evidence_board"), dict)


def _json_safe(value: Any) -> Any:
    if isinstance(value, Decimal):
        try:
            return float(value)
        except Exception:
            return str(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return str(value)


def audience_mode(viewer_role: str) -> str:
    role = re.sub(r"\s+", "", str(viewer_role or "").strip().lower())
    if not role:
        return ""
    if "校长" in role or "principal" in role or "headmaster" in role:
        return "principal"
    if any(token in role for token in ["班主任", "年级组长", "年级主任", "homeroom", "gradelead", "grade_lead", "gradeleader"]):
        return "grade_homeroom"
    return ""


def _is_portrait_query(question: str, route_name: str, context_source: str, answer_text: str = "") -> bool:
    q = str(question or "")
    if any(
        token in q
        for token in [
            "班级画像",
            "年级画像",
            "班级概况",
            "年级概况",
            "班级综合分析",
            "年级整体表现",
            "学生画像",
            "同学画像",
            "学生档案",
            "教师画像",
            "老师画像",
            "教师档案",
            "老师档案",
            "教师情况",
            "老师情况",
        ]
    ):
        return True
    route = str(route_name or "").lower()
    source = str(context_source or "").lower()
    if "class_profile" in route or "class_profile" in source:
        if any(token in q for token in ["画像", "概况", "综合分析", "整体情况", "整体表现", "近况"]):
            return True
        if "画像（统计区间" in str(answer_text or ""):
            return True
    if ("student_profile" in route or "student_profile" in source) and any(
        token in q for token in ["画像", "档案", "情况", "概况", "综合", "介绍"]
    ):
        return True
    if ("teacher_management" in route or "teacher_management" in source) and any(
        token in q for token in ["画像", "档案", "情况", "概况", "全貌", "综合", "介绍"]
    ):
        return True
    return False


def is_portrait_query(
    *,
    question: str,
    route_name: str,
    context_source: str,
    answer_text: str = "",
) -> bool:
    """公开的画像问句判定（供路由/回答链路复用）。"""
    return _is_portrait_query(question, route_name, context_source, answer_text)


def audience_answer_guard(
    *,
    viewer_role: str,
    question: str,
    route_name: str,
    context_source: str,
    has_data_context: bool,
) -> str:
    if not has_data_context:
        return ""
    mode = audience_mode(viewer_role)
    if not mode:
        return ""
    if not _is_portrait_query(question, route_name, context_source):
        return ""
    if mode == "principal":
        return (
            "受众边界（校长）：风险分级、资源判断和决策事项必须有证据。"
        )
    return (
        "受众口径要求（年级组长/班主任）：回答必须采用“到班到人 + 可执行动作 + 下周重点”。"
        "先点明涉及的班级与重点对象；再给出本周可执行动作（最多5条）；"
        "最后给出下周重点（1-3条），需要可跟踪、可复盘。"
    )


def apply_audience_direct_style(
    *,
    text: str,
    viewer_role: str,
    question: str,
    route_name: str,
    context_source: str,
) -> str:
    body = str(text or "").strip()
    if not body:
        return body
    mode = audience_mode(viewer_role)
    if not mode or not _is_portrait_query(question, route_name, context_source, body):
        return body
    if any(token in body for token in ["风险分级：", "到班到人：", "可执行动作（本周）：", "下周重点："]):
        return body

    first_line = next((line.strip() for line in body.splitlines() if line.strip()), "")
    headline = first_line or "已完成本次画像汇总。"
    if mode == "principal":
        high_flag = any(token in body for token in ["一级预警", "未返校", "连续缺勤", "高风险"])
        mid_flag = any(token in body for token in ["异常", "缺交", "扣分", "病假", "事假"])
        return (
            f"{headline}\n\n"
            "风险分级：\n"
            + (f"- 高风险：{'存在高优先级对象，请当天闭环处置。' if high_flag else '本次未见明确高风险对象。'}\n")
            + (f"- 中风险：{'存在波动指标，建议按班级逐项复盘。' if mid_flag else '本次中风险波动较小，维持常规跟进。'}\n")
            + "- 低风险：其余指标总体可控，按现有节奏持续监测。\n\n"
            "资源与决策建议：\n"
            "1. 人力：异常班级优先配置年级管理力量，明确到人负责。\n"
            "2. 节奏：按“日跟进、周复盘”机制推进，避免问题跨周累积。\n"
            "3. 督导：围绕高频异常指标设置专项督导，跟踪整改完成率。\n\n"
            f"数据支撑：\n{body}"
        )
    return (
        f"本周结论：{headline}\n\n"
        "到班到人：\n"
        "- 到班：优先锁定异常指标靠前班级，逐班核对原因与进展。\n"
        "- 到人：对缺勤、未返校、缺交等对象建立跟进清单并责任到人。\n\n"
        "可执行动作（本周）：\n"
        "1. 今日内完成重点班级问题清单复核。\n"
        "2. 明确每个问题对象的责任老师与完成时限。\n"
        "3. 每日收口一次进展，形成可复盘记录。\n\n"
        "下周重点：\n"
        "- 聚焦连续波动指标，按班级做专项改进。\n"
        "- 对反复出现问题的对象做二次核查，防止反弹。\n\n"
        f"数据支撑：\n{body}"
    )


def build_answer_style_guard_prompt(wants_json: bool, has_data_context: bool) -> str:
    if wants_json:
        return _GLOBAL_ANSWER_STYLE_JSON
    if has_data_context:
        return _GLOBAL_ANSWER_STYLE_WITH_DATA
    return _GLOBAL_ANSWER_STYLE_NO_DATA


def assemble_llm_messages(
    *,
    user_question: str,
    data_context_prompt: str = "",
    system_prompt: str = "",
    style_guard: str = "",
    llm_messages: list[BaseMessage],
) -> list[BaseMessage]:
    """Assemble the final ordered message list sent to the LLM.

    The user question is intentionally accepted as a named input so callers have
    one stable assembly boundary for tracing and future prompt policies. The
    chat history itself remains in llm_messages.
    """
    del user_question
    assembled: list[BaseMessage] = []
    for prompt in (system_prompt, data_context_prompt, style_guard):
        text = str(prompt or "").strip()
        if text:
            assembled.append(SystemMessage(content=text))
    assembled.extend(list(llm_messages))
    return assembled

def build_context_prompt(
    *,
    payload: Any,
    question: str = "",
    viewer_role: str = "",
    dataset_id: str = "",
    feature_name: str = "",
    route_name: str = "",
    context_source: str = "",
    domain_id: str = "",
    context_note: str = "",
) -> str:
    resolved_domain = resolve_domain_id(
        dataset_id=dataset_id,
        feature_name=feature_name,
        route_name=route_name,
        context_source=context_source,
        domain_hint=domain_id,
    )
    agent_line = DOMAIN_AGENT_LABEL.get(resolved_domain, DOMAIN_AGENT_LABEL["generic_school_data"])
    domain_line = DOMAIN_CONTEXT_LAYER.get(resolved_domain, DOMAIN_CONTEXT_LAYER["generic_school_data"])

    lines = [agent_line, _FINAL_REQUEST_LAYER_PROTOCOL, _GLOBAL_CONTEXT_BASE, domain_line]
    expert_playbook = expert_playbook_for_domain(resolved_domain)
    if expert_playbook:
        lines.append(expert_playbook)
        lines.append(CROSS_DOMAIN_ANALYSIS_LAYER)
    final_answer_guide = final_answer_guide_for_domain(resolved_domain)
    if final_answer_guide:
        lines.append(final_answer_guide)
    lines.append(_QUESTION_TYPE_FINAL_LAYER)
    if _payload_requires_policy_evidence(payload):
        lines.append(_POLICY_EVIDENCE_ANSWER_LAYER)
    citation_sources = citation_sources_from_payload(payload)
    if citation_sources:
        lines.append(
            "引用格式要求：不要只在正文中写网站名称或域名。"
            "凡引用政策、通知、官网网页或联网资料，正文对应句末使用编号引用，例如 [1]；"
            "正文不要直接输出 http/https 明文网址，也不要使用 Markdown 链接；"
            "不要在答案末尾手写“引用来源”清单，系统会通过 OpenWebUI 原生引用组件展示来源。"
        )
    if _payload_has_evidence_board(payload):
        lines.append(_EVIDENCE_BOARD_ANSWER_LAYER)
    if isinstance(payload, dict) and payload.get("query_mode") in {
        "planned_multi_query",
        "planned_multi_source",
        "ddl_react_agent",
        "ddl_evidence",
        "react_agent",
        "gateway_agent",
    }:
        lines.append(_COMPLEX_EVIDENCE_ANSWER_LAYER)
    if any(
        token in str(question or "")
        for token in ["成果", "申报", "学术", "论文", "发表", "亮点", "述职", "评优", "业绩", "职称"]
    ):
        lines.append(
            "教师发展成果表达要求：不要只给流水账。"
            "必须把成果明细整理成可读的材料分析：简短结论 -> 数据总览 -> 分层亮点 -> 代表性成果表格 -> 可用于申报/述职的表达。"
            "如果证据中有成果名称、主办单位、级别、等第、积分等字段，必须输出一张 Markdown 表格列出至少 5 条代表性成果；不允许省略表格。"
            "开头要给出可由证据支持的总项目数、总积分或已检索范围，不能把 LIMIT 样本误说成全量。"
            "分层时优先使用证据中的指标层级、级别、等第、积分、主办单位和成果名称；"
            "如果证据里没有某类成果，不要编造该类别。"
        )
    if "职称" in str(question or ""):
        lines.append(
            "职称判断要求：必须区分校内数据事实、政策硬条件和待人工核验项。"
            "回答开头要简要说明本次参考了哪些证据类型，例如教师基础档案、当前学年积分、历年积分趋势、成果申报记录、政策依据；"
            "不得把校内积分或成果数量直接说成已经满足职称申报门槛，除非 policy_evidence 明确给出对应门槛；"
            "如果缺少当前职称、聘任/取得时间、学历、任教年限、师德考核、继续教育学分等证据，要明确列为待核验。"
        )
    audience = audience_mode(viewer_role)
    if audience == "principal":
        lines.append("受众提醒：以校长视角组织表达，优先结论、风险分级与决策建议。")
    elif audience == "grade_homeroom":
        lines.append("受众提醒：以年级组长/班主任视角组织表达，优先到班到人和可执行动作。")

    q = str(question or "").strip()
    if q:
        lines.append(f"当前问题：{q}")
    note = str(context_note or "").strip()
    if note:
        lines.append(note)
    if citation_sources:
        lines.append(f"引用来源索引(JSON):\n{json.dumps(citation_sources, ensure_ascii=False, default=_json_safe)}")
    lines.append(f"检索结果(JSON):\n{json.dumps(payload, ensure_ascii=False, default=_json_safe)}")
    return "\n".join(lines)


def build_class_grade_portrait_note(scope: str = "class") -> str:
    mode = str(scope or "class").strip().lower()
    if mode == "grade":
        return (
            "画像任务（年级画像）：你是一位年级组长与教育数据分析者。"
            "请基于检索结果构建“年级运行全景画像”，将离散数据转成可执行洞察。"
            "分析维度：1) 均衡性分析（班际作业与行规差异）；"
            "2) 资源消耗分析（文印总量与教学压力）；"
            "3) 异常预警（晨午检+请假的群体风险）；"
            "4) 学风研判（作业执行与文印协同）。"
            "只允许依据检索结果里的班级/年级数据，不得补充不存在的班级表现。"
        )
    return (
        "画像任务（班级画像）：只基于检索结果生成班级画像。"
        "分析维度：1) 基础情况；2) 晨午检；3) 作业；4) 文印；5) 行规；6) 请假。"
        "只允许依据检索结果中的班级数据，不得补充不存在的学生或班级事件。"
    )


def build_student_teacher_portrait_note(scope: str = "student") -> str:
    mode = str(scope or "student").strip().lower()
    if mode == "teacher":
        return (
            "画像任务（教师画像）：只基于检索结果生成教师画像。"
            "约束：结论必须基于检索结果的真实数据，不得编造。"
        )
    return (
        "画像任务（学生画像）：只基于检索结果生成学生画像。"
        "约束：必须基于检索结果的真实数据，不猜测。"
    )
