from gateway_core.agents.school_sql.sql_tools import DDLReactTools
from gateway_core.schema_context.query_experience import experience_top_k, experience_top_k_for_question
from gateway_core.schema_context.query_experience_store import _experience_row_to_dict


def test_sql_history_recall_defaults_are_small(monkeypatch) -> None:
    for key in ("SQL_HISTORY_TOP_K", "TENANT_QUERY_EXPERIENCE_TOP_K", "SQL_HISTORY_COMPLEX_TOP_K", "SQL_HISTORY_MAX_K"):
        monkeypatch.delenv(key, raising=False)

    assert experience_top_k() == 3
    assert experience_top_k_for_question("本月请假情况") == 3
    assert experience_top_k_for_question("本月请假趋势和主要分布") == 4


def test_sql_history_recall_hard_caps_stale_server_env(monkeypatch) -> None:
    monkeypatch.setenv("SQL_HISTORY_TOP_K", "5")
    monkeypatch.setenv("SQL_HISTORY_COMPLEX_TOP_K", "8")
    monkeypatch.setenv("SQL_HISTORY_MAX_K", "10")
    monkeypatch.delenv("SQL_HISTORY_TOP_K_HARD_MAX", raising=False)
    monkeypatch.delenv("SQL_HISTORY_COMPLEX_TOP_K_HARD_MAX", raising=False)

    assert experience_top_k() == 3
    assert experience_top_k_for_question("本月请假趋势和主要分布") == 4


def test_sql_history_recall_clips_large_sql(monkeypatch) -> None:
    monkeypatch.setenv("SQL_HISTORY_RECALL_SQL_MAX_CHARS", "300")
    long_sql = "SELECT " + ", ".join(f'"字段{i}"' for i in range(80)) + ' FROM "zx_mlh"."大表"'

    item = _experience_row_to_dict(
        (
            "问题",
            long_sql,
            '["zx_mlh.大表"]',
            '["字段1"]',
            10,
            1,
            "v1",
            0.9,
        )
    )

    assert len(item["raw_sql"]) < len(long_sql)
    assert item["raw_sql"].endswith("...[truncated]")


def test_sql_history_candidates_flush_only_after_final_evidence() -> None:
    tools = DDLReactTools.__new__(DDLReactTools)
    tools._pending_sql_experiences = []
    tools.evidence_by_task = {"task": {"row_count": 3}}
    tools.source_views = ["教师销假_请假明细"]
    recorded: list[str] = []

    assert tools._queue_sql_experience_candidate(
        task_id="task",
        sql='SELECT COUNT(*) FROM zx_mlh."教师销假_请假明细"',
        row_count=3,
        selected=[{"dataset_id": "ddl_table:教师销假_请假明细", "source_schema": "zx_mlh", "source_view": "教师销假_请假明细"}],
        referenced_views=["zx_mlh.教师销假_请假明细"],
    )

    def fake_record(**kwargs):
        recorded.append(kwargs["task_id"])
        return True

    tools._record_sql_experience = fake_record
    result = tools.flush_sql_experience_candidates(final_status="answered")

    assert result["recorded_count"] == 1
    assert recorded == ["task"]


def test_sql_history_candidates_skip_without_final_evidence() -> None:
    tools = DDLReactTools.__new__(DDLReactTools)
    tools._pending_sql_experiences = [
        {
            "task_id": "task",
            "sql": "SELECT 1",
            "row_count": 1,
            "selected": [],
            "referenced_views": ["zx_mlh.t"],
        }
    ]
    tools.evidence_by_task = {}
    tools.source_views = []

    result = tools.flush_sql_experience_candidates(final_status="answered")

    assert result["recorded_count"] == 0
    assert result["skipped_reason"] == "missing_final_evidence"
