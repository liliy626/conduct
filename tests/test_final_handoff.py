from gateway_core.agents.school_sql.final_handoff import (
    has_handoff_evidence,
    should_emit_missing_handoff_draft,
)


def test_empty_fallback_handoff_has_no_evidence() -> None:
    assert not has_handoff_evidence(
        {
            "data_evidence": {},
            "evidence_board": {},
            "external_evidence": [],
            "source_views": [],
        }
    )


def test_handoff_evidence_detects_nested_rows() -> None:
    assert has_handoff_evidence(
        {
            "data_evidence": {
                "五月请假": {
                    "rows": [{"类型": "因公请假", "次数": 73}],
                }
            },
            "source_views": [],
        }
    )


def test_missing_handoff_can_preserve_user_facing_agent_draft() -> None:
    assert should_emit_missing_handoff_draft(
        draft_text="好的，老师！5月已经查到：教师因公请假73次，学生病假因病182次。",
        handoff_payload={"data_evidence": {}, "source_views": []},
        source_views=[],
    )


def test_missing_handoff_does_not_preserve_pending_tool_plan() -> None:
    assert not should_emit_missing_handoff_draft(
        draft_text="我先查询一下五月的请假数据，然后再整理给你。",
        handoff_payload={"data_evidence": {}, "source_views": []},
        source_views=[],
    )


def test_missing_handoff_keeps_evidence_path_when_tools_have_sources() -> None:
    assert not should_emit_missing_handoff_draft(
        draft_text="五月请假情况如下。",
        handoff_payload={"data_evidence": {}, "source_views": []},
        source_views=["教师销假_请假明细"],
    )
