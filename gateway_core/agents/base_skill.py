from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, AsyncIterator

from gateway_core.agents.universal_hub.models import MultimodalOutputContract, SkillEvent
from gateway_core.agents.universal_hub.state import UniversalAgentState


class RuntimeContext(dict):
    """Runtime-only context for non-serializable objects.

    FastAPI Request handles, emitters, trace objects, model clients, and test
    doubles belong here, never in UniversalAgentState.
    """


class BaseAgentSkill(ABC):
    @property
    @abstractmethod
    def name(self) -> str:
        raise NotImplementedError

    @abstractmethod
    async def astream(
        self,
        state: UniversalAgentState,
        ctx: RuntimeContext | dict[str, Any],
    ) -> AsyncIterator[SkillEvent]:
        raise NotImplementedError

    async def arun(self, state: UniversalAgentState, ctx: RuntimeContext | dict[str, Any]) -> dict[str, Any]:
        events: list[SkillEvent] = []
        async for event in self.astream(state, ctx):
            events.append(event)
        return {"events": [event.model_dump() for event in events]}


class BaseMultimodalAgentSkill(BaseAgentSkill):
    """Base class for artifact skills that must emit a typed proof-bearing event."""

    async def _execute_multimodal_core(
        self,
        state: UniversalAgentState,
        ctx: RuntimeContext | dict[str, Any],
    ) -> MultimodalOutputContract:
        raise NotImplementedError

    async def astream(
        self,
        state: UniversalAgentState,
        ctx: RuntimeContext | dict[str, Any],
    ) -> AsyncIterator[SkillEvent]:
        contract: MultimodalOutputContract | None = None
        try:
            contract = await self._execute_multimodal_core(state, ctx)
            yield self._event_from_multimodal_contract(contract)
        finally:
            del contract

    def _event_from_multimodal_contract(self, contract: MultimodalOutputContract) -> SkillEvent:
        return SkillEvent(event_type="evidence_completed", data=_multimodal_event_data(contract))


def _multimodal_event_data(contract: MultimodalOutputContract) -> dict[str, Any]:
    proof_key = _proof_key_for_artifact_type(contract.artifact_type)
    payload = {
        "artifact_id": contract.artifact_id,
        "cdn_url": contract.cdn_url,
        "status": "completed",
        **contract.meta_payload,
    }
    if proof_key:
        payload[proof_key] = contract.crypto_proof
    return {"type": contract.artifact_type, "payload": payload}


def _proof_key_for_artifact_type(artifact_type: str) -> str:
    return {
        "image_artifact": "image_md5_proof",
        "pdf_artifact": "pdf_sha256",
        "table_artifact": "table_hash",
    }.get(str(artifact_type or ""), "")
