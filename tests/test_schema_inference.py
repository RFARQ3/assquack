from __future__ import annotations

import duckdb

from assquack._materialization.fragments import NDJSONFragmentWriter
from assquack._materialization.inference import (
    InferenceState,
    resolve_reader_projections,
)
from assquack._materialization.models import NormalizedChunk
from assquack._materialization.observation import observe_fragment


def test_reader_json_columns_become_variant() -> None:
    [projection] = resolve_reader_projections([("value", "JSON")], [])

    assert projection.source_field == "$.value"
    assert projection.column_name == "value"
    assert projection.duckdb_type == "VARIANT"


def test_reader_schema_preserves_a_missing_historical_column() -> None:
    projections = resolve_reader_projections(
        [("id", "BIGINT")],
        [
            {
                "source_field": "$.late_field",
                "column_name": "late_field",
                "duckdb_type": "VARCHAR",
            }
        ],
    )

    assert [(item.column_name, item.duckdb_type) for item in projections] == [
        ("id", "BIGINT"),
        ("late_field", "VARCHAR"),
    ]


def test_reader_schema_widens_numeric_history() -> None:
    [projection] = resolve_reader_projections(
        [("amount", "DOUBLE")],
        [
            {
                "source_field": "$.amount",
                "column_name": "amount",
                "duckdb_type": "BIGINT",
            }
        ],
    )

    assert projection.duckdb_type == "DOUBLE"


def test_fragment_observation_comes_from_duckdb_json_functions(tmp_path) -> None:
    connection = duckdb.connect()
    chunk = NormalizedChunk(
        batch_id=0,
        rows=[
            {"Links": {"self": "/groups/1"}, "ids": [1, 2]},
            {"Links": {"self": "/groups/2"}, "ids": [3]},
        ],
    )
    writer = NDJSONFragmentWriter(tmp_path / "fragments", max_rows=10)
    writer.write_rows(chunk.rows)
    [fragment] = writer.finish()

    evidence = observe_fragment(connection, fragment)
    state = InferenceState()
    state.observe(evidence)

    assert evidence.json_structure == {
        "Links": {"self": "VARCHAR"},
        "ids": ["UBIGINT"],
    }
    assert any(
        item.path == "$.Links" and item.observed_type == "OBJECT"
        for item in state.observations()
    )
    assert any(
        item.path == "$.Links.self" and item.observed_type == "VARCHAR"
        for item in state.observations()
    )
