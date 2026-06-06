from __future__ import annotations

from gateway_core.observability.langfuse_exporter import export_school_trace_to_langfuse
from gateway_core.observability.phoenix_exporter import export_school_trace_to_phoenix
from gateway_core.school.trace import SchoolTrace


def export_school_trace_to_observability(trace: SchoolTrace) -> dict[str, bool]:
    # 观测导出是旁路能力；每个 exporter 自己处理可恢复失败，避免影响主链路。
    return {
        "langfuse": export_school_trace_to_langfuse(trace),
        "phoenix": export_school_trace_to_phoenix(trace),
    }
