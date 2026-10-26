from __future__ import annotations

import duckdb

from assquack._materialization.json_reader import read_json_sql
from assquack._materialization.models import FragmentFile, ProjectionSpec
from assquack._storage.sql import quote_identifier, quote_table
from assquack._storage.tables import TableReference


def describe_fragments(
    connection: duckdb.DuckDBPyConnection,
    fragments: list[FragmentFile],
) -> list[tuple[str, str]]:
    if not fragments:
        return []

    source = read_json_sql([fragment.path for fragment in fragments])
    rows = connection.execute(f"DESCRIBE SELECT * FROM {source}").fetchall()
    return [(row[0], row[1]) for row in rows]


def create_shaped_staging(
    connection: duckdb.DuckDBPyConnection,
    shaped: TableReference,
    fragments: list[FragmentFile],
    projections: list[ProjectionSpec],
    run_id: str,
) -> None:
    shaped_table = quote_table(shaped.schema_name, shaped.table_name)
    if not fragments:
        empty_projections = "".join(
            f",\n                CAST(NULL AS {projection.duckdb_type}) AS "
            f"{quote_identifier(projection.column_name)}"
            for projection in projections
        )
        connection.execute(
            f"""
            CREATE TABLE {shaped_table} AS
            SELECT
                CAST(NULL AS VARCHAR) AS _qa_run_id,
                CAST(NULL AS TIMESTAMPTZ) AS _qa_loaded_at{empty_projections}
            WHERE false
            """
        )
        return

    projected = ",\n                ".join(
        f"try_cast({quote_identifier(item.column_name)} AS {item.duckdb_type}) "
        f"AS {quote_identifier(item.column_name)}"
        for item in projections
    )
    suffix = f",\n                {projected}" if projected else ""
    source = read_json_sql([fragment.path for fragment in fragments])
    connection.execute(
        f"""
        CREATE TABLE {shaped_table} AS
        SELECT
            ?::VARCHAR AS _qa_run_id,
            current_timestamp AS _qa_loaded_at{suffix}
        FROM {source}
        """,
        [run_id],
    )


def drop_shaped_staging(
    connection: duckdb.DuckDBPyConnection,
    shaped: TableReference,
) -> None:
    connection.execute(
        f"DROP TABLE {quote_table(shaped.schema_name, shaped.table_name)}"
    )
