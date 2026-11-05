"""Native SQL defaults and import initialization, using only synthetic paths."""

import os
import subprocess
import sys
from pathlib import Path

import duckdb
import pytest

import assquack
from assquack import AssquackConfig


def test_removed_query_surface():
    assert not hasattr(assquack, "query")
    assert not hasattr(assquack, "use_config")


@pytest.mark.parametrize("placement", ["ASSQUACK_HOME", "ASSQUACK_DATABASE_PATH"])
def test_import_initializes_and_reopens_environment_database(tmp_path, placement):
    home = tmp_path / "configured"
    database_path = home / "assquack.duckdb"
    environment = clean_environment()
    environment[placement] = str(
        home if placement == "ASSQUACK_HOME" else database_path
    )
    environment["ASSQUACK_ATTACHMENT_NAME"] = "people data"
    environment["ASSQUACK_DUCKDB__THREADS"] = "2"
    script = """
import assquack
import duckdb
assert duckdb.query("SELECT current_database()").fetchone() == ("people data",)
assert duckdb.query("SELECT current_setting('threads')").fetchone() == (2,)
assert duckdb.query("SELECT count(*) FROM assquack_meta.assets").fetchone() == (0,)
duckdb.execute("CREATE TABLE IF NOT EXISTS saved AS SELECT 42 AS id")
assert duckdb.query("SELECT id FROM saved").fetchone() == (42,)
"""
    for _ in range(2):
        result = subprocess.run(
            [sys.executable, "-c", script],
            cwd=tmp_path,
            env=environment,
            text=True,
            capture_output=True,
        )
        assert result.returncode == 0, result.stderr
    assert database_path.is_file()


@pytest.mark.parametrize("resource_only", [False, True])
def test_import_without_placement_creates_no_disk_database(tmp_path, resource_only):
    environment = clean_environment()
    if resource_only:
        environment.update(
            {
                "ASSQUACK_ATTACHMENT_NAME": "unused",
                "ASSQUACK_DUCKDB__THREADS": "2",
                "ASSQUACK_DUCKDB__TEMP_DIRECTORY": str(tmp_path / "spill"),
                "ASSQUACK_INGESTION__FRAGMENT_DIRECTORY": str(tmp_path / "fragments"),
                "ASSQUACK_EXPORTS": str(tmp_path / "exports"),
            }
        )
    script = """
import assquack
import duckdb
assert duckdb.query("SELECT current_database()").fetchone() == ("memory",)
assert duckdb.query("SELECT 7").fetchone() == (7,)
"""
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=tmp_path,
        env=environment,
        text=True,
        capture_output=True,
    )
    assert result.returncode == 0, result.stderr
    assert list(tmp_path.iterdir()) == []


def clean_environment():
    # Never pass configured database paths or cloud credentials into subprocesses.
    environment = {
        name: value
        for name, value in os.environ.items()
        if not name.upper().startswith(("ASSQUACK_", "DUCKDB_", "AZURE_"))
    }
    environment["PYTHONPATH"] = str(Path(__file__).resolve().parents[1] / "src")
    return environment


def test_first_attachment_default_and_native_search_path_are_preserved(tmp_path):
    first = AssquackConfig(home=tmp_path / "first", attachment_name="first")
    second = AssquackConfig(home=tmp_path / "second", attachment_name="second")
    first.attach()
    duckdb.execute("CREATE TABLE items AS SELECT 1 AS id")
    second.attach()
    duckdb.execute("CREATE TABLE second.main.items AS SELECT 2 AS id")
    assert duckdb.query("SELECT id FROM items").fetchone() == (1,)
    assert duckdb.query(
        "SELECT * FROM query_table($name)", params={"name": "items"}
    ).fetchone() == (1,)
    duckdb.execute("USE second")
    first.attach()
    assert duckdb.query("SELECT id FROM items").fetchone() == (2,)
    duckdb.execute("SET search_path = 'first.main'")
    second.attach()
    assert duckdb.query("SELECT id FROM items").fetchone() == (1,)
    assert duckdb.query(
        "SELECT a.id, b.id FROM first.main.items a, second.main.items b"
    ).fetchone() == (1, 2)


def test_asset_config_selects_writes_without_switching_native_reads(tmp_path):
    import asyncio

    from assquack import asset

    first = AssquackConfig(home=tmp_path / "first", attachment_name="first")
    second = AssquackConfig(home=tmp_path / "second", attachment_name="second")
    first.attach()
    duckdb.execute("CREATE TABLE input AS SELECT 7 AS id")

    @asset("main.output", config=second)
    def output():
        return duckdb.query("SELECT id FROM input")

    result = asyncio.run(output())
    assert asyncio.run(result.query()).project("id").fetchone() == (7,)
    assert duckdb.query("SELECT id FROM second.main.output").fetchone() == (7,)
    assert duckdb.query("SELECT current_database()").fetchone() == ("first",)
