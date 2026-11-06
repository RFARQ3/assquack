from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from typing import Any

import duckdb

from assquack._config import AssquackConfig
from assquack._exports.destinations import ExportDestination
from assquack._exports.operations import export_result
from assquack._query import execute_query, query_dataframe
from assquack._storage.cache import asset_exists
from assquack._storage.database import database_catalog, database_operation
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
        params: Sequence[object] | Mapping[str, object] | None = None,
    ) -> duckdb.DuckDBPyRelation:
        return execute_query(self.config, self.table, sql, params)

    def to_df(self) -> Any:
        return query_dataframe(self.config, self.table)

    def exists(self) -> bool:
        with database_operation(self.config) as connection:
            return asset_exists(
                connection,
                MetadataRepository(connection, database_catalog(self.config)),
                asset_id=self.asset_id,
                table=replace(self.table, catalog_name=database_catalog(self.config)),
            )

    def export(self, destination: str | ExportDestination) -> str | list[str]:
        """Export the current table to a path or destination-owned target."""
        return export_result(self, destination)
