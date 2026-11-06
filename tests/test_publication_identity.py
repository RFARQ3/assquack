import asyncio

from assquack import AssquackConfig, asset
from database_helpers import inspect_database


def test_cache_requires_the_same_asset_to_still_occupy_the_table(tmp_path):
    calls = []

    @asset("main.shared")
    def rows(value: int):
        calls.append(value)
        return [{"value": value}]

    config = AssquackConfig(home=tmp_path)
    first = asyncio.run(rows(1, assquack_config=config))
    second = asyncio.run(rows(2, assquack_config=config))
    refreshed = asyncio.run(rows.cache_first()(1, assquack_config=config))

    assert calls == [1, 2, 1]
    assert not refreshed.cached
    assert refreshed.run_id not in (first.run_id, second.run_id)
    with inspect_database(config) as connection:
        assert connection.sql("SELECT value FROM main.shared").fetchall() == [(1,)]


def test_old_result_export_records_the_current_publication(tmp_path):
    @asset("main.shared")
    def rows(count: int):
        return [{"value": value} for value in range(count)]

    config = AssquackConfig(home=tmp_path)
    old = asyncio.run(rows(1, assquack_config=config))
    current = asyncio.run(rows(3, assquack_config=config))
    output = str(tmp_path / "current.parquet")
    old.export(output)

    with inspect_database(config) as connection:
        assert connection.execute(
            "SELECT run_id, asset_id, row_count FROM assquack_meta.exports"
        ).fetchall() == [(current.run_id, current.asset_id, 3)]
        assert connection.execute(
            "SELECT count(*) FROM read_parquet(?)", [output]
        ).fetchone() == (3,)
