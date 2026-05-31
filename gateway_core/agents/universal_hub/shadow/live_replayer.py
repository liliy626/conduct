from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from dataclasses import asdict
from typing import Any

from gateway_core.agents.school_sql.school_sql_skill import SchoolSqlSkill
from gateway_core.agents.universal_hub.models import SkillEvent
from gateway_core.agents.universal_hub.shadow.comparator import SkillStreamComparator
from gateway_core.agents.universal_hub.shadow.injection import LiveInjectionProtocol
from gateway_core.agents.universal_hub.shadow.models import (
    LegacyTraceSnapshot,
    LiveShadowRunRecord,
)
from gateway_core.agents.universal_hub.shadow.recorder import LocalShadowAuditor
from gateway_core.agents.universal_hub.shadow.replayer import OfflineShadowReplayer
from gateway_core.agents.universal_hub.shadow.runtime_provider import ReadOnlyRuntimeProvider
from gateway_core.agents.universal_hub.shadow.trace_stream import legacy_stream_from_trace_payload
from gateway_core.infra.api_keys import current_api_key_record, set_current_api_key_record


LiveSkillStreamFactory = Callable[[dict[str, Any], dict[str, Any]], AsyncIterator[SkillEvent]]


class OfflineLiveReplayer:
    """Offline live Skill replayer.

    This still does not touch HTTP or UniversalHub graph wiring. By default it
    invokes ``SchoolSqlSkill().astream`` with an isolated runtime context. Tests
    and smoke runs can inject a custom stream factory to avoid network/DB calls.
    """

    def __init__(
        self,
        *,
        auditor: LocalShadowAuditor,
        max_concurrency: int = 2,
        runtime_overrides: dict[str, Any] | None = None,
        runtime_provider: ReadOnlyRuntimeProvider | None = None,
        skill_stream_factory: LiveSkillStreamFactory | None = None,
    ) -> None:
        self._auditor = auditor
        self._max_concurrency = max(1, int(max_concurrency))
        self._runtime_overrides = dict(runtime_overrides or {})
        self._runtime_provider = runtime_provider
        self._skill_stream_factory = skill_stream_factory or _default_school_sql_skill_stream

    async def run_live_replay(self, snapshots: list[LegacyTraceSnapshot]) -> dict[str, Any]:
        replayer = OfflineShadowReplayer(replay_one=self._replay_one)
        results = await replayer.replay_batch(snapshots, concurrency=self._max_concurrency)
        passed = [item for item in results if item.get("audit_passed") is True]
        failed = [item for item in results if item.get("audit_passed") is not True]
        return {
            "total_live_replayed": len(results),
            "passed": len(passed),
            "failed": len(failed),
            "failed_details": failed,
        }

    async def _replay_one(self, snapshot: LegacyTraceSnapshot) -> dict[str, Any]:
        if not snapshot.trace_payload:
            return {
                "trace_id": snapshot.trace_id,
                "audit_passed": False,
                "error": "missing_trace_payload",
            }

        sandbox = LiveInjectionProtocol.prepare_sandbox(
            school_id=_snapshot_school_id(snapshot),
            schema_name=_snapshot_schema_name(snapshot),
            thread_id=snapshot.trace_id,
            turn_id=_snapshot_turn_id(snapshot),
            history_messages=snapshot.to_langchain_messages(),
            required_outputs=snapshot.contract_snapshot.get("required_outputs") or ["data_evidence"],
            runtime_overrides=self._runtime_overrides,
        )
        if self._runtime_provider is not None:
            sandbox.runtime_ctx.update(
                self._runtime_provider.inject_live_dependencies(
                    _snapshot_school_id(snapshot),
                    sandbox.runtime_ctx,
                )
            )
        previous_api_key_record = current_api_key_record()
        api_key_record = sandbox.runtime_ctx.get("api_key_record")
        if api_key_record is not None:
            set_current_api_key_record(api_key_record)
        try:
            live_metric, metric = await SkillStreamComparator.compare_live_streams(
                user_query=snapshot.user_query,
                legacy_stream=legacy_stream_from_trace_payload(snapshot.trace_payload),
                skill_stream=self._skill_stream_factory(sandbox.state, sandbox.runtime_ctx),
            )
        finally:
            set_current_api_key_record(previous_api_key_record)
        record = LiveShadowRunRecord(
            trace_id=snapshot.trace_id,
            user_query=snapshot.user_query,
            metrics=live_metric,
            live_audit_passed=False,
        )
        passed = self._auditor.record_live_audit(record)
        return {
            "trace_id": snapshot.trace_id,
            "audit_passed": passed,
            "metrics": asdict(live_metric),
            "fixture_metrics": asdict(metric),
        }


def _default_school_sql_skill_stream(state: dict[str, Any], runtime_ctx: dict[str, Any]) -> AsyncIterator[SkillEvent]:
    return SchoolSqlSkill().astream(state, runtime_ctx)


def _snapshot_school_id(snapshot: LegacyTraceSnapshot) -> str:
    payload = snapshot.trace_payload or {}
    value = payload.get("school_id") or payload.get("tenant_id") or ""
    return str(value or "")


def _snapshot_schema_name(snapshot: LegacyTraceSnapshot) -> str:
    payload = snapshot.trace_payload or {}
    for step in payload.get("steps") or []:
        if not isinstance(step, dict):
            continue
        if step.get("name") != "school.resolve":
            continue
        output = step.get("output")
        if isinstance(output, dict) and output.get("schema_name"):
            return str(output["schema_name"])
    return str(payload.get("schema_name") or _snapshot_school_id(snapshot) or "")


def _snapshot_turn_id(snapshot: LegacyTraceSnapshot) -> int:
    user_turns = sum(1 for item in snapshot.history_messages_snapshot if item.get("role") == "user")
    return max(1, user_turns)
