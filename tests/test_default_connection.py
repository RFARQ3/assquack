import asyncio
from pathlib import Path

import duckdb
import pytest
from pydantic import ValidationError

from assquack import AssquackConfig, DuckDBConfig, asset
from assquack._storage.database import database_catalog
from assquack._storage.metadata import MetadataRepository


def test_manual_attachment_is_lazy_named_and_idempotent(tmp_path, monkeypatch):
    monkeypatch.delenv("ASSQUACK_HOME", raising=False)
    monkeypatch.delenv("ASSQUACK_DATABASE_PATH", raising=False)
    config = AssquackConfig(home=tmp_path / "people", attachment_name="People")

    @asset("hr.employees", config=config)
    def employees():
        return [{"id": 1}]

    assert not config.home.exists()
    assert not asyncio.iscoroutinefunction(config.attach)
    assert config.attach() is None
    config.attach()
    equivalent = AssquackConfig(
        home=config.home / ".." / "people", attachment_name="people"
    )
    equivalent.attach()
    duckdb.execute("CREATE TABLE people.main.manual AS SELECT 42 AS id")
    rows = duckdb.query("SELECT * FROM people.main.manual")
    assert isinstance(rows, duckdb.DuckDBPyRelation)
    assert duckdb.query("SELECT * FROM rows").fetchone() == (42,)
    assert duckdb.sql("SELECT current_database()").fetchone() == ("people",)
    asyncio.run(employees())
    assert employees.exists(assquack_config=equivalent)
    assert asyncio.run(employees.query(assquack_config=equivalent)).project(
        "id"
    ).fetchall() == [(1,)]
    assert asyncio.run(employees.cache_first()(assquack_config=equivalent)).cached
    assert database_catalog(config) == "people"


def test_attachment_conflicts_do_not_create_the_other_database(tmp_path):
    first = AssquackConfig(home=tmp_path / "one", attachment_name="people")
    first.attach()
    other = AssquackConfig(home=tmp_path / "two", attachment_name="PEOPLE")
    with pytest.raises(ValueError, match="already used"):
        other.attach()
    assert not other.home.exists()
    alias = AssquackConfig(home=first.home, attachment_name="alias")
    with pytest.raises(ValueError, match="already attached"):
        alias.attach()
    with pytest.raises(ValueError, match="already used"):
        AssquackConfig(home=tmp_path / "memory", attachment_name="memory").attach()


@pytest.mark.parametrize("name", ["", "  ", "bad\0name"])
def test_attachment_names_reject_empty_or_nul(name):
    with pytest.raises(ValidationError):
        AssquackConfig(attachment_name=name)


def test_engine_settings_preserve_omission_and_reject_explicit_conflicts(tmp_path):
    first = AssquackConfig(
        home=tmp_path / "one",
        attachment_name="one",
        duckdb=DuckDBConfig(threads=2, memory_limit="1GB"),
    )
    first.attach()
    second = AssquackConfig(home=tmp_path / "two", attachment_name="two")
    assert second.duckdb.model_fields_set == set()
    second.attach()
    assert duckdb.sql("SELECT current_setting('threads')").fetchone() == (2,)
    matching = AssquackConfig(
        home=tmp_path / "three",
        attachment_name="three",
        duckdb=DuckDBConfig(
            threads=2, memory_limit="1GB", temp_directory=first.resolved_temp_directory
        ),
    )
    matching.attach()
    for name, value in [
        ("threads", 4),
        ("memory_limit", "8GB"),
        ("temp_directory", tmp_path / "elsewhere"),
    ]:
        conflict = AssquackConfig(
            home=tmp_path / name,
            attachment_name=name,
            duckdb=DuckDBConfig.model_validate({name: value}),
        )
        with pytest.raises(ValueError, match=name):
            conflict.attach()
        assert not conflict.home.exists()
    assert duckdb.sql("SELECT current_setting('threads')").fetchone() == (2,)


def test_nested_child_task_and_cross_database_native_join(tmp_path):
    people = AssquackConfig(home=tmp_path / "people", attachment_name="people")
    work = AssquackConfig(home=tmp_path / "work", attachment_name="work")

    @asset("hr.employees", config=people)
    async def employees():
        await asyncio.sleep(0)
        yield {"id": 1, "dept": 3}

    @asset("hr.departments", config=work)
    def departments():
        return [{"dept": 3, "label": "Engineering"}]

    @asset("reports.joined", config=work)
    async def joined():
        employee_rows = await asyncio.create_task(employees.query())
        department_rows = await departments.query()  # noqa: F841
        assert isinstance(employee_rows, duckdb.DuckDBPyRelation)
        return duckdb.query(
            "SELECT id, label FROM employee_rows JOIN department_rows USING(dept)"
        )

    result = asyncio.run(asyncio.wait_for(joined(), 5))
    assert result.row_count == 1
    assert asyncio.run(result.query("SELECT id, label")).fetchall() == [
        (1, "Engineering")
    ]
    assert duckdb.sql(
        "SELECT count(*) FROM people.assquack_meta.runs WHERE status='success'"
    ).fetchone() == (1,)
    assert duckdb.sql(
        "SELECT count(*) FROM work.assquack_meta.runs WHERE status='success'"
    ).fetchone() == (2,)


def test_cancelled_capture_preserves_other_publication_and_previous_table(tmp_path):
    config = AssquackConfig(home=tmp_path / "one", attachment_name="one")
    other = AssquackConfig(home=tmp_path / "two", attachment_name="two")
    started = asyncio.Event()
    cancel = False

    @asset("main.items", config=config)
    async def items():
        if cancel:
            started.set()
            await asyncio.Event().wait()
        return [{"id": 1}]

    @asset("main.items", config=other)
    def other_items():
        return [{"id": 2}]

    async def run():
        nonlocal cancel
        published = await items()
        cancel = True
        task = asyncio.create_task(items())
        await started.wait()
        other_published = await other_items()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert (await published.query("SELECT id")).fetchone() == (1,)
        assert (await other_published.query("SELECT id")).fetchone() == (2,)

    asyncio.run(asyncio.wait_for(run(), 5))
    assert duckdb.sql(
        "SELECT status FROM one.assquack_meta.runs ORDER BY runtime"
    ).fetchall() == [("success",), ("failed",)]


def test_publication_failure_rolls_back_only_its_destination(tmp_path, monkeypatch):
    first = AssquackConfig(home=tmp_path / "one", attachment_name="one")
    second = AssquackConfig(home=tmp_path / "two", attachment_name="two")

    @asset("main.items")
    def items(value):
        return [{"id": value}]

    asyncio.run(items(1, assquack_config=first))
    asyncio.run(items(2, assquack_config=second))
    original = MetadataRepository.mark_run_successful

    def fail(self, *args, **kwargs):
        original(self, *args, **kwargs)
        raise RuntimeError("publication failure")

    monkeypatch.setattr(MetadataRepository, "mark_run_successful", fail)
    with pytest.raises(RuntimeError, match="publication failure"):
        asyncio.run(items(9, assquack_config=first))
    assert duckdb.sql("SELECT id FROM one.main.items").fetchone() == (1,)
    assert duckdb.sql("SELECT id FROM two.main.items").fetchone() == (2,)
    assert duckdb.sql(
        "SELECT status FROM one.assquack_meta.runs ORDER BY runtime"
    ).fetchall() == [("success",), ("failed",)]


@pytest.mark.parametrize("name", ["order.items", "orders.csv", "folder/orders"])
def test_scoped_quoted_table_wins_over_default_and_file_scans(tmp_path, name):
    from assquack._storage.sql import quote_identifier

    config = AssquackConfig(home=tmp_path, attachment_name="people")
    config.attach()
    quoted = quote_identifier(name)
    duckdb.execute(f"CREATE TABLE people.main.{quoted} AS SELECT 9 AS id")
    duckdb.execute(f"CREATE TABLE memory.main.{quoted} AS SELECT 999 AS id")
    result = duckdb.query(f"SELECT * FROM {quoted}")
    assert result.fetchone() == (9,)


@pytest.mark.parametrize("name", ["\u00c4", "people data", 'odd"name'])
def test_attachment_names_preserve_quoted_unicode(tmp_path, name):
    from assquack._storage.sql import quote_identifier

    config = AssquackConfig(home=tmp_path, attachment_name=name)
    config.attach()
    quoted = quote_identifier(name)
    duckdb.execute(f"CREATE TABLE {quoted}.main.items AS SELECT 4 id")
    assert duckdb.query(f"SELECT * FROM {quoted}.main.items").fetchone() == (4,)


def test_existing_native_attachment_preserves_actual_catalog_spelling(tmp_path):
    from assquack._storage.sql import quote_literal

    path = tmp_path / "assquack.duckdb"
    duckdb.execute(
        f"ATTACH {quote_literal(str(path))} AS People (STORAGE_VERSION 'v1.5.0')"
    )
    config = AssquackConfig(home=tmp_path, attachment_name="people")

    @asset("main.items", config=config)
    def items():
        return [{"id": 4}]

    asyncio.run(items())
    assert database_catalog(config) == "People"
    assert items.exists()
    assert asyncio.run(items.query("SELECT id")).fetchone() == (4,)
    assert duckdb.query("SELECT id FROM items").fetchone() == (4,)


def test_explicit_none_temp_directory_means_inherit(tmp_path):
    first = AssquackConfig(home=tmp_path / "one", attachment_name="one")
    first.attach()
    second = AssquackConfig(
        home=tmp_path / "two",
        attachment_name="two",
        duckdb=DuckDBConfig(temp_directory=None),
    )
    assert second.duckdb.model_fields_set == {"temp_directory"}
    second.attach()
    configured = duckdb.sql("SELECT current_setting('temp_directory')").fetchone()
    assert configured is not None
    assert Path(configured[0]).resolve() == first.resolved_temp_directory.resolve()
