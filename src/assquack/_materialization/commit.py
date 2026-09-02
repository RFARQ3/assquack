from __future__ import annotations

from typing import Protocol

import duckdb

from assquack._materialization.models import StagingTables
from assquack._storage.database import transaction
from assquack._storage.metadata import MetadataRepository
from assquack._storage.sql import quote_table
from assquack._storage.tables import TableReference, create_schema


class CommitStrategy(Protocol):
    def publish(
        self,
        connection: duckdb.DuckDBPyConnection,
        metadata: MetadataRepository,
        *,
        target: TableReference,
        staging: StagingTables,
        run_id: str,
        row_count: int,
        duration_ms: int,
    ) -> None: ...


class ReplaceCommitStrategy:
    """Atomically replace the current table and advance successful metadata."""

    def publish(
        self,
        connection: duckdb.DuckDBPyConnection,
        metadata: MetadataRepository,
        *,
        target: TableReference,
        staging: StagingTables,
        run_id: str,
        row_count: int,
        duration_ms: int,
    ) -> None:
        with transaction(connection):
            create_schema(connection, target.schema_name)
            connection.execute(
                f"""
                CREATE OR REPLACE TABLE
                    {quote_table(target.schema_name, target.table_name)} AS
                SELECT *
                FROM {quote_table(staging.shaped.schema_name, staging.shaped.table_name)}
                """
            )
            metadata.mark_run_successful(
                run_id,
                duration_ms=duration_ms,
                row_count=row_count,
            )
