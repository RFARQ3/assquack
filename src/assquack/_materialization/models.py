from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any, TypeAlias

from assquack._storage.models import SchemaObservation
from assquack._storage.tables import TableReference


JsonStructure: TypeAlias = str | dict[str, Any] | list[Any]


@dataclass(frozen=True, slots=True)
class MaterializationRequest:
    fn: Callable[..., Any]
    arguments: tuple[Any, ...]
    keyword_arguments: Mapping[str, Any]
    asset_id: str
    asset_name: str
    asset_signature: str
    table: TableReference
    export: str | Mapping[str, str] | None
    use_cache: bool = False


@dataclass(frozen=True, slots=True)
class NormalizedChunk:
    batch_id: int
    rows: list[dict[str, Any]]


@dataclass(frozen=True, slots=True)
class BatchSchemaEvidence:
    json_structure: JsonStructure
    observations: list[SchemaObservation]
    total_count: int


@dataclass(frozen=True, slots=True)
class ProjectionSpec:
    source_field: str
    column_name: str
    duckdb_type: str

    def as_dict(self) -> dict[str, str]:
        return {
            "source_field": self.source_field,
            "column_name": self.column_name,
            "duckdb_type": self.duckdb_type,
        }


@dataclass(frozen=True, slots=True)
class StagingTables:
    raw: TableReference
    shaped: TableReference
