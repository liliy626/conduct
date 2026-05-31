from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal


DataClassification = Literal["public", "internal", "restricted", "highly_confidential"]
EvidenceDataType = Literal["postgres_rows", "vector_chunks", "image_binary_meta", "artifact_meta"]


@dataclass(frozen=True)
class EvidenceMetadata:
    owner_school_id: str
    api_key_id_hash: str
    thread_id: str
    turn_id: int
    data_classification: DataClassification


@dataclass(frozen=True)
class EvidencePayload:
    metadata: EvidenceMetadata
    data_type: EvidenceDataType
    payload: Any
