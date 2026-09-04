from __future__ import annotations

import duckdb

from assquack._materialization.inference import InferenceState, infer_projections
from assquack._materialization.models import (
    BatchSchemaEvidence,
    NormalizedChunk,
)
from assquack._materialization.observation import observe_staged_batch
from assquack._materialization.projection import projection_expression
from assquack._materialization.staging import create_raw_staging, stage_chunk
from assquack._storage.models import SchemaObservation
from assquack._storage.tables import TableReference


def test_stable_nested_object_becomes_one_struct_projection() -> None:
    state = InferenceState()
    state.observe(
        BatchSchemaEvidence(
            json_structure={
                "Links": {
                    "self": "VARCHAR",
                    "related": "VARCHAR",
                }
            },
            observations=[
                _observation("$.Links", "OBJECT"),
                _observation("$.Links.self", "VARCHAR"),
                _observation("$.Links.related", "VARCHAR"),
            ],
            total_count=1,
        )
    )

    [projection] = infer_projections(state, [])

    assert projection.source_field == "$.Links"
    assert projection.column_name == "Links"
    assert projection.duckdb_type == 'STRUCT("related" VARCHAR, "self" VARCHAR)'
    assert projection_expression(projection) == (
        "try_cast(variant_extract(_qa_payload, '$.Links') "
        'AS STRUCT("related" VARCHAR, "self" VARCHAR)) AS "Links"'
    )


def test_stable_array_uses_duckdb_list_type() -> None:
    state = InferenceState()
    state.observe(
        BatchSchemaEvidence(
            json_structure={"ids": ["BIGINT"]},
            observations=[
                _observation("$.ids", "ARRAY"),
                _observation("$.ids[*]", "BIGINT"),
            ],
            total_count=1,
        )
    )

    [projection] = infer_projections(state, [])

    assert projection.duckdb_type == "BIGINT[]"


def test_conflicting_nested_shape_remains_variant() -> None:
    state = InferenceState()
    state.observe(
        BatchSchemaEvidence(
            json_structure={"value": {"id": "BIGINT"}},
            observations=[
                _observation("$.value", "OBJECT"),
                _observation("$.value.id", "BIGINT"),
            ],
            total_count=1,
        )
    )
    state.observe(
        BatchSchemaEvidence(
            json_structure={"value": "VARCHAR"},
            observations=[_observation("$.value", "VARCHAR")],
            total_count=1,
        )
    )

    [projection] = infer_projections(state, [])

    assert projection.duckdb_type == "VARIANT"


def test_object_with_inconsistent_keys_remains_variant() -> None:
    state = InferenceState()
    state.observe(
        BatchSchemaEvidence(
            json_structure={
                "attributes": {
                    "customer_a": "VARCHAR",
                    "customer_b": "VARCHAR",
                }
            },
            observations=[
                SchemaObservation("$.attributes", "OBJECT", 2, 0, 2),
                SchemaObservation("$.attributes.customer_a", "VARCHAR", 1, 0, 2),
                SchemaObservation("$.attributes.customer_b", "VARCHAR", 1, 0, 2),
            ],
            total_count=2,
        )
    )

    [projection] = infer_projections(state, [])

    assert projection.duckdb_type == "VARIANT"


def test_staged_batch_observation_comes_from_duckdb_json_functions() -> None:
    connection = duckdb.connect()
    table = TableReference("main", "raw_test")
    create_raw_staging(connection, table)
    chunk = NormalizedChunk(
        batch_id=0,
        rows=[
            {"Links": {"self": "/groups/1"}, "ids": [1, 2]},
            {"Links": {"self": "/groups/2"}, "ids": [3]},
        ],
    )
    stage_chunk(connection, table, "run-1", chunk, 0)

    evidence = observe_staged_batch(connection, table, "run-1", 0, 2)

    assert evidence.json_structure == {
        "Links": {"self": "VARCHAR"},
        "ids": ["UBIGINT"],
    }
    assert any(
        item.path == "$.Links" and item.observed_type == "OBJECT"
        for item in evidence.observations
    )
    assert any(
        item.path == "$.Links.self" and item.observed_type == "VARCHAR"
        for item in evidence.observations
    )


def _observation(path: str, observed_type: str) -> SchemaObservation:
    return SchemaObservation(
        path=path,
        observed_type=observed_type,
        present_count=1,
        null_count=int(observed_type == "NULL"),
        total_count=1,
    )
