from __future__ import annotations

import asyncio
import json
from pathlib import Path

import duckdb

from assquack import AssquackConfig, IngestionConfig, asset
from assquack._materialization.fragments import fragment_run_directory


def test_materialization_uses_bounded_local_ndjson_fragments(tmp_path: Path) -> None:
    @asset(table="main.fragmented_orders")
    async def orders() -> list[dict[str, object]]:
        return [
            {"id": 1, "details": {"name": "one"}},
            {"id": 2, "details": {"name": "two"}},
            {"id": 3, "late_field": "found"},
            {"id": 4},
            {"id": 5},
        ]

    config = AssquackConfig(
        home=tmp_path,
        chunk_size=3,
        ingestion=IngestionConfig(fragment_max_rows=2),
    )

    result = asyncio.run(orders(assquack_config=config))

    run_directory = (
        config.resolved_fragment_directory / "asset=orders" / f"run_id={result.run_id}"
    )
    fragments = sorted(run_directory.glob("*.ndjson"))
    assert [path.name for path in fragments] == [
        "fragment=000000.ndjson",
        "fragment=000001.ndjson",
        "fragment=000002.ndjson",
    ]
    assert [
        len(path.read_text(encoding="utf-8").splitlines()) for path in fragments
    ] == [
        2,
        2,
        1,
    ]
    assert (run_directory / "_COMPLETE").exists()
    assert not list(run_directory.glob("*.partial"))

    connection = duckdb.connect(str(config.resolved_database_path))
    try:
        rows = connection.execute(
            "SELECT id, late_field FROM main.fragmented_orders ORDER BY id"
        ).fetchall()
        observations = connection.execute(
            """
            SELECT path, sum(present_count), max(total_count)
            FROM assquack_meta.schema_observations
            WHERE run_id = ? AND path = '$.late_field'
            GROUP BY path
            """,
            [result.run_id],
        ).fetchall()
    finally:
        connection.close()

    assert rows == [(1, None), (2, None), (3, "found"), (4, None), (5, None)]
    assert observations == [("$.late_field", 1, 5)]

    decoded = [
        json.loads(line)
        for fragment in fragments
        for line in fragment.read_text(encoding="utf-8").splitlines()
    ]
    assert [row["id"] for row in decoded] == [1, 2, 3, 4, 5]


def test_fragment_path_uses_safe_hive_partition_values(tmp_path: Path) -> None:
    directory = fragment_run_directory(tmp_path, "sales/orders:daily", "run-123")

    assert directory == tmp_path / "asset=sales_orders_daily" / "run_id=run-123"


def test_reader_preserves_heterogeneous_values_as_variant(tmp_path: Path) -> None:
    @asset(table="main.mixed_values")
    async def values() -> list[dict[str, object]]:
        return [
            {"id": 1, "value": {"nested": True}},
            {"id": 2, "value": "plain"},
        ]

    config = AssquackConfig(home=tmp_path)
    asyncio.run(values(assquack_config=config))

    connection = duckdb.connect(str(config.resolved_database_path))
    try:
        schema = connection.execute(
            "DESCRIBE SELECT value FROM main.mixed_values"
        ).fetchone()
        value_types = connection.execute(
            "SELECT variant_typeof(value) FROM main.mixed_values ORDER BY id"
        ).fetchall()
    finally:
        connection.close()

    assert schema is not None and schema[1] == "VARIANT"
    assert value_types[0][0].startswith("OBJECT(")
    assert value_types[1] == ("VARCHAR",)


def test_empty_refresh_preserves_the_latest_schema(tmp_path: Path) -> None:
    rows: list[dict[str, object]] = [{"id": 1, "late_field": "present"}]

    @asset(table="main.refreshable")
    async def refreshable() -> list[dict[str, object]]:
        return rows

    config = AssquackConfig(home=tmp_path)
    asyncio.run(refreshable(assquack_config=config))
    rows.clear()
    asyncio.run(refreshable(assquack_config=config))

    connection = duckdb.connect(str(config.resolved_database_path))
    try:
        schema = connection.execute(
            "DESCRIBE SELECT id, late_field FROM main.refreshable"
        ).fetchall()
        row_count = connection.execute(
            "SELECT count(*) FROM main.refreshable"
        ).fetchone()
    finally:
        connection.close()

    assert [row[0] for row in schema] == ["id", "late_field"]
    assert row_count == (0,)
