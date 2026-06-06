from __future__ import annotations

from types import SimpleNamespace

import gateway_core.agents.school_sql.agent_stream as agent_stream


def test_schema_catalog_context_compacts_teacher_leave_candidates(monkeypatch) -> None:
    datasets = [
        SimpleNamespace(source_schema="zx_mlh", source_view="教师销假_请假明细", label="教师销假_请假明细", description="教师请假新表"),
        SimpleNamespace(source_schema="zx_mlh", source_view="教师销假_离校报备", label="教师销假_离校报备", description="教师离校报备"),
        SimpleNamespace(source_schema="zx_mlh", source_view="考勤管理_教师请假_因公", label="考勤管理_教师请假_因公", description="旧因公请假表"),
        SimpleNamespace(source_schema="zx_mlh", source_view="考勤管理_教师请假_因私", label="考勤管理_教师请假_因私", description="旧因私请假表"),
        SimpleNamespace(source_schema="zx_mlh", source_view="考勤管理_考勤统计数据", label="考勤管理_考勤统计数据", description="教师考勤统计"),
        SimpleNamespace(source_schema="zx_mlh", source_view="人事档案_人员信息", label="人事档案_人员信息", description="教师人员档案"),
        SimpleNamespace(source_schema="zx_mlh", source_view="学生评教_主课老师底表", label="学生评教_主课老师底表", description="学生评教教师底表"),
        SimpleNamespace(source_schema="zx_mlh", source_view="AI五育管理平台_作品信息表", label="AI五育管理平台_作品信息表", description="学生作品"),
    ]
    schema_index = SimpleNamespace(source_schema="zx_mlh", datasets=datasets)
    monkeypatch.setattr(agent_stream, "_startup_catalog_max_tables", lambda: 6)
    monkeypatch.setattr(agent_stream, "_startup_catalog_max_chars", lambda: 2000)

    context = agent_stream._schema_catalog_context(schema_index, question="今天教师请假人数是多少？")

    assert "候选表目录：" in context
    assert "已省略" in context
    assert "zx_mlh.教师销假_请假明细" in context
    assert "zx_mlh.考勤管理_教师请假_因公" in context


def test_schema_catalog_context_does_not_use_business_domain_fast_path(monkeypatch) -> None:
    datasets = [
        SimpleNamespace(source_schema="zx_mlh", source_view="设备报修_报修申请", label="设备报修_报修申请", description="设备维修申请"),
        SimpleNamespace(source_schema="zx_mlh", source_view="教师销假_请假明细", label="教师销假_请假明细", description="教师请假明细"),
        SimpleNamespace(source_schema="zx_mlh", source_view="公文流转_公文通知", label="公文流转_公文通知", description="行政公文通知"),
    ]
    schema_index = SimpleNamespace(source_schema="zx_mlh", datasets=datasets)
    monkeypatch.setattr(agent_stream, "_startup_catalog_max_tables", lambda: 20)
    monkeypatch.setattr(agent_stream, "_startup_catalog_max_chars", lambda: 4000)

    context = agent_stream._schema_catalog_context(schema_index, question="学校有哪些业务领域？")

    assert "候选表目录：" in context
    assert "全量业务表目录快照" not in context
    assert "通常情况下" not in context
    assert "final_answer_handoff" not in context
    assert "zx_mlh.设备报修_报修申请" in context
    assert "zx_mlh.教师销假_请假明细" in context
    assert "zx_mlh.公文流转_公文通知" in context


def test_enhanced_content_does_not_soft_guide_global_catalog_questions() -> None:
    content = agent_stream._enhanced_content(
        question="学校有哪些业务领域？",
        sql_experience="{}",
        ddl_context="全量业务表目录快照：\n- zx_mlh.设备报修_报修申请: 设备维修申请",
        conversation_context="",
    )

    assert "通常情况下" not in content
    assert "直接依据目录快照" not in content
    assert "final_answer_handoff" not in content


def test_enhanced_content_keeps_history_context_generic() -> None:
    content = agent_stream._enhanced_content(
        question="学校有哪些业务领域？",
        sql_experience='{"experiences":[{"raw_sql":"SELECT * FROM information_schema.tables","answer_summary":"按表名归纳业务领域"}]}',
        ddl_context="全量业务表目录快照：\n- zx_mlh.设备报修_报修申请: 设备维修申请",
        conversation_context="",
    )

    assert "按表名归纳业务领域" in content
    assert "SELECT * FROM information_schema.tables" in content


def test_startup_catalog_context_summarizes_catalog_generically(monkeypatch) -> None:
    snapshot = "全量业务表目录快照：\n" + "\n".join(
        f"- zx_mlh.领域{i}_业务表: 领域{i}业务，包含运行记录、配置底表、业务明细和统计汇总" for i in range(1, 80)
    )
    monkeypatch.setattr(agent_stream, "_startup_catalog_max_chars", lambda: 6000)

    context = agent_stream._startup_catalog_context_for_prompt(snapshot)

    assert "zx_mlh.领域79_业务表" not in context
    assert "Table:" not in context


def test_scripted_handoff_answer_uses_business_markdown_without_llm(monkeypatch) -> None:
    monkeypatch.setenv("SCHOOL_AGENT_SCRIPTED_HANDOFF_FINAL_ENABLED", "1")
    handoff_payload = {
        "pure_business_data_markdown": "## 学校近期异常\n\n- 心理健康特殊个案需关注",
        "source_views": ["内部表名"],
        "tool_contract": {"answer_mode": "data"},
        "caveats": [
            "数据截止至2026-06-02",
            "handoff JSON contained internal payload",
        ],
    }

    answer = agent_stream._scripted_handoff_answer(handoff_payload)

    assert answer.startswith("## 学校近期异常")
    assert "心理健康特殊个案需关注" in answer
    assert "### 注意事项" in answer
    assert "数据截止至2026-06-02" in answer
    assert "handoff" not in answer.lower()
    assert "内部表名" not in answer
    assert "tool_contract" not in answer


def test_scripted_handoff_answer_appends_business_prompt_disclaimer(monkeypatch) -> None:
    monkeypatch.setenv("SCHOOL_AGENT_SCRIPTED_HANDOFF_FINAL_ENABLED", "1")
    business_prompt_context = (
        '{"evidence":[{"content":{"role_context":{"disclaimer":"具体操作请以本年度校办/教务处下发的正式文件及相关平台实时数据为准。"}}}]}'
    )

    answer = agent_stream._scripted_handoff_answer(
        {"pure_business_data_markdown": "## 学校近期异常\n\n- 心理危机个案需关注"},
        business_prompt_context=business_prompt_context,
    )

    assert answer.endswith("具体操作请以本年度校办/教务处下发的正式文件及相关平台实时数据为准。")


def test_scripted_handoff_answer_reads_nested_summary_json_before_truth_snapshot(monkeypatch) -> None:
    monkeypatch.setenv("SCHOOL_AGENT_SCRIPTED_HANDOFF_FINAL_ENABLED", "1")
    handoff_payload = {
        "summary": (
            '{"pure_business_data_markdown":"## 学校近期异常\\n\\n- 有2起高优先级心理危机个案",'
            '"caveats":"数据截止至2026-06-02"}'
        ),
        "data_evidence": {
            "ddl_sql_query_1": {
                "evidence_summary": {
                    "truth_data_markdown": (
                        "【真实数据快照】\n"
                        "| 学生姓名 | __modified_time |\n"
                        "| --- | --- |\n"
                        "| 张三 | 2026-06-02 |"
                    )
                }
            }
        },
    }

    answer = agent_stream._scripted_handoff_answer(handoff_payload)

    assert answer.startswith("## 学校近期异常")
    assert "高优先级心理危机个案" in answer
    assert "数据截止至2026-06-02" in answer
    assert "真实数据快照" not in answer
    assert "__modified_time" not in answer


def test_scripted_handoff_answer_does_not_collapse_to_last_sql_snapshot(monkeypatch) -> None:
    monkeypatch.setenv("SCHOOL_AGENT_SCRIPTED_HANDOFF_FINAL_ENABLED", "1")
    handoff_payload = {
        "data_evidence": {
            "ddl_sql_query_1": {
                "evidence_summary": {
                    "truth_data_markdown": "【真实数据快照】\n| 教师总数 |\n| --- |\n| 42 |"
                }
            },
            "ddl_sql_query_2": {
                "evidence_summary": {
                    "truth_data_markdown": "【真实数据快照】\n| 离校报备次数 |\n| --- |\n| 369 |"
                }
            },
        },
        "caveats": ["教师画像需综合多张表，不可只取最后一次查询。"],
    }

    assert agent_stream._scripted_handoff_answer(handoff_payload) == ""


def test_scripted_handoff_answer_ignores_truth_data_markdown_without_business_summary(monkeypatch) -> None:
    monkeypatch.setenv("SCHOOL_AGENT_SCRIPTED_HANDOFF_FINAL_ENABLED", "1")
    handoff_payload = {
        "truth_data_markdown": "【真实数据快照】\n| 离校报备次数 |\n| --- |\n| 369 |",
        "caveats": ["不能把单张数据快照当作整体画像答案。"],
    }

    assert agent_stream._scripted_handoff_answer(handoff_payload) == ""


def test_scripted_handoff_answer_can_be_disabled(monkeypatch) -> None:
    monkeypatch.setenv("SCHOOL_AGENT_SCRIPTED_HANDOFF_FINAL_ENABLED", "0")

    assert (
        agent_stream._scripted_handoff_answer(
            {"pure_business_data_markdown": "## 学校近期异常\n\n- 心理健康特殊个案需关注"}
        )
        == ""
    )


def test_handoff_payload_with_tool_evidence_restores_data_evidence() -> None:
    tools = SimpleNamespace(
        evidence_by_task={
            "ddl_sql_query_1": {
                "task_id": "ddl_sql_query_1",
                "allowed": True,
                "intent": "raw_sql_select",
                "dataset_label": "班级概况",
                "row_count": 1,
                "raw_sql_handle": "trace://current/task/ddl_sql_query_1/raw_rows",
                "row_sample": [{"班级": "预备1班"}],
                "sql_lineage": {"tables_used": ["班级概况"], "row_count": 1},
                "evidence_summary": {
                    "truth_data_markdown": "【真实数据快照】\n| 班级 |\n| --- |\n| 预备1班 |",
                    "row_sample": [{"班级": "预备1班"}],
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
    assert payload["inter_agent_state"]["data_evidence"]["ddl_sql_query_1"]["ref"]["id"].startswith("trace://")


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


def test_fast_final_answer_prompt_does_not_add_boundary_notes_without_business_prompt() -> None:
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

    assert "业务提示词/证据边界" not in prompt
    assert "business_prompt_context" not in prompt


def test_fast_final_answer_prompt_includes_business_prompt_context() -> None:
    prompt = agent_stream._fast_final_answer_prompt(
        question="学校有哪些业务领域？",
        handoff_payload={"business_domains": [{"name": "设备报修", "table_count": 3}]},
        source_views=[],
        business_prompt_context='{"evidence_boundaries":["跨领域判断必须说明证据来源和不确定性。"]}',
    )

    assert "业务提示词/证据边界" in prompt
    assert "跨领域判断必须说明证据来源和不确定性" in prompt


def test_fast_final_answer_prompt_compacts_multi_sql_evidence() -> None:
    prompt = agent_stream._fast_final_answer_prompt(
        question="学校的整体教师画像",
        handoff_payload={
            "data_evidence": {
                "ddl_sql_query_1": {
                    "row_count": 2,
                    "referenced_views": ["zx_mlh.作业公示_新_教师数据"],
                    "raw_rows": [{"教师": "张三", "very_large_blob": "x" * 1000}],
                    "evidence_summary": {
                        "truth_data_markdown": "【真实数据快照】\n| 教师总数 |\n| --- |\n| 42 |",
                        "notable_findings": ["教师总数基于作业公示系统教师数据。"],
                    },
                },
                "ddl_sql_query_2": {
                    "row_count": 1,
                    "referenced_views": ["zx_mlh.教师获奖_获奖上报"],
                    "evidence_summary": {
                        "truth_data_markdown": "【真实数据快照】\n| 获奖总记录数 |\n| --- |\n| 244 |",
                    },
                },
            }
        },
        source_views=["作业公示_新_教师数据", "教师获奖_获奖上报"],
    )

    assert "evidence_digest" in prompt
    assert "ddl_sql_query_1" in prompt
    assert "教师总数" in prompt
    assert "获奖总记录数" in prompt
    assert "very_large_blob" not in prompt
    assert "raw_rows" not in prompt


def test_final_answer_handoff_tool_description_prefers_compact_json() -> None:
    tool = agent_stream._final_answer_handoff_tool(
        trace=None,
        final_model=None,
        question="学校有哪些业务领域？",
        source_views_fn=lambda: [],
    )

    description = tool.description
    assert "紧凑 JSON" in description
    assert "不要把完整 Markdown 长答案放入 handoff_json" in description
    assert "business_domains" in description


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
    assert payload["inter_agent_state"]["question"] == "学校整体概况怎么样？"


def test_policy_tool_end_sources_become_openwebui_citation_bubbles() -> None:
    event = {
        "event": "on_tool_end",
        "name": "official_policy_search",
        "data": {
            "output": (
                '{"ok":true,"sources":[{"title":"上海市教师职称评审通知",'
                '"official_url":"https://example.edu/policy",'
                '"source_name":"上海教育","content":"高级职称申报依据"}]}'
            )
        },
    }

    citation_sources = agent_stream._event_citation_sources(event)
    openwebui_sources = agent_stream._openwebui_sources_from_tool_sources(citation_sources)

    assert citation_sources[0]["url"] == "https://example.edu/policy"
    assert openwebui_sources[0]["source"]["url"] == "https://example.edu/policy"
    assert openwebui_sources[0]["metadata"][0]["url"] == "https://example.edu/policy"
    assert "高级职称申报依据" in openwebui_sources[0]["document"][0]
