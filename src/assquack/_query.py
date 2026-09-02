from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from assquack._config import AssquackConfig
from assquack._errors import MissingAssetTableError
from assquack._storage.database import open_database
from assquack._storage.sql import quote_table
from assquack._storage.tables import TableReference, table_exists


def execute_query(
    config: AssquackConfig,
    table: TableReference,
    sql: str | None = None,
    params: Sequence[object] | None = None,
) -> list[tuple[Any, ...]]:
    connection = open_database(config)
    try:
        _create_data_alias(connection, table)
        return connection.execute(sql or "SELECT * FROM data", params or []).fetchall()
    finally:
        connection.close()


def query_dataframe(
    config: AssquackConfig,
    table: TableReference,
    sql: str | None = None,
    params: Sequence[object] | None = None,
) -> Any:
    connection = open_database(config)
    try:
        _create_data_alias(connection, table)
        return connection.execute(sql or "SELECT * FROM data", params or []).fetchdf()
    finally:
        connection.close()


def _create_data_alias(connection: Any, table: TableReference) -> None:
    if not table_exists(connection, table):
        raise MissingAssetTableError(
            "The asset has no published table. Materialize it successfully before querying."
        )
    connection.execute(
        f"""
        CREATE OR REPLACE TEMP VIEW data AS
        SELECT * FROM {quote_table(table.schema_name, table.table_name)}
        """
    )
