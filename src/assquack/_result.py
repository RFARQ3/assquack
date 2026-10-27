from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

import duckdb

from assquack._cache import asset_exists
from assquack._config import AssquackConfig
from assquack._exports import resolve_exports
from assquack._exports.destinations import FileSystemDestination
from assquack._query import execute_query, query_dataframe
from assquack._storage.database import open_database
from assquack._storage.locking import writer_lock
from assquack._storage.metadata import MetadataRepository
from assquack._storage.tables import TableReference


@dataclass(frozen=True, slots=True)
class AssquackResult:
    """Reference to a successfully published DuckDB asset table."""

    config: AssquackConfig
    asset_id: str
    asset_name: str
    run_id: str
    table: TableReference
    row_count: int
    cached: bool = False

    async def query(
        self,
        sql: str | None = None,
        params: Sequence[object] | None = None,
    ) -> duckdb.DuckDBPyRelation:
        return execute_query(self.config, self.table, sql, params)

    def to_df(self) -> Any:
        return query_dataframe(self.config, self.table)

    def exists(self) -> bool:
        connection = open_database(self.config)
        try:
            return asset_exists(
                connection,
                MetadataRepository(connection),
                asset_id=self.asset_id,
                table=self.table,
            )
        finally:
            connection.close()

    def export_parquet(self, path: str | None = None) -> str | list[str]:
        """Export to configured destinations, or the default local base if disabled."""
        destinations = self.config.export_destinations or [
            FileSystemDestination(
                base_uri=str(self.config.home / self.config.environment / "exports")
            )
        ]
        with resolve_exports(
            {"uri": path or "parquet", "format": "parquet"},
            self.asset_name,
            destinations,
        ) as exports:
            with writer_lock(self.config.resolved_database_path):
                connection = open_database(self.config)
                try:
                    for destination, target in exports:
                        destination.export(connection, self.table, target)
                        MetadataRepository(connection).record_export(
                            run_id=self.run_id,
                            asset_id=self.asset_id,
                            alias=target.alias,
                            uri=target.logical_uri,
                            resolved_uri=target.resolved_uri,
                            destination_fingerprint=target.destination_fingerprint,
                            row_count=self.row_count,
                            format_name=target.format,
                            role=target.role,
                            options=target.options,
                        )
                finally:
                    connection.close()
            uris = [target.resolved_uri for _, target in exports]
            return uris[0] if len(uris) == 1 else uris
