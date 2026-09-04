from __future__ import annotations

import asyncio
from datetime import timedelta
from pathlib import Path

import duckdb

from assquack import AssquackConfig, ExportsConfig, asset


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
        orders.cache_first(expiration=timedelta(hours=1))(
            assquack_config=config
        )
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


def test_declared_export_requires_matching_manifest_row(tmp_path: Path) -> None:
    calls = 0

    @asset("cached/orders.parquet", table="main.exported_orders")
    async def orders() -> list[dict[str, int]]:
        nonlocal calls
        calls += 1
        return [{"id": calls}]

    config = AssquackConfig(
        home=tmp_path,
        exports=ExportsConfig(enabled=True),
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

    assert calls == 2
    assert refreshed.cached is False
    assert refreshed.run_id != first.run_id
