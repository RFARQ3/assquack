from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import Mock

import duckdb
import pytest

from assquack import AssquackConfig, asset
from assquack._materialization import pipeline
from assquack._storage.metadata import MetadataRepository
from assquack._storage.tables import bootstrap_database


def timestamps(config: AssquackConfig):
    with duckdb.connect(str(config.resolved_database_path)) as db:
        return db.execute(
            "SELECT first_materialized_at, last_materialized_at "
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
    with duckdb.connect(str(config.resolved_database_path)) as db:
        assert db.execute(
            "SELECT materialized_at FROM assquack_meta.runs WHERE run_id = ?",
            [first.run_id],
        ).fetchone() == (first_dates[0],)

    value = 2
    second = asyncio.run(orders(assquack_config=config))
    second_dates = timestamps(config)
    assert second_dates is not None
    assert second_dates[0] == first_dates[0]
    assert second_dates[1] > first_dates[1]
    with duckdb.connect(str(config.resolved_database_path)) as db:
        assert db.execute(
            "SELECT materialized_at FROM assquack_meta.runs WHERE run_id = ?",
            [second.run_id],
        ).fetchone() == (second_dates[1],)

    failing = True
    assert asyncio.run(orders.cache_first()(assquack_config=config)).cached
    assert timestamps(config) == second_dates
    with pytest.raises(ValueError, match="capture failed"):
        asyncio.run(orders(assquack_config=config))
    assert timestamps(config) == second_dates
    with duckdb.connect(str(config.resolved_database_path)) as db:
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
    with duckdb.connect(str(config.resolved_database_path)) as db:
        if previous_success:
            assert db.execute("SELECT id FROM main.orders").fetchall() == [(1,)]
        else:
            assert db.execute(
                "SELECT count(*) FROM information_schema.tables "
                "WHERE table_schema = 'main' AND table_name = 'orders'"
            ).fetchone() == (0,)
        assert db.execute(
            "SELECT status, materialized_at FROM assquack_meta.runs "
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
    with duckdb.connect(str(config.resolved_database_path)) as db:
        assert db.execute("SELECT id FROM main.orders").fetchall() == [(1,)]
        assert db.execute(
            "SELECT status, materialized_at FROM assquack_meta.runs"
        ).fetchone() == ("success", dates[0])


@pytest.mark.parametrize("interrupt_migration", [False, True])
def test_legacy_migration_uses_only_successful_run_evidence(
    tmp_path: Path, interrupt_migration: bool
) -> None:
    with duckdb.connect(str(tmp_path / "legacy.duckdb")) as db:
        db.execute("SET TimeZone = 'UTC'")
        bootstrap_database(db)
        db.execute("DROP TABLE assquack_meta.assets")
        db.execute(
            """
            CREATE TABLE assquack_meta.assets (
                asset_id VARCHAR PRIMARY KEY, asset_name VARCHAR NOT NULL,
                asset_signature VARCHAR, schema_name VARCHAR NOT NULL,
                table_name VARCHAR NOT NULL, materialization_mode VARCHAR NOT NULL,
                created_at TIMESTAMPTZ NOT NULL, updated_at TIMESTAMPTZ NOT NULL
            )
            """
        )
        for name in ("published", "failed", "missing", "undated"):
            db.execute(
                "INSERT INTO assquack_meta.assets VALUES "
                "(?, ?, NULL, 'main', ?, 'replace', '2000-01-01', '2099-01-01')",
                [name, name, name],
            )
        db.execute(
            """
            INSERT INTO assquack_meta.runs (run_id, asset_id, status, runtime,
                                            materialized_at) VALUES
            ('1', 'published', 'success', '2026-01-01', '2026-01-02'),
            ('2', 'published', 'success', '2026-02-01', '2026-02-02'),
            ('3', 'published', 'failed', '2026-03-01', '2026-03-02'),
            ('4', 'failed', 'failed', '2026-01-01', NULL),
            ('5', 'undated', 'success', '2026-01-01', NULL)
            """
        )
        if interrupt_migration:
            old_rows = db.execute("SELECT * FROM assquack_meta.assets").fetchall()

            def execute_then_fail(sql, *args):
                result = db.execute(sql, *args)
                if "UPDATE assquack_meta.assets AS a" in sql:
                    raise RuntimeError("migration interrupted")
                return result

            interrupted = Mock(wraps=db)
            interrupted.execute.side_effect = execute_then_fail
            with pytest.raises(RuntimeError, match="migration interrupted"):
                bootstrap_database(interrupted)
            assert (
                db.execute("SELECT * FROM assquack_meta.assets").fetchall() == old_rows
            )
            assert [
                row[0] for row in db.execute("DESCRIBE assquack_meta.assets").fetchall()
            ][-2:] == ["created_at", "updated_at"]

        bootstrap_database(db)
        expected = [
            ("failed", None, None),
            ("missing", None, None),
            (
                "published",
                datetime(2026, 1, 2, tzinfo=UTC),
                datetime(2026, 2, 2, tzinfo=UTC),
            ),
            ("undated", None, None),
        ]
        query = (
            "SELECT asset_id, first_materialized_at, last_materialized_at "
            "FROM assquack_meta.assets ORDER BY asset_id"
        )
        assert db.execute(query).fetchall() == expected
        columns = {
            row[0]: row[1]
            for row in db.execute("DESCRIBE assquack_meta.assets").fetchall()
        }
        assert "created_at" not in columns and "updated_at" not in columns
        assert columns["first_materialized_at"] == "TIMESTAMP WITH TIME ZONE"
        assert columns["last_materialized_at"] == "TIMESTAMP WITH TIME ZONE"
        assert db.execute("SELECT count(*) FROM assquack_meta.runs").fetchone() == (5,)

        # Bootstrap never recomputes current-schema dates from edited run history.
        db.execute("DELETE FROM assquack_meta.runs")
        bootstrap_database(db)
        bootstrap_database(db)
        assert db.execute(query).fetchall() == expected
