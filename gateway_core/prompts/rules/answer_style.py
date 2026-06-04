from __future__ import annotations

from gateway_core.prompts.prompt_layer import PROMPT_REGISTRY, PromptBuilder, PromptRender, PromptTemplate


ANSWER_STYLE_PROMPT_VERSION = "2026-06-03.1"
ANSWER_STYLE_WITH_DATA_PROMPT_ID = "answers.style.with_data"
ANSWER_STYLE_NO_DATA_PROMPT_ID = "answers.style.no_data"
ANSWER_STYLE_JSON_PROMPT_ID = "answers.style.json"

ANSWER_STYLE_WITH_DATA_TEXT = (
    "数据回答要求：所有数字、名单和判断必须来自当前数据上下文。"
    "风险、遗漏或流程问题只能按证据说明。"
)
ANSWER_STYLE_NO_DATA_TEXT = "无数据回答要求：不要编造数据。"
ANSWER_STYLE_JSON_TEXT = (
    "JSON 要求：用户明确要求结构化结果时，直接输出合法 JSON。"
    "JSON之外不要补充任何自然语言。"
)


def _render_answer_style_with_data_prompt() -> str:
    return ANSWER_STYLE_WITH_DATA_TEXT


def _render_answer_style_no_data_prompt() -> str:
    return ANSWER_STYLE_NO_DATA_TEXT


def _render_answer_style_json_prompt() -> str:
    return ANSWER_STYLE_JSON_TEXT


PROMPT_REGISTRY.register(
    PromptTemplate(
        prompt_id=ANSWER_STYLE_WITH_DATA_PROMPT_ID,
        version=ANSWER_STYLE_PROMPT_VERSION,
        renderer=_render_answer_style_with_data_prompt,
        description="有数据上下文时的最终回答风格约束",
        tags=("answer", "style", "data"),
    )
)
PROMPT_REGISTRY.register(
    PromptTemplate(
        prompt_id=ANSWER_STYLE_NO_DATA_PROMPT_ID,
        version=ANSWER_STYLE_PROMPT_VERSION,
        renderer=_render_answer_style_no_data_prompt,
        description="无数据上下文时的最终回答风格约束",
        tags=("answer", "style", "no_data"),
    )
)
PROMPT_REGISTRY.register(
    PromptTemplate(
        prompt_id=ANSWER_STYLE_JSON_PROMPT_ID,
        version=ANSWER_STYLE_PROMPT_VERSION,
        renderer=_render_answer_style_json_prompt,
        description="用户要求 JSON 时的最终回答风格约束",
        tags=("answer", "style", "json"),
    )
)


def answer_style_prompt_id(*, wants_json: bool, has_data_context: bool) -> str:
    if wants_json:
        return ANSWER_STYLE_JSON_PROMPT_ID
    if has_data_context:
        return ANSWER_STYLE_WITH_DATA_PROMPT_ID
    return ANSWER_STYLE_NO_DATA_PROMPT_ID


def render_answer_style_guard_prompt(*, wants_json: bool, has_data_context: bool) -> PromptRender:
    prompt_id = answer_style_prompt_id(wants_json=wants_json, has_data_context=has_data_context)
    return (
        PromptBuilder(PROMPT_REGISTRY)
        .add(prompt_id, ANSWER_STYLE_PROMPT_VERSION)
        .build(prompt_id=prompt_id, version=ANSWER_STYLE_PROMPT_VERSION)
    )


def build_answer_style_guard_prompt(*, wants_json: bool, has_data_context: bool) -> str:
    return render_answer_style_guard_prompt(wants_json=wants_json, has_data_context=has_data_context).text
