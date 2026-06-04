from __future__ import annotations

from typing import Any, Optional

from langchain_core.messages import BaseMessage, SystemMessage

from gateway_core.prompts.answer_context_prompt import (
    ANSWER_CONTEXT_PAYLOAD_PROMPT_ID,
    ANSWER_CONTEXT_PROMPT_ID,
    ANSWER_CONTEXT_PROMPT_VERSION,
    audience_mode,
    build_context_prompt,
    render_context_prompt,
)
from gateway_core.prompts.data_format_guard import build_data_format_guard
from gateway_core.prompts.prompt_domains import resolve_domain_id
from gateway_core.prompts.prompt_layer import PromptRender
from gateway_core.prompts.rules.answer_style import (
    build_answer_style_guard_prompt as _build_answer_style_guard_prompt,
    render_answer_style_guard_prompt as _render_answer_style_guard_prompt,
)


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
    return _build_answer_style_guard_prompt(wants_json=wants_json, has_data_context=has_data_context)


def render_answer_style_guard_prompt(wants_json: bool, has_data_context: bool) -> PromptRender:
    return _render_answer_style_guard_prompt(wants_json=wants_json, has_data_context=has_data_context)


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
