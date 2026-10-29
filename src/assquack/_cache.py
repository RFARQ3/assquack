from __future__ import annotations

from datetime import UTC, datetime, timedelta

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
    expiration: timedelta | None = None,
) -> SuccessfulRun | None:
    latest = metadata.find_latest_successful_run(asset_id)
    if latest is None or not table_exists(connection, table):
        return None
    if expiration is not None and _is_expired(latest, expiration):
        return None
    return latest


def _is_expired(run: SuccessfulRun, expiration: timedelta) -> bool:
    if expiration < timedelta(0):
        raise ValueError("Cache expiration cannot be negative.")
    if expiration == timedelta(0):
        return True

    materialized_at = run.materialized_at
    if materialized_at.tzinfo is None:
        materialized_at = materialized_at.replace(tzinfo=UTC)
    return datetime.now(UTC) - materialized_at >= expiration


def asset_exists(
    connection: duckdb.DuckDBPyConnection,
    metadata: MetadataRepository,
    *,
    asset_id: str,
    table: TableReference,
) -> bool:
    return (
        find_cached_run(
            connection,
            metadata,
            asset_id=asset_id,
            table=table,
        )
        is not None
    )
