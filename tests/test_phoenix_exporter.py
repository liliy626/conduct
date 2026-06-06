from __future__ import annotations

from opentelemetry.sdk.trace.export import SpanExportResult

from gateway_core.observability.phoenix_exporter import export_school_trace_to_phoenix, phoenix_status
from gateway_core.school.trace import SchoolTrace, SchoolTraceStep


class _FakeSpanExporter:
    def __init__(self, *, result: SpanExportResult = SpanExportResult.SUCCESS) -> None:
        self.result = result
        self.spans = []
        self.shutdown_called = False
        self.flush_called = False

    def export(self, spans) -> SpanExportResult:
        self.spans.extend(spans)
        return self.result

    def shutdown(self) -> None:
        self.shutdown_called = True

    def force_flush(self, timeout_millis: int = 30000) -> bool:
        del timeout_millis
        self.flush_called = True
        return True


def _sample_trace() -> SchoolTrace:
    trace = SchoolTrace(trace_id="trace_phoenix", question="今天请假情况")
    trace.school_id = "sch_zx_mlh"
    trace.created_at = 1000.0
    trace.steps.append(
        SchoolTraceStep(
            name="agent_native.contract.plan",
            input={"question": "今天请假情况"},
            output={"allowed_tools": ["school.sql_db_query"]},
            duration_ms=20,
            started_at=1000.0,
            ended_at=1000.02,
        )
    )
    trace.steps.append(
        SchoolTraceStep(
            name="ddl_react.tool.sql_db_query",
            input={"sql": "select 1"},
            output={"row_count": 1},
            duration_ms=30,
            started_at=1000.03,
            ended_at=1000.06,
        )
    )
    return trace


def test_phoenix_status_reports_local_defaults(monkeypatch) -> None:
    monkeypatch.setenv("PHOENIX_ENABLED", "1")
    monkeypatch.delenv("PHOENIX_COLLECTOR_ENDPOINT", raising=False)

    status = phoenix_status(exporter_available=True)

    assert status["enabled"] is True
    assert status["configured"] is True
    assert status["collector_endpoint"] == "http://127.0.0.1:16006/v1/traces"
    assert status["dashboard_url"] == "http://127.0.0.1:16006"


def test_phoenix_exporter_noops_when_disabled(monkeypatch) -> None:
    monkeypatch.delenv("PHOENIX_ENABLED", raising=False)

    exporter = _FakeSpanExporter()

    assert export_school_trace_to_phoenix(_sample_trace(), exporter_factory=lambda: exporter) is False
    assert exporter.spans == []


def test_phoenix_exporter_writes_root_and_step_spans(monkeypatch) -> None:
    monkeypatch.setenv("PHOENIX_ENABLED", "1")
    exporter = _FakeSpanExporter()

    assert export_school_trace_to_phoenix(_sample_trace(), exporter_factory=lambda: exporter) is True

    names = [span.name for span in exporter.spans]
    assert "school_trace" in names
    assert "agent_native.contract.plan" in names
    assert "ddl_react.tool.sql_db_query" in names
    root = next(span for span in exporter.spans if span.name == "school_trace")
    sql_step = next(span for span in exporter.spans if span.name == "ddl_react.tool.sql_db_query")
    assert root.attributes["gateway.trace_id"] == "trace_phoenix"
    assert root.attributes["gateway.school_id"] == "sch_zx_mlh"
    assert sql_step.attributes["openinference.span.kind"] == "TOOL"
    assert '"row_count": 1' in sql_step.attributes["gateway.output_json"]
    assert exporter.shutdown_called is True


def test_phoenix_exporter_reports_export_failure(monkeypatch) -> None:
    monkeypatch.setenv("PHOENIX_ENABLED", "1")
    exporter = _FakeSpanExporter(result=SpanExportResult.FAILURE)

    assert export_school_trace_to_phoenix(_sample_trace(), exporter_factory=lambda: exporter) is False
    assert exporter.shutdown_called is True
