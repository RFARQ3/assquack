import asyncio
import gc
import weakref

import duckdb
import pytest

import assquack
from assquack import AssquackConfig, asset
from database_helpers import inspect_database


@pytest.mark.parametrize(
    "sql,params",
    [
        (None, None),
        ("SELECT value FROM data /* ; -- comment */", None),
        ("SELECT value FROM data /* outer /* ; */ nested */;", None),
        ("SELECT value FROM data;;", None),
        ("SELECT value FROM data WHERE value = ?; -- ending", [3]),
        (
            "WITH x AS (SELECT * FROM data) SELECT value FROM x WHERE value = ?",
            [3],
        ),
    ],
)
def test_asset_alias_cannot_be_redirected_by_existing_table(tmp_path, sql, params):
    config = AssquackConfig(home=tmp_path)

    @asset("bronze.items", config=config)
    def items():
        return [{"value": 3}]

    async def run():
        await items()
        with inspect_database(config) as connection:
            connection.execute("CREATE TABLE main.data AS SELECT 999 AS value")
        rows = await items.query(sql, params)
        assert rows.project("value").fetchall() == [(3,)]

    asyncio.run(run())


@pytest.mark.parametrize("first", ["asset", "general", "result"])
def test_python_variable_chains_and_native_materialization(tmp_path, first):
    config = AssquackConfig(home=tmp_path)
    original_default = duckdb.default_connection()

    @asset("bronze.reports", config=config)
    def reports_asset():
        return [{"day": 1, "value": 3}, {"day": 2, "value": 7}]

    @asset("silver.summary", config=config)
    def output(rows):
        return rows

    async def run():
        published = await reports_asset()
        with assquack.use_config(config):
            if first == "asset":
                reports = await reports_asset.query("SELECT day, value FROM data")
            elif first == "result":
                reports = await published.query("SELECT day, value FROM data")
            else:
                reports = await assquack.query("SELECT day, value FROM bronze.reports")  # noqa: F841
            latest = duckdb.query("SELECT * FROM reports WHERE day = ?", params=[2])  # noqa: F841
            summary = duckdb.query("SELECT sum(value) AS total FROM latest")
        assert summary.fetchall() == [(7,)]
        assert (await output(summary)).row_count == 1
        assert (await output(summary)).row_count == 1
        assert summary.fetchall() == [(7,)]
        assert duckdb.default_connection() is original_default
        assert duckdb.query("SELECT 42").fetchall() == [(42,)]

    asyncio.run(run())


def test_same_database_assets_publish_then_join_in_one_execution(tmp_path):
    config = AssquackConfig(home=tmp_path)
    alternate = AssquackConfig(home=tmp_path, chunk_size=9, exports="./unused")

    @asset("bronze.first", config=config)
    def first():
        return [{"id": 1, "left_value": 3}]

    @asset("bronze.second", config=alternate)
    def second():
        return [{"id": 1, "right_value": 7}]

    @asset("silver.joined", config=config)
    async def joined():
        lhs = await first.query()  # noqa: F841
        rhs = await second.query()  # noqa: F841
        return duckdb.query(
            "SELECT l.id, left_value + right_value AS total "
            "FROM lhs AS l JOIN rhs AS r USING(id)"
        )

    result = asyncio.run(joined())
    assert result.row_count == 1
    assert asyncio.run(result.query("SELECT total FROM data")).fetchall() == [(10,)]


def test_standalone_assets_reuse_owner_and_namespace_is_local(tmp_path):
    config = AssquackConfig(home=tmp_path)

    @asset("bronze.items", config=config)
    def items():
        return [{"value": 3}]

    def helper(argument):
        return duckdb.query(
            "SELECT value + $extra AS total FROM argument", params={"extra": 1}
        )

    async def run():
        published = await items()
        first = await published.query("SELECT value FROM data")
        second = await published.query("SELECT value FROM data")  # noqa: F841
        joined = duckdb.query(
            "SELECT a.value + b.value FROM first a CROSS JOIN second b"
        )
        assert joined.fetchall() == [(6,)]
        assert helper(first).fetchall() == [(4,)]
        with pytest.raises(duckdb.CatalogException):
            duckdb.query("SELECT * FROM argument")
        # Error recovery occurs at the next root query, without caller cleanup.
        refreshed = await published.query("SELECT value FROM data")
        assert refreshed.fetchall() == [(3,)]
        assert duckdb.query(
            "SELECT count(*) FROM duckdb_views() "
            "WHERE view_name IN ('first', 'second', 'argument')"
        ).fetchone() == (0,)

    asyncio.run(run())


def test_root_refreshes_retained_lazy_results(tmp_path):
    config = AssquackConfig(home=tmp_path)

    @asset("bronze.items", config=config)
    def items(value):
        return [{"value": value}]

    async def run():
        await items(1)
        with assquack.use_config(config):
            retained = await assquack.query("SELECT value FROM bronze.items")
            await items(2)
            assert retained.fetchall() == [(2,)]
            latest = await assquack.query("SELECT value FROM bronze.items")
            assert latest.fetchall() == [(2,)]
            assert retained.fetchall() == [(2,)]

    asyncio.run(run())


def test_sequential_config_scopes_keep_same_task_database_owner(tmp_path):
    config = AssquackConfig(home=tmp_path)
    with inspect_database(config) as connection:
        connection.execute("CREATE TABLE items AS SELECT 3 AS value")

    async def run():
        with assquack.use_config(config):
            first = await assquack.query("SELECT value FROM items")  # noqa: F841
        with assquack.use_config(config):
            second = await assquack.query("SELECT value FROM items")  # noqa: F841
        assert duckdb.query(
            "SELECT a.value + b.value FROM first a CROSS JOIN second b"
        ).fetchall() == [(6,)]

    asyncio.run(run())


def test_concurrent_configs_and_child_task_owners(tmp_path):
    configs = [
        AssquackConfig(home=tmp_path / str(i), attachment_name=(tmp_path / str(i)).name)
        for i in range(2)
    ]

    @asset("bronze.items")
    def items(value):
        return [{"value": value}]

    async def read(config):
        with assquack.use_config(config):
            rows = await assquack.query(  # noqa: F841
                f'SELECT value FROM "{config.attachment_name}".bronze.items'
            )
            await asyncio.sleep(0)
            return duckdb.query("SELECT value + 10 FROM rows")

    async def run():
        for i, config in enumerate(configs):
            await items(i, assquack_config=config)
        with assquack.use_config(configs[0]):
            parent = await assquack.query('SELECT value FROM "0".bronze.items')
            first, second = await asyncio.gather(read(configs[0]), read(configs[1]))
            assert first.fetchall() == [(10,)]
            assert second.fetchall() == [(11,)]
            assert parent.fetchall() == [(0,)]
            assert duckdb.query("SELECT * FROM first").fetchall() == [(10,)]
            await assquack.query('SELECT value FROM "0".bronze.items')
            assert duckdb.query("SELECT * FROM second").fetchall() == [(11,)]
            assert (
                await assquack.query('SELECT value FROM "0".bronze.items')
            ).fetchall() == [(0,)]

    asyncio.run(run())


def test_scoped_binding_precedence_and_no_retained_caller_frame(tmp_path):
    config = AssquackConfig(home=tmp_path)

    @asset("bronze.items", config=config)
    def items():
        return [{"value": 3}]

    class Marker:
        pass

    async def run():
        await items()
        rows = await items.query("SELECT value FROM data")
        with assquack.use_config(config):
            unrelated = await assquack.query("SELECT 99 AS value")  # noqa: F841
        __assquack_query__ = rows  # noqa: F841
        assert duckdb.query("SELECT * FROM __assquack_query__").fetchall() == [(3,)]
        assert duckdb.query(
            "WITH rows AS (SELECT 8 AS value) SELECT * FROM rows"
        ).fetchall() == [(8,)]

        def bind():
            marker = Marker()
            reference = weakref.ref(marker)
            result = duckdb.query("SELECT value FROM rows")
            return result, reference

        derived, reference = bind()
        gc.collect()
        assert reference() is None
        assert derived.fetchall() == [(3,)]

    asyncio.run(run())
