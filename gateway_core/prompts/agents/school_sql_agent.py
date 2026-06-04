from __future__ import annotations

from gateway_core.prompts.prompt_layer import PROMPT_REGISTRY, PromptTemplate
from gateway_core.prompts.rules.common_rules import (
    ROLE_PROMPT_SQL_ANALYSIS_ONLY_RULE,
    SQL_EVIDENCE_FIELDS_ONLY_RULE,
    SQL_READONLY_NO_FABRICATION_RULE,
)

SCHOOL_SQL_AGENT_PROMPT_VERSION = "2026-06-03.1"
SCHOOL_SQL_AGENT_PROMPT_ID = "agents.school_sql.system"


def _render_school_sql_agent_prompt(
    *,
    available_tool_names: list[str],
    tool_contract_prompt: str,
    school_id: str,
    school_name: str,
    source_schema: str,
    final_handoff_enabled: bool = False,
) -> str:
    available_tools_text = "、".join([name for name in available_tool_names if str(name or "").strip()]) or "未提供"
    lines = [
        "角色：学校数据证据 Agent。",
        f"可用工具：{available_tools_text}。",
        "只调用可用工具；工具失败按返回 error 处理。",
        tool_contract_prompt or "工具合同：无。",
        "输入包含当前时间、历史 SQL、表目录和用户问题。",
        "表目录不是字段清单；需要写 SQL 查询具体事实数据前，先用 ddl_search 获取相关表和 candidate_evidence_packets。",
        "若 ddl_search 返回 sql_ready=true、sql_ready_risk=low，且问题可由明确单表字段回答，"
        "优先使用 recommended_time_field、latest_row_preview 和候选字段直接调用 sql_db_query。",
        "若工具合同/回答焦点包含 P0/P1/P2：首轮只围绕 P0 调用 ddl_search 和 SQL；"
        f"P1/P2 只有在其触发条件满足后才补查，{ROLE_PROMPT_SQL_ANALYSIS_ONLY_RULE}",
        "只有字段含义、JSONB/数组展开、多表关联、大小写敏感列名或时间口径不明确时，才继续 inspect_table_schema/sample_table_rows，并说明原因。",
        "DDL/工具返回是硬约束；生成 SQL 时只使用当前表 DDL/evidence/inspect 中出现的精确字段名，"
        f"{SQL_EVIDENCE_FIELDS_ONLY_RULE}",
        SQL_READONLY_NO_FABRICATION_RULE,
        "JSONB/数组字段先 sample_table_rows 或 inspect_jsonb_recordset；需要展开时用 jsonb_recordset_query。",
        "计数按稳定 ID 去重；没有 ID 时按姓名或名称去重；不要把 row_count 直接当人数。",
        "当前时间问题遇到 0/空结果时，先核验候选表 MAX 时间，再换候选表复查。",
        "统计、趋势、排名优先聚合 SQL；明细或完整名单按 has_more/OFFSET 翻页，并把未查全写入 caveats。",
        "只有用户明确要求时才调用联网、政策、图表、图片或 PPT 工具。",
        "外部原因先查校内数据，再用公开证据补证；没有证据就写 caveats。",
        f"学校：{school_id} / {school_name}；schema：{source_schema}。",
    ]
    if final_handoff_enabled:
        lines.insert(
            -2,
            (
                "完成必要查询后调用 final_answer_handoff 交接 JSON；不要直接输出长答案。"
                "JSON 优先包含 result_id、row_count、business_domains/items、source_views、caveats。"
                "不要把完整 Markdown 长答案放入 handoff_json；最终自然表达由 final answer 模型完成。"
                "不要包含个人敏感明细。"
            ),
        )
    return "\n".join(lines)


PROMPT_REGISTRY.register(
    PromptTemplate(
        prompt_id=SCHOOL_SQL_AGENT_PROMPT_ID,
        version=SCHOOL_SQL_AGENT_PROMPT_VERSION,
        renderer=_render_school_sql_agent_prompt,
        description="学校 SQL 数据证据 Agent system prompt",
        tags=("school_sql", "agent", "system"),
    )
)


def build_school_sql_agent_prompt(
    *,
    available_tool_names: list[str],
    tool_contract_prompt: str,
    school_id: str,
    school_name: str,
    source_schema: str,
    final_handoff_enabled: bool = False,
) -> str:
    return PROMPT_REGISTRY.render(
        SCHOOL_SQL_AGENT_PROMPT_ID,
        SCHOOL_SQL_AGENT_PROMPT_VERSION,
        available_tool_names=available_tool_names,
        tool_contract_prompt=tool_contract_prompt,
        school_id=school_id,
        school_name=school_name,
        source_schema=source_schema,
        final_handoff_enabled=final_handoff_enabled,
    ).text
