from __future__ import annotations

from gateway_core.prompts.prompt_layer import PROMPT_REGISTRY, PromptTemplate


ANSWER_EVIDENCE_PROMPT_VERSION = "2026-06-03.1"
ANSWER_POLICY_EVIDENCE_PROMPT_ID = "answers.evidence.policy"
ANSWER_COMPLEX_EVIDENCE_PROMPT_ID = "answers.evidence.complex"
ANSWER_EVIDENCE_BOARD_PROMPT_ID = "answers.evidence.board"

ANSWER_POLICY_EVIDENCE_TEXT = (
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

ANSWER_COMPLEX_EVIDENCE_TEXT = (
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

ANSWER_EVIDENCE_BOARD_TEXT = (
    "Evidence Board 回答要求：当检索结果(JSON)包含 evidence_board 时，回答必须区分三类信息："
    "1）直接事实：只能来自主问题已执行查询和 evidence_by_task 的结果；"
    "2）由事实触发的相关发现：只能来自 related_queries 或相关查询已经返回的证据；"
    "3）未探索线索：只能作为可继续核查的方向，不得把未探索线索写成事实。"
    "不得根据 business_clues 或 unexplored_clues 自行补全事实、名单、原因或结论。"
)


def _render_policy_evidence_prompt() -> str:
    return ANSWER_POLICY_EVIDENCE_TEXT


def _render_complex_evidence_prompt() -> str:
    return ANSWER_COMPLEX_EVIDENCE_TEXT


def _render_evidence_board_prompt() -> str:
    return ANSWER_EVIDENCE_BOARD_TEXT


PROMPT_REGISTRY.register(
    PromptTemplate(
        prompt_id=ANSWER_POLICY_EVIDENCE_PROMPT_ID,
        version=ANSWER_EVIDENCE_PROMPT_VERSION,
        renderer=_render_policy_evidence_prompt,
        description="政策证据回答规则",
        tags=("answer", "evidence", "policy"),
    )
)
PROMPT_REGISTRY.register(
    PromptTemplate(
        prompt_id=ANSWER_COMPLEX_EVIDENCE_PROMPT_ID,
        version=ANSWER_EVIDENCE_PROMPT_VERSION,
        renderer=_render_complex_evidence_prompt,
        description="复杂多证据回答规则",
        tags=("answer", "evidence", "complex"),
    )
)
PROMPT_REGISTRY.register(
    PromptTemplate(
        prompt_id=ANSWER_EVIDENCE_BOARD_PROMPT_ID,
        version=ANSWER_EVIDENCE_PROMPT_VERSION,
        renderer=_render_evidence_board_prompt,
        description="Evidence Board 回答规则",
        tags=("answer", "evidence", "board"),
    )
)
