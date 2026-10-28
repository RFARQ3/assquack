from __future__ import annotations

import asyncio
from datetime import timedelta
from pathlib import Path

import duckdb
import pytest

from assquack import AssquackConfig, FileSystemDestination, asset
from assquack._exports.writer.file import DuckDBFileWriter


def test_empty_cache_materializes(tmp_path: Path) -> None:
    calls = 0

    @asset(table="main.uncached_orders")
    async def orders() -> list[dict[str, int]]:
        nonlocal calls
        calls += 1
        return [{"id": calls}]

    result = asyncio.run(
        orders.cache_first()(assquack_config=AssquackConfig(home=tmp_path))
    )

    assert calls == 1
    assert result.cached is False


def test_cache_hit_skips_asset_function(tmp_path: Path) -> None:
    calls = 0

    @asset(table="main.cached_orders")
    async def orders() -> list[dict[str, int]]:
        nonlocal calls
        calls += 1
        return [{"id": calls}]

    config = AssquackConfig(home=tmp_path)

    first = asyncio.run(orders(assquack_config=config))
    second = asyncio.run(orders.cache_first()(assquack_config=config))

    assert calls == 1
    assert second.cached is True
    assert second.run_id == first.run_id


def test_expired_cache_rematerializes(tmp_path: Path) -> None:
    calls = 0

    @asset(table="main.stale_orders")
    async def orders() -> list[dict[str, int]]:
        nonlocal calls
        calls += 1
        return [{"id": calls}]

    config = AssquackConfig(home=tmp_path)
    first = asyncio.run(orders(assquack_config=config))

    connection = duckdb.connect(str(config.resolved_database_path))
    try:
        connection.execute(
            """
            UPDATE assquack_meta.runs
            SET materialized_at = current_timestamp - INTERVAL '2 hours'
            WHERE run_id = ?
            """,
            [first.run_id],
        )
    finally:
        connection.close()

    second = asyncio.run(
        orders.cache_first(expiration=timedelta(hours=1))(assquack_config=config)
    )

    assert calls == 2
    assert second.cached is False
    assert second.run_id != first.run_id


def test_missing_current_table_rematerializes(tmp_path: Path) -> None:
    calls = 0

    @asset(table="main.missing_orders")
    async def orders() -> list[dict[str, int]]:
        nonlocal calls
        calls += 1
        return [{"id": calls}]

    config = AssquackConfig(home=tmp_path)
    first = asyncio.run(orders(assquack_config=config))

    connection = duckdb.connect(str(config.resolved_database_path))
    try:
        connection.execute("DROP TABLE main.missing_orders")
    finally:
        connection.close()

    second = asyncio.run(orders.cache_first()(assquack_config=config))

    assert calls == 2
    assert second.cached is False
    assert second.run_id != first.run_id


def test_missing_export_manifest_reuses_materialization(tmp_path: Path) -> None:
    calls = 0

    @asset("cached/orders.parquet", table="main.exported_orders")
    async def orders() -> list[dict[str, int]]:
        nonlocal calls
        calls += 1
        return [{"id": calls}]

    config = AssquackConfig(
        home=tmp_path,
        exports=FileSystemDestination(base_uri=str(tmp_path / "exports")),
    )
    first = asyncio.run(orders(assquack_config=config))
    cached = asyncio.run(orders.cache_first()(assquack_config=config))

    assert calls == 1
    assert cached.cached is True

    connection = duckdb.connect(str(config.resolved_database_path))
    try:
        connection.execute(
            "DELETE FROM assquack_meta.exports WHERE run_id = ?",
            [first.run_id],
        )
    finally:
        connection.close()

    refreshed = asyncio.run(orders.cache_first()(assquack_config=config))

    assert calls == 1
    assert refreshed.cached is True
    assert refreshed.run_id == first.run_id


@pytest.mark.parametrize("failed_format", ["parquet", "json"])
def test_export_retries_preserve_materialization_and_partial_successes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failed_format: str
) -> None:
    calls = 0
    writes: list[str] = []
    failing = True
    original_write = DuckDBFileWriter.write

    def write(self, connection, source, target, uri):
        writes.append(target.format)
        if failing and target.format == failed_format:
            raise ValueError("synthetic export failure")
        original_write(self, connection, source, target, uri)

    monkeypatch.setattr(DuckDBFileWriter, "write", write)

    @asset("orders.parquet|json|csv", table="main.orders")
    def orders():
        nonlocal calls
        calls += 1
        return [{"id": calls}]

    config = AssquackConfig(
        home=tmp_path,
        exports=FileSystemDestination(base_uri=str(tmp_path / "exports")),
    )
    cached_orders = orders.cache_first(expiration=timedelta(hours=1))
    with pytest.raises(ValueError, match="synthetic export failure"):
        asyncio.run(cached_orders(assquack_config=config))
    with duckdb.connect(str(config.resolved_database_path)) as db:
        # Age the original publication so retries cannot silently renew its TTL.
        db.execute(
            "UPDATE assquack_meta.runs SET materialized_at = "
            "current_timestamp - INTERVAL '30 minutes'"
        )
        original_run = db.execute(
            "SELECT to_json(r) FROM assquack_meta.runs r"
        ).fetchall()
        original_exports = db.execute(
            "SELECT to_json(e) FROM assquack_meta.exports e"
        ).fetchall()

    writes.clear()
    with pytest.raises(ValueError, match="synthetic export failure"):
        asyncio.run(cached_orders(assquack_config=config))
    assert writes == [failed_format]
    assert calls == 1

    failing = False
    writes.clear()
    result = asyncio.run(cached_orders(assquack_config=config))
    assert writes == (
        ["parquet", "json", "csv"] if failed_format == "parquet" else ["json", "csv"]
    )
    assert result.cached and calls == 1
    with duckdb.connect(str(config.resolved_database_path)) as db:
        assert (
            db.execute("SELECT to_json(r) FROM assquack_meta.runs r").fetchall()
            == original_run
        )
        exports = db.execute(
            "SELECT to_json(e) FROM assquack_meta.exports e"
        ).fetchall()
        assert len(exports) == 3
        assert all(row in exports for row in original_exports)
        assert db.execute(
            "SELECT DISTINCT run_id FROM assquack_meta.exports"
        ).fetchall() == [(result.run_id,)]
        assert db.execute("SELECT id FROM main.orders").fetchall() == [(1,)]

    writes.clear()
    assert asyncio.run(cached_orders(assquack_config=config)).cached
    assert writes == []
    # A shorter TTL expires the original publication even just after export retry.
    refreshed = asyncio.run(
        orders.cache_first(expiration=timedelta(minutes=10))(assquack_config=config)
    )
    assert calls == 2 and not refreshed.cached
    assert refreshed.run_id != result.run_id
    assert writes == ["parquet", "json", "csv"]

    writes.clear()
    uncached = asyncio.run(orders(assquack_config=config))
    assert calls == 3 and not uncached.cached
    assert uncached.run_id != refreshed.run_id
    assert writes == ["parquet", "json", "csv"]


def test_changed_destination_fingerprint_reexports_same_table(tmp_path: Path) -> None:
    calls = 0

    @asset("orders.parquet", table="main.orders")
    def orders():
        nonlocal calls
        calls += 1
        return [{"id": calls}]

    config = AssquackConfig(
        home=tmp_path,
        exports=FileSystemDestination(base_uri=str(tmp_path / "exports")),
    )
    first = asyncio.run(orders(assquack_config=config))
    # Explicit local auth changes the fingerprint while retaining the same path.
    changed = config.model_copy(
        update={
            "exports": [
                FileSystemDestination(base_uri=str(tmp_path / "exports"), auth="none")
            ]
        }
    )
    result = asyncio.run(orders.cache_first()(assquack_config=changed))
    assert result.cached and result.run_id == first.run_id and calls == 1
    with duckdb.connect(str(config.resolved_database_path)) as db:
        assert db.execute(
            "SELECT count(*), count(DISTINCT run_id), count(DISTINCT resolved_uri), "
            "count(DISTINCT destination_fingerprint) FROM assquack_meta.exports"
        ).fetchone() == (2, 1, 1, 2)
    assert asyncio.run(orders.cache_first()(assquack_config=changed)).cached
    with duckdb.connect(str(config.resolved_database_path)) as db:
        assert db.execute("SELECT count(*) FROM assquack_meta.exports").fetchone() == (
            2,
        )
