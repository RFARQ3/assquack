from __future__ import annotations

from dataclasses import dataclass

import duckdb

from assquack._storage.sql import quote_identifier


@dataclass(frozen=True, slots=True)
class TableReference:
    schema_name: str
    table_name: str


def bootstrap_database(connection: duckdb.DuckDBPyConnection) -> None:
    connection.execute("CREATE SCHEMA IF NOT EXISTS assquack")
    connection.execute("CREATE SCHEMA IF NOT EXISTS assquack_stage")
    connection.execute("CREATE SCHEMA IF NOT EXISTS assquack_assets")

    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS assquack.assets (
            asset_id VARCHAR PRIMARY KEY,
            asset_name VARCHAR NOT NULL,
            asset_signature VARCHAR,
            schema_name VARCHAR NOT NULL,
            table_name VARCHAR NOT NULL,
            materialization_mode VARCHAR NOT NULL,
            created_at TIMESTAMPTZ NOT NULL,
            updated_at TIMESTAMPTZ NOT NULL
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS assquack.runs (
            run_id VARCHAR PRIMARY KEY,
            asset_id VARCHAR NOT NULL,
            status VARCHAR NOT NULL,
            runtime TIMESTAMPTZ NOT NULL,
            materialized_at TIMESTAMPTZ,
            duration_ms BIGINT,
            row_count BIGINT,
            error VARCHAR
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS assquack.schemas (
            asset_id VARCHAR NOT NULL,
            run_id VARCHAR NOT NULL,
            schema_json JSON,
            json_structure JSON,
            created_at TIMESTAMPTZ NOT NULL
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS assquack.schema_observations (
            asset_id VARCHAR NOT NULL,
            run_id VARCHAR NOT NULL,
            path VARCHAR NOT NULL,
            observed_type VARCHAR NOT NULL,
            present_count BIGINT NOT NULL,
            null_count BIGINT NOT NULL,
            total_count BIGINT NOT NULL,
            first_seen_at TIMESTAMPTZ NOT NULL,
            last_seen_at TIMESTAMPTZ NOT NULL
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS assquack.exports (
            export_id VARCHAR PRIMARY KEY,
            run_id VARCHAR NOT NULL,
            asset_id VARCHAR NOT NULL,
            alias VARCHAR,
            uri VARCHAR NOT NULL,
            resolved_uri VARCHAR NOT NULL,
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
    connection.execute(
        f"CREATE SCHEMA IF NOT EXISTS {quote_identifier(schema_name)}"
    )
