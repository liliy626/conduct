from __future__ import annotations

from gateway_core.prompts.output_contracts.per_turn_contract_plan import PER_TURN_CONTRACT_PLAN_SCHEMA_TEXT
from gateway_core.prompts.prompt_layer import PROMPT_REGISTRY, PromptTemplate
from gateway_core.prompts.rules.common_rules import (
    CONTRACT_PLANNER_BOUNDARY_ONLY_RULE,
    CONTRACT_PLANNER_JSON_ONLY_RULE,
    CONTRACT_PLANNER_NO_FINAL_OR_SQL_RULE,
    NO_FABRICATION_FACTS_RULE,
    NO_FABRICATION_SCHOOL_FACTS_RULE,
    QUESTION_SCOPE_NO_EXPANSION_RULE,
    ROLE_PROMPT_ANALYSIS_ONLY_RULE,
)

CONTRACT_PLANNER_PROMPT_VERSION = "2026-06-03.1"
CONTRACT_PLANNER_SYSTEM_PROMPT_ID = "agents.contract_planner.system"
CONTRACT_PLANNER_USER_PROMPT_ID = "agents.contract_planner.user"


def _render_contract_planner_system_prompt(available_tools: list[str]) -> str:
    return (
        "你是学校智能问答网关的单轮契约规划器。"
        "你只负责本轮边界裁决：route、answer_focus、allowed_tools、required_artifacts、answer_mode。"
        f"{CONTRACT_PLANNER_NO_FINAL_OR_SQL_RULE}{NO_FABRICATION_FACTS_RULE}"
        f"{QUESTION_SCOPE_NO_EXPANSION_RULE}"
        "只允许选择当前可用工具列表中的非 SQL 工具。"
        f"当前可用非 SQL 工具：{', '.join(available_tools) if available_tools else '无'}。"
    )


def _render_contract_planner_user_prompt(
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
            CONTRACT_PLANNER_JSON_ONLY_RULE,
            CONTRACT_PLANNER_BOUNDARY_ONLY_RULE,
            f"硬约束：{NO_FABRICATION_FACTS_RULE}{QUESTION_SCOPE_NO_EXPANSION_RULE}",
            "route 规则：",
            "- route=data：需要学校数据、表目录、历史 SQL、政策/网页/工具证据、图表、PPT、图片等完成回答。开放式学校运营问题也属于 data，例如“最近有什么值得关注”“昨天发生的事情”。",
            f"- route=chat：仅限无需学校数据、无需工具、无需政策/网页证据的普通寒暄、通用写作、翻译、泛泛解释，{NO_FABRICATION_SCHOOL_FACTS_RULE}",
            "answer_focus 规则：",
            "- answer_focus 必须是数组。",
            f"- P0 必须完整复述用户原问题，只描述本轮回答焦点，{NO_FABRICATION_SCHOOL_FACTS_RULE}",
            "- 如需扩展，只能写 P1/P2；P1/P2 必须写明 trigger_condition；没有明确触发条件时不要扩展。",
            f"- {ROLE_PROMPT_ANALYSIS_ONLY_RULE}",
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
            f"输出 JSON Schema：{PER_TURN_CONTRACT_PLAN_SCHEMA_TEXT}",
            f"可选工具白名单：{available_tools}",
            f"记忆/会话上下文：{conversation_context}",
            f"元数据目录快照：{metadata_catalog_context}",
            f"DDL 向量检索配置：{ddl_vector_context}",
            f"业务提示词：{business_prompt_context}",
            f"历史 SQL 经验检索：{sql_experience_context}",
            f"用户最新问题：{question}",
        ]
    )


PROMPT_REGISTRY.register(
    PromptTemplate(
        prompt_id=CONTRACT_PLANNER_SYSTEM_PROMPT_ID,
        version=CONTRACT_PLANNER_PROMPT_VERSION,
        renderer=_render_contract_planner_system_prompt,
        description="单轮契约规划器 system prompt",
        tags=("contract", "planner", "system"),
    )
)
PROMPT_REGISTRY.register(
    PromptTemplate(
        prompt_id=CONTRACT_PLANNER_USER_PROMPT_ID,
        version=CONTRACT_PLANNER_PROMPT_VERSION,
        renderer=_render_contract_planner_user_prompt,
        description="单轮契约规划器 user prompt",
        tags=("contract", "planner", "user"),
    )
)


def build_contract_planner_system_prompt(available_tools: list[str]) -> str:
    return PROMPT_REGISTRY.render(
        CONTRACT_PLANNER_SYSTEM_PROMPT_ID,
        CONTRACT_PLANNER_PROMPT_VERSION,
        available_tools=available_tools,
    ).text


def build_contract_planner_user_prompt(
    *,
    question: str,
    conversation_context: str,
    metadata_catalog_context: str,
    ddl_vector_context: str,
    business_prompt_context: str,
    sql_experience_context: str,
    available_tools: list[str],
) -> str:
    return PROMPT_REGISTRY.render(
        CONTRACT_PLANNER_USER_PROMPT_ID,
        CONTRACT_PLANNER_PROMPT_VERSION,
        question=question,
        conversation_context=conversation_context,
        metadata_catalog_context=metadata_catalog_context,
        ddl_vector_context=ddl_vector_context,
        business_prompt_context=business_prompt_context,
        sql_experience_context=sql_experience_context,
        available_tools=available_tools,
    ).text
