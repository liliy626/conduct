from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from dataclasses import asdict
from typing import Any

from gateway_core.agents.universal_hub.models import SkillEvent
from gateway_core.agents.universal_hub.shadow.comparator import SkillStreamComparator
from gateway_core.agents.universal_hub.shadow.models import LegacyTraceSnapshot, ShadowRunRecord
from gateway_core.agents.universal_hub.shadow.recorder import LocalShadowAuditor, audit_passed
from gateway_core.agents.universal_hub.shadow.replayer import OfflineShadowReplayer
from gateway_core.agents.universal_hub.shadow.trace_stream import legacy_stream_from_trace_payload


SkillStreamFactory = Callable[[LegacyTraceSnapshot], AsyncIterator[SkillEvent]]


async def trace_fixture_skill_stream_factory(snapshot: LegacyTraceSnapshot) -> AsyncIterator[SkillEvent]:
    """Replay a persisted trace as SkillEvents.

    This is a harness self-check mode, not a live SchoolSqlSkill run. It proves
    the loader, comparator, auditor, and bounded-concurrency replay path before
    any live LLM/DB dependency is attached.
    """

    async for event_type, data in legacy_stream_from_trace_payload(snapshot.trace_payload or {}):
        if event_type == "reasoning":
            yield SkillEvent(event_type="process", data=data)
        elif event_type == "tool_start":
            yield SkillEvent(event_type="tool_start", data=data)
        elif event_type == "sources":
            yield SkillEvent(event_type="evidence", data=data)
        elif event_type == "artifact":
            yield SkillEvent(event_type="artifact", data=data)
        elif event_type == "final_content":
            yield SkillEvent(event_type="content", data=data)


class OfflineTraceBombard:
    """Run graph-free, HTTP-free replay audits over persisted trace snapshots."""

    def __init__(
        self,
        *,
        auditor: LocalShadowAuditor,
        skill_stream_factory: SkillStreamFactory,
    ) -> None:
        self._auditor = auditor
        self._skill_stream_factory = skill_stream_factory

    async def replay_one(self, snapshot: LegacyTraceSnapshot) -> dict[str, Any]:
        if not snapshot.trace_payload:
            return {
                "trace_id": snapshot.trace_id,
                "audit_passed": False,
                "error": "missing_trace_payload",
            }

        metric = await SkillStreamComparator.compare_streams(
            user_query=snapshot.user_query,
            legacy_stream=legacy_stream_from_trace_payload(snapshot.trace_payload),
            skill_stream=self._skill_stream_factory(snapshot),
        )
        record = ShadowRunRecord(
            test_case_id=snapshot.trace_id,
            user_query=snapshot.user_query,
            contract_snapshot=snapshot.contract_snapshot,
            legacy_tools_called=metric.legacy_tools_called,
            skill_tools_called=metric.skill_tools_called,
            metrics=metric,
            audit_passed=False,
        )
        passed = audit_passed(record)
        self._auditor.record_audit(record)
        return {
            "trace_id": snapshot.trace_id,
            "audit_passed": passed,
            "metrics": asdict(metric),
        }

    async def replay_batch(
        self,
        snapshots: list[LegacyTraceSnapshot],
        *,
        concurrency: int = 10,
    ) -> dict[str, Any]:
        replayer = OfflineShadowReplayer(replay_one=self.replay_one)
        results = await replayer.replay_batch(snapshots, concurrency=concurrency)
        passed = [item for item in results if item.get("audit_passed") is True]
        failed = [item for item in results if item.get("audit_passed") is not True]
        return {
            "total_replayed": len(results),
            "passed": len(passed),
            "failed": len(failed),
            "failed_details": failed,
        }
