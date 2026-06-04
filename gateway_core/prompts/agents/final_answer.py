from __future__ import annotations

from gateway_core.prompts.prompt_layer import PROMPT_REGISTRY, PromptTemplate
from gateway_core.prompts.rules.common_rules import (
    FINAL_ANSWER_EVIDENCE_ONLY_RULE,
    NO_INTERNALS_IN_USER_ANSWER_RULE,
    NO_TOOL_PROCESS_NARRATION_RULE,
)

FINAL_ANSWER_PROMPT_VERSION = "2026-06-03.1"
FINAL_ANSWER_SYSTEM_PROMPT_ID = "agents.final_answer.system"
FINAL_ANSWER_USER_PROMPT_ID = "agents.final_answer.user"


FINAL_ANSWER_SYSTEM_PROMPT = (
    "你根据客观证据包回答学校数据问题。"
    f"{FINAL_ANSWER_EVIDENCE_ONLY_RULE}"
    f"{NO_INTERNALS_IN_USER_ANSWER_RULE}"
    "在不改变事实的前提下，可以自由组织最终呈现方式；用自然、克制、面向学校管理者的语言，避免模板腔和生硬口号。"
)

FINAL_ANSWER_USER_INSTRUCTION = (
    "基于证据回答用户问题。呈现方式自由；结合业务提示词里的证据边界，用清楚、柔和、可读的学校业务语言表达；"
    f"{NO_TOOL_PROCESS_NARRATION_RULE}"
)


def _render_final_answer_system_prompt() -> str:
    return FINAL_ANSWER_SYSTEM_PROMPT


def _render_final_answer_prompt(evidence_packet: str) -> str:
    return "\n".join(
        [
            "客观证据包：",
            evidence_packet,
            FINAL_ANSWER_USER_INSTRUCTION,
        ]
    )


PROMPT_REGISTRY.register(
    PromptTemplate(
        prompt_id=FINAL_ANSWER_SYSTEM_PROMPT_ID,
        version=FINAL_ANSWER_PROMPT_VERSION,
        renderer=_render_final_answer_system_prompt,
        description="最终答案 system prompt",
        tags=("final_answer", "system"),
    )
)
PROMPT_REGISTRY.register(
    PromptTemplate(
        prompt_id=FINAL_ANSWER_USER_PROMPT_ID,
        version=FINAL_ANSWER_PROMPT_VERSION,
        renderer=_render_final_answer_prompt,
        description="最终答案 user prompt",
        tags=("final_answer", "user"),
    )
)


def build_final_answer_prompt(evidence_packet: str) -> str:
    return PROMPT_REGISTRY.render(
        FINAL_ANSWER_USER_PROMPT_ID,
        FINAL_ANSWER_PROMPT_VERSION,
        evidence_packet=evidence_packet,
    ).text


def build_final_answer_system_prompt() -> str:
    return PROMPT_REGISTRY.render(FINAL_ANSWER_SYSTEM_PROMPT_ID, FINAL_ANSWER_PROMPT_VERSION).text
