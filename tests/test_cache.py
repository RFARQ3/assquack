from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path

import duckdb
import pytest

from assquack import AssquackConfig, FileSystemDestination, asset
from assquack._cache import _is_expired
from assquack._exports.writer.file import DuckDBFileWriter
from assquack._storage.models import SuccessfulRun


def test_cache_expires_at_ttl_boundary(monkeypatch: pytest.MonkeyPatch) -> None:
    now = datetime(2026, 10, 29, tzinfo=UTC)

    class FixedDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return now

    monkeypatch.setattr("assquack._cache.datetime", FixedDatetime)
    run = SuccessfulRun("run", now - timedelta(hours=2), 1)
    assert _is_expired(run, timedelta(hours=2))
    assert not _is_expired(run, timedelta(hours=2, microseconds=1))
    future_run = SuccessfulRun("future", now + timedelta(hours=1), 1)
    assert _is_expired(future_run, timedelta(0))


def test_decorator_cache_reuses_then_expires(tmp_path: Path) -> None:
    calls = 0

    @asset("main.decorator_orders", cache_expiration=timedelta(hours=2))
    def orders():
        nonlocal calls
        calls += 1
        return [{"id": calls}]

    config = AssquackConfig(home=tmp_path)
    first = asyncio.run(orders(assquack_config=config))
    cached = asyncio.run(orders(assquack_config=config))
    assert not first.cached and cached.cached and calls == 1
    assert cached.run_id == first.run_id

    with duckdb.connect(str(config.resolved_database_path)) as db:
        db.execute(
            "UPDATE assquack_meta.runs SET materialized_at = "
            "current_timestamp - INTERVAL '3 hours' WHERE run_id = ?",
            [first.run_id],
        )
    refreshed = asyncio.run(orders(assquack_config=config))
    assert not refreshed.cached and calls == 2
    assert refreshed.run_id != first.run_id


@pytest.mark.parametrize(
    "options", [{}, {"cache_expiration": None}, {"cache_expiration": timedelta(0)}]
)
def test_decorator_without_positive_ttl_rematerializes(tmp_path: Path, options) -> None:
    calls = 0

    @asset("main.fresh_orders", **options)
    def orders():
        nonlocal calls
        calls += 1
        return [{"id": calls}]

    config = AssquackConfig(home=tmp_path)
    first = asyncio.run(orders(assquack_config=config))
    second = asyncio.run(orders(assquack_config=config))
    assert calls == 2 and not second.cached
    assert first.run_id != second.run_id


def test_decorator_ttl_preserves_binding_and_fluent_overrides(tmp_path: Path) -> None:
    calls = 0

    @asset("main.{target}", cache_expiration=timedelta(hours=2))
    def orders(target: str):
        nonlocal calls
        calls += 1
        return [{"id": calls}]

    bound = orders.with_arguments(target="bound_orders")
    config = AssquackConfig(home=tmp_path)
    first = asyncio.run(bound(assquack_config=config))
    with duckdb.connect(str(config.resolved_database_path)) as db:
        db.execute(
            "UPDATE assquack_meta.runs SET materialized_at = "
            "current_timestamp - INTERVAL '90 minutes' WHERE run_id = ?",
            [first.run_id],
        )
    assert asyncio.run(bound(assquack_config=config)).cached
    unlimited = bound.cache_first()
    assert unlimited.cache_expiration is None
    assert asyncio.run(unlimited(assquack_config=config)).cached
    shorter = bound.cache_first(timedelta(hours=1))
    assert not asyncio.run(shorter(assquack_config=config)).cached
    assert calls == 2
    assert orders.cache_expiration == bound.cache_expiration == timedelta(hours=2)
    assert not orders.bound_arguments
    assert asyncio.run(bound(assquack_config=config)).cached
    assert not asyncio.run(
        bound.cache_first(timedelta(0))(assquack_config=config)
    ).cached


def test_decorator_rejects_negative_expiration() -> None:
    with pytest.raises(ValueError, match="Cache expiration cannot be negative"):
        asset("main.orders", cache_expiration=timedelta(seconds=-1))


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

    @asset(table="main.exported_orders", export="cached/orders.parquet")
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

    @asset(table="main.orders", export="orders.parquet|json|csv")
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

    @asset(table="main.orders", export="orders.parquet")
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
