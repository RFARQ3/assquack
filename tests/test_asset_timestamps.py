from __future__ import annotations

import asyncio
from pathlib import Path

import duckdb
import pytest

from assquack import AssquackConfig, asset
from assquack._materialization import pipeline
from assquack._storage.metadata import MetadataRepository
from assquack._storage.tables import bootstrap_database
from database_helpers import inspect_database


def test_fresh_schema_defines_nullable_publication_dates(tmp_path: Path) -> None:
    with duckdb.connect(str(tmp_path / "fresh.duckdb")) as db:
        bootstrap_database(db, "fresh")
        bootstrap_database(db, "fresh")
        columns = {
            row[0]: (row[1], row[2])
            for row in db.execute("DESCRIBE assquack_meta.assets").fetchall()
        }
        assert columns == {
            "asset_id": ("VARCHAR", "NO"),
            "asset_name": ("VARCHAR", "NO"),
            "asset_signature": ("VARCHAR", "YES"),
            "schema_name": ("VARCHAR", "NO"),
            "table_name": ("VARCHAR", "NO"),
            "materialization_mode": ("VARCHAR", "NO"),
            "first_materialized_at": ("TIMESTAMP WITH TIME ZONE", "YES"),
            "last_materialized_at": ("TIMESTAMP WITH TIME ZONE", "YES"),
        }


def timestamps(config: AssquackConfig):
    with inspect_database(config) as db:
        return db.execute(
            "SELECT CAST(first_materialized_at AS VARCHAR), "
            "CAST(last_materialized_at AS VARCHAR) "
            "FROM assquack_meta.assets"
        ).fetchone()


def test_publication_dates_ignore_failed_attempts_and_cache(tmp_path: Path) -> None:
    failing = True
    value = 1

    @asset("main.orders")
    def orders():
        if failing:
            raise ValueError("capture failed")
        return [{"id": value}]

    config = AssquackConfig(home=tmp_path)
    with pytest.raises(ValueError, match="capture failed"):
        asyncio.run(orders(assquack_config=config))
    assert timestamps(config) == (None, None)

    failing = False
    first = asyncio.run(orders(assquack_config=config))
    first_dates = timestamps(config)
    assert first_dates is not None
    assert first_dates[0] is not None and first_dates[0] == first_dates[1]
    with inspect_database(config) as db:
        assert db.execute(
            "SELECT CAST(materialized_at AS VARCHAR) FROM assquack_meta.runs "
            "WHERE run_id = ?",
            [first.run_id],
        ).fetchone() == (first_dates[0],)

    value = 2
    second = asyncio.run(orders(assquack_config=config))
    second_dates = timestamps(config)
    assert second_dates is not None
    assert second_dates[0] == first_dates[0]
    assert second_dates[1] > first_dates[1]
    with inspect_database(config) as db:
        assert db.execute(
            "SELECT CAST(materialized_at AS VARCHAR) FROM assquack_meta.runs "
            "WHERE run_id = ?",
            [second.run_id],
        ).fetchone() == (second_dates[1],)

    failing = True
    assert asyncio.run(orders.cache_first()(assquack_config=config)).cached
    assert timestamps(config) == second_dates
    with pytest.raises(ValueError, match="capture failed"):
        asyncio.run(orders(assquack_config=config))
    assert timestamps(config) == second_dates
    with inspect_database(config) as db:
        assert db.execute("SELECT id FROM main.orders").fetchall() == [(2,)]
        assert db.execute(
            "SELECT count(*) FROM assquack_meta.runs WHERE status = 'failed' "
            "AND materialized_at IS NULL"
        ).fetchone() == (2,)


@pytest.mark.parametrize("previous_success", [False, True])
def test_publication_rollback_reverts_table_and_dates(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, previous_success: bool
) -> None:
    value = 1

    @asset("main.orders")
    def orders():
        return [{"id": value}]

    config = AssquackConfig(home=tmp_path)
    if previous_success:
        asyncio.run(orders(assquack_config=config))
        before = timestamps(config)
    else:
        before = (None, None)
    value = 2
    mark_successful = MetadataRepository.mark_run_successful

    def fail_after_metadata(self, *args, **kwargs):
        mark_successful(self, *args, **kwargs)
        raise RuntimeError("publication rolled back")

    monkeypatch.setattr(MetadataRepository, "mark_run_successful", fail_after_metadata)
    with pytest.raises(RuntimeError, match="publication rolled back"):
        asyncio.run(orders(assquack_config=config))
    assert timestamps(config) == before
    with inspect_database(config) as db:
        if previous_success:
            assert db.execute("SELECT id FROM main.orders").fetchall() == [(1,)]
        else:
            assert db.execute(
                "SELECT count(*) FROM information_schema.tables "
                "WHERE table_schema = 'main' AND table_name = 'orders'"
            ).fetchone() == (0,)
        assert db.execute(
            "SELECT status, CAST(materialized_at AS VARCHAR) FROM assquack_meta.runs "
            "ORDER BY runtime DESC LIMIT 1"
        ).fetchone() == ("failed", None)


def test_export_failure_preserves_successful_publication(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    @asset("main.orders")
    def orders():
        return [{"id": 1}]

    def fail_export(*args, **kwargs):
        raise RuntimeError("export failed")

    config = AssquackConfig(home=tmp_path)
    monkeypatch.setattr(pipeline, "_write_exports", fail_export)
    with pytest.raises(RuntimeError, match="export failed"):
        asyncio.run(orders(assquack_config=config))
    dates = timestamps(config)
    assert dates is not None and dates[0] is not None and dates[0] == dates[1]
    with inspect_database(config) as db:
        assert db.execute("SELECT id FROM main.orders").fetchall() == [(1,)]
        assert db.execute(
            "SELECT status, CAST(materialized_at AS VARCHAR) FROM assquack_meta.runs"
        ).fetchone() == ("success", dates[0])
