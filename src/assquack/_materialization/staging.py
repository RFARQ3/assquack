from __future__ import annotations

import duckdb

from assquack._materialization.models import NormalizedChunk, ProjectionSpec
from assquack._materialization.projection import projection_list
from assquack._storage.sql import quote_table
from assquack._storage.tables import TableReference


def create_raw_staging(
    connection: duckdb.DuckDBPyConnection,
    table: TableReference,
) -> None:
    connection.execute(
        f"""
        CREATE TABLE {quote_table(table.schema_name, table.table_name)} (
            _qa_run_id VARCHAR NOT NULL,
            _qa_batch_id INTEGER NOT NULL,
            _qa_row_number BIGINT NOT NULL,
            _qa_loaded_at TIMESTAMPTZ NOT NULL,
            _qa_payload VARIANT NOT NULL
        )
        """
    )


def stage_chunk(
    connection: duckdb.DuckDBPyConnection,
    table: TableReference,
    run_id: str,
    chunk: NormalizedChunk,
    first_row_number: int,
) -> int:
    rows = [
        [
            run_id,
            chunk.batch_id,
            first_row_number + offset,
            row,
        ]
        for offset, row in enumerate(chunk.rows)
    ]
    connection.executemany(
        f"""
        INSERT INTO {quote_table(table.schema_name, table.table_name)}
        SELECT ?, ?, ?, current_timestamp, CAST(? AS VARIANT)
        """,
        rows,
    )
    return len(rows)


def create_shaped_staging(
    connection: duckdb.DuckDBPyConnection,
    raw: TableReference,
    shaped: TableReference,
    projections: list[ProjectionSpec],
) -> None:
    projected = projection_list(projections)
    suffix = f",\n                {projected}" if projected else ""
    connection.execute(
        f"""
        CREATE TABLE {quote_table(shaped.schema_name, shaped.table_name)} AS
        SELECT
            _qa_run_id,
            _qa_loaded_at,
            _qa_payload{suffix}
        FROM {quote_table(raw.schema_name, raw.table_name)}
        ORDER BY _qa_row_number
        """
    )


def drop_shaped_staging(
    connection: duckdb.DuckDBPyConnection,
    shaped: TableReference,
) -> None:
    connection.execute(f"DROP TABLE {quote_table(shaped.schema_name, shaped.table_name)}")
    # TODO: Prune old successful raw and failed staging tables by metadata policy.
