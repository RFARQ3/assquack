import asyncio
import gc
import weakref

import duckdb
import pytest

from assquack import AssquackConfig, asset
from database_helpers import inspect_database


@pytest.mark.parametrize(
    "sql,params",
    [
        (None, None),
        ("SELECT value /* ; -- comment */", None),
        ("SELECT value /* outer /* ; */ nested */;", None),
        ("SELECT value;;", None),
        ("SELECT value WHERE value = ?; -- ending", [3]),
        (
            "SELECT value WHERE value = $value",
            {"value": 3},
        ),
    ],
)
def test_from_first_source_ignores_existing_data_table(tmp_path, sql, params):
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
        if first == "asset":
            reports = await reports_asset.query("SELECT day, value")
        elif first == "result":
            reports = await published.query("SELECT day, value")
        else:
            reports = duckdb.query("SELECT day, value FROM bronze.reports")  # noqa: F841
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
    assert asyncio.run(result.query("SELECT total")).fetchall() == [(10,)]


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
        first = await published.query("SELECT value")
        second = await published.query("SELECT value")  # noqa: F841
        joined = duckdb.query(
            "SELECT a.value + b.value FROM first a CROSS JOIN second b"
        )
        assert joined.fetchall() == [(6,)]
        assert helper(first).fetchall() == [(4,)]
        with pytest.raises(duckdb.CatalogException):
            duckdb.query("SELECT * FROM argument")
        # A native query error does not prevent subsequent result reads.
        refreshed = await published.query("SELECT value")
        assert refreshed.fetchall() == [(3,)]
        assert duckdb.query(
            "SELECT count(*) FROM duckdb_views() "
            "WHERE view_name IN ('first', 'second', 'argument')"
        ).fetchone() == (0,)

    asyncio.run(run())


def test_retained_relations_read_replaced_table(tmp_path):
    config = AssquackConfig(home=tmp_path)

    @asset("bronze.items", config=config)
    def items(value):
        return [{"value": value}]

    async def run():
        await items(1)
        retained = duckdb.query("SELECT value FROM bronze.items")
        await items(2)
        assert retained.fetchall() == [(2,)]
        latest = duckdb.query("SELECT value FROM bronze.items")
        assert latest.fetchall() == [(2,)]
        assert retained.fetchall() == [(2,)]

    asyncio.run(run())


def test_native_queries_compose_on_default_connection(tmp_path):
    config = AssquackConfig(home=tmp_path)
    with inspect_database(config) as connection:
        connection.execute("CREATE TABLE items AS SELECT 3 AS value")

    async def run():
        first = duckdb.query("SELECT value FROM items")  # noqa: F841
        second = duckdb.query("SELECT value FROM items")  # noqa: F841
        assert duckdb.query(
            "SELECT a.value + b.value FROM first a CROSS JOIN second b"
        ).fetchall() == [(6,)]

    asyncio.run(run())


def test_concurrent_cross_catalog_relations(tmp_path):
    configs = [
        AssquackConfig(home=tmp_path / str(i), attachment_name=(tmp_path / str(i)).name)
        for i in range(2)
    ]

    @asset("bronze.items")
    def items(value):
        return [{"value": value}]

    async def read(config):
        rows = duckdb.query(  # noqa: F841
            f'SELECT value FROM "{config.attachment_name}".bronze.items'
        )
        await asyncio.sleep(0)
        return duckdb.query("SELECT value + 10 FROM rows")

    async def run():
        for i, config in enumerate(configs):
            await items(i, assquack_config=config)
        parent = duckdb.query('SELECT value FROM "0".bronze.items')
        first, second = await asyncio.gather(read(configs[0]), read(configs[1]))
        assert first.fetchall() == [(10,)]
        assert second.fetchall() == [(11,)]
        assert parent.fetchall() == [(0,)]
        assert duckdb.query("SELECT * FROM first").fetchall() == [(10,)]
        duckdb.query('SELECT value FROM "0".bronze.items')
        assert duckdb.query("SELECT * FROM second").fetchall() == [(11,)]
        assert (duckdb.query('SELECT value FROM "0".bronze.items')).fetchall() == [(0,)]

    asyncio.run(run())


def test_native_binding_precedence_and_no_retained_caller_frame(tmp_path):
    config = AssquackConfig(home=tmp_path)

    @asset("bronze.items", config=config)
    def items():
        return [{"value": 3}]

    class Marker:
        pass

    async def run():
        await items()
        rows = await items.query("SELECT value")
        unrelated = duckdb.query("SELECT 99 AS value")  # noqa: F841
        __assquack_query__ = rows  # noqa: F841
        assert duckdb.query("SELECT * FROM __assquack_query__").fetchall() == [(3,)]
        assert duckdb.query(
            "WITH rows AS (SELECT 8 AS value) SELECT * FROM rows"
        ).fetchall() == [(8,)]

        def bind(rows):
            marker = Marker()
            reference = weakref.ref(marker)
            result = duckdb.query("SELECT value FROM rows")
            return result, reference

        derived, reference = bind(rows)
        gc.collect()
        assert reference() is None
        assert derived.fetchall() == [(3,)]

    asyncio.run(run())


def test_native_ctes_compose_through_both_avenues(tmp_path):
    config = AssquackConfig(home=tmp_path)

    @asset("sales.orders", config=config)
    def orders():
        return [{"customer": "a", "amount": 2}, {"customer": "a", "amount": 3}]

    rows = asyncio.run(orders.query("SELECT customer, amount WHERE amount > ?", [1]))
    sql = (
        "WITH totals AS (SELECT customer, sum(amount) AS total "
        "FROM rows GROUP BY customer) SELECT * FROM totals"
    )
    assert rows.query("rows", sql).fetchall() == [("a", 5)]
    assert duckdb.query(sql).fetchall() == [("a", 5)]


def test_result_query_is_lazy_and_uses_quoted_physical_source(tmp_path):
    from dataclasses import replace

    from assquack._storage.tables import TableReference

    config = AssquackConfig(home=tmp_path, attachment_name='a "catalog')

    @asset("main.source", config=config)
    def source():
        return [{"value": 3}]

    published = asyncio.run(source())
    duckdb.execute('CREATE SCHEMA "a ""catalog"."a schema"')
    duckdb.execute(
        'CREATE TABLE "a ""catalog"."a schema"."a ""table" AS SELECT 7 AS value'
    )
    published = replace(published, table=TableReference("a schema", 'a "table'))
    calls = []

    def observed(value: int) -> int:
        calls.append(value)
        return value

    duckdb.create_function("observed", observed, side_effects=True)
    duckdb.execute("USE memory")
    duckdb.execute("CREATE TABLE data AS SELECT 999 AS value")
    rows = asyncio.run(published.query("SELECT observed(value) AS value"))
    assert calls == []
    assert rows.fetchall() == [(7,)]
    assert calls == [7]


def test_result_query_leaves_sql_errors_to_duckdb(tmp_path):
    config = AssquackConfig(home=tmp_path)

    @asset("main.items", config=config)
    def items():
        return [{"value": 3}]

    published = asyncio.run(items())
    with pytest.raises(duckdb.ParserException):
        asyncio.run(published.query("WITH x AS (SELECT 1) SELECT * FROM x"))
    duckdb.execute("DROP TABLE main.items")
    with pytest.raises(duckdb.CatalogException):
        asyncio.run(published.query())
