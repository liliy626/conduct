from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from typing import Any

from gateway_core.agents.universal_hub.protocols.evidence import EvidenceDataType, EvidenceMetadata, EvidencePayload


_thread_locks: dict[str, asyncio.Lock] = {}


def get_thread_lock(thread_id: str) -> asyncio.Lock:
    clean_thread_id = str(thread_id or "default")
    lock = _thread_locks.get(clean_thread_id)
    if lock is None:
        lock = asyncio.Lock()
        _thread_locks[clean_thread_id] = lock
    return lock


@dataclass
class _StoredEvidence:
    envelope: EvidencePayload
    expires_at: float


class InMemoryEvidenceBackend:
    """Small async backend for tests and local development.

    The production shape is intentionally simple so it can be swapped for a
    Redis backend with the same get/setex/delete semantics.
    """

    def __init__(self) -> None:
        self._items: dict[str, _StoredEvidence] = {}

    async def setex(self, key: str, ttl_sec: int, value: EvidencePayload) -> None:
        self._items[str(key)] = _StoredEvidence(envelope=value, expires_at=time.time() + max(1, int(ttl_sec)))

    async def get(self, key: str) -> EvidencePayload | None:
        stored = self._items.get(str(key))
        if stored is None:
            return None
        if stored.expires_at < time.time():
            self._items.pop(str(key), None)
            return None
        return stored.envelope

    async def find_by_ref(self, ref_id: str) -> tuple[str, EvidencePayload] | None:
        suffix = f":{ref_id}"
        for key, stored in list(self._items.items()):
            if stored.expires_at < time.time():
                self._items.pop(key, None)
                continue
            if key.endswith(suffix):
                return key, stored.envelope
        return None


class SecurityEvidenceGate:
    """Single access gate for external evidence payloads."""

    def __init__(self, backend: InMemoryEvidenceBackend | None = None) -> None:
        self._backend = backend or InMemoryEvidenceBackend()

    async def write_evidence(
        self,
        *,
        ref_id: str,
        payload: Any,
        meta: EvidenceMetadata,
        data_type: EvidenceDataType = "postgres_rows",
        ttl_sec: int = 86400,
    ) -> str:
        clean_ref_id = str(ref_id or "").strip()
        if not clean_ref_id:
            raise ValueError("ref_id is required")
        key = _composite_key(meta.owner_school_id, meta.thread_id, meta.turn_id, clean_ref_id)
        async with get_thread_lock(meta.thread_id):
            await self._backend.setex(
                key,
                ttl_sec,
                EvidencePayload(metadata=meta, data_type=data_type, payload=payload),
            )
        return clean_ref_id

    async def read_evidence(self, ref_id: str, request_ctx: dict[str, Any]) -> Any:
        req_school = str(request_ctx.get("school_id") or "")
        req_thread = str(request_ctx.get("thread_id") or "")
        req_turn = int(request_ctx.get("turn_id", 0) or 0)
        key = _composite_key(req_school, req_thread, req_turn, str(ref_id or ""))
        async with get_thread_lock(req_thread):
            envelope = await self._backend.get(key)
        if envelope is None:
            found = await self._backend.find_by_ref(str(ref_id or ""))
            if found is not None:
                _, foreign_envelope = found
                if (
                    foreign_envelope.metadata.thread_id == req_thread
                    and foreign_envelope.metadata.turn_id == req_turn
                    and foreign_envelope.metadata.owner_school_id != req_school
                ):
                    raise PermissionError("cross-tenant evidence read blocked")
            raise KeyError(f"evidence not found or expired: {ref_id}")
        if envelope.metadata.owner_school_id != req_school or envelope.metadata.thread_id != req_thread:
            raise PermissionError("cross-tenant evidence read blocked")
        return envelope.payload


def _composite_key(school_id: str, thread_id: str, turn_id: int, ref_id: str) -> str:
    return f"yili:evb:{school_id}:{thread_id}:{int(turn_id)}:{ref_id}"
