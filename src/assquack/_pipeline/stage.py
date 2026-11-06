from __future__ import annotations

import duckdb

from assquack._run import AssquackRun
from assquack._storage.database import database_operation
from assquack._storage.fragment_writer import FragmentFile
from assquack._storage.json_reader import JsonReadRecovery, read_json_sql
from assquack._storage.schemas import ProjectionSpec, replace_schema_strategy
from assquack._storage.sql import quote_identifier
from assquack._storage.tables import TableReference


def create_shaped_staging(
    connection: duckdb.DuckDBPyConnection,
    shaped: TableReference,
    fragments: list[FragmentFile],
    projections: list[ProjectionSpec],
    run_id: str,
    reader: JsonReadRecovery | None = None,
) -> None:
    shaped_table = shaped.sql
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
    paths = [fragment.path for fragment in fragments]
    reader = reader or JsonReadRecovery()
    reader.read(
        paths,
        lambda limit: connection.execute(
            f"""
        CREATE TABLE {shaped_table} AS
        SELECT
            ?::VARCHAR AS _qa_run_id,
            current_timestamp AS _qa_loaded_at{suffix}
        FROM {read_json_sql(paths, limit)}
        """,
            [run_id],
        ),
    )


def drop_shaped_staging(
    connection: duckdb.DuckDBPyConnection,
    shaped: TableReference,
) -> None:
    connection.execute(f"DROP TABLE IF EXISTS {shaped.sql}")


def stage(run: AssquackRun) -> None:
    """Infer this run's columns and load its fragments into a candidate table."""
    assert run.candidate_table is not None
    reader = JsonReadRecovery()
    with database_operation(run.config) as connection:
        projections = replace_schema_strategy(
            run.config.schema, connection, lambda: run.fragments, reader
        ).resolve()
        create_shaped_staging(
            connection,
            run.candidate_table,
            run.fragments,
            projections,
            run.id,
            reader,
        )
