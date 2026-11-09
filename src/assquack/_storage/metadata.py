from __future__ import annotations

import json
from datetime import datetime
from typing import TYPE_CHECKING
from uuid import uuid4

if TYPE_CHECKING:
    from assquack._run import AssquackRun

import duckdb

from assquack._storage.records import AssetRecord, PublicationRecord
from assquack._storage.sql import quote_identifier
from assquack._storage.tables import TableReference


class MetadataRepository:
    """Domain-oriented access to the small metadata catalog used by the MVP."""

    def __init__(
        self, connection: duckdb.DuckDBPyConnection, catalog: str | None = None
    ) -> None:
        self._connection = connection
        self._schema = (
            f"{quote_identifier(catalog)}." if catalog else ""
        ) + "assquack_meta"

    def save_asset(self, asset: AssetRecord) -> None:
        self._connection.execute(
            f"""
            INSERT INTO {self._schema}.assets (
                asset_id, asset_name, asset_signature, schema_name, table_name,
                materialization_mode
            ) VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT (asset_id) DO UPDATE SET
                asset_name = excluded.asset_name,
                asset_signature = excluded.asset_signature,
                schema_name = excluded.schema_name,
                table_name = excluded.table_name,
                materialization_mode = excluded.materialization_mode
            """,
            [
                asset.asset_id,
                asset.asset_name,
                asset.asset_signature,
                asset.schema_name,
                asset.table_name,
                asset.materialization_mode,
            ],
        )

    def start_run(self, run_id: str, asset_id: str, runtime: datetime) -> None:
        self._connection.execute(
            f"""
            INSERT INTO {self._schema}.runs (
                run_id, asset_id, status, runtime, fragments_owned
            ) VALUES (?, ?, 'running', ?, false)
            """,
            [run_id, asset_id, runtime],
        )

    def save_run_state(self, run: AssquackRun) -> None:
        """Save invocation diagnostics without changing the publication status."""
        self._connection.execute(
            f"""
            UPDATE {self._schema}.runs SET
                export_status = ?, outcome = ?,
                failed_stage = ?, error_type = ?, error_message = ?, duration_ms = ?
            WHERE run_id = ?
            """,
            [
                run.export_status,
                run.outcome,
                run.failed_stage,
                run.error_type,
                run.error_message,
                run.duration_ms,
                run.id,
            ],
        )

    def claim_run_fragments(self, run_id: str) -> None:
        """Record ownership only after exclusively creating the run directory."""

        self._connection.execute(
            f"UPDATE {self._schema}.runs SET fragments_owned = true WHERE run_id = ?",
            [run_id],
        )

    def find_current_publication(
        self, table: TableReference
    ) -> PublicationRecord | None:
        """Find the run whose rows currently occupy this physical table."""
        row = self._connection.execute(
            f"""
            SELECT r.run_id, CAST(r.materialized_at AS VARCHAR),
                   coalesce(r.row_count, 0), r.asset_id
            FROM {self._schema}.current_publications p
            JOIN {self._schema}.runs r ON r.run_id = p.run_id
            WHERE lower(p.schema_name) = lower(?) AND lower(p.table_name) = lower(?)
                  AND r.status = 'success'
            """,
            [table.schema_name, table.table_name],
        ).fetchone()
        if row is None:
            return None
        return PublicationRecord(row[0], datetime.fromisoformat(row[1]), row[2], row[3])

    def record_publication(self, table: TableReference, run_id: str) -> None:
        self._connection.execute(
            f"""
            INSERT INTO {self._schema}.current_publications
            VALUES (lower(?), lower(?), ?)
            ON CONFLICT (schema_name, table_name) DO UPDATE SET run_id = excluded.run_id
            """,
            [table.schema_name, table.table_name, run_id],
        )

    def recover_interrupted_runs(
        self, asset_id: str, active_run_ids: tuple[str, ...] = ()
    ) -> None:
        """Mark abandoned attempts failed while holding the shared connection lock."""

        self._connection.execute(
            f"""
            UPDATE {self._schema}.runs
            SET status = CASE WHEN status = 'success'
                                  THEN 'success' ELSE 'failed' END,
                outcome = 'failed',
                export_status = CASE WHEN export_status = 'running'
                    THEN 'failed' ELSE export_status END,
                failed_stage = CASE WHEN export_status = 'running' THEN 'export' END,
                error_type = 'InterruptedRun',
                error_message = 'Interrupted before completion',
                error = CASE WHEN status = 'success' THEN error
                    ELSE 'Interrupted before publication completed' END
            WHERE asset_id = ? AND outcome = 'running'
                AND run_id NOT IN (SELECT unnest(?::VARCHAR[]))
            """,
            [asset_id, list(active_run_ids)],
        )

    def find_obsolete_fragment_runs(self, asset_id: str) -> list[str]:
        """Select completed attempts outside retention using catalog chronology."""

        # Keep the latest successful extraction and the latest three failures.
        # Order successes by publication time and failures by start time.
        rows = self._connection.execute(
            f"""
            SELECT run_id FROM (
                SELECT run_id, status, fragments_owned, row_number() OVER (
                    PARTITION BY status
                    ORDER BY CASE WHEN status = 'success' THEN materialized_at
                                  ELSE runtime END DESC, runtime DESC, run_id DESC
                ) AS position
                FROM {self._schema}.runs
                WHERE asset_id = ? AND status IN ('success', 'failed')
                    AND fragments_owned
            )
            WHERE fragments_owned AND (
                (status = 'success' AND position > 1)
                OR (status = 'failed' AND position > 3)
            )
            """,
            [asset_id],
        ).fetchall()
        return [row[0] for row in rows]

    def has_export(
        self, run_id: str, resolved_uri: str, destination_fingerprint: str
    ) -> bool:
        row = self._connection.execute(
            f"""
            SELECT count(*)
            FROM {self._schema}.exports
            WHERE run_id = ? AND resolved_uri = ? AND destination_fingerprint = ?
            """,
            [run_id, resolved_uri, destination_fingerprint],
        ).fetchone()
        return bool(row and row[0])

    def mark_run_successful(
        self,
        run_id: str,
        *,
        duration_ms: int,
        row_count: int,
    ) -> None:
        self._connection.execute(
            f"""
            UPDATE {self._schema}.runs
            SET status = 'success',
                materialized_at = current_timestamp,
                duration_ms = ?,
                row_count = ?,
                error = NULL
            WHERE run_id = ?
            """,
            [duration_ms, row_count, run_id],
        )

        # The main pipeline owns the transaction containing the table
        # replacement and both metadata updates. Use the run's exact timestamp.
        self._connection.execute(
            f"""
            UPDATE {self._schema}.assets AS a
            SET first_materialized_at = coalesce(
                    a.first_materialized_at, r.materialized_at
                ),
                last_materialized_at = r.materialized_at
            FROM {self._schema}.runs AS r
            WHERE r.run_id = ? AND a.asset_id = r.asset_id
            """,
            [run_id],
        )

    def mark_run_failed(self, run_id: str, error: str, duration_ms: int) -> None:
        """Fail an unpublished attempt without invalidating a committed table."""
        self._connection.execute(
            f"""
            UPDATE {self._schema}.runs
            SET status = 'failed', duration_ms = ?, error = ?
            WHERE run_id = ? AND status = 'running'
            """,
            [duration_ms, error[:4_000], run_id],
        )

    def record_export(
        self,
        *,
        run_id: str,
        asset_id: str,
        alias: str | None,
        uri: str,
        resolved_uri: str,
        destination_fingerprint: str,
        row_count: int,
        format_name: str = "parquet",
        role: str = "default",
        options: dict[str, object] | None = None,
    ) -> None:
        self._connection.execute(
            f"""
            INSERT INTO {self._schema}.exports (
                export_id, run_id, asset_id, alias, uri, resolved_uri,
                destination_fingerprint,
                format, role, row_count, options_json, created_at
            ) VALUES (
                ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, current_timestamp
            )
            """,
            [
                str(uuid4()),
                run_id,
                asset_id,
                alias,
                uri,
                resolved_uri,
                destination_fingerprint,
                format_name,
                role,
                row_count,
                json.dumps(options or {}),
            ],
        )
