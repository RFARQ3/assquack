from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from assquack._cache import asset_exists
from assquack._config import AssquackConfig
from assquack._exports.parquet import ParquetExportWriter
from assquack._exports.targets import default_parquet_target
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
    ) -> list[tuple[Any, ...]]:
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

    def export_parquet(self, path: str | None = None) -> str:
        target = default_parquet_target(
            path,
            asset_name=self.asset_name,
            config=self.config,
        )
        with writer_lock(self.config.resolved_database_path):
            connection = open_database(self.config)
            try:
                ParquetExportWriter().write(connection, self.table, target)
                MetadataRepository(connection).record_export(
                    run_id=self.run_id,
                    asset_id=self.asset_id,
                    alias=target.alias,
                    uri=target.logical_uri,
                    resolved_uri=target.resolved_uri,
                    row_count=self.row_count,
                )
            finally:
                connection.close()
        return target.resolved_uri
