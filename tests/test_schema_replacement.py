from __future__ import annotations

import asyncio
import json
from pathlib import Path

import duckdb
import pytest

from assquack import AssquackConfig, IngestionConfig, SchemaConfig, asset
from assquack._materialization import pipeline
from assquack._materialization.models import ProjectionSpec
from assquack._materialization.schema import (
    CurrentRunSchemaStrategy,
    SchemaStrategy,
    replace_schema_strategy,
)
from assquack._storage.metadata import MetadataRepository


def test_replacement_uses_only_current_fragments_and_records_final_schema(
    tmp_path: Path,
) -> None:
    rows: list[dict[str, object]] = [
        {
            "id": 1,
            "amount": 2.5,
            "removed": "old",
            "shape": "old",
            "items": ["old"],
        }
    ]

    @asset(table="main.current_schema")
    async def source() -> list[dict[str, object]]:
        return rows

    config = AssquackConfig(
        home=tmp_path,
        ingestion=IngestionConfig(fragment_target_mb=0.000001),
    )
    asyncio.run(source(assquack_config=config))
    rows[:] = [
        {"id": 2, "amount": 3, "shape": {"name": "two"}, "items": [1]},
        {"id": 3, "amount": 4, "shape": {"extra": True}, "items": [2, 3]},
        {"id": 4, "amount": None, "late": "present"},
    ]
    result = asyncio.run(source(assquack_config=config))

    with duckdb.connect(str(config.resolved_database_path)) as connection:
        schema = dict(
            (row[0], row[1])
            for row in connection.execute("DESCRIBE main.current_schema").fetchall()
        )
        actual = connection.execute(
            """
            SELECT id, amount, shape.name, shape.extra, items, late
            FROM main.current_schema ORDER BY id
            """
        ).fetchall()
        observation_count = connection.execute(
            "SELECT count(*) FROM assquack_meta.schema_observations"
        ).fetchone()
        recorded = connection.execute(
            "SELECT schema_json FROM assquack_meta.schemas WHERE run_id = ?",
            [result.run_id],
        ).fetchone()

    assert set(schema) == {
        "_qa_run_id",
        "_qa_loaded_at",
        "id",
        "amount",
        "shape",
        "items",
        "late",
    }
    assert schema["amount"] == "BIGINT"
    assert schema["shape"].startswith("STRUCT(")
    assert schema["items"] == "BIGINT[]"
    assert actual == [
        (2, 3, "two", None, [1], None),
        (3, 4, None, True, [2, 3], None),
        (4, None, None, None, None, "present"),
    ]
    assert observation_count == (0,)
    assert recorded is not None
    recorded_schema = {
        item["column_name"]: item["duckdb_type"] for item in json.loads(recorded[0])
    }
    assert recorded_schema == {
        name: column_type
        for name, column_type in schema.items()
        if not name.startswith("_qa_")
    }


def test_per_asset_config_selects_the_strategy_used_for_publication(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    native_config = AssquackConfig(home=tmp_path)
    selected_config = AssquackConfig(
        home=tmp_path, schema=SchemaConfig(replace="current_run")
    )
    assert isinstance(
        replace_schema_strategy(native_config.schema), CurrentRunSchemaStrategy
    )

    class TextIdStrategy:
        def resolve(
            self, observed_schema: list[tuple[str, str]]
        ) -> list[ProjectionSpec]:
            assert observed_schema == [("id", "BIGINT")]
            return [ProjectionSpec("$.id", "id", "VARCHAR")]

    def select_strategy(config: SchemaConfig) -> SchemaStrategy:
        if config is selected_config.schema:
            return TextIdStrategy()
        return replace_schema_strategy(config)

    # Substitute a policy at the internal extension point without adding an
    # unsupported public setting. Its output must reach the published table.
    monkeypatch.setattr(pipeline, "replace_schema_strategy", select_strategy)

    @asset(table="main.native_asset")
    async def native_asset() -> list[dict[str, int]]:
        return [{"id": 1}]

    @asset(table="main.selected_asset")
    async def selected_asset() -> list[dict[str, int]]:
        return [{"id": 2}]

    asyncio.run(native_asset(assquack_config=native_config))
    asyncio.run(selected_asset(assquack_config=selected_config))
    with duckdb.connect(str(native_config.resolved_database_path)) as connection:
        assert connection.execute("SELECT id FROM main.native_asset").fetchall() == [
            (1,)
        ]
        assert connection.execute("SELECT id FROM main.selected_asset").fetchall() == [
            ("2",)
        ]


def test_fragment_partition_names_do_not_replace_source_fields(tmp_path: Path) -> None:
    @asset(table="main.source_partition_fields")
    async def source() -> list[dict[str, str]]:
        return [{"asset": "source-asset", "run_id": "source-run"}]

    config = AssquackConfig(home=tmp_path)
    asyncio.run(source(assquack_config=config))
    with duckdb.connect(str(config.resolved_database_path)) as connection:
        assert connection.execute(
            "SELECT asset, run_id FROM main.source_partition_fields"
        ).fetchall() == [("source-asset", "source-run")]


@pytest.mark.parametrize("previous_success", [False, True])
def test_failed_publication_rolls_back_table_and_success_status(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    previous_success: bool,
) -> None:
    rows: list[dict[str, object]] = [{"id": 1, "original": "published"}]

    @asset(table="main.safe_replacement")
    async def source() -> list[dict[str, object]]:
        return rows

    config = AssquackConfig(home=tmp_path)
    first = asyncio.run(source(assquack_config=config)) if previous_success else None
    rows[:] = [{"id": 2, "replacement": "unpublished"}]

    # Fail after the table swap, inside the publication transaction.
    def fail_success_write(*args: object, **kwargs: object) -> None:
        raise RuntimeError("synthetic metadata failure")

    with monkeypatch.context() as patch:
        patch.setattr(MetadataRepository, "mark_run_successful", fail_success_write)
        with pytest.raises(RuntimeError, match="synthetic metadata failure"):
            asyncio.run(source(assquack_config=config))

    with duckdb.connect(str(config.resolved_database_path)) as connection:
        statuses = connection.execute(
            "SELECT status, count(*) FROM assquack_meta.runs GROUP BY status"
        ).fetchall()
        assert dict(statuses) == (
            {"success": 1, "failed": 1} if previous_success else {"failed": 1}
        )
        if first is not None:
            assert connection.execute(
                "SELECT id, original, _qa_run_id FROM main.safe_replacement"
            ).fetchall() == [(1, "published", first.run_id)]
        else:
            assert connection.execute(
                """
                SELECT count(*) FROM information_schema.tables
                WHERE table_schema = 'main' AND table_name = 'safe_replacement'
                """
            ).fetchone() == (0,)

    recovered = asyncio.run(source(assquack_config=config))
    with duckdb.connect(str(config.resolved_database_path)) as connection:
        assert connection.execute(
            "SELECT id, replacement, _qa_run_id FROM main.safe_replacement"
        ).fetchall() == [(2, "unpublished", recovered.run_id)]
