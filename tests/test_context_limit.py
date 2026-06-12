from gateway_core.agents.school_sql.context_limit import (
    build_context_limit_clarification,
    is_context_limit_error,
)


def test_detects_context_limit_error_from_exception_text() -> None:
    assert is_context_limit_error(RuntimeError("maximum context length exceeded"))
    assert is_context_limit_error(RuntimeError("输入超过模型上下文限制"))


def test_does_not_treat_generic_upstream_error_as_context_limit() -> None:
    assert not is_context_limit_error(RuntimeError("upstream model temporarily unavailable"))


def test_context_limit_clarification_asks_for_specific_data_direction() -> None:
    text = build_context_limit_clarification("学校整体情况怎么样？")

    assert "超过了模型上下文限制" in text
    assert "更具体的方向" in text
    assert "德育/行规/纪律" in text
    assert "学生请假/健康/心理" in text
