from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime
from decimal import Decimal

from gateway_core.observability.langfuse_exporter import export_school_trace_to_langfuse, langfuse_status
from gateway_core.school.trace import SchoolTrace, SchoolTraceStep


class _FakeObservation:
    def __init__(self, calls: list[dict], name: str, as_type: str = "span") -> None:
        self.calls = calls
        self.name = name
        self.as_type = as_type

    def update(self, **kwargs) -> None:
        self.calls.append({"name": self.name, "as_type": self.as_type, "update": kwargs})


class _FakeClient:
    def __init__(self) -> None:
        self.calls: list[dict] = []
        self.flushed = False

    @contextmanager
    def start_as_current_observation(self, *, name: str, as_type: str = "span", **kwargs):
        self.calls.append({"name": name, "as_type": as_type, "start": kwargs})
        yield _FakeObservation(self.calls, name=name, as_type=as_type)

    def flush(self) -> None:
        self.flushed = True


def _sample_trace() -> SchoolTrace:
    trace = SchoolTrace(trace_id="trace_123", question="今天请假情况")
    trace.school_id = "sch_zx_mlh"
    trace.steps.append(
        SchoolTraceStep(
            name="agent_native.start",
            input={"question": "今天请假情况"},
            output={"route": "school_sql"},
            duration_ms=12,
        )
    )
    trace.steps.append(
        SchoolTraceStep(
            name="chat_completion.final",
            input={"route_name": "school_agent_native"},
            output={"answer_preview": "今天共有 2 人请假，其中学生 1 人、教师 1 人。"},
            duration_ms=3,
        )
    )
    return trace


def test_langfuse_exporter_noops_when_disabled(monkeypatch) -> None:
    monkeypatch.delenv("LANGFUSE_ENABLED", raising=False)
    monkeypatch.delenv("LANGFUSE_PUBLIC_KEY", raising=False)
    monkeypatch.delenv("LANGFUSE_SECRET_KEY", raising=False)

    assert export_school_trace_to_langfuse(_sample_trace(), client_factory=lambda: _FakeClient()) is False


def test_langfuse_exporter_writes_trace_and_steps(monkeypatch) -> None:
    monkeypatch.setenv("LANGFUSE_ENABLED", "1")
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk-test")
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk-test")
    monkeypatch.setenv("LANGFUSE_BASE_URL", "http://langfuse.local")
    client = _FakeClient()

    assert export_school_trace_to_langfuse(_sample_trace(), client_factory=lambda: client) is True

    root = client.calls[0]
    assert root["name"] == "学校数据流观测"
    assert root["start"]["input"] == {"question": "今天请假情况"}
    assert root["start"]["metadata"]["school_id"] == "sch_zx_mlh"
    assert root["start"]["metadata"]["original_name"] == "school_trace"
    root_update = next(call for call in client.calls if call["name"] == "学校数据流观测" and "update" in call)
    assert root_update["update"]["output"]["final_answer"] == "今天共有 2 人请假，其中学生 1 人、教师 1 人。"
    step_start = next(call for call in client.calls if call["name"] == "智能体启动")
    assert step_start["start"]["metadata"]["original_name"] == "agent_native.start"
    final_answer = next(call for call in client.calls if call["name"] == "最终回答" and "start" in call)
    assert final_answer["start"]["input"] == {"route_name": "school_agent_native"}
    assert client.flushed is True


def test_langfuse_status_reports_dashboard_url(monkeypatch) -> None:
    monkeypatch.setenv("LANGFUSE_ENABLED", "1")
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk-test")
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk-test")
    monkeypatch.setenv("LANGFUSE_HOST", "http://langfuse.local")

    status = langfuse_status(sdk_available=True)

    assert status["enabled"] is True
    assert status["configured"] is True
    assert status["dashboard_url"] == "http://langfuse.local"


def test_langfuse_exporter_marks_answer_with_internal_error_as_recovered(monkeypatch) -> None:
    monkeypatch.setenv("LANGFUSE_ENABLED", "1")
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk-test")
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk-test")
    trace = _sample_trace()
    trace.steps.insert(
        0,
        SchoolTraceStep(
            name="agent_native.contract.plan",
            status="error",
            input={"question": "今天请假情况"},
            output={"reason": "contract planner unavailable: BadRequestError"},
            error="BadRequestError",
            duration_ms=2,
        ),
    )
    client = _FakeClient()

    assert export_school_trace_to_langfuse(trace, client_factory=lambda: client) is True

    root_update = next(call for call in client.calls if call["name"] == "学校数据流观测" and "update" in call)
    assert root_update["update"]["output"]["status"] == "recovered"
    assert root_update["update"]["output"]["answer_status"] == "generated_with_internal_error"


def test_langfuse_exporter_makes_sql_outputs_json_safe(monkeypatch) -> None:
    monkeypatch.setenv("LANGFUSE_ENABLED", "1")
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk-test")
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk-test")
    trace = _sample_trace()
    trace.steps.append(
        SchoolTraceStep(
            name="ddl_react.tool.sql_db_query",
            input={"params": (datetime(2026, 6, 2, 8, 30),)},
            output={
                "rows": [
                    {
                        "date": datetime(2026, 6, 2, 9, 15),
                        "duration": Decimal("1.5"),
                    }
                ]
            },
        )
    )
    client = _FakeClient()

    assert export_school_trace_to_langfuse(trace, client_factory=lambda: client) is True

    sql_start = next(call for call in client.calls if call["name"] == "SQL 查询执行" and "start" in call)
    sql_update = next(call for call in client.calls if call["name"] == "SQL 查询执行" and "update" in call)
    assert sql_start["start"]["input"]["params"] == ["2026-06-02T08:30:00"]
    assert sql_update["update"]["output"]["rows"][0]["date"] == "2026-06-02T09:15:00"
    assert sql_update["update"]["output"]["rows"][0]["duration"] == 1.5
