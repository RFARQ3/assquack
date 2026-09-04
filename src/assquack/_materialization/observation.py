from __future__ import annotations

import json

import duckdb

from assquack._materialization.models import BatchSchemaEvidence, JsonStructure
from assquack._storage.models import SchemaObservation
from assquack._storage.sql import quote_table
from assquack._storage.tables import TableReference


def observe_staged_batch(
    connection: duckdb.DuckDBPyConnection,
    table: TableReference,
    run_id: str,
    batch_id: int,
    total_count: int,
) -> BatchSchemaEvidence:
    """Collect DuckDB's recursive JSON structure and path evidence for a batch."""

    raw_table = quote_table(table.schema_name, table.table_name)
    structure_row = connection.execute(
        f"""
        SELECT json_group_structure(CAST(_qa_payload AS JSON))
        FROM {raw_table}
        WHERE _qa_run_id = ? AND _qa_batch_id = ?
        """,
        [run_id, batch_id],
    ).fetchone()
    json_structure = _decode_structure(structure_row[0] if structure_row else None)

    rows = connection.execute(
        f"""
        WITH path_evidence AS (
            SELECT
                raw._qa_row_number,
                regexp_replace(
                    tree.fullkey,
                    '\\[[0-9]+\\]',
                    '[*]',
                    'g'
                ) AS normalized_path,
                tree.type AS observed_type
            FROM {raw_table} AS raw,
                 LATERAL json_tree(CAST(raw._qa_payload AS JSON)) AS tree
            WHERE raw._qa_run_id = ?
              AND raw._qa_batch_id = ?
              AND tree.fullkey <> '$'
        )
        SELECT
            normalized_path,
            observed_type,
            count(DISTINCT _qa_row_number) AS present_count,
            count(DISTINCT _qa_row_number)
                FILTER (WHERE observed_type = 'NULL') AS null_count
        FROM path_evidence
        GROUP BY normalized_path, observed_type
        ORDER BY normalized_path, observed_type
        """,
        [run_id, batch_id],
    ).fetchall()

    observations = [
        SchemaObservation(
            path=path,
            observed_type=observed_type,
            present_count=present_count,
            null_count=null_count,
            total_count=total_count,
        )
        for path, observed_type, present_count, null_count in rows
    ]
    return BatchSchemaEvidence(json_structure, observations, total_count)


def _decode_structure(value: object) -> JsonStructure:
    if value is None:
        return {}
    if isinstance(value, str):
        return json.loads(value)
    if isinstance(value, (dict, list)):
        return value
    return str(value)
