from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import timedelta
from typing import Any, Literal

from assquack._storage.tables import TableReference

AssetMode = Literal["replace"]


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
    mode: AssetMode
    use_cache: bool = False
    cache_expiration: timedelta | None = None


@dataclass(frozen=True, slots=True)
class FragmentFile:
    index: int
    path: str
    row_count: int


@dataclass(frozen=True, slots=True)
class ProjectionSpec:
    source_field: str
    column_name: str
    duckdb_type: str


@dataclass(frozen=True, slots=True)
class StagingTables:
    shaped: TableReference
