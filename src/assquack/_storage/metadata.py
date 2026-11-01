from __future__ import annotations

import json
from datetime import datetime
from uuid import uuid4

import duckdb

from assquack._storage.models import AssetRecord, SuccessfulRun


class MetadataRepository:
    """Domain-oriented access to the small metadata catalog used by the MVP."""

    def __init__(self, connection: duckdb.DuckDBPyConnection) -> None:
        self._connection = connection

    def save_asset(self, asset: AssetRecord) -> None:
        self._connection.execute(
            """
            INSERT INTO assquack_meta.assets VALUES (
                ?, ?, ?, ?, ?, ?, current_timestamp, current_timestamp
            )
            ON CONFLICT (asset_id) DO UPDATE SET
                asset_name = excluded.asset_name,
                asset_signature = excluded.asset_signature,
                schema_name = excluded.schema_name,
                table_name = excluded.table_name,
                materialization_mode = excluded.materialization_mode,
                updated_at = excluded.updated_at
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
            """
            INSERT INTO assquack_meta.runs (
                run_id, asset_id, status, runtime, fragments_owned
            ) VALUES (?, ?, 'running', ?, false)
            """,
            [run_id, asset_id, runtime],
        )

    def claim_run_fragments(self, run_id: str) -> None:
        """Record ownership only after exclusively creating the run directory."""

        self._connection.execute(
            "UPDATE assquack_meta.runs SET fragments_owned = true WHERE run_id = ?",
            [run_id],
        )

    def find_latest_successful_run(self, asset_id: str) -> SuccessfulRun | None:
        row = self._connection.execute(
            """
            SELECT run_id, CAST(materialized_at AS VARCHAR), coalesce(row_count, 0)
            FROM assquack_meta.runs
            WHERE asset_id = ? AND status = 'success'
            ORDER BY materialized_at DESC
            LIMIT 1
            """,
            [asset_id],
        ).fetchone()
        if row is None:
            return None
        return SuccessfulRun(
            run_id=row[0],
            materialized_at=datetime.fromisoformat(row[1]),
            row_count=row[2],
        )

    def recover_interrupted_runs(
        self, asset_id: str, active_run_ids: tuple[str, ...] = ()
    ) -> None:
        """Mark abandoned attempts failed while holding the database writer lock."""

        self._connection.execute(
            """
            UPDATE assquack_meta.runs
            SET status = 'failed', error = 'Interrupted before publication completed'
            WHERE asset_id = ? AND status = 'running'
                AND run_id NOT IN (SELECT unnest(?::VARCHAR[]))
            """,
            [asset_id, list(active_run_ids)],
        )

    def find_obsolete_fragment_runs(self, asset_id: str) -> list[str]:
        """Select completed attempts outside retention using catalog chronology."""

        rows = self._connection.execute(
            """
            SELECT run_id FROM (
                SELECT run_id, status, fragments_owned, row_number() OVER (
                    PARTITION BY status
                    ORDER BY CASE WHEN status = 'success' THEN materialized_at
                                  ELSE runtime END DESC, runtime DESC, run_id DESC
                ) AS position
                FROM assquack_meta.runs
                WHERE asset_id = ? AND status IN ('success', 'failed')
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
            """
            SELECT count(*)
            FROM assquack_meta.exports
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
            """
            UPDATE assquack_meta.runs
            SET status = 'success',
                materialized_at = current_timestamp,
                duration_ms = ?,
                row_count = ?,
                error = NULL
            WHERE run_id = ?
            """,
            [duration_ms, row_count, run_id],
        )

    def mark_run_failed(self, run_id: str, error: str, duration_ms: int) -> None:
        self._connection.execute(
            """
            UPDATE assquack_meta.runs
            SET status = 'failed', duration_ms = ?, error = ?
            WHERE run_id = ?
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
            """
            INSERT INTO assquack_meta.exports (
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
