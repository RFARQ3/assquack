from __future__ import annotations

import json
from typing import cast

import duckdb

from assquack._materialization.json_reader import read_json_objects_sql
from assquack._materialization.models import (
    BatchSchemaEvidence,
    FragmentFile,
    JsonStructure,
)
from assquack._storage.models import SchemaObservation


def observe_fragment(
    connection: duckdb.DuckDBPyConnection,
    fragment: FragmentFile,
) -> BatchSchemaEvidence:
    """Collect recursive schema evidence from one bounded NDJSON fragment."""

    source = read_json_objects_sql(fragment.path)
    structure_row = connection.execute(
        f"SELECT json_group_structure(json) FROM {source}"
    ).fetchone()
    json_structure = _decode_structure(structure_row[0] if structure_row else None)

    rows = connection.execute(
        f"""
        WITH source_rows AS (
            SELECT row_number() OVER () AS row_number, json
            FROM {source}
        ),
        path_evidence AS (
            SELECT
                source_rows.row_number,
                regexp_replace(tree.fullkey, '\\[[0-9]+\\]', '[*]', 'g')
                    AS normalized_path,
                tree.type AS observed_type
            FROM source_rows,
                 LATERAL json_tree(source_rows.json) AS tree
            WHERE tree.fullkey <> '$'
        )
        SELECT
            normalized_path,
            observed_type,
            count(DISTINCT row_number) AS present_count,
            count(DISTINCT row_number)
                FILTER (WHERE observed_type = 'NULL') AS null_count
        FROM path_evidence
        GROUP BY normalized_path, observed_type
        ORDER BY normalized_path, observed_type
        """
    ).fetchall()

    observations = [
        SchemaObservation(
            path=path,
            observed_type=observed_type,
            present_count=present_count,
            null_count=null_count,
            total_count=fragment.row_count,
        )
        for path, observed_type, present_count, null_count in rows
    ]
    return BatchSchemaEvidence(json_structure, observations, fragment.row_count)


def _decode_structure(value: object) -> JsonStructure:
    if value is None:
        return {}
    if isinstance(value, str):
        return cast(JsonStructure, json.loads(value))
    if isinstance(value, (dict, list)):
        return cast(JsonStructure, value)
    return str(value)
