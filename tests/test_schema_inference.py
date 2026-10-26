from __future__ import annotations

from pathlib import Path

import duckdb
import pytest

from assquack import SchemaConfig
from assquack._materialization.fragments import NDJSONFragmentWriter
from assquack._materialization.schema import (
    CurrentRunSchemaStrategy,
    replace_schema_strategy,
)


def test_strategy_infers_fragments_and_preserves_native_types(tmp_path: Path) -> None:
    writer = NDJSONFragmentWriter(tmp_path / "fragments", target_bytes=1)
    writer.write_rows(
        [
            {"value": {"nested": True}, "items": [1], "odd.name": 1},
            {"value": "text", "items": [2, 3], "late": True},
        ]
    )
    with duckdb.connect() as connection:
        strategy = replace_schema_strategy(SchemaConfig(), connection, writer.finish)
        assert isinstance(strategy, CurrentRunSchemaStrategy)
        projections = {item.column_name: item for item in strategy.resolve()}

    assert projections["value"].duckdb_type == "VARIANT"
    assert projections["items"].duckdb_type == "BIGINT[]"
    assert projections["late"].duckdb_type == "BOOLEAN"
    assert projections["odd.name"].source_field == '$."odd.name"'
    assert (tmp_path / "fragments" / "_COMPLETE").exists()


def test_strategy_instances_keep_their_own_run_resources(tmp_path: Path) -> None:
    first = NDJSONFragmentWriter(tmp_path / "first", target_bytes=1)
    second = NDJSONFragmentWriter(tmp_path / "second", target_bytes=1)
    first.write_rows([{"first": 1}])
    second.write_rows([{"second": "two"}])
    with duckdb.connect() as first_connection, duckdb.connect() as second_connection:
        first_strategy = replace_schema_strategy(
            SchemaConfig(), first_connection, first.finish
        )
        second_strategy = replace_schema_strategy(
            SchemaConfig(), second_connection, second.finish
        )
        assert [item.column_name for item in second_strategy.resolve()] == ["second"]
        assert [item.column_name for item in first_strategy.resolve()] == ["first"]
        assert [item.column_name for item in second_strategy.resolve()] == ["second"]


def test_empty_run_resolves_without_historical_schema(tmp_path: Path) -> None:
    writer = NDJSONFragmentWriter(tmp_path / "empty", target_bytes=1)
    with duckdb.connect() as connection:
        strategy = replace_schema_strategy(SchemaConfig(), connection, writer.finish)
        assert strategy.resolve() == []


def test_unknown_internal_strategy_is_rejected(tmp_path: Path) -> None:
    writer = NDJSONFragmentWriter(tmp_path / "unknown", target_bytes=1)
    config = SchemaConfig.model_construct(replace="unsupported")
    with (
        duckdb.connect() as connection,
        pytest.raises(ValueError, match="Unsupported replacement schema strategy"),
    ):
        replace_schema_strategy(config, connection, writer.finish)


def test_candidate_failed_cast_becomes_null(tmp_path: Path) -> None:
    from assquack._materialization.models import ProjectionSpec
    from assquack._materialization.staging import create_shaped_staging
    from assquack._storage.tables import TableReference

    writer = NDJSONFragmentWriter(tmp_path / "casts", target_bytes=1)
    writer.write_rows([{"value": "12"}, {"value": "invalid"}])
    with duckdb.connect() as connection:
        create_shaped_staging(
            connection,
            TableReference("main", "candidate"),
            writer.finish(),
            [ProjectionSpec("$.value", "value", "BIGINT")],
            "synthetic-run",
        )
        assert connection.execute(
            "SELECT value FROM candidate ORDER BY value NULLS LAST"
        ).fetchall() == [(12,), (None,)]
