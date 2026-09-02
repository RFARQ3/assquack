from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ExportTarget:
    alias: str | None
    logical_uri: str
    resolved_uri: str
    format: str = "parquet"
