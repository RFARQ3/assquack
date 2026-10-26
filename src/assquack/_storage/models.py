from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True, slots=True)
class AssetRecord:
    asset_id: str
    asset_name: str
    asset_signature: str
    schema_name: str
    table_name: str
    materialization_mode: str


@dataclass(frozen=True, slots=True)
class SuccessfulRun:
    run_id: str
    materialized_at: datetime
    row_count: int
