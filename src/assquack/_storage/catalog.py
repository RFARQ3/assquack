from __future__ import annotations

import duckdb

from assquack._storage.sql import quote_identifier


def bootstrap_database(connection: duckdb.DuckDBPyConnection, catalog: str) -> None:
    prefix = quote_identifier(catalog)
    connection.execute(f"CREATE SCHEMA IF NOT EXISTS {prefix}.assquack_meta")
    connection.execute(f"CREATE SCHEMA IF NOT EXISTS {prefix}.assquack_stage")

    connection.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {prefix}.assquack_meta.assets (
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
        f"""
        CREATE TABLE IF NOT EXISTS {prefix}.assquack_meta.runs (
            run_id VARCHAR PRIMARY KEY,
            asset_id VARCHAR NOT NULL,
            status VARCHAR NOT NULL,
            runtime TIMESTAMPTZ NOT NULL,
            materialized_at TIMESTAMPTZ,
            duration_ms BIGINT,
            row_count BIGINT,
            error VARCHAR,
            fragments_owned BOOLEAN DEFAULT true,
            resolve_status VARCHAR NOT NULL DEFAULT 'unstarted',
            materialize_status VARCHAR NOT NULL DEFAULT 'unstarted',
            stage_status VARCHAR NOT NULL DEFAULT 'unstarted',
            publish_status VARCHAR NOT NULL DEFAULT 'unstarted',
            export_status VARCHAR NOT NULL DEFAULT 'unstarted',
            outcome VARCHAR NOT NULL DEFAULT 'running',
            failed_stage VARCHAR,
            error_type VARCHAR,
            error_message VARCHAR
        )
        """
    )
    connection.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {prefix}.assquack_meta.exports (
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

    columns = {
        row[0]
        for row in connection.execute(
            f"DESCRIBE {prefix}.assquack_meta.runs"
        ).fetchall()
    }
    required = {
        "resolve_status",
        "materialize_status",
        "stage_status",
        "publish_status",
        "export_status",
        "outcome",
        "failed_stage",
        "error_type",
        "error_message",
    }
    if not required.issubset(columns):
        raise ValueError(
            "This database has an incompatible Assquack metadata schema. "
            "Use a fresh database path; existing data has not been migrated."
        )

    connection.execute(f"""
        CREATE TABLE IF NOT EXISTS {prefix}.assquack_meta.current_publications (
            schema_name VARCHAR NOT NULL,
            table_name VARCHAR NOT NULL,
            run_id VARCHAR NOT NULL,
            PRIMARY KEY (schema_name, table_name)
        )
    """)
