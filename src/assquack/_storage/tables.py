from __future__ import annotations

from dataclasses import dataclass

import duckdb

from assquack._storage.sql import quote_identifier


@dataclass(frozen=True, slots=True)
class TableReference:
    schema_name: str
    table_name: str


def bootstrap_database(connection: duckdb.DuckDBPyConnection) -> None:
    connection.execute("CREATE SCHEMA IF NOT EXISTS assquack_meta")
    connection.execute("CREATE SCHEMA IF NOT EXISTS assquack_stage")

    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS assquack_meta.assets (
            asset_id VARCHAR PRIMARY KEY,
            asset_name VARCHAR NOT NULL,
            asset_signature VARCHAR,
            schema_name VARCHAR NOT NULL,
            table_name VARCHAR NOT NULL,
            materialization_mode VARCHAR NOT NULL,
            first_materialized_at TIMESTAMPTZ,
            last_materialized_at TIMESTAMPTZ
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS assquack_meta.runs (
            run_id VARCHAR PRIMARY KEY,
            asset_id VARCHAR NOT NULL,
            status VARCHAR NOT NULL,
            runtime TIMESTAMPTZ NOT NULL,
            materialized_at TIMESTAMPTZ,
            duration_ms BIGINT,
            row_count BIGINT,
            error VARCHAR,
            fragments_owned BOOLEAN DEFAULT true
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS assquack_meta.exports (
            export_id VARCHAR PRIMARY KEY,
            run_id VARCHAR NOT NULL,
            asset_id VARCHAR NOT NULL,
            alias VARCHAR,
            uri VARCHAR NOT NULL,
            resolved_uri VARCHAR NOT NULL,
            destination_fingerprint VARCHAR NOT NULL,
            format VARCHAR NOT NULL,
            role VARCHAR NOT NULL,
            row_count BIGINT,
            byte_count BIGINT,
            content_hash VARCHAR,
            options_json JSON,
            created_at TIMESTAMPTZ NOT NULL
        )
        """
    )

    _migrate_asset_timestamps(connection)


def _migrate_asset_timestamps(connection: duckdb.DuckDBPyConnection) -> None:
    """Replace registration dates with publication evidence from retained runs."""
    columns = {
        row[0] for row in connection.execute("DESCRIBE assquack_meta.assets").fetchall()
    }
    if "created_at" not in columns and "updated_at" not in columns:
        return

    connection.execute("BEGIN TRANSACTION")
    try:
        connection.execute(
            "ALTER TABLE assquack_meta.assets "
            "ADD COLUMN IF NOT EXISTS first_materialized_at TIMESTAMPTZ"
        )
        connection.execute(
            "ALTER TABLE assquack_meta.assets "
            "ADD COLUMN IF NOT EXISTS last_materialized_at TIMESTAMPTZ"
        )
        for column in ("created_at", "updated_at"):
            if column in columns:
                connection.execute(
                    f"ALTER TABLE assquack_meta.assets DROP COLUMN {column}"
                )

        # Retention removes fragments, not run records. Missing or manually
        # deleted history cannot be reconstructed from registration timestamps.
        connection.execute(
            """
            UPDATE assquack_meta.assets AS a
            SET first_materialized_at = evidence.first_materialized_at,
                last_materialized_at = evidence.last_materialized_at
            FROM (
                SELECT asset_id, min(materialized_at) AS first_materialized_at,
                       max(materialized_at) AS last_materialized_at
                FROM assquack_meta.runs
                WHERE status = 'success' AND materialized_at IS NOT NULL
                GROUP BY asset_id
            ) AS evidence
            WHERE a.asset_id = evidence.asset_id
            """
        )
    except BaseException:
        connection.execute("ROLLBACK")
        raise
    else:
        connection.execute("COMMIT")


def table_exists(
    connection: duckdb.DuckDBPyConnection,
    table: TableReference,
) -> bool:
    row = connection.execute(
        """
        SELECT count(*)
        FROM information_schema.tables
        WHERE table_schema = ? AND table_name = ?
        """,
        [table.schema_name, table.table_name],
    ).fetchone()
    return bool(row and row[0])


def create_schema(
    connection: duckdb.DuckDBPyConnection,
    schema_name: str,
) -> None:
    connection.execute(f"CREATE SCHEMA IF NOT EXISTS {quote_identifier(schema_name)}")
