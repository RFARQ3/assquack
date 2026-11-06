from __future__ import annotations

from dataclasses import dataclass

import duckdb

from assquack._storage.sql import quote_identifier, quote_table


@dataclass(frozen=True, slots=True)
class TableReference:
    schema_name: str
    table_name: str
    catalog_name: str | None = None

    @property
    def sql(self) -> str:
        return quote_table(self.schema_name, self.table_name, self.catalog_name)


def table_exists(
    connection: duckdb.DuckDBPyConnection,
    table: TableReference,
) -> bool:
    row = connection.execute(
        """
        SELECT count(*)
        FROM information_schema.tables
        WHERE table_catalog = coalesce(?, current_database())
            AND table_schema = ? AND table_name = ?
        """,
        [table.catalog_name, table.schema_name, table.table_name],
    ).fetchone()
    return bool(row and row[0])


def create_schema(
    connection: duckdb.DuckDBPyConnection,
    schema_name: str,
    catalog_name: str | None = None,
) -> None:
    schema = quote_identifier(schema_name)
    if catalog_name is not None:
        schema = f"{quote_identifier(catalog_name)}.{schema}"
    connection.execute(f"CREATE SCHEMA IF NOT EXISTS {schema}")
