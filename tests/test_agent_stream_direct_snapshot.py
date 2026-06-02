from __future__ import annotations

from types import SimpleNamespace

import gateway_core.agents.school_sql.agent_stream as agent_stream


def test_direct_snapshot_answer_uses_truth_markdown_for_table_only_request() -> None:
    handoff_payload = {
        "data_evidence": {
            "ddl_sql_query_1": {
                "evidence_summary": {
                    "truth_data_markdown": "【真实数据快照】\n| 年级 | 扣除总分 |\n| --- | --- |\n| 7年级 | 127.0 |",
                    "top_items": [{"年级": "7年级", "扣除总分": 127.0}],
                }
            }
        }
    }

    assert callable(getattr(agent_stream, "_direct_snapshot_answer", None))

    answer = agent_stream._direct_snapshot_answer(
        question="眼保健操时间纪律最差的年级是哪个？只输出查询结果表格。",
        handoff_payload=handoff_payload,
    )

    assert answer == "【真实数据快照】\n| 年级 | 扣除总分 |\n| --- | --- |\n| 7年级 | 127.0 |"


def test_direct_snapshot_answer_does_not_bypass_normal_analysis_request() -> None:
    handoff_payload = {"truth_data_markdown": "【真实数据快照】\n| 姓名 |\n| --- |\n| 张三 |"}

    assert callable(getattr(agent_stream, "_direct_snapshot_answer", None))

    answer = agent_stream._direct_snapshot_answer(
        question="帮我分析一下这批请假数据有什么风险。",
        handoff_payload=handoff_payload,
    )

    assert answer == ""


def test_handoff_payload_with_tool_evidence_restores_truth_markdown() -> None:
    tools = SimpleNamespace(
        evidence_by_task={
            "ddl_sql_query_1": {
                "evidence_summary": {
                    "truth_data_markdown": "【真实数据快照】\n| 班级 |\n| --- |\n| 预备1班 |"
                }
            }
        }
    )

    assert callable(getattr(agent_stream, "_handoff_payload_with_tool_evidence", None))

    payload = agent_stream._handoff_payload_with_tool_evidence(
        {"conclusion": "模型自行总结"},
        tools=tools,
    )

    assert payload["data_evidence"] == tools.evidence_by_task
    assert agent_stream._direct_snapshot_answer(
        question="只输出查询结果表格",
        handoff_payload=payload,
    ) == "【真实数据快照】\n| 班级 |\n| --- |\n| 预备1班 |"


def test_direct_snapshot_prefers_latest_sql_evidence_markdown() -> None:
    handoff_payload = {
        "data_evidence": {
            "ddl_sql_query_1": {
                "evidence_summary": {
                    "truth_data_markdown": "【真实数据快照】\n| 明细 |\n| --- |\n| 流水行 |"
                }
            },
            "ddl_sql_query_2": {
                "evidence_summary": {
                    "truth_data_markdown": "【真实数据快照】\n| 年级 | 总扣分 |\n| --- | --- |\n| 7年级 | 127 |"
                }
            },
        }
    }

    assert agent_stream._direct_snapshot_answer(
        question="只输出查询结果表格",
        handoff_payload=handoff_payload,
    ) == "【真实数据快照】\n| 年级 | 总扣分 |\n| --- | --- |\n| 7年级 | 127 |"


def test_direct_snapshot_suppresses_natural_answer_after_tool_evidence() -> None:
    tools = SimpleNamespace(evidence_by_task={"ddl_sql_query_1": {"evidence_summary": {"truth_data_markdown": "| A |\n| --- |"}}})

    assert callable(getattr(agent_stream, "_should_suppress_natural_answer_for_direct_snapshot", None))

    assert agent_stream._should_suppress_natural_answer_for_direct_snapshot(
        question="只输出查询结果表格",
        tools=tools,
    )
    assert not agent_stream._should_suppress_natural_answer_for_direct_snapshot(
        question="帮我分析一下数据",
        tools=tools,
    )


def test_sanitize_final_answer_removes_agent_process_preamble() -> None:
    raw = (
        "好的，现在我有清晰的结构认识了。根据数据：教师请假旧表已经停更。"
        "让我先查教师销假_请假明细中今天的数据。\n\n"
        "以下是美兰湖中学今天（2026年6月2日 星期二）教师请假情况的完整汇报。\n\n"
        "| 教师 | 请假类型 |\n| --- | --- |\n| 王老师 | 病假 |"
    )

    cleaned = agent_stream._sanitize_final_answer(raw)

    assert cleaned.startswith("以下是美兰湖中学今天")
    assert "让我先查" not in cleaned
    assert "清晰的结构认识" not in cleaned


def test_fast_final_answer_prompt_contains_leave_evidence_boundary() -> None:
    prompt = agent_stream._fast_final_answer_prompt(
        question="今天教师请假情况怎么样？",
        handoff_payload={
            "data_evidence": {
                "ddl_sql_query_1": {
                    "evidence_summary": {
                        "truth_data_markdown": "| 教师 | 请假类型 |\n| --- | --- |\n| 王老师 | 病假 |"
                    }
                }
            }
        },
        source_views=["教师销假_请假明细"],
    )

    assert "未提供课表、代课安排、考勤签到或全员在岗证据" in prompt
    assert "不得判断课程已安排代课" in prompt
    assert "不得判断其余教师均正常在岗" in prompt


def test_fast_final_answer_prompt_flattens_evidence_without_format_lock() -> None:
    system_prompt = agent_stream._fast_final_answer_system_prompt()
    prompt = agent_stream._fast_final_answer_prompt(
        question="学校整体概况怎么样？",
        handoff_payload={
            "data_evidence": {"row_sample": [{"班级数": 14, "学生数": 559}]},
            "evidence_board": {"business_clues": [{"value": "四年制初中"}]},
        },
        source_views=["班级底表", "学生信息表"],
    )

    assert "客观证据包：" in prompt
    assert "自由" in system_prompt
    assert "自由" in prompt
    assert "高维证据矩阵" not in prompt
    assert "handoff JSON" not in prompt
    assert "优先用 Markdown 表格" not in prompt
    assert "结论先行" not in prompt
    assert "必须用表格" not in system_prompt
    assert "格式自由主权" not in system_prompt
    assert "Markdown 表格" not in system_prompt


def test_fallback_handoff_payload_has_no_suggested_structure() -> None:
    tools = SimpleNamespace(evidence_by_task={}, source_views=[], evidence_board_payload=lambda: {})

    payload = agent_stream._fallback_final_handoff_payload(question="学校整体概况怎么样？", tools=tools)

    assert "suggested_structure" not in payload
    assert "conclusion" not in payload
    assert "key_facts" not in payload
    assert "pure_business_data_markdown" in payload
