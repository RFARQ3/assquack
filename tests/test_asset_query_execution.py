import asyncio
from datetime import timedelta

import duckdb
import pytest

from assquack import AssquackConfig, asset
from database_helpers import inspect_database


def test_first_query_runs_bound_asset_with_override_and_native_chaining(tmp_path):
    original = AssquackConfig(home=tmp_path / "original", attachment_name="original")
    override = AssquackConfig(home=tmp_path / "override", attachment_name="override")
    calls = []

    @asset("main.{target}", config=original)
    def source(target, value):
        calls.append((target, value))
        return [{"id": value}]

    bound = source.with_arguments(target="rows", value=7)
    rows = asyncio.run(
        bound.query("SELECT id WHERE id = ?", [7], assquack_config=override)
    )
    assert isinstance(rows, duckdb.DuckDBPyRelation)
    assert duckdb.sql("SELECT id + 1 FROM rows").fetchall() == [(8,)]
    assert calls == [("rows", 7)]
    assert not original.resolved_database_path.exists()
    assert bound.exists(assquack_config=override)


@pytest.mark.parametrize("policy", ["unlimited", "ttl", "none", "zero"])
def test_query_uses_normal_cache_policy(tmp_path, policy):
    config = AssquackConfig(home=tmp_path)
    calls = 0

    @asset("main.rows", config=config)
    def source():
        nonlocal calls
        calls += 1
        return [{"id": calls}]

    if policy == "unlimited":
        source = source.cache_first()
    elif policy == "ttl":
        source = source.with_options(cache_expiration=timedelta(hours=1))
    elif policy == "zero":
        source = source.with_options(cache_expiration=timedelta(0))
    assert asyncio.run(source.query()).project("id").fetchall() == [(1,)]
    expected = 1 if policy in {"unlimited", "ttl"} else 2
    assert asyncio.run(source.query()).project("id").fetchall() == [(expected,)]
    assert calls == expected

    if policy == "ttl":
        with inspect_database(config) as connection:
            connection.execute(
                "UPDATE assquack_meta.runs SET materialized_at = "
                "current_timestamp - INTERVAL '2 hours'"
            )
        assert asyncio.run(source.query()).project("id").fetchall() == [(2,)]
        assert calls == 2


def test_snapshot_query_materializes_missing_base_then_publishes(tmp_path):
    config = AssquackConfig(home=tmp_path)
    calls = 0

    @asset("main.base", config=config)
    def base():
        nonlocal calls
        calls += 1
        return [{"id": 1}, {"id": 2}]

    @asset("main.snapshot", config=config)
    async def snapshot():
        return await base.query("SELECT id WHERE id = ?", [2])

    assert not config.resolved_database_path.exists()
    result = asyncio.run(snapshot())
    assert asyncio.run(result.query("SELECT id")).fetchall() == [(2,)]
    assert calls == 1
    with inspect_database(config) as connection:
        assert connection.sql("SELECT id FROM main.base ORDER BY id").fetchall() == [
            (1,),
            (2,),
        ]
        assert connection.sql(
            "SELECT count(*) FROM assquack_meta.runs WHERE status='success'"
        ).fetchone() == (2,)


def test_source_failure_propagates_and_read_queries_do_not_extract(tmp_path):
    config = AssquackConfig(home=tmp_path)
    calls = 0

    @asset("main.rows", config=config)
    def source():
        nonlocal calls
        calls += 1
        if calls > 1:
            raise RuntimeError("source failed")
        return [{"id": 7}]

    published = asyncio.run(source())
    with pytest.raises(RuntimeError, match="source failed"):
        asyncio.run(source.query())
    assert asyncio.run(published.query("SELECT id")).fetchall() == [(7,)]
    assert duckdb.query("SELECT id FROM main.rows").fetchall() == [(7,)]
    assert calls == 2
