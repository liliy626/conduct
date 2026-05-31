from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
from typing import Any

from gateway_core.agents.universal_hub.shadow.bombard import (
    OfflineTraceBombard,
    trace_fixture_skill_stream_factory,
)
from gateway_core.agents.universal_hub.shadow.recorder import LocalShadowAuditor
from gateway_core.agents.universal_hub.shadow.trace_loader import load_school_trace_snapshots


async def run_trace_fixture_bombard(
    *,
    trace_dir: str | Path = "logs/school_trace",
    audit_log_path: str | Path = "logs/universal_hub_shadow/production_mass_audit.jsonl",
    limit: int = 200,
    concurrency: int = 10,
) -> dict[str, Any]:
    """Run a no-network trace-fixture bombard over persisted school traces.

    This mode validates the loader, legacy trace stream extraction, semantic
    comparator, auditor, and bounded-concurrency replay shell. It is not a live
    SchoolSqlSkill replay against LLM/database dependencies.
    """

    snapshots = load_school_trace_snapshots(trace_dir, limit=limit)
    auditor = LocalShadowAuditor(log_path=str(audit_log_path))
    runner = OfflineTraceBombard(
        auditor=auditor,
        skill_stream_factory=trace_fixture_skill_stream_factory,
    )
    return await runner.replay_batch(snapshots, concurrency=concurrency)


def main() -> int:
    parser = argparse.ArgumentParser(description="Offline Universal Hub shadow trace bombard.")
    parser.add_argument("--trace-dir", default="logs/school_trace")
    parser.add_argument("--audit-log", default="logs/universal_hub_shadow/production_mass_audit.jsonl")
    parser.add_argument("--limit", type=int, default=200)
    parser.add_argument("--concurrency", type=int, default=10)
    parser.add_argument(
        "--mode",
        choices=["trace-fixture"],
        default="trace-fixture",
        help="trace-fixture is graph-free and no-network; live Skill replay is intentionally not wired here.",
    )
    args = parser.parse_args()

    summary = asyncio.run(
        run_trace_fixture_bombard(
            trace_dir=args.trace_dir,
            audit_log_path=args.audit_log,
            limit=args.limit,
            concurrency=args.concurrency,
        )
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2, default=str))
    return 0 if summary.get("failed", 0) == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
