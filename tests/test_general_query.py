import asyncio
import gc
import inspect
from pathlib import Path

import duckdb
import pytest

import assquack
from assquack import AssquackConfig, asset
from assquack._query.context import current_query_config
from database_helpers import inspect_database


@pytest.fixture(autouse=True)
def query_environment(monkeypatch):
    monkeypatch.delenv("ASSQUACK_DATABASE_PATH", raising=False)
    monkeypatch.delenv("ASSQUACK_HOME", raising=False)


def source_config(path: Path, value: int = 1) -> AssquackConfig:
    path.mkdir(parents=True)
    config = AssquackConfig(home=path, attachment_name=path.name)
    with inspect_database(config) as connection:
        connection.execute("CREATE SCHEMA bronze")
        connection.execute("CREATE TABLE bronze.items AS SELECT ? AS value", [value])
    return config


def test_public_surface():
    assert list(inspect.signature(assquack.query).parameters) == ["sql", "params"]
    assert not hasattr(assquack, "AssquackQueryResult")
    assert not hasattr(assquack, "cross_database_query")
    with (
        pytest.raises(TypeError, match="AssquackConfig"),
        assquack.use_config(None),  # pyright: ignore[reportArgumentType]
    ):
        pass


def test_native_lifetime_and_source_immutability(tmp_path):
    config = source_config(tmp_path / "source", 3)
    with assquack.use_config(config):
        result = asyncio.run(
            assquack.query(
                "SELECT value + $extra AS total FROM bronze.items", {"extra": 2}
            )
        )
    assert isinstance(result, duckdb.DuckDBPyRelation)
    gc.collect()
    assert result.fetchall() == [(5,)]
    derived = result.project("total + 1 AS total")
    del result
    gc.collect()
    assert derived.fetchall() == [(6,)]
    del derived
    gc.collect()
    # The process owns attachments; result lifetime does not close the engine.
    assert duckdb.sql("SELECT value FROM source.bronze.items").fetchall() == [(3,)]
    with inspect_database(config) as conn:
        assert conn.execute("SHOW ALL TABLES").fetchall()[0][1] == "bronze"
        assert conn.execute(
            "SELECT count(*) FROM information_schema.schemata "
            "WHERE schema_name='assquack_meta'"
        ).fetchone() == (0,)

        assert conn.sql("SELECT value FROM bronze.items").fetchall() == [(3,)]


@pytest.mark.parametrize("empty", [False, True])
def test_native_types_empty_results_and_retained_asset_input(tmp_path, empty):
    config = source_config(tmp_path / "source")
    with assquack.use_config(config):
        result = asyncio.run(
            assquack.query(
                "SELECT {'x': [1, 2]} nested, 12.34::DECIMAL(8,2) amount, "
                "DATE '2026-01-01' AS event_date, {'a': 1}::VARIANT AS dynamic_value "
                + ("WHERE false" if empty else "")
            )
        )
    assert str(result.types[1]) == "DECIMAL(8,2)"

    @asset("silver.output", config=config)
    def output():
        return result

    assert asyncio.run(output()).row_count == (0 if empty else 1)
    assert len(result.fetchall()) == (0 if empty else 1)


def test_retained_snapshot_allows_same_table_replacement_and_nested_publication(
    tmp_path,
):
    config = source_config(tmp_path / "source", 3)
    retained = []

    @asset("bronze.items", config=config)
    def replace():
        return [{"value": 9}]

    @asset("silver.output", config=config)
    async def output():
        result = await assquack.query("SELECT value FROM bronze.items")
        retained.append(result.project("value + 1 AS value"))
        await replace()
        return result

    assert asyncio.run(output()).row_count == 1
    assert retained[0].fetchall() == [(10,)]
    with inspect_database(config) as conn:
        assert conn.sql("SELECT value FROM bronze.items").fetchall() == [(9,)]
        assert conn.sql("SELECT value FROM silver.output").fetchall() == [(9,)]


@pytest.mark.parametrize("environment", ["database", "home", "blank_database"])
def test_environment_source(tmp_path, monkeypatch, environment):
    config = source_config(tmp_path / "source")
    monkeypatch.setenv("ASSQUACK_ATTACHMENT_NAME", config.attachment_name)
    if environment == "database":
        monkeypatch.setenv("ASSQUACK_DATABASE_PATH", str(config.resolved_database_path))
    else:
        monkeypatch.setenv("ASSQUACK_HOME", str(config.home))
        if environment == "blank_database":
            monkeypatch.setenv("ASSQUACK_DATABASE_PATH", "   ")
    result = asyncio.run(
        assquack.query("SELECT value FROM bronze.items WHERE value = ?", [1])
    )
    assert result.fetchall() == [(1,)]


def test_missing_context_and_source_do_not_fall_back(tmp_path, monkeypatch):
    source_config(tmp_path / "source")
    monkeypatch.chdir(tmp_path / "source")
    monkeypatch.setenv("ASSQUACK_DUCKDB__THREADS", "2")
    monkeypatch.setenv("ASSQUACK_HOME", " ")
    assert asyncio.run(assquack.query("SELECT 1")).fetchone() == (1,)
    missing = AssquackConfig(
        home=tmp_path / "missing", attachment_name=(tmp_path / "missing").name
    )
    with assquack.use_config(missing), pytest.raises(ValueError, match="attach"):
        asyncio.run(assquack.query("SELECT 1"))
    assert not missing.home.exists()


def test_query_supports_native_sequence_expression(tmp_path):
    config = source_config(tmp_path / "source")
    with inspect_database(config) as conn:
        conn.execute("CREATE SEQUENCE seq")
    with assquack.use_config(config):
        result = asyncio.run(assquack.query("SELECT nextval('source.main.seq')"))
    assert result.fetchone() == (1,)


def test_failed_select_allows_following_query(tmp_path):
    config = source_config(tmp_path / "source")
    with assquack.use_config(config), pytest.raises(duckdb.Error):
        asyncio.run(assquack.query("SELECT * FROM missing"))
    with assquack.use_config(config):
        assert asyncio.run(assquack.query("SELECT * FROM bronze.items")).fetchall() == [
            (1,)
        ]


def test_config_selects_writes_but_not_short_reads(tmp_path, monkeypatch):
    parent_config = source_config(tmp_path / "parent", 2)
    child_config = source_config(tmp_path / "child", 7)
    explicit = source_config(tmp_path / "explicit", 11)
    inner = source_config(tmp_path / "inner", 13)
    monkeypatch.setenv("ASSQUACK_HOME", str(inner.home))
    monkeypatch.setenv("ASSQUACK_ATTACHMENT_NAME", inner.attachment_name)
    seen = []

    async def read():
        with pytest.raises(ValueError, match=r"database\.schema\.table"):
            await assquack.query("SELECT value FROM bronze.items")
        return (
            await assquack.query("SELECT value FROM parent.bronze.items")
        ).fetchall()[0][0]

    @asset("silver.child", config=child_config)
    async def child():
        seen.append(await read())
        yield {"value": 7}

    @asset("silver.parent", config=parent_config)
    async def parent():
        seen.append(await read())
        with assquack.use_config(explicit):
            await child()
            with assquack.use_config(inner):
                seen.append(await read())
            yield await assquack.query("SELECT value FROM explicit.bronze.items")
        await child()
        seen.append(await read())
        yield await assquack.query('SELECT value FROM "inner".bronze.items')

    assert asyncio.run(parent(assquack_config=inner)).row_count == 2
    assert seen == [2, 2, 2, 2, 2]
    with inspect_database(inner) as conn:
        assert conn.sql(
            "SELECT value FROM silver.parent ORDER BY value"
        ).fetchall() == [(11,), (13,)]
    with inspect_database(child_config) as conn:
        assert conn.sql("SELECT value FROM silver.child").fetchall() == [(7,)]
    assert asyncio.run(read()) == 2


@pytest.mark.parametrize("cancel", [False, True])
def test_explicit_scopes_restore_after_exception_and_cancellation(tmp_path, cancel):
    outer = source_config(tmp_path / "outer", 1)
    inner = source_config(tmp_path / "inner", 2)

    async def run():
        with assquack.use_config(outer):
            with (
                pytest.raises(asyncio.CancelledError if cancel else RuntimeError),
                assquack.use_config(inner),
            ):
                if cancel:
                    raise asyncio.CancelledError()
                raise RuntimeError("deliberate")
            assert current_query_config() is outer
            assert (
                await assquack.query('SELECT value FROM "outer".bronze.items')
            ).fetchall() == [(1,)]
        assert (await assquack.query("SELECT 1")).fetchone() == (1,)

    asyncio.run(run())


def test_concurrent_scopes_and_child_task_inheritance(tmp_path):
    configs = [source_config(tmp_path / str(i), i) for i in range(2)]
    ready = asyncio.Event()
    entered = 0

    async def read(config):
        assert current_query_config() is config
        return (
            await assquack.query(
                f'SELECT value FROM "{config.attachment_name}".bronze.items'
            )
        ).fetchall()

    async def scoped(config):
        nonlocal entered
        with assquack.use_config(config):
            entered += 1
            if entered == 2:
                ready.set()
            await ready.wait()
            return await asyncio.create_task(read(config))

    async def run():
        return await asyncio.gather(*(scoped(c) for c in configs))

    assert asyncio.run(run()) == [[(0,)], [(1,)]]


def test_capture_failure_does_not_close_default_connection(tmp_path):
    config = source_config(tmp_path / "source")
    connection = duckdb.default_connection()

    @asset("silver.failed", config=config)
    def failed():
        raise RuntimeError("capture failure")

    with pytest.raises(RuntimeError, match="capture failure"):
        asyncio.run(failed())
    assert duckdb.default_connection() is connection
    assert duckdb.sql("SELECT 1").fetchone() == (1,)


def test_sync_generator_can_invoke_query_during_deferred_consumption(tmp_path):
    config = source_config(tmp_path / "source", 6)

    @asset("silver.output", config=config)
    def output():
        # query currently performs synchronous DuckDB work inside its async API.
        coroutine = assquack.query("SELECT value FROM bronze.items")
        try:
            coroutine.send(None)
        except StopIteration as finished:
            yield finished.value

    assert asyncio.run(output()).row_count == 1


def test_concurrent_assets_have_separate_contexts(tmp_path):
    configs = [source_config(tmp_path / str(i), i) for i in range(2)]
    ready = asyncio.Event()
    entered = 0

    @asset("silver.output")
    async def output():
        nonlocal entered
        entered += 1
        if entered == 2:
            ready.set()
        await ready.wait()
        config = current_query_config()
        assert config is not None
        return await assquack.query(
            f'SELECT value FROM "{config.attachment_name}".bronze.items'
        )

    async def run():
        return await asyncio.gather(*(output(assquack_config=c) for c in configs))

    results = asyncio.run(run())
    assert [
        asyncio.run(r.query("SELECT value FROM data")).fetchall() for r in results
    ] == [
        [(0,)],
        [(1,)],
    ]


@pytest.mark.parametrize("cancel", [False, True])
def test_failure_and_cancellation_restore_context_and_record_failure(tmp_path, cancel):
    config = source_config(tmp_path / "source")

    @asset("silver.failed", config=config)
    async def failed():
        yield await assquack.query("SELECT value FROM bronze.items")
        if cancel:
            raise asyncio.CancelledError()
        raise RuntimeError("deliberate")

    async def run():
        with pytest.raises(asyncio.CancelledError if cancel else RuntimeError):
            await failed()
        assert (await assquack.query("SELECT 1")).fetchone() == (1,)

    asyncio.run(run())
    with inspect_database(config) as connection:
        assert connection.execute(
            "SELECT status FROM assquack_meta.runs"
        ).fetchall() == [("failed",)]


@pytest.mark.parametrize("asynchronous", [False, True])
@pytest.mark.parametrize("cleanup_fails", [False, True])
def test_failed_generator_consumption_restores_explicit_scope(
    tmp_path, asynchronous, cleanup_fails
):
    own = source_config(tmp_path / "own", 1)
    external = source_config(tmp_path / "external", 2)

    def rows():
        with assquack.use_config(external):
            try:
                yield object()
            finally:
                if cleanup_fails:
                    raise RuntimeError("cleanup failed")

    async def async_rows():
        with assquack.use_config(external):
            try:
                yield object()
            finally:
                if cleanup_fails:
                    raise RuntimeError("cleanup failed")

    output = asset("silver.failed", config=own)(async_rows if asynchronous else rows)

    async def run():
        with assquack.use_config(own):
            with pytest.raises(Exception, match="Assets must return") as error:
                await output()
            if cleanup_fails:
                assert "cleanup failed" in error.value.__notes__[0]
            assert current_query_config() is own
            assert (
                await assquack.query("SELECT value FROM own.bronze.items")
            ).fetchall() == [(1,)]
        assert (await assquack.query("SELECT 1")).fetchone() == (1,)

    asyncio.run(run())
