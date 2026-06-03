from __future__ import annotations

import pytest

from gateway_core.agents.school_sql.lineage_route import decide_lineage_route
from gateway_core.agents.school_sql.query_result_summarizer import (
    display_rows_for_shape,
    load_lossless_result_rows,
    summarize_query_result,
)


@pytest.fixture(autouse=True)
def _artifact_vault(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("GATEWAY_ARTIFACT_DIR", str(tmp_path))


def _summary(*, question: str, referenced_views: list[str] | None = None, rows: list[dict] | None = None) -> dict:
    return summarize_query_result(
        intent="list",
        row_count=len(rows or [{"对象": "样例", "数量": 1}]),
        formatted_rows=rows or [{"对象": "样例", "数量": 1}],
        field_labels={},
        question=question,
        referenced_views=referenced_views or [],
    )


def test_teacher_leave_routes_to_hr_bar_line() -> None:
    summary = _summary(
        question="本学期全校教师请假概况如何？",
        referenced_views=["zx_mlh.教师销假_请假明细"],
        rows=[{"教师姓名": "张老师", "请假次数": 8, "总请假时长": 32.5}],
    )

    assert summary["domain_key"] == "人事线"
    assert "证据边界" in summary["domain_role_preset"]
    assert "人事处/教师工作部" not in summary["domain_role_preset"]
    assert "张老师" in summary["truth_data_markdown"]


def test_leave_table_lineage_overrides_moral_department_keyword() -> None:
    summary = _summary(
        question="调取一下最近德育处老师的请假审批和加班工时明细。",
        referenced_views=["zx_mlh.教师销假_请假明细"],
        rows=[{"教师姓名": "王老师", "部门名称": "德育处", "请假工时": 4.0}],
    )

    assert summary["domain_key"] == "人事线"


def test_lineage_route_decision_uses_table_lineage_before_question_terms() -> None:
    decision = decide_lineage_route(
        question="调取一下最近德育处老师的请假审批和加班工时明细。",
        table_names=["zx_mlh.教师销假_请假明细"],
        columns=["教师姓名", "部门名称", "请假工时"],
    )

    assert decision.domain_key == "人事线"
    assert decision.confidence == 1.0
    assert any("教师销假_请假明细" in table for table in decision.matched_tables)


def test_conduct_routes_to_student_moral_bar_line() -> None:
    summary = _summary(
        question="眼保健操纪律最差的年级是哪个？",
        referenced_views=["zx_mlh.行规检查_行规检查"],
        rows=[{"年级": "六年级", "扣分": 11}],
    )

    assert summary["domain_key"] == "学生与德育线"
    assert "证据边界" in summary["domain_role_preset"]
    assert "德育处/学工部" not in summary["domain_role_preset"]


def test_asset_and_repair_routes_to_logistics_bar_line() -> None:
    summary = _summary(
        question="本月资产维修和消防巡查情况如何？",
        referenced_views=["zx_mlh.资产报修_报修明细"],
        rows=[{"点位": "实验楼", "报修次数": 4}],
    )

    assert summary["domain_key"] == "后勤保障线"
    assert "证据边界" in summary["domain_role_preset"]
    assert "总务处/后勤保障中心" not in summary["domain_role_preset"]


def test_research_routes_to_research_bar_line() -> None:
    summary = _summary(
        question="今年教师课题申报和论文发表情况如何？",
        referenced_views=["zx_mlh.教师发展_科研成果"],
        rows=[{"教师": "李老师", "课题数": 2, "论文数": 1}],
    )

    assert summary["domain_key"] == "科研线"
    assert "证据边界" in summary["domain_role_preset"]
    assert "教科室/教师发展部" not in summary["domain_role_preset"]


def test_unknown_domain_falls_back_to_general_campus_context() -> None:
    summary = _summary(question="帮我看看这批数据有什么要点。")

    assert summary["domain_key"] == "通用智慧校园"
    assert "证据边界" in summary["domain_role_preset"]
    assert "智慧校园首席教务数据智囊" not in summary["domain_role_preset"]


def test_query_summarizer_express_locker_lossless() -> None:
    mock_rows = []
    departments = ["教研组-数学组", "教研组-物理组", "行政处室-信息中心", "少先队大队部"]
    for index in range(120):
        mock_rows.append(
            {
                "员工UserId": f"uid_{index}",
                "姓名": f"教师_{index}",
                "部门名称": departments[index % len(departments)],
                "请假工时": 2.0,
            }
        )

    summary = summarize_query_result(
        intent="rank",
        row_count=120,
        formatted_rows=mock_rows,
        field_labels={"员工UserId": "ID", "姓名": "教师姓名", "部门名称": "所属教研组", "请假工时": "工时"},
    )

    assert "full_result_ref" in summary
    ref = summary["full_result_ref"]
    assert ref["is_lossless"] is True
    assert ref["result_id"].startswith("res_idx_")
    assert "tail_summary" not in summary
    assert len(summary["top_items"]) == 120
    assert len(summary["row_sample"]) == 120
    assert all("员工UserId" not in row for row in summary["top_items"])
    assert summary["top_items"][119]["姓名"] == "教师_119"
    assert "教师_49" in summary["truth_data_markdown"]
    assert "教师_50" not in summary["truth_data_markdown"]
    assert "员工UserId" not in summary["truth_data_markdown"]
    assert "仅展示前 50 行，实际返回 120 行" in summary["truth_data_markdown"]


def test_query_summarizer_pure_truth_data_no_loss() -> None:
    mock_raw_rows = [
        {
            "id": 1001,
            "tenant_id": "tenant_mlh_01",
            "姓名": "张三",
            "部门名称": "数学组",
            "考勤扣分": 2.0,
            "created_at": "2026-06-01",
        }
    ]

    summary = summarize_query_result(
        intent="rank",
        row_count=1,
        formatted_rows=mock_raw_rows,
        field_labels={"姓名": "教师姓名", "部门名称": "所属团队", "考勤扣分": "假勤分值"},
    )

    assert "top_items" in summary
    clean_data = summary["top_items"][0]
    assert "tenant_id" not in clean_data
    assert "id" not in clean_data
    assert "created_at" not in clean_data
    assert clean_data["姓名"] == "张三"
    assert clean_data["部门名称"] == "数学组"
    assert summary["field_labels"] == {"姓名": "教师姓名", "部门名称": "所属团队", "考勤扣分": "假勤分值"}
    assert "【真实数据快照】" in summary["truth_data_markdown"]
    assert "张三" in summary["truth_data_markdown"]
    assert "tenant_mlh_01" not in summary["truth_data_markdown"]


def test_query_summarizer_removes_yida_technical_fields_from_business_rows() -> None:
    rows = [
        {
            "__instance_id": "inst_001",
            "__tenant_id": "tenant_mlh",
            "originator_user_id": "uid_001",
            "gmt_create": "2026-06-01T00:00:00Z",
            "学生姓名": "张三",
            "班级": "六年级1班",
            "请假类型": "病假",
        }
    ]

    summary = summarize_query_result(
        intent="list",
        row_count=1,
        formatted_rows=rows,
        field_labels={
            "__instance_id": "实例ID",
            "__tenant_id": "租户ID",
            "originator_user_id": "发起人UserId",
            "gmt_create": "创建时间",
            "学生姓名": "学生姓名",
            "班级": "班级",
            "请假类型": "请假类型",
        },
    )

    clean_row = summary["row_sample"][0]
    assert clean_row == {"学生姓名": "张三", "班级": "六年级1班", "请假类型": "病假"}
    assert summary["field_labels"] == {"学生姓名": "学生姓名", "班级": "班级", "请假类型": "请假类型"}
    assert "__instance_id" not in summary["truth_data_markdown"]
    assert "uid_001" not in summary["truth_data_markdown"]
    stored_rows = load_lossless_result_rows(summary["full_result_ref"]["result_id"])
    assert stored_rows == [{"学生姓名": "张三", "班级": "六年级1班", "请假类型": "病假"}]


def test_display_rows_for_shape_returns_business_rows_only() -> None:
    display = display_rows_for_shape(
        evidence_shape="display",
        formatted_rows=[
            {
                "__instance_id": "inst_001",
                "tenant_id": "tenant_mlh",
                "学生姓名": "张三",
                "班级": "六年级1班",
            }
        ],
    )

    assert display["display_rows"] == [{"学生姓名": "张三", "班级": "六年级1班"}]


def test_query_summarizer_lossless_ref_points_to_stored_rows(tmp_path, monkeypatch) -> None:
    rows = [{"姓名": "教师_1", "部门名称": "教研组-数学组", "请假工时": 2.5}]

    summary = summarize_query_result(
        intent="rank",
        row_count=1,
        formatted_rows=rows,
        field_labels={},
        question="本学期全校教师请假概况如何？",
        referenced_views=["zx_mlh.教师销假_请假明细"],
    )

    stored_rows = load_lossless_result_rows(summary["full_result_ref"]["result_id"])
    assert stored_rows == rows
