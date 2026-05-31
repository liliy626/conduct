from __future__ import annotations

import json
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from gateway_core.agents.universal_hub.shadow.models import LegacyTraceSnapshot


def load_school_trace_snapshots(
    source: str | Path | Iterable[str | Path],
    *,
    limit: int = 200,
    min_steps: int = 1,
) -> list[LegacyTraceSnapshot]:
    """Load persisted school trace JSONL files into replay snapshots.

    The loader is intentionally offline-only: it reads local JSONL traces and
    produces lightweight snapshots. It does not call HTTP, the graph, or any
    model provider.
    """

    snapshots: list[LegacyTraceSnapshot] = []
    for path in _iter_trace_files(source):
        with path.open("r", encoding="utf-8") as handle:
            for line in handle:
                if len(snapshots) >= limit:
                    return snapshots
                raw = line.strip()
                if not raw:
                    continue
                try:
                    payload = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                if len(payload.get("steps") or []) < min_steps:
                    continue
                snapshot = snapshot_from_trace_payload(payload)
                if snapshot is not None:
                    snapshots.append(snapshot)
    return snapshots


def snapshot_from_trace_payload(payload: dict[str, Any]) -> LegacyTraceSnapshot | None:
    trace_id = str(payload.get("trace_id") or "").strip()
    question = str(payload.get("question") or "").strip()
    if not trace_id or not question:
        return None
    return LegacyTraceSnapshot(
        trace_id=trace_id,
        user_query=question,
        history_messages_snapshot=_history_messages(payload, question),
        contract_snapshot=_contract_snapshot(payload),
        trace_payload=payload,
    )


def _iter_trace_files(source: str | Path | Iterable[str | Path]) -> list[Path]:
    if isinstance(source, (str, Path)):
        path = Path(source)
        if path.is_dir():
            return sorted(path.glob("school_trace_*.jsonl"), reverse=True)
        return [path]
    files: list[Path] = []
    for item in source:
        path = Path(item)
        if path.is_dir():
            files.extend(sorted(path.glob("school_trace_*.jsonl"), reverse=True))
        else:
            files.append(path)
    return files


def _history_messages(payload: dict[str, Any], question: str) -> list[dict[str, Any]]:
    raw_messages = payload.get("messages") or payload.get("history_messages") or []
    if isinstance(raw_messages, list) and raw_messages:
        messages: list[dict[str, Any]] = []
        for item in raw_messages:
            if not isinstance(item, dict):
                continue
            content = str(item.get("content") or "").strip()
            if not content:
                continue
            role = str(item.get("role") or "user").strip() or "user"
            messages.append({"role": role, "content": content})
        if messages:
            return messages
    return [{"role": "user", "content": question}]


def _contract_snapshot(payload: dict[str, Any]) -> dict[str, Any]:
    for step in payload.get("steps") or []:
        if not isinstance(step, dict):
            continue
        if step.get("name") == "agent_native.contract.plan":
            output = step.get("output")
            return output if isinstance(output, dict) else {}
        if step.get("name") == "agent_native.start":
            output = step.get("output")
            if isinstance(output, dict) and isinstance(output.get("tool_contract"), dict):
                return output["tool_contract"]
    return {}
