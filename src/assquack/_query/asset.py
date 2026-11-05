from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import replace
from typing import Any

import duckdb

from assquack._config import AssquackConfig
from assquack._storage.database import database_catalog, database_operation
from assquack._storage.tables import TableReference


def execute_query(
    config: AssquackConfig,
    table: TableReference,
    sql: str | None = None,
    params: Sequence[object] | Mapping[str, object] | None = None,
) -> duckdb.DuckDBPyRelation:
    with database_operation(config) as connection:
        table = replace(table, catalog_name=database_catalog(config))
        # Only the physical source is generated; DuckDB owns the caller's SQL.
        statement = f"FROM {table.sql}"
        if sql is not None:
            statement += f" {sql}"
        return connection.sql(statement, params=params)


def query_dataframe(
    config: AssquackConfig,
    table: TableReference,
    sql: str | None = None,
    params: Sequence[object] | Mapping[str, object] | None = None,
) -> Any:
    with database_operation(config):
        return execute_query(config, table, sql, params).fetchdf()
