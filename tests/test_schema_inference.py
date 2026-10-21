from __future__ import annotations

import duckdb

from assquack._materialization.fragments import NDJSONFragmentWriter
from assquack._materialization.inference import (
    InferenceState,
)
from assquack._materialization.models import NormalizedChunk
from assquack._materialization.observation import observe_fragment
from assquack._materialization.schema import CurrentRunSchemaStrategy


def test_reader_json_columns_become_variant() -> None:
    [projection] = CurrentRunSchemaStrategy().resolve([("value", "JSON")])

    assert projection.source_field == "$.value"
    assert projection.column_name == "value"
    assert projection.duckdb_type == "VARIANT"


def test_fragment_observation_comes_from_duckdb_json_functions(tmp_path) -> None:
    connection = duckdb.connect()
    chunk = NormalizedChunk(
        batch_id=0,
        rows=[
            {"Links": {"self": "/groups/1"}, "ids": [1, 2]},
            {"Links": {"self": "/groups/2"}, "ids": [3]},
        ],
    )
    writer = NDJSONFragmentWriter(tmp_path / "fragments", target_bytes=1_000_000)
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
