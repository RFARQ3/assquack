from __future__ import annotations

import asyncio
import json
from contextlib import nullcontext
from pathlib import Path

import duckdb
import pytest

from assquack import AssquackConfig, IngestionConfig, asset
from assquack._materialization.fragments import NDJSONFragmentWriter
from assquack._storage.locking import writer_lock
from database_helpers import inspect_database


def test_python_writer_accepts_single_record_and_lazy_records(tmp_path: Path) -> None:
    writer = NDJSONFragmentWriter(tmp_path / "capture", target_bytes=15)
    assert writer.write_rows({"id": 1}) == 1
    assert writer.write_rows({"id": i, "nested": {"late": [i]}} for i in (2, 3)) == 2
    fragments = writer.finish()
    assert sum(item.row_count for item in fragments) == 3
    assert all(Path(item.path).stat().st_size for item in fragments)


def test_native_copy_multiple_calls_and_empty_output(tmp_path: Path) -> None:
    writer = NDJSONFragmentWriter(tmp_path / "capture", target_bytes=1)
    with nullcontext(duckdb.default_connection()) as connection:
        assert writer.write_relation(connection.sql("SELECT 1 id WHERE false")) == 0
        assert (
            writer.write_relation(connection.sql("SELECT range id FROM range(5000)"))
            == 5000
        )
        assert writer.write_rows({"id": 5000}) == 1
        assert writer.write_relation(connection.sql("SELECT 5001 id")) == 1
        assert writer.write_relation(connection.sql("SELECT 1 id WHERE false")) == 0
    fragments = writer.finish()
    rows = [
        json.loads(line)
        for item in fragments
        for line in Path(item.path).read_bytes().splitlines()
    ]
    assert sorted(row["id"] for row in rows) == list(range(5002))
    assert sum(item.row_count for item in fragments) == 5002
    assert len({item.path for item in fragments}) == len(fragments)
    assert all(
        item.row_count > 0 and Path(item.path).stat().st_size > 0 for item in fragments
    )
    assert not list((tmp_path / "capture").glob("*.partial"))
    assert len(list((tmp_path / "capture").glob("_COMPLETE"))) == 1


@pytest.mark.parametrize("kind", ["pandas", "table", "batch", "relation"])
@pytest.mark.parametrize("empty", [False, True])
def test_native_inputs_materialize(tmp_path: Path, kind: str, empty: bool) -> None:
    with nullcontext(duckdb.default_connection()) as connection:
        ids = [] if empty else [1, 2]
        if kind == "pandas":
            pd = pytest.importorskip("pandas")
            value = pd.DataFrame({"id": ids, "nested": [{"label": "x"}] * len(ids)})
        elif kind in ("table", "batch"):
            pa = pytest.importorskip("pyarrow")
            factory = pa.table if kind == "table" else pa.record_batch
            value = factory({"id": ids, "nested": [{"label": "x"}] * len(ids)})
        else:
            value = connection.sql(
                "SELECT range + 1 id, {'label': 'x'} nested FROM range(?)",
                params=[len(ids)],
            )

        @asset("main.native")
        def source():
            return value

        config = AssquackConfig(home=tmp_path)
        result = asyncio.run(source(assquack_config=config))
        assert result.row_count == len(ids)
        fragments = list(config.resolved_fragment_directory.rglob("*.ndjson"))
        assert bool(fragments) is not empty
        assert all(path.stat().st_size for path in fragments)
        with inspect_database(config) as stored:
            assert stored.sql("SELECT count(*) FROM main.native").fetchone() == (
                len(ids),
            )
            if not empty:
                assert stored.sql(
                    "SELECT id, nested.label FROM main.native ORDER BY id"
                ).fetchall() == [(1, "x"), (2, "x")]


@pytest.mark.parametrize("asynchronous", [False, True])
def test_mixed_generators_dispatch_each_item(
    tmp_path: Path, asynchronous: bool
) -> None:
    pa = pytest.importorskip("pyarrow")
    pd = pytest.importorskip("pandas")
    with nullcontext(duckdb.default_connection()) as connection:

        def items():
            yield []
            yield {"id": 1}
            yield [{"id": 2, "late": {"values": [1, 2]}}]
            yield pd.DataFrame({"id": [3]})
            yield pa.table({"id": [4]})
            yield pa.record_batch({"id": [5]})
            yield connection.sql("SELECT 6 id")
            yield connection.sql("SELECT 7 id")
            yield pa.table({"id": []})

        async def async_items():
            for item in items():
                yield item

        @asset("main.mixed")
        def source():
            return async_items() if asynchronous else items()

        config = AssquackConfig(
            home=tmp_path, ingestion=IngestionConfig(fragment_target_mb=0.00001)
        )
        result = asyncio.run(source(assquack_config=config))
        assert result.row_count == 7
        with inspect_database(config) as stored:
            assert stored.sql("SELECT id FROM main.mixed ORDER BY id").fetchall() == [
                (i,) for i in range(1, 8)
            ]
            assert stored.sql(
                "SELECT late.values FROM main.mixed WHERE id=2"
            ).fetchone() == ([1, 2],)


def test_nested_asset_cache_and_result_never_reexecutes(tmp_path: Path) -> None:
    calls = 0

    @asset("main.inner")
    def inner():
        nonlocal calls
        calls += 1
        return [{"id": calls}]

    @asset("main.outer")
    def outer():
        return inner.cache_first()

    config = AssquackConfig(home=tmp_path)

    async def exercise():
        first = await outer(assquack_config=config)
        second = await outer(assquack_config=config)
        assert first.row_count == second.row_count == 1

        @asset("main.from_result")
        def from_result():
            return first

        copied = await from_result(assquack_config=config)
        assert copied.row_count == 1

    asyncio.run(asyncio.wait_for(exercise(), timeout=10))
    assert calls == 1
    with inspect_database(config) as stored:
        assert stored.sql("SELECT id FROM main.from_result").fetchall() == [(1,)]
        assert stored.sql(
            "SELECT DISTINCT status FROM assquack_meta.runs"
        ).fetchall() == [("success",)]


def test_nested_failure_releases_lock_and_preserves_outer_table(tmp_path: Path) -> None:
    fail = False

    @asset("main.inner")
    def inner():
        if fail:
            raise RuntimeError("nested failure")
        return {"id": 1}

    @asset("main.outer")
    def outer():
        return inner

    config = AssquackConfig(home=tmp_path)

    async def exercise():
        nonlocal fail
        await outer(assquack_config=config)
        fail = True
        with pytest.raises(RuntimeError, match="nested failure"):
            await outer(assquack_config=config)
        fail = False
        await outer(assquack_config=config)

    asyncio.run(asyncio.wait_for(exercise(), timeout=10))
    with inspect_database(config) as stored:
        assert stored.sql("SELECT id FROM main.outer").fetchall() == [(1,)]
        assert stored.sql(
            "SELECT count(*) FROM assquack_meta.runs WHERE status='failed'"
        ).fetchone() == (2,)


def test_nested_lock_does_not_release_outer_or_admit_child_task(tmp_path: Path) -> None:
    entered = asyncio.Event()

    async def child():
        async with writer_lock(tmp_path / "db"):
            entered.set()

    async def exercise():
        async with writer_lock(tmp_path / "db"):
            async with writer_lock(tmp_path / "db"):
                pass
            task = asyncio.create_task(child())
            await asyncio.sleep(0.1)
            assert not entered.is_set()
        await asyncio.wait_for(task, 2)
        assert entered.is_set()

    asyncio.run(exercise())


@pytest.mark.parametrize("rows", [[], [{}], [{}, {}], [{"_QA_RUN_ID": "old"}]])
def test_metadata_only_asset_and_result_preserve_rows(tmp_path: Path, rows) -> None:
    @asset("main.inner")
    def inner():
        return rows

    @asset("main.outer")
    def outer():
        return inner

    config = AssquackConfig(home=tmp_path)

    async def exercise():
        result = await outer(assquack_config=config)

        @asset("main.copied")
        def copied():
            return result

        return await copied(assquack_config=config)

    result = asyncio.run(asyncio.wait_for(exercise(), 10))
    assert result.row_count == len(rows)
    with inspect_database(config) as stored:
        assert [
            name
            for name in stored.table("main.copied").columns
            if name.lower().startswith("_qa_")
        ] == ["_qa_run_id", "_qa_loaded_at"]
        assert stored.sql("SELECT count(*) FROM main.copied").fetchone() == (len(rows),)
        if rows:
            assert stored.sql(
                "SELECT DISTINCT _qa_run_id FROM main.copied"
            ).fetchone() == (result.run_id,)
    assert all(
        path.stat().st_size
        for path in config.resolved_fragment_directory.rglob("*.ndjson")
    )


def test_same_asset_nested_run_does_not_recover_live_outer(tmp_path: Path) -> None:
    nested = False
    config = AssquackConfig(home=tmp_path)

    @asset("main.recursive")
    async def source():
        nonlocal nested
        if nested:
            with inspect_database(config, bootstrap=True) as stored:
                assert stored.sql(
                    "SELECT status FROM assquack_meta.runs"
                ).fetchall() == [("running",), ("running",)]
            return {"id": 1}
        nested = True
        return source

    result = asyncio.run(asyncio.wait_for(source(assquack_config=config), 10))
    assert result.row_count == 1
    with inspect_database(config) as stored:
        assert stored.sql("SELECT status FROM assquack_meta.runs").fetchall() == [
            ("success",),
            ("success",),
        ]


@pytest.mark.parametrize("filename", ["copy_000000_0.partial"])
def test_native_partial_files_follow_retention_allowlist(
    tmp_path: Path, filename
) -> None:
    from assquack._materialization.retention import _remove_run_directory

    run_id = "a" * 32
    directory = tmp_path / "asset=source" / f"run_id={run_id}"
    directory.mkdir(parents=True)
    (directory / filename).write_bytes(b'{"id":1}\n')
    _remove_run_directory(tmp_path, "source", run_id)
    assert not directory.exists()


def test_native_evaluation_failure_cleans_intermediate(
    tmp_path: Path,
) -> None:
    from assquack._materialization.retention import _remove_run_directory

    run_id = "b" * 32
    directory = tmp_path / "asset=source" / f"run_id={run_id}"
    writer = NDJSONFragmentWriter(directory, target_bytes=1000)
    with nullcontext(duckdb.default_connection()) as connection:
        relation = connection.sql(
            "SELECT CASE WHEN i=1000000 THEN error('synthetic COPY failure') "
            "ELSE i END id FROM range(1000001) t(i)"
        )
        with pytest.raises(duckdb.Error, match="synthetic COPY failure"):
            writer.write_relation(relation)
    writer.abort()
    assert list(directory.glob("*.partial"))
    assert not list(directory.glob("*.parquet"))
    assert not list(directory.glob("*.ndjson"))
    assert not (directory / "_COMPLETE").exists()
    _remove_run_directory(tmp_path, "source", run_id)
    assert not directory.exists()


def test_default_native_capture_preserves_json_types(tmp_path):
    database = tmp_path / "source.duckdb"
    expected = tmp_path / "expected.ndjson"
    sql = (
        "SELECT {'x': [1, NULL, 2]} nested, 12.34::DECIMAL(8,2) amount, "
        "DATE '2026-01-01' AS day, TIMESTAMP '2026-01-01 01:02:03' stamp, "
        "{'a': [1, 2]}::VARIANT dynamic_value, MAP {'a': 1, 'b': NULL} AS mapping, "
        "NULL::INTEGER missing, union_value(a := 42) AS tagged, "
        "170141183460469231731687303715884105727::HUGEINT AS huge, "
        "340282366920938463463374607431768211455::UHUGEINT AS unsigned_huge, "
        "TIME WITH TIME ZONE '12:00:00+02:00' AS zoned_time, "
        "INTERVAL '1 month 2 days 1 microsecond' AS duration"
    )
    with duckdb.connect(
        str(database), config={"storage_compatibility_version": "v1.5.0"}
    ) as conn:
        conn.execute("CREATE TABLE items AS " + sql)
        conn.execute("COPY items TO ? (FORMAT JSON, ARRAY false)", [str(expected)])
    directory = tmp_path / "capture"
    writer = NDJSONFragmentWriter(directory, target_bytes=1000)
    assert writer.write_relation(duckdb.sql(sql)) == 1
    actual = [
        json.loads(line)
        for f in writer.finish()
        for line in Path(f.path).read_text().splitlines()
    ]
    assert actual == [json.loads(line) for line in expected.read_text().splitlines()]
    assert not list(directory.glob("*.parquet"))


def test_foreign_relation_rejected_without_transport(tmp_path):
    from assquack._errors import MaterializationError

    directory = tmp_path / "capture"
    writer = NDJSONFragmentWriter(directory, target_bytes=1000)
    with duckdb.connect() as source:
        relation = source.sql("SELECT 1 AS value")
        with pytest.raises(MaterializationError, match="independent connections"):
            writer.write_relation(relation)
    assert not list(directory.iterdir())


def test_direct_capture_preserves_decimal_and_ignores_alias_collision(tmp_path):
    from decimal import Decimal

    duckdb.execute("CREATE TABLE relation AS SELECT 999 AS amount")
    directory = tmp_path / "capture"
    writer = NDJSONFragmentWriter(directory, target_bytes=1000)
    relation = duckdb.sql("SELECT 12345678901234567890.123456789::DECIMAL(38,9) amount")
    assert writer.write_relation(relation) == 1
    fragment = writer.finish()[0]
    row = json.loads(Path(fragment.path).read_text(), parse_float=Decimal)
    assert row["amount"] == Decimal("12345678901234567890.123456789")
    assert not list(directory.glob("*.parquet"))
    assert duckdb.sql(
        "SELECT count(*) FROM duckdb_views() WHERE view_name LIKE 'assquack_capture_%'"
    ).fetchone() == (0,)


def test_copy_uses_replacement_scan_without_views_or_preexecution(
    tmp_path, monkeypatch
):
    from types import SimpleNamespace

    from assquack._materialization import fragments as module

    connection = duckdb.default_connection()
    connection.execute("SET python_scan_all_frames = true")
    monkeypatch.setattr(module, "uuid4", lambda: SimpleNamespace(hex="forced"))
    connection.execute("CREATE TABLE assquack_capture_forced AS SELECT 999 id")
    connection.execute("CREATE VIEW assquack_capture_forced_ AS SELECT 998 id")
    before = connection.sql(
        "SELECT count(*) FROM duckdb_views() WHERE NOT internal"
    ).fetchall()[0][0]
    calls = []

    def observe(value):
        calls.append(value)
        return value

    connection.create_function(
        "observe_capture", observe, ["BIGINT"], "BIGINT", side_effects=True
    )
    relation = connection.sql(
        "SELECT observe_capture(i) id, "
        "(SELECT count(*) FROM duckdb_views() WHERE NOT internal) views_during_copy "
        "FROM range(3) rows(i)"
    )
    assert calls == []
    copied_sql = []

    class CopyObserver:
        def execute(self, sql):
            if sql.startswith("COPY "):
                copied_sql.append(sql)
                assert (
                    connection.sql(
                        "SELECT count(*) FROM duckdb_views() WHERE NOT internal"
                    ).fetchall()[0][0]
                    == before
                )
            return connection.execute(sql)

        def register(self, *args):
            pytest.fail("Capture must not register a SQL object")

        def unregister(self, *args):
            pytest.fail("Capture must not unregister a SQL object")

    monkeypatch.setattr(module, "default_connection", CopyObserver)
    writer = NDJSONFragmentWriter(tmp_path / "direct", target_bytes=1)
    assert writer.write_relation(relation) == 3
    assert calls == [0, 1, 2]
    assert len(copied_sql) == 1
    assert 'COPY "assquack_capture_forced__"' in copied_sql[0]
    rows = [
        json.loads(line)
        for fragment in writer.finish()
        for line in Path(fragment.path).read_text().splitlines()
    ]
    assert sorted((row["id"], row["views_during_copy"]) for row in rows) == [
        (i, before) for i in range(3)
    ]
    assert relation.project("id").fetchall() == [(0,), (1,), (2,)]
    assert calls == [0, 1, 2, 0, 1, 2]
