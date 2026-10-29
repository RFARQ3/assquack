import asyncio
from datetime import timedelta
from pathlib import Path

import pytest

from assquack import AssquackConfig, asset


def test_option_copies_preserve_templates_arguments_and_resets(tmp_path: Path):
    config = AssquackConfig(home=tmp_path)

    @asset(
        "bronze.{endpoint}",
        name="source_{endpoint}",
        export={"uri": "{endpoint}.parquet", "format": "parquet"},
        cache_expiration=timedelta(hours=2),
        config=config,
    )
    def source(endpoint: str):
        raise AssertionError("Configuration must not execute the function")

    bound = source.with_arguments(endpoint="orders")
    changed = bound.with_options(table="silver.{endpoint}", name="copy_{endpoint}")
    rebound = changed.cache_first().with_options().with_arguments(endpoint="items")
    assert (rebound.table, rebound.name) == ("silver.items", "copy_items")
    assert rebound.export == {"uri": "items.parquet", "format": "parquet"}
    assert rebound.config is config and rebound.prefer_cache
    assert rebound.cache_expiration is None
    assert bound.table == "bronze.orders" and bound.name == "source_orders"
    assert bound.cache_expiration == timedelta(hours=2)
    assert source.bound_arguments == {}
    opposite = source.with_options(table="silver.{endpoint}", name="copy_{endpoint}")
    assert opposite.with_arguments(endpoint="orders") == changed
    reset = rebound.with_options(
        export=None, name=None, config=None, cache_expiration=None
    )
    assert reset.export is None and reset.config is None
    assert reset.name == "source" and not reset.prefer_cache
    assert reset.fn is source.fn and reset.bound_arguments == {"endpoint": "items"}


@pytest.mark.parametrize(
    "options",
    [
        {"table": None},
        {"table": ""},
        {"table": " "},
        {"mode": "append"},
        {"cache_expiration": timedelta(seconds=-1)},
    ],
)
def test_option_copies_validate_like_decorator(options):
    @asset("main.rows")
    def rows():
        return []

    with pytest.raises((TypeError, ValueError)):
        rows.with_options(**options)


def test_config_precedence_execution_query_exists_and_clear(
    tmp_path: Path, monkeypatch
):
    attached = AssquackConfig(home=tmp_path / "attached")
    override = AssquackConfig(home=tmp_path / "override")
    monkeypatch.setenv("ASSQUACK_HOME", str(tmp_path / "default"))
    calls = 0

    @asset("main.rows", config=attached, cache_expiration=timedelta(hours=1))
    def rows():
        nonlocal calls
        calls += 1
        return [{"value": calls}]

    assert not rows.exists() and not rows.exists(assquack_config=override)
    asyncio.run(rows())
    assert rows.exists() and not rows.exists(assquack_config=override)
    asyncio.run(rows(assquack_config=override))
    assert asyncio.run(rows.query("SELECT value FROM data")).fetchall() == [(1,)]
    assert asyncio.run(
        rows.query("SELECT value FROM data", assquack_config=override)
    ).fetchall() == [(2,)]
    assert asyncio.run(rows()).cached
    cleared = rows.with_options(config=None)
    assert not cleared.exists()
    asyncio.run(cleared())
    assert asyncio.run(cleared.query("SELECT value FROM data")).fetchall() == [(3,)]
    assert rows.config is attached
    assert asyncio.run(rows.with_options(config=override)()).cached
    assert calls == 3


@pytest.mark.parametrize("expiration", [None, timedelta(0), timedelta(hours=1)])
def test_option_ttl_overrides_unlimited_cache(tmp_path: Path, expiration):
    @asset("main.rows", config=AssquackConfig(home=tmp_path))
    def rows():
        return [{"id": 1}]

    asyncio.run(rows())
    copied = rows.cache_first().with_options(cache_expiration=expiration)
    result = asyncio.run(copied())
    assert result.cached == (expiration == timedelta(hours=1))


def test_nested_returned_assets_use_parent_override(tmp_path: Path):
    child_config = AssquackConfig(home=tmp_path / "child")
    parent_config = AssquackConfig(home=tmp_path / "parent")
    override = AssquackConfig(home=tmp_path / "override")

    @asset("main.child", config=child_config)
    def child():
        return [{"id": 7}]

    @asset("main.parent", config=parent_config)
    def parent():
        return child

    result = asyncio.run(parent(assquack_config=override))
    assert asyncio.run(result.query("SELECT id FROM data")).fetchall() == [(7,)]
    assert child.exists(assquack_config=override)
    assert not child_config.resolved_database_path.exists()
    assert not parent_config.resolved_database_path.exists()


def test_direct_nested_calls_use_their_attached_shared_config(tmp_path: Path):
    config = AssquackConfig(home=tmp_path)

    @asset("main.child", config=config)
    def child():
        return [{"id": 9}]

    @asset("main.parent", config=config)
    async def parent():
        return await child.cache_first()()

    result = asyncio.run(parent())
    assert asyncio.run(result.query("SELECT id FROM data")).fetchall() == [(9,)]
    assert child.exists() and parent.exists()
