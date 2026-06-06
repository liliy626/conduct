from __future__ import annotations

from types import SimpleNamespace

import gateway_core.agents.school_sql.agent_stream as agent_stream
from gateway_core.agents.contracts.tool_contract import ToolContract
from gateway_core.agents.school_sql.tool_contract_events import record_tool_result


def test_final_answer_handoff_allows_after_sql_data_evidence_contract_event() -> None:
    contract = ToolContract(question="今天请假情况", required_outputs={"data_evidence"})
    tools = SimpleNamespace(tool_contract=contract)

    record_tool_result(
        tools,
        "sql_db_query",
        {
            "task_id": "ddl_sql_query_1",
            "allowed": True,
            "intent": "raw_sql_select",
            "dataset_label": "学生请假_学生请假",
            "row_count": 1,
            "row_sample": [{"班级": "六年级1班", "人数": 3}],
            "sql_lineage": {"tables_used": ["学生请假_学生请假"], "row_count": 1},
            "evidence_summary": {"row_sample": [{"班级": "六年级1班", "人数": 3}]},
            "raw_sql_handle": "trace://current/task/ddl_sql_query_1/raw_rows",
        },
    )
    tool = agent_stream._final_answer_handoff_tool(
        trace=None,
        final_model=None,
        question="今天请假情况",
        source_views_fn=lambda: ["学生请假_学生请假"],
        tool_contract=contract,
    )

    result = tool.func('{"summary":"今天有请假记录"}')

    assert result.startswith("FINAL_ANSWER_HANDOFF_JSON: ")
    assert contract.handoff_block_payload() is None
