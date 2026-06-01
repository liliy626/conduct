from __future__ import annotations

from gateway_core.agents.school_sql.query_result_summarizer import summarize_query_result


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
    assert "人事处/教师工作部" in summary["domain_role_preset"]
    assert "张老师" in summary["truth_data_markdown"]


def test_conduct_routes_to_student_moral_bar_line() -> None:
    summary = _summary(
        question="眼保健操纪律最差的年级是哪个？",
        referenced_views=["zx_mlh.行规检查_行规检查"],
        rows=[{"年级": "六年级", "扣分": 11}],
    )

    assert summary["domain_key"] == "学生与德育线"
    assert "德育处/学工部" in summary["domain_role_preset"]


def test_asset_and_repair_routes_to_logistics_bar_line() -> None:
    summary = _summary(
        question="本月资产维修和消防巡查情况如何？",
        referenced_views=["zx_mlh.资产报修_报修明细"],
        rows=[{"点位": "实验楼", "报修次数": 4}],
    )

    assert summary["domain_key"] == "后勤保障线"
    assert "总务处/后勤保障中心" in summary["domain_role_preset"]


def test_research_routes_to_research_bar_line() -> None:
    summary = _summary(
        question="今年教师课题申报和论文发表情况如何？",
        referenced_views=["zx_mlh.教师发展_科研成果"],
        rows=[{"教师": "李老师", "课题数": 2, "论文数": 1}],
    )

    assert summary["domain_key"] == "科研线"
    assert "教科室/教师发展部" in summary["domain_role_preset"]


def test_unknown_domain_falls_back_to_general_campus_context() -> None:
    summary = _summary(question="帮我看看这批数据有什么要点。")

    assert summary["domain_key"] == "通用智慧校园"
    assert "智慧校园首席教务数据智囊" in summary["domain_role_preset"]


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
    assert len(summary["top_items"]) == 8

    tail = summary["tail_summary"]
    assert tail["row_count"] == 112
    assert "教研组-物理组" in tail["affected_departments"]
    assert "少先队大队部" in tail["affected_departments"]
    assert tail["total_metric_value"] == 112 * 2.0
