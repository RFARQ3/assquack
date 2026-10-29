import asyncio
from pathlib import Path

import duckdb
import pytest

import assquack
from assquack import AssquackConfig, asset


@pytest.fixture(autouse=True)
def query_environment(monkeypatch):
    monkeypatch.delenv("ASSQUACK_DATABASE_PATH", raising=False)
    monkeypatch.delenv("ASSQUACK_HOME", raising=False)


def source_config(path: Path, value: int = 1) -> AssquackConfig:
    path.mkdir(parents=True)
    config = AssquackConfig(home=path)
    with duckdb.connect(str(config.resolved_database_path)) as connection:
        connection.execute("CREATE SCHEMA bronze")
        connection.execute("CREATE TABLE bronze.items AS SELECT ? AS value", [value])
    return config


def test_explicit_join_parameters_lifetime_and_source_immutability(tmp_path):
    left = source_config(tmp_path / "left", 3)
    right = source_config(tmp_path / "right", 4)
    before = left.resolved_database_path.read_bytes()
    result = asyncio.run(
        assquack.query(
            "SELECT l.value + r.value + $extra AS total "
            'FROM "odd.alias".bronze.items l '
            'CROSS JOIN "quote""alias".bronze.items r',
            {"extra": 2},
            sources={"odd.alias": left, 'quote"alias': right},
        )
    )
    assert left.resolved_database_path.read_bytes() == before
    with result:
        assert result.fetchall() == [(9,)]
        # The query result no longer owns source file handles.
        with duckdb.connect(str(left.resolved_database_path)) as connection:
            connection.execute("UPDATE bronze.items SET value = 10")
        assert result.fetchall() == [(9,)]
    result.close()
    with pytest.raises(duckdb.ConnectionException):
        result.fetchall()
    with duckdb.connect(str(right.resolved_database_path)) as connection:
        assert connection.execute("SHOW ALL TABLES").fetchall()[0][1] == "bronze"
        assert connection.execute(
            "SELECT count(*) FROM information_schema.schemata "
            "WHERE schema_name='assquack_meta'"
        ).fetchone() == (0,)
    assert before != left.resolved_database_path.read_bytes()
    assert not list(left.resolved_temp_directory.glob("assquack-query-*"))


@pytest.mark.parametrize("empty", [False, True])
def test_native_types_empty_results_and_asset_cleanup(tmp_path, empty):
    config = source_config(tmp_path / "source")
    result = asyncio.run(
        assquack.query(
            "SELECT {'x': [1, 2]} nested, 12.34::DECIMAL(8,2) amount, "
            "DATE '2026-01-01' AS event_date, {'a': 1}::VARIANT AS dynamic_value "
            + ("WHERE false" if empty else ""),
            sources={"source": config},
        )
    )
    assert str(result.relation.types[1]) == "DECIMAL(8,2)"
    assert len(result.fetchall()) == (0 if empty else 1)

    @asset("silver.output", config=config)
    def output():
        return result

    assert asyncio.run(output()).row_count == (0 if empty else 1)
    with pytest.raises(duckdb.ConnectionException):
        result.fetchall()
    assert not list(config.resolved_temp_directory.glob("assquack-query-*"))


def test_explicit_sources_exclude_implicit_context(tmp_path):
    own = source_config(tmp_path / "own", 2)
    external = source_config(tmp_path / "external", 5)

    @asset("silver.output", config=own)
    async def output():
        with pytest.raises(duckdb.Error):
            await assquack.query(
                "SELECT * FROM assquack_source.bronze.items", sources={"ext": external}
            )
        return await assquack.query(
            "SELECT value FROM ext.bronze.items", sources={"ext": external}
        )

    asyncio.run(output())
    with duckdb.connect(str(own.resolved_database_path)) as connection:
        assert connection.execute("SELECT value FROM silver.output").fetchall() == [
            (5,)
        ]


def test_capture_error_survives_reopen_failure(tmp_path, monkeypatch):
    from assquack._materialization import pipeline

    config = source_config(tmp_path / "source")
    original = pipeline.open_database
    opened = 0

    def open_once(config):
        nonlocal opened
        opened += 1
        if opened > 1:
            raise OSError("unavailable")
        return original(config)

    @asset("silver.failed", config=config)
    def failed():
        raise RuntimeError("capture failure")

    monkeypatch.setattr(pipeline, "open_database", open_once)
    with pytest.raises(RuntimeError, match="capture failure") as error:
        asyncio.run(failed())
    assert "Could not reopen" in error.value.__notes__[0]
    with pytest.raises(ValueError, match="Outside an asset"):
        asyncio.run(assquack.query("SELECT 1"))


def test_asset_copy_failure_closes_owned_result(tmp_path, monkeypatch):
    from assquack._materialization.fragments import NDJSONFragmentWriter

    config = source_config(tmp_path / "source")
    result = asyncio.run(
        assquack.query("SELECT 1 AS value", sources={"source": config})
    )

    def fail_copy(self, relation):
        raise RuntimeError("copy failure")

    @asset("silver.output", config=config)
    def output():
        return result

    monkeypatch.setattr(NDJSONFragmentWriter, "write_relation", fail_copy)
    with pytest.raises(RuntimeError, match="copy failure"):
        asyncio.run(output())
    with pytest.raises(duckdb.ConnectionException):
        result.fetchall()
    assert not list(config.resolved_temp_directory.glob("assquack-query-*"))


@pytest.mark.parametrize("environment", ["database", "home", "blank_database"])
def test_environment_source(tmp_path, monkeypatch, environment):
    config = source_config(tmp_path / "source")
    if environment == "database":
        monkeypatch.setenv("ASSQUACK_DATABASE_PATH", str(config.resolved_database_path))
    else:
        monkeypatch.setenv("ASSQUACK_HOME", str(config.home))
        if environment == "blank_database":
            monkeypatch.setenv("ASSQUACK_DATABASE_PATH", "   ")
    with asyncio.run(
        assquack.query("SELECT value FROM bronze.items WHERE value = ?", [1])
    ) as result:
        assert result.fetchall() == [(1,)]


def test_missing_context_does_not_fall_back(tmp_path, monkeypatch):
    source_config(tmp_path / "source")
    monkeypatch.chdir(tmp_path / "source")
    monkeypatch.setenv("ASSQUACK_DUCKDB__THREADS", "2")
    monkeypatch.setenv("ASSQUACK_HOME", " ")
    with pytest.raises(ValueError, match="Outside an asset"):
        asyncio.run(assquack.query("SELECT 1"))


@pytest.mark.parametrize("sources", [{}, {"": None}, {"temp": None}, {"a": None}])
def test_invalid_sources(sources):
    with pytest.raises((ValueError, TypeError)):
        asyncio.run(assquack.query("SELECT 1", sources=sources))


def test_duplicate_paths_aliases_and_missing_sources(tmp_path):
    config = source_config(tmp_path / "source")
    for sources in ({"a": config, "b": config}, {"a": config, "A": config}):
        with pytest.raises(ValueError, match="distinct"):
            asyncio.run(assquack.query("SELECT 1", sources=sources))
    missing = AssquackConfig(home=tmp_path / "missing")
    with pytest.raises(FileNotFoundError):
        asyncio.run(assquack.query("SELECT 1", sources={"missing": missing}))
    assert not missing.home.exists()


@pytest.mark.parametrize(
    "sql",
    [
        "DELETE FROM source.bronze.items",
        "CREATE TABLE x (a INT)",
        "SELECT 1; DELETE FROM source.bronze.items",
        "COPY (SELECT 1) TO 'oops.csv'",
        "ATTACH 'oops.duckdb' AS other",
        "SET threads = 1",
        "",
        "-- comment",
    ],
)
def test_reject_writes_without_source_changes(tmp_path, sql):
    config = source_config(tmp_path / "source")
    before = config.resolved_database_path.read_bytes()
    with pytest.raises(ValueError, match="read-only SELECT"):
        asyncio.run(assquack.query(sql, sources={"source": config}))
    assert config.resolved_database_path.read_bytes() == before
    assert not list(config.resolved_temp_directory.glob("assquack-query-*"))


def test_failed_select_cleans_up(tmp_path):
    config = source_config(tmp_path / "source")
    with pytest.raises(duckdb.Error):
        asyncio.run(assquack.query("SELECT * FROM missing", sources={"source": config}))
    assert not list(config.resolved_temp_directory.glob("assquack-query-*"))
    with duckdb.connect(str(config.resolved_database_path)) as connection:
        assert connection.execute("SELECT * FROM bronze.items").fetchall() == [(1,)]


def test_effective_config_override_and_cross_database_publication(tmp_path):
    attached = source_config(tmp_path / "attached", 2)
    override = source_config(tmp_path / "override", 5)
    external = source_config(tmp_path / "external", 8)

    @asset("silver.local", config=attached)
    async def local():
        return await assquack.query("SELECT value FROM bronze.items")

    @asset("silver.joined", config=override)
    async def joined():
        return await assquack.query(
            "SELECT a.value + b.value AS total FROM a.bronze.items a "
            "CROSS JOIN b.bronze.items b",
            sources={"a": override, "b": external},
        )

    assert asyncio.run(local(assquack_config=override)).row_count == 1
    assert asyncio.run(joined()).row_count == 1
    with duckdb.connect(str(override.resolved_database_path)) as connection:
        assert connection.execute("SELECT value FROM silver.local").fetchall() == [(5,)]
        assert connection.execute("SELECT total FROM silver.joined").fetchall() == [
            (13,)
        ]
    with pytest.raises(ValueError, match="Outside an asset"):
        asyncio.run(assquack.query("SELECT 1"))


def test_nested_direct_assets_restore_parent_and_generator_context(tmp_path):
    parent_config = source_config(tmp_path / "parent", 2)
    child_config = source_config(tmp_path / "child", 7)
    seen = []

    @asset("silver.child", config=child_config)
    async def child():
        with await assquack.query("SELECT value FROM bronze.items") as result:
            seen.append(result.fetchall()[0][0])
        yield {"value": 7}

    @asset("silver.parent", config=parent_config)
    async def parent():
        await child()
        yield await assquack.query("SELECT value FROM bronze.items")
        yield await assquack.query("SELECT value FROM bronze.items")

    assert asyncio.run(parent()).row_count == 2
    assert seen == [7]


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
        return await assquack.query("SELECT value FROM bronze.items")

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
        with pytest.raises(ValueError, match="Outside an asset"):
            await assquack.query("SELECT 1")

    asyncio.run(run())
    with duckdb.connect(str(config.resolved_database_path)) as connection:
        assert connection.execute(
            "SELECT status FROM assquack_meta.runs"
        ).fetchall() == [("failed",)]
