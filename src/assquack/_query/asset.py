from __future__ import annotations

from collections.abc import Sequence
from dataclasses import replace
from typing import Any

import duckdb

from assquack._config import AssquackConfig
from assquack._errors import MissingAssetTableError
from assquack._query.general import qualify_sql
from assquack._storage.database import database_catalog, database_operation
from assquack._storage.tables import TableReference, table_exists


def execute_query(
    config: AssquackConfig,
    table: TableReference,
    sql: str | None = None,
    params: Sequence[object] | None = None,
) -> duckdb.DuckDBPyRelation:
    with database_operation(config) as connection:
        table = replace(table, catalog_name=database_catalog(config))
        if not table_exists(connection, table):
            raise MissingAssetTableError(
                "The asset has no published table. Materialize it successfully "
                "before querying."
            )
        if not sql and params is None:
            return connection.table(table.sql)

        # The statement-local alias cannot redirect other asset queries.
        normalized = connection.execute(
            "SELECT json_deserialize_sql(json_serialize_sql(?))",
            [sql or "SELECT * FROM data"],
        ).fetchone()
        assert normalized is not None
        statement, bindings = qualify_sql(
            connection,
            f"WITH data AS (SELECT * FROM {table.sql}) "
            f"SELECT * FROM (\n{normalized[0]}\n)",
            params,
        )
        return connection.sql(statement, params=bindings)


def query_dataframe(
    config: AssquackConfig,
    table: TableReference,
    sql: str | None = None,
    params: Sequence[object] | None = None,
) -> Any:
    with database_operation(config):
        return execute_query(config, table, sql, params).fetchdf()
