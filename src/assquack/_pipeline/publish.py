from __future__ import annotations

from collections.abc import Callable, Mapping
from types import MappingProxyType
from typing import Protocol

import duckdb

from assquack._run import AssquackRun
from assquack._storage.database import database_operation
from assquack._storage.metadata import MetadataRepository
from assquack._storage.tables import TableReference, create_schema


class PublicationStrategy(Protocol):
    def publish(
        self,
        connection: duckdb.DuckDBPyConnection,
        metadata: MetadataRepository,
        *,
        target: TableReference,
        staging: TableReference,
        run_id: str,
        row_count: int,
        duration_ms: int,
    ) -> None: ...


class ReplacePublicationStrategy:
    """Replace the table and publication metadata in the caller's transaction."""

    def publish(
        self,
        connection: duckdb.DuckDBPyConnection,
        metadata: MetadataRepository,
        *,
        target: TableReference,
        staging: TableReference,
        run_id: str,
        row_count: int,
        duration_ms: int,
    ) -> None:
        create_schema(connection, target.schema_name, target.catalog_name)
        shaped_table = staging.sql
        connection.execute(
            f"""
            CREATE OR REPLACE TABLE
                {target.sql} AS
            SELECT *
            FROM {shaped_table}
            """
        )
        metadata.mark_run_successful(
            run_id,
            duration_ms=duration_ms,
            row_count=row_count,
        )
        metadata.record_publication(target, run_id)


_STRATEGIES: Mapping[str, Callable[[], PublicationStrategy]] = MappingProxyType(
    {"replace": ReplacePublicationStrategy}
)


def publication_strategy(mode: str) -> PublicationStrategy:
    """Construct the declared mode's strategy from the framework-owned registry."""
    try:
        factory = _STRATEGIES[mode]
    except KeyError:
        raise ValueError(f"Unsupported materialization mode: {mode!r}") from None
    return factory()


def publish(run: AssquackRun) -> None:
    """Replace the table and publication metadata inside the pipeline transaction."""
    assert run.candidate_table is not None
    with database_operation(run.config) as connection:
        publication_strategy(run.resolved.mode).publish(
            connection,
            run.repository,
            target=run.resolved.table,
            staging=run.candidate_table,
            run_id=run.id,
            row_count=run.row_count,
            duration_ms=run.duration_ms,
        )
