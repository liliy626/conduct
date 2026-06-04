from __future__ import annotations

from gateway_core.prompts.prompt_layer import PROMPT_REGISTRY, PromptTemplate


ANSWER_CONTEXT_BASE_PROMPT_ID = "answers.context.global_base"
ANSWER_CONTEXT_BASE_PROMPT_VERSION = "2026-06-03.1"
ANSWER_REQUEST_PROTOCOL_PROMPT_ID = "answers.context.request_protocol"
ANSWER_REQUEST_PROTOCOL_PROMPT_VERSION = "2026-06-03.1"
ANSWER_QUESTION_TYPE_PROMPT_ID = "answers.context.question_type"
ANSWER_QUESTION_TYPE_PROMPT_VERSION = "2026-06-03.1"
ANSWER_CITATION_FORMAT_PROMPT_ID = "answers.context.citation_format"
ANSWER_CITATION_FORMAT_PROMPT_VERSION = "2026-06-03.1"
ANSWER_TEACHER_DEVELOPMENT_PROMPT_ID = "answers.context.teacher_development"
ANSWER_TEACHER_DEVELOPMENT_PROMPT_VERSION = "2026-06-03.1"
ANSWER_TITLE_EVALUATION_PROMPT_ID = "answers.context.title_evaluation"
ANSWER_TITLE_EVALUATION_PROMPT_VERSION = "2026-06-03.1"
ANSWER_AUDIENCE_REMINDER_PROMPT_ID = "answers.context.audience_reminder"
ANSWER_AUDIENCE_REMINDER_PROMPT_VERSION = "2026-06-03.1"

ANSWER_CONTEXT_BASE_TEXT = (
    "请严格基于“检索结果(JSON)”作答，不得编造数据。"
    "先给结论，再给数据支撑。"
    "面向用户输出时禁止出现数据库字段名、数据库表名或 snake_case 技术标识。"
    "当检索结果中存在记录、统计值、名单、时间明细或任一可见数据时，严禁回答“暂无数据”“没有相关数据”“未检索到数据”“未找到记录”等表述。"
    "如果数据不完整或仅返回部分结果，应如实展示当前可见范围与限制，不得以“无数据”替代。"
)

ANSWER_REQUEST_PROTOCOL_TEXT = (
    "最终请求层级说明：最终发给模型的请求由“用户问题、数据上下文、system prompt、LLM messages”组成。"
    "用户问题是本轮唯一需要回答的目标；数据上下文是回答数据问题时的唯一事实依据，必须优先于历史消息中的旧结论；"
    "system prompt 规定安全边界，必须持续遵守；"
    "LLM messages 是原始对话历史，历史消息只作为对话上下文，不得用历史消息覆盖当前检索结果。"
    "如果历史消息与当前数据上下文冲突，以当前数据上下文为准，并用简短自然语言说明口径。"
)

ANSWER_QUESTION_TYPE_TEXT = (
    "轻量回答提示：请由模型根据用户问题、证据类型和上下文自主决定回答结构，不要机械套固定模板。"
    "可以参考问题类型选择表达重点：明细问题重对象/时间/状态；概况问题重总量/结构；"
    "趋势问题重变化方向；对比问题重差距和相对位置；诊断/建议问题要区分已证实事实、相关线索和待核查因素。"
    "材料整理或报告生成可以组织成可直接使用的文字，但不得编造不存在的经历、荣誉或政策条件。"
)

ANSWER_CITATION_FORMAT_TEXT = (
    "引用格式要求：不要只在正文中写网站名称或域名。"
    "凡引用政策、通知、官网网页或联网资料，正文对应句末使用编号引用，例如 [1]；"
    "正文不要直接输出 http/https 明文网址，也不要使用 Markdown 链接；"
    "不要在答案末尾手写“引用来源”清单，系统会通过 OpenWebUI 原生引用组件展示来源。"
)

ANSWER_TEACHER_DEVELOPMENT_TEXT = (
    "教师发展成果表达要求：不要只给流水账。"
    "必须把成果明细整理成可读的材料分析：简短结论 -> 数据总览 -> 分层亮点 -> 代表性成果表格 -> 可用于申报/述职的表达。"
    "如果证据中有成果名称、主办单位、级别、等第、积分等字段，必须输出一张 Markdown 表格列出至少 5 条代表性成果；不允许省略表格。"
    "开头要给出可由证据支持的总项目数、总积分或已检索范围，不能把 LIMIT 样本误说成全量。"
    "分层时优先使用证据中的指标层级、级别、等第、积分、主办单位和成果名称；"
    "如果证据里没有某类成果，不要编造该类别。"
)

ANSWER_TITLE_EVALUATION_TEXT = (
    "职称判断要求：必须区分校内数据事实、政策硬条件和待人工核验项。"
    "回答开头要简要说明本次参考了哪些证据类型，例如教师基础档案、当前学年积分、历年积分趋势、成果申报记录、政策依据；"
    "不得把校内积分或成果数量直接说成已经满足职称申报门槛，除非 policy_evidence 明确给出对应门槛；"
    "如果缺少当前职称、聘任/取得时间、学历、任教年限、师德考核、继续教育学分等证据，要明确列为待核验。"
)

ANSWER_AUDIENCE_REMINDER_TEXT: dict[str, str] = {
    "principal": "受众提醒：以校长视角组织表达，优先结论、风险分级与决策建议。",
    "grade_homeroom": "受众提醒：以年级组长/班主任视角组织表达，优先到班到人和可执行动作。",
}


def _render_answer_context_base_prompt() -> str:
    return ANSWER_CONTEXT_BASE_TEXT


def _render_answer_request_protocol_prompt() -> str:
    return ANSWER_REQUEST_PROTOCOL_TEXT


def _render_answer_question_type_prompt() -> str:
    return ANSWER_QUESTION_TYPE_TEXT


def _render_answer_citation_format_prompt() -> str:
    return ANSWER_CITATION_FORMAT_TEXT


def _render_answer_teacher_development_prompt() -> str:
    return ANSWER_TEACHER_DEVELOPMENT_TEXT


def _render_answer_title_evaluation_prompt() -> str:
    return ANSWER_TITLE_EVALUATION_TEXT


def _render_answer_audience_reminder_prompt(*, audience: str = "") -> str:
    return ANSWER_AUDIENCE_REMINDER_TEXT.get(str(audience or "").strip(), "")


PROMPT_REGISTRY.register(
    PromptTemplate(
        prompt_id=ANSWER_CONTEXT_BASE_PROMPT_ID,
        version=ANSWER_CONTEXT_BASE_PROMPT_VERSION,
        renderer=_render_answer_context_base_prompt,
        description="最终回答检索结果基础规则",
        tags=("answer", "context", "rule"),
    )
)
PROMPT_REGISTRY.register(
    PromptTemplate(
        prompt_id=ANSWER_CITATION_FORMAT_PROMPT_ID,
        version=ANSWER_CITATION_FORMAT_PROMPT_VERSION,
        renderer=_render_answer_citation_format_prompt,
        description="最终回答引用格式规则",
        tags=("answer", "context", "citation"),
    )
)
PROMPT_REGISTRY.register(
    PromptTemplate(
        prompt_id=ANSWER_TEACHER_DEVELOPMENT_PROMPT_ID,
        version=ANSWER_TEACHER_DEVELOPMENT_PROMPT_VERSION,
        renderer=_render_answer_teacher_development_prompt,
        description="教师发展成果表达规则",
        tags=("answer", "context", "teacher_development"),
    )
)
PROMPT_REGISTRY.register(
    PromptTemplate(
        prompt_id=ANSWER_TITLE_EVALUATION_PROMPT_ID,
        version=ANSWER_TITLE_EVALUATION_PROMPT_VERSION,
        renderer=_render_answer_title_evaluation_prompt,
        description="职称判断表达规则",
        tags=("answer", "context", "title_evaluation"),
    )
)
PROMPT_REGISTRY.register(
    PromptTemplate(
        prompt_id=ANSWER_AUDIENCE_REMINDER_PROMPT_ID,
        version=ANSWER_AUDIENCE_REMINDER_PROMPT_VERSION,
        renderer=_render_answer_audience_reminder_prompt,
        description="最终回答受众视角提醒",
        tags=("answer", "context", "audience"),
    )
)
PROMPT_REGISTRY.register(
    PromptTemplate(
        prompt_id=ANSWER_REQUEST_PROTOCOL_PROMPT_ID,
        version=ANSWER_REQUEST_PROTOCOL_PROMPT_VERSION,
        renderer=_render_answer_request_protocol_prompt,
        description="最终回答请求层级规则",
        tags=("answer", "context", "request"),
    )
)
PROMPT_REGISTRY.register(
    PromptTemplate(
        prompt_id=ANSWER_QUESTION_TYPE_PROMPT_ID,
        version=ANSWER_QUESTION_TYPE_PROMPT_VERSION,
        renderer=_render_answer_question_type_prompt,
        description="最终回答问题类型表达规则",
        tags=("answer", "context", "question_type"),
    )
)


def build_answer_context_base_prompt() -> str:
    return PROMPT_REGISTRY.render(ANSWER_CONTEXT_BASE_PROMPT_ID, ANSWER_CONTEXT_BASE_PROMPT_VERSION).text


def build_answer_request_protocol_prompt() -> str:
    return PROMPT_REGISTRY.render(ANSWER_REQUEST_PROTOCOL_PROMPT_ID, ANSWER_REQUEST_PROTOCOL_PROMPT_VERSION).text


def build_answer_question_type_prompt() -> str:
    return PROMPT_REGISTRY.render(ANSWER_QUESTION_TYPE_PROMPT_ID, ANSWER_QUESTION_TYPE_PROMPT_VERSION).text


def build_answer_citation_format_prompt() -> str:
    return PROMPT_REGISTRY.render(ANSWER_CITATION_FORMAT_PROMPT_ID, ANSWER_CITATION_FORMAT_PROMPT_VERSION).text


def build_answer_teacher_development_prompt() -> str:
    return PROMPT_REGISTRY.render(ANSWER_TEACHER_DEVELOPMENT_PROMPT_ID, ANSWER_TEACHER_DEVELOPMENT_PROMPT_VERSION).text


def build_answer_title_evaluation_prompt() -> str:
    return PROMPT_REGISTRY.render(ANSWER_TITLE_EVALUATION_PROMPT_ID, ANSWER_TITLE_EVALUATION_PROMPT_VERSION).text


def build_answer_audience_reminder_prompt(*, audience: str) -> str:
    return PROMPT_REGISTRY.render(
        ANSWER_AUDIENCE_REMINDER_PROMPT_ID,
        ANSWER_AUDIENCE_REMINDER_PROMPT_VERSION,
        audience=audience,
    ).text
