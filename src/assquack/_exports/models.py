from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True, slots=True)
class ExportTarget:
    alias: str | None
    logical_uri: str
    resolved_uri: str
    format: str = "parquet"
    role: str = "default"
    options: dict[str, object] = field(default_factory=dict[str, object])
