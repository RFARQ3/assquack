from __future__ import annotations

import duckdb

from assquack._storage.metadata import MetadataRepository
from assquack._storage.models import SuccessfulRun
from assquack._storage.tables import TableReference, table_exists


def find_cached_run(
    connection: duckdb.DuckDBPyConnection,
    metadata: MetadataRepository,
    *,
    asset_id: str,
    table: TableReference,
) -> SuccessfulRun | None:
    latest = metadata.find_latest_successful_run(asset_id)
    if latest is None or not table_exists(connection, table):
        return None
    # TODO: Apply configurable TTL and export-required freshness rules.
    return latest


def asset_exists(
    connection: duckdb.DuckDBPyConnection,
    metadata: MetadataRepository,
    *,
    asset_id: str,
    table: TableReference,
) -> bool:
    return find_cached_run(
        connection,
        metadata,
        asset_id=asset_id,
        table=table,
    ) is not None
