from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from pathlib import Path

import duckdb
import pytest

from assquack import AssquackConfig, IngestionConfig, asset
from assquack._materialization.fragments import (
    NDJSONFragmentWriter,
    fragment_run_directory,
)


@pytest.mark.parametrize("chunk_size", [1, 3, 20])
def test_materialization_uses_bounded_local_ndjson_fragments(
    tmp_path: Path, chunk_size: int
) -> None:
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
        chunk_size=chunk_size,
        ingestion=IngestionConfig(fragment_target_mb=0.000035),
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
    finally:
        connection.close()

    assert rows == [(1, None), (2, None), (3, "found"), (4, None), (5, None)]

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


@pytest.mark.parametrize("previous_success", [False, True])
def test_empty_run_has_only_metadata_columns(
    tmp_path: Path, previous_success: bool
) -> None:
    rows: list[dict[str, object]] = [{"id": 1, "late_field": "present"}]

    @asset(table="main.refreshable")
    async def refreshable() -> list[dict[str, object]]:
        return rows

    config = AssquackConfig(home=tmp_path)
    if previous_success:
        asyncio.run(refreshable(assquack_config=config))
    rows.clear()
    asyncio.run(refreshable(assquack_config=config))

    connection = duckdb.connect(str(config.resolved_database_path))
    try:
        schema = connection.execute("DESCRIBE main.refreshable").fetchall()
        row_count = connection.execute(
            "SELECT count(*) FROM main.refreshable"
        ).fetchone()
    finally:
        connection.close()

    assert [row[0] for row in schema] == ["_qa_run_id", "_qa_loaded_at"]
    assert row_count == (0,)


@pytest.mark.parametrize(
    ("target_bytes", "expected_counts"),
    [(26, [2, 2]), (27, [3, 1]), (1, [1, 1, 1, 1])],
)
@pytest.mark.parametrize("batch_size", [1, 3, 4])
def test_writer_counts_serialized_utf8_and_newline_bytes(
    tmp_path: Path, target_bytes: int, expected_counts: list[int], batch_size: int
) -> None:
    # Each record is 13 bytes: multibyte text, an escaped newline, and final LF.
    record = {"v": "é\n"}
    expected_line = b'{"v":"\xc3\xa9\\n"}\n'
    assert len(expected_line) == 13
    rows = [record] * 4
    directory = tmp_path / "fragments"
    writer = NDJSONFragmentWriter(directory, target_bytes=target_bytes)
    for start in range(0, len(rows), batch_size):
        assert writer.write_rows(rows[start : start + batch_size]) == len(
            rows[start : start + batch_size]
        )
    fragments = writer.finish()

    assert [fragment.row_count for fragment in fragments] == expected_counts
    assert [Path(fragment.path).read_bytes() for fragment in fragments] == [
        expected_line * count for count in expected_counts
    ]
    assert not list(directory.glob("*.partial"))
    assert (directory / "_COMPLETE").exists()
    assert writer.finish() == fragments
    with pytest.raises(RuntimeError, match="complete"):
        writer.write_rows([record])


def test_empty_capture_has_no_fragment(tmp_path: Path) -> None:
    directory = tmp_path / "fragments"
    writer = NDJSONFragmentWriter(directory, target_bytes=10)
    assert writer.write_rows([]) == 0
    assert writer.finish() == []
    assert sorted(path.name for path in directory.iterdir()) == ["_COMPLETE"]


@pytest.mark.parametrize("previous_success", [False, True])
def test_failed_extraction_after_rollover_preserves_publication(
    tmp_path: Path, previous_success: bool
) -> None:
    fail = False

    @asset(table="main.safe_fragments")
    async def source() -> AsyncIterator[list[dict[str, int]]]:
        yield [{"id": 2 if fail else 1}]
        if fail:
            raise RuntimeError("synthetic extraction failure")

    config = AssquackConfig(
        home=tmp_path,
        chunk_size=1,
        ingestion=IngestionConfig(fragment_target_mb=0.000001),
    )
    first = asyncio.run(source(assquack_config=config)) if previous_success else None
    fail = True
    with pytest.raises(RuntimeError, match="synthetic extraction failure"):
        asyncio.run(source(assquack_config=config))

    with duckdb.connect(str(config.resolved_database_path)) as connection:
        failed = connection.execute(
            "SELECT run_id FROM assquack_meta.runs WHERE status = 'failed'"
        ).fetchone()
        assert failed is not None
        if first is not None:
            assert connection.execute(
                "SELECT id, _qa_run_id FROM main.safe_fragments"
            ).fetchall() == [(1, first.run_id)]
        else:
            assert connection.execute(
                "SELECT count(*) FROM information_schema.tables "
                "WHERE table_schema = 'main' AND table_name = 'safe_fragments'"
            ).fetchone() == (0,)

    failed_directory = fragment_run_directory(
        config.resolved_fragment_directory, "source", failed[0]
    )
    assert len(list(failed_directory.glob("*.ndjson"))) == 1
    assert not (failed_directory / "_COMPLETE").exists()

    fail = False
    recovered = asyncio.run(source(assquack_config=config))
    assert recovered.run_id != failed[0]
    with duckdb.connect(str(config.resolved_database_path)) as connection:
        assert connection.execute(
            "SELECT id, _qa_run_id FROM main.safe_fragments"
        ).fetchall() == [(1, recovered.run_id)]


@pytest.mark.parametrize(
    ("target_mb", "expected_counts"),
    [(0.000031, [1, 1, 1, 1]), (0.0000311, [2, 2]), (0.000062, [2, 2])],
)
def test_per_call_megabyte_target_controls_actual_fragment_boundaries(
    tmp_path: Path, target_mb: float, expected_counts: list[int]
) -> None:
    @asset(table="main.byte_boundaries")
    async def source() -> list[dict[str, str]]:
        return [{"v": "x" * 22}] * 4

    config = AssquackConfig(
        home=tmp_path,
        ingestion=IngestionConfig(fragment_target_mb=target_mb),
    )
    result = asyncio.run(source(assquack_config=config))
    directory = fragment_run_directory(
        config.resolved_fragment_directory, "source", result.run_id
    )
    assert [path.stat().st_size for path in sorted(directory.glob("*.ndjson"))] == [
        31 * count for count in expected_counts
    ]
