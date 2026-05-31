from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class SchoolFieldIndex:
    field_id: str
    source_field: str
    label: str
    role: str
    field_type: str = ""
    searchable_text: str = ""
    filterable: bool = False
    groupable: bool = False
    sensitive: bool = False


@dataclass(frozen=True)
class SchoolDatasetIndex:
    dataset_id: str
    label: str
    description: str
    source_schema: str
    source_view: str
    searchable_text: str
    fields: list[SchoolFieldIndex] = field(default_factory=list)
    default_field_ids: list[str] = field(default_factory=list)
    time_fields: list[str] = field(default_factory=list)
    person_fields: list[str] = field(default_factory=list)
    organization_fields: list[str] = field(default_factory=list)
    metric_fields: list[str] = field(default_factory=list)
    status_fields: list[str] = field(default_factory=list)
    sensitive_fields: list[str] = field(default_factory=list)
    raw: dict[str, Any] = field(default_factory=dict)

    def field(self, field_id: str) -> SchoolFieldIndex:
        for item in self.fields:
            if item.field_id == field_id:
                return item
        raise KeyError(f"unknown school dataset field: {field_id}")

    def source_field(self, field_id: str) -> str:
        return self.field(field_id).source_field


@dataclass(frozen=True)
class SchoolSchemaIndex:
    school_id: str
    school_name: str
    source_schema: str
    datasets: list[SchoolDatasetIndex]

    @property
    def tenant_id(self) -> str:
        return self.school_id

    @property
    def tenant_name(self) -> str:
        return self.school_name

    def dataset(self, dataset_id: str) -> SchoolDatasetIndex:
        for item in self.datasets:
            if item.dataset_id == dataset_id:
                return item
        raise KeyError(f"unknown school dataset: {dataset_id}")
