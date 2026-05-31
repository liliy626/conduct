from __future__ import annotations

import asyncio
from typing import Any, Awaitable, Callable

from gateway_core.agents.universal_hub.shadow.models import LegacyTraceSnapshot


ReplayOne = Callable[[LegacyTraceSnapshot], Awaitable[dict[str, Any]]]


class OfflineShadowReplayer:
    """Bounded-concurrency offline replayer.

    It is intentionally independent of HTTP and UniversalHub graph wiring.
    """

    def __init__(self, *, replay_one: ReplayOne) -> None:
        self._replay_one = replay_one

    async def replay_batch(
        self,
        snapshots: list[LegacyTraceSnapshot],
        *,
        concurrency: int = 10,
    ) -> list[dict[str, Any]]:
        semaphore = asyncio.Semaphore(max(1, int(concurrency)))

        async def run_one(snapshot: LegacyTraceSnapshot) -> dict[str, Any]:
            async with semaphore:
                return await self._replay_one(snapshot)

        return await asyncio.gather(*(run_one(snapshot) for snapshot in snapshots))
