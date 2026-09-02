from __future__ import annotations

import json
from uuid import uuid4

import duckdb

from assquack._storage.models import AssetRecord, SchemaObservation, SuccessfulRun


class MetadataRepository:
    """Domain-oriented access to the small metadata catalog used by the MVP."""

    def __init__(self, connection: duckdb.DuckDBPyConnection) -> None:
        self._connection = connection

    def save_asset(self, asset: AssetRecord) -> None:
        self._connection.execute(
            """
            INSERT INTO assquack.assets VALUES (
                ?, ?, ?, ?, ?, ?, current_timestamp, current_timestamp
            )
            ON CONFLICT (asset_id) DO UPDATE SET
                asset_name = excluded.asset_name,
                asset_signature = excluded.asset_signature,
                schema_name = excluded.schema_name,
                table_name = excluded.table_name,
                materialization_mode = excluded.materialization_mode,
                updated_at = current_timestamp
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

    def start_run(self, run_id: str, asset_id: str) -> None:
        self._connection.execute(
            """
            INSERT INTO assquack.runs (
                run_id, asset_id, status, runtime
            ) VALUES (?, ?, 'running', current_timestamp)
            """,
            [run_id, asset_id],
        )

    def find_latest_successful_run(self, asset_id: str) -> SuccessfulRun | None:
        row = self._connection.execute(
            """
            SELECT run_id, materialized_at, coalesce(row_count, 0)
            FROM assquack.runs
            WHERE asset_id = ? AND status = 'success'
            ORDER BY materialized_at DESC
            LIMIT 1
            """,
            [asset_id],
        ).fetchone()
        if row is None:
            return None
        return SuccessfulRun(run_id=row[0], materialized_at=row[1], row_count=row[2])

    def find_latest_schema(self, asset_id: str) -> list[dict[str, str]]:
        row = self._connection.execute(
            """
            SELECT s.schema_json
            FROM assquack.schemas AS s
            JOIN assquack.runs AS r USING (run_id)
            WHERE s.asset_id = ?
              AND s.schema_json IS NOT NULL
              AND r.status = 'success'
            ORDER BY s.created_at DESC
            LIMIT 1
            """,
            [asset_id],
        ).fetchone()
        if row is None or row[0] is None:
            return []
        value = json.loads(row[0]) if isinstance(row[0], str) else row[0]
        return list(value)

    def record_schema(
        self,
        asset_id: str,
        run_id: str,
        schema: list[dict[str, str]],
        observations: list[SchemaObservation],
    ) -> None:
        self._connection.execute(
            """
            INSERT INTO assquack.schemas (
                asset_id, run_id, schema_json, json_structure, created_at
            ) VALUES (?, ?, ?::JSON, NULL, current_timestamp)
            """,
            [asset_id, run_id, json.dumps(schema)],
        )

        if not observations:
            return
        self._connection.executemany(
            """
            INSERT INTO assquack.schema_observations VALUES (
                ?, ?, ?, ?, ?, ?, ?, current_timestamp, current_timestamp
            )
            """,
            [
                [
                    asset_id,
                    run_id,
                    item.path,
                    item.observed_type,
                    item.present_count,
                    item.null_count,
                    item.total_count,
                ]
                for item in observations
            ],
        )

    def mark_run_successful(
        self,
        run_id: str,
        *,
        duration_ms: int,
        row_count: int,
    ) -> None:
        self._connection.execute(
            """
            UPDATE assquack.runs
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
            UPDATE assquack.runs
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
        row_count: int,
    ) -> None:
        self._connection.execute(
            """
            INSERT INTO assquack.exports (
                export_id, run_id, asset_id, alias, uri, resolved_uri,
                format, role, row_count, options_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, 'parquet', 'default', ?, '{}', current_timestamp)
            """,
            [
                str(uuid4()),
                run_id,
                asset_id,
                alias,
                uri,
                resolved_uri,
                row_count,
            ],
        )
