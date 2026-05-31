from __future__ import annotations

import json
import os
from dataclasses import asdict

from gateway_core.agents.universal_hub.shadow.models import LiveShadowRunRecord, ShadowRunRecord


class LocalShadowAuditor:
    """Append-only local JSONL recorder for offline shadow audits."""

    def __init__(self, log_path: str = "logs/universal_hub_shadow/audit_master.jsonl") -> None:
        self.log_path = str(log_path)
        os.makedirs(os.path.dirname(self.log_path) or ".", exist_ok=True)

    def record_audit(self, record: ShadowRunRecord) -> None:
        audit_passed = _audit_passed(record)
        payload = {
            "test_case_id": record.test_case_id,
            "query": record.user_query,
            "contract_snapshot": record.contract_snapshot,
            "tools_aligned": record.legacy_tools_called == record.skill_tools_called,
            "tools_semantically_equivalent": record.metrics.tools_semantically_equivalent,
            "core_tools_preserved": record.metrics.core_tools_preserved,
            "legacy_tools_called": record.legacy_tools_called,
            "skill_tools_called": record.skill_tools_called,
            "metrics": {
                "process_ok": record.metrics.process_preserved,
                "content_ok": record.metrics.content_preserved,
                "sources_ok": record.metrics.sources_preserved,
                "sql_safe": record.metrics.sql_leak_prevented,
                "artifact_ok": record.metrics.artifact_embedded,
                "facts_ok": record.metrics.fact_preserved,
                "delta_ms": record.metrics.duration_delta_ms,
            },
            "audit_passed": audit_passed,
        }
        with open(self.log_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(payload, ensure_ascii=False, default=str) + "\n")

    def record_live_audit(
        self,
        record: LiveShadowRunRecord,
        *,
        hard_sla_timeout_ms: float = 120_000.0,
    ) -> bool:
        live_passed = live_audit_passed(record, hard_sla_timeout_ms=hard_sla_timeout_ms)
        payload = {
            "trace_id": record.trace_id,
            "mode": "LIVE_REAL_LLM_DB",
            "query": record.user_query,
            "metrics": {
                "process_ok": record.metrics.process_preserved,
                "content_ok": record.metrics.content_preserved,
                "sources_ok": record.metrics.sources_preserved,
                "sql_safe": record.metrics.sql_leak_prevented,
                "total_duration_ms": record.metrics.total_live_duration_ms,
            },
            "live_audit_passed": live_passed,
        }
        with open(self.log_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(payload, ensure_ascii=False, default=str) + "\n")
        return live_passed


def _audit_passed(record: ShadowRunRecord) -> bool:
    return audit_passed(record)


def audit_passed(record: ShadowRunRecord) -> bool:
    metric = record.metrics
    return (
        metric.process_preserved
        and metric.content_preserved
        and metric.sources_preserved
        and metric.sql_leak_prevented
        and metric.artifact_embedded
        and metric.tools_semantically_equivalent
        and metric.fact_preserved
        and metric.duration_delta_ms < 50.0
    )


def live_audit_passed(
    record: LiveShadowRunRecord,
    *,
    hard_sla_timeout_ms: float = 120_000.0,
) -> bool:
    metric = record.metrics
    return (
        metric.process_preserved
        and metric.content_preserved
        and metric.sources_preserved
        and metric.sql_leak_prevented
        and metric.total_live_duration_ms < hard_sla_timeout_ms
    )
