from __future__ import annotations


NO_FABRICATION_FACTS_RULE = "不要编造事实、数字或结论。"
NO_FABRICATION_SCHOOL_FACTS_RULE = "不得编造学校事实。"

QUESTION_SCOPE_NO_EXPANSION_RULE = "不要扩大用户问题范围，不要因为角色提示词扩大查询范围。"
ROLE_PROMPT_ANALYSIS_ONLY_RULE = "角色提示词不能自动扩大查询范围；它只影响分析角度、总结口径和建议风格。"
ROLE_PROMPT_SQL_ANALYSIS_ONLY_RULE = "角色提示词只影响分析角度，不能自动扩大查询范围。"

CONTRACT_PLANNER_NO_FINAL_OR_SQL_RULE = "不要生成最终答案，不要规划 SQL/DDL，"
CONTRACT_PLANNER_JSON_ONLY_RULE = "请输出 PerTurnContractPlan，且只输出合法 JSON。"
CONTRACT_PLANNER_BOUNDARY_ONLY_RULE = "你只做本轮边界裁决，不回答问题，不规划 SQL/DDL。"

FINAL_ANSWER_EVIDENCE_ONLY_RULE = "所有数字、名单、判断、政策与来源必须来自证据，不得新增未提供的事实。"
NO_INTERNALS_IN_USER_ANSWER_RULE = (
    "禁止向用户泄露 SQL、数据表名、工具名、Handoff JSON、内部节点名或“我先查询/我需要查看”等过程性话术。"
)
NO_TOOL_PROCESS_NARRATION_RULE = "不要泄露内部结构，不要复述工具过程，不要说“根据校医院反馈”等未提供来源。"

SQL_READONLY_NO_FABRICATION_RULE = "只执行当前 schema 的只读 SELECT；不得编造表、字段、数字、名单或政策。"
SQL_EVIDENCE_FIELDS_ONLY_RULE = (
    "不要把其他候选表或历史 SQL 的字段迁移到本表；字段没有证据时先 inspect_table_schema 修正。历史 SQL 只作参考。"
)
