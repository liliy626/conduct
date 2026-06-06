from __future__ import annotations

from gateway_core.agents.contracts.tool_contract import ToolContract


def test_policy_search_result_completes_required_policy_evidence_even_when_empty() -> None:
    contract = ToolContract(question="继续教育学时影响职称吗", required_outputs={"policy_evidence"})

    contract.record_tool_result(
        "official_policy_search",
        {
            "ok": True,
            "sources": [],
            "artifacts": [
                {
                    "type": "official_policy",
                    "content": {"policy_evidence": [], "official_urls": []},
                }
            ],
        },
    )

    assert contract.is_completed("policy_evidence")
    assert contract.handoff_block_payload() is None


def test_web_search_result_completes_required_web_evidence_and_keeps_artifacts() -> None:
    contract = ToolContract(question="官网最新链接", required_outputs={"web_evidence"})
    artifact = {
        "type": "web_search_result",
        "content": {"results": [{"title": "上海市教委", "url": "https://edu.sh.gov.cn/"}]},
    }

    contract.record_tool_result(
        "web_search",
        {
            "ok": True,
            "sources": [{"title": "上海市教委", "url": "https://edu.sh.gov.cn/"}],
            "artifacts": [artifact],
        },
    )

    assert contract.is_completed("web_evidence")
    assert contract.handoff_block_payload() is None
    assert contract.artifacts == [artifact]


def test_sql_result_completes_required_data_evidence() -> None:
    contract = ToolContract(question="今天请假情况", required_outputs={"data_evidence"})

    contract.record_tool_result(
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

    assert contract.is_completed("data_evidence")
    assert contract.handoff_block_payload() is None


def test_jsonb_recordset_result_completes_required_data_evidence() -> None:
    contract = ToolContract(question="今天执勤安排", required_outputs={"data_evidence"})

    contract.record_tool_result(
        "jsonb_recordset_query",
        {
            "task_id": "ddl_sql_query_1",
            "allowed": True,
            "intent": "jsonb_recordset_select",
            "dataset_label": "执勤安排",
            "row_count": 1,
            "row_sample": [{"星期": "星期一", "负责人": "张老师"}],
            "sql_lineage": {"tables_used": ["执勤安排"], "row_count": 1},
            "evidence_summary": {"row_sample": [{"星期": "星期一", "负责人": "张老师"}]},
            "raw_sql_handle": "trace://current/task/ddl_sql_query_1/raw_rows",
        },
    )

    assert contract.is_completed("data_evidence")
    assert contract.handoff_block_payload() is None
