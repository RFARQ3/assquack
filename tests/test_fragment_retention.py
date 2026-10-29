from __future__ import annotations

import asyncio
import hashlib
import sys
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import Mock

import duckdb
import pytest

from assquack import AssquackConfig, IngestionConfig, asset
from assquack._materialization import pipeline
from assquack._materialization.fragments import fragment_run_directory
from assquack._materialization.retention import prune_fragments
from assquack._storage.database import open_database
from assquack._storage.metadata import MetadataRepository


def test_retention_preserves_success_and_three_failures_then_retries_fresh(
    tmp_path: Path,
) -> None:
    fail = False
    calls = 0

    @asset(table="main.retained", name="source")
    async def source() -> AsyncIterator[list[dict[str, int]]]:
        nonlocal calls
        calls += 1
        yield [{"id": calls}]
        if fail:
            raise RuntimeError("extraction failed")

    config = AssquackConfig(
        home=tmp_path / "database",
        chunk_size=1,
        ingestion=IngestionConfig(
            fragment_directory=tmp_path / "configured-fragments",
            fragment_target_mb=0.000001,
        ),
    )
    first = asyncio.run(source(assquack_config=config))
    latest = asyncio.run(source(assquack_config=config))
    root = config.resolved_fragment_directory
    assert not fragment_run_directory(root, "source", first.run_id).exists()

    fail = True
    for _ in range(5):
        with pytest.raises(RuntimeError, match="extraction failed"):
            asyncio.run(source(assquack_config=config))

    with duckdb.connect(str(config.resolved_database_path)) as connection:
        failures = connection.execute(
            "SELECT run_id FROM assquack_meta.runs "
            "WHERE status = 'failed' ORDER BY runtime DESC"
        ).fetchall()
        assert connection.execute(
            "SELECT id, _qa_run_id FROM main.retained"
        ).fetchall() == [(2, latest.run_id)]
        assert connection.execute(
            "SELECT count(*) FROM assquack_meta.runs"
        ).fetchone() == (7,)
    assert len(failures) == 5
    for index, (run_id,) in enumerate(failures):
        assert fragment_run_directory(root, "source", run_id).exists() == (index < 3)
    assert fragment_run_directory(root, "source", latest.run_id).exists()

    cached = asyncio.run(source.cache_first()(assquack_config=config))
    assert cached.cached and cached.run_id == latest.run_id
    assert calls == 7

    fail = False
    recovered = asyncio.run(source(assquack_config=config))
    assert recovered.run_id not in {row[0] for row in failures}
    assert not fragment_run_directory(root, "source", latest.run_id).exists()
    with duckdb.connect(str(config.resolved_database_path)) as connection:
        assert connection.execute(
            "SELECT id, _qa_run_id FROM main.retained"
        ).fetchall() == [(8, recovered.run_id)]


def test_parameterized_assets_share_directory_without_sharing_retention(
    tmp_path: Path,
) -> None:
    @asset(table="main.orders_{region}", name="orders")
    async def orders(region: str) -> list[dict[str, str]]:
        return [{"region": region}]

    config = AssquackConfig(home=tmp_path)
    other = asyncio.run(orders("west", assquack_config=config))
    old = asyncio.run(orders("east", assquack_config=config))
    current = asyncio.run(orders("east", assquack_config=config))
    assert other.asset_id != current.asset_id
    root = config.resolved_fragment_directory
    assert not fragment_run_directory(root, "orders", old.run_id).exists()
    assert fragment_run_directory(root, "orders", other.run_id).exists()
    assert fragment_run_directory(root, "orders", current.run_id).exists()


def seed_run(
    connection: duckdb.DuckDBPyConnection,
    root: Path,
    run_id: str,
    status: str,
    day: int,
    *,
    asset_id: str = "asset-id",
) -> Path:
    timestamp = datetime(2026, 1, day, tzinfo=UTC)
    connection.execute(
        "INSERT INTO assquack_meta.runs "
        "(run_id, asset_id, status, runtime, materialized_at) VALUES (?, ?, ?, ?, ?)",
        [
            run_id,
            asset_id,
            status,
            timestamp,
            timestamp if status == "success" else None,
        ],
    )
    directory = fragment_run_directory(root, "orders", run_id)
    directory.mkdir(parents=True)
    (directory / "fragment=000000.partial").write_text("evidence", encoding="utf-8")
    return directory


def test_catalog_chronology_and_identity_preserve_running_unknown_and_history(
    tmp_path: Path,
) -> None:
    config = AssquackConfig(home=tmp_path)
    root = config.resolved_fragment_directory
    with open_database(config) as connection:
        old_success = seed_run(connection, root, "f" * 32, "success", 1)
        latest_success = seed_run(connection, root, "0" * 32, "success", 2)
        old_failure = seed_run(
            connection, root, "ffffffff-ffff-ffff-ffff-ffffffffffff", "failed", 3
        )
        retained = [
            seed_run(connection, root, str(day) * 32, "failed", day)
            for day in (4, 5, 6)
        ]
        running = seed_run(connection, root, "a" * 32, "running", 1)
        other = seed_run(connection, root, "b" * 32, "success", 1, asset_id="other")
        orphan = fragment_run_directory(root, "orders", "c" * 32)
        orphan.mkdir()
        (orphan / "fragment=000000.ndjson").write_text("orphan", encoding="utf-8")
        before = connection.execute(
            "SELECT to_json(r) FROM assquack_meta.runs r ORDER BY run_id"
        ).fetchall()

        prune_fragments(MetadataRepository(connection), root, "asset-id", "orders")

        assert not old_success.exists()
        assert not old_failure.exists()
        assert all(
            path.exists()
            for path in [latest_success, *retained, running, other, orphan]
        )
        assert (
            connection.execute(
                "SELECT to_json(r) FROM assquack_meta.runs r ORDER BY run_id"
            ).fetchall()
            == before
        )


def test_colliding_orphan_is_not_claimed_by_failed_run_and_later_deleted(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = Mock()
    clock.now.return_value = datetime(2026, 10, 22, tzinfo=UTC)
    monkeypatch.setattr(pipeline, "datetime", clock)
    fail = False

    @asset(table="main.orders", name="orders")
    async def orders() -> list[dict[str, int]]:
        if fail:
            raise RuntimeError("fresh failure")
        return [{"id": 1}]

    config = AssquackConfig(home=tmp_path)
    first = asyncio.run(orders(assquack_config=config))
    clock.now.return_value += timedelta(seconds=1)
    run_id = hashlib.md5(
        f"{first.asset_id}:{clock.now.return_value.isoformat()}".encode()
    ).hexdigest()
    orphan = fragment_run_directory(
        config.resolved_fragment_directory, "orders", run_id
    )
    orphan.mkdir()
    evidence = orphan / "fragment=000000.ndjson"
    evidence.write_bytes(b"unknown evidence\n")
    with pytest.raises(FileExistsError):
        asyncio.run(orders(assquack_config=config))
    with duckdb.connect(str(config.resolved_database_path)) as connection:
        assert connection.execute(
            "SELECT fragments_owned FROM assquack_meta.runs WHERE run_id = ?",
            [run_id],
        ).fetchone() == (False,)
    with open_database(config) as connection:
        assert connection.execute(
            "SELECT fragments_owned FROM assquack_meta.runs WHERE run_id = ?",
            [run_id],
        ).fetchone() == (False,)

    fail = True
    for _ in range(3):
        clock.now.return_value += timedelta(seconds=1)
        with pytest.raises(RuntimeError, match="fresh failure"):
            asyncio.run(orders(assquack_config=config))
    assert evidence.read_bytes() == b"unknown evidence\n"


@pytest.mark.parametrize("redirect", ["root", "asset", "run", "child"])
def test_retention_rejects_redirected_directories(
    tmp_path: Path, redirect: str
) -> None:
    config = AssquackConfig(home=tmp_path)
    root = config.resolved_fragment_directory
    with open_database(config) as connection:
        old = seed_run(connection, root, "a" * 32, "success", 1)
        seed_run(connection, root, "b" * 32, "success", 2)
        link = {"root": root, "asset": old.parent, "run": old, "child": old / "nested"}[
            redirect
        ]
        target = tmp_path / "outside-fragment-root"
        if redirect == "child":
            target.mkdir()
            (target / "fragment=000000.partial").write_text("outside", encoding="utf-8")
        else:
            link.rename(target)
        before = {
            p.relative_to(target): p.read_bytes()
            for p in target.rglob("*")
            if p.is_file()
        }
        if sys.platform == "win32":
            import _winapi

            _winapi.CreateJunction(str(target), str(link))
        else:
            link.symlink_to(target, target_is_directory=True)

        prune_fragments(MetadataRepository(connection), root, "asset-id", "orders")

        assert link.exists()
        assert {
            p.relative_to(target): p.read_bytes()
            for p in target.rglob("*")
            if p.is_file()
        } == before


@pytest.mark.parametrize("unexpected", ["notes.txt", "subdirectory"])
def test_unknown_content_prevents_partial_deletion(
    tmp_path: Path, unexpected: str
) -> None:
    config = AssquackConfig(home=tmp_path)
    root = config.resolved_fragment_directory
    with open_database(config) as connection:
        old = seed_run(connection, root, "a" * 32, "success", 1)
        seed_run(connection, root, "b" * 32, "success", 2)
        extra = old / unexpected
        if unexpected == "subdirectory":
            extra.mkdir()
        else:
            extra.write_text("unknown", encoding="utf-8")
        prune_fragments(MetadataRepository(connection), root, "asset-id", "orders")
        assert extra.exists()
        assert (old / "fragment=000000.partial").read_text() == "evidence"


@pytest.mark.parametrize("fail", [False, True])
def test_cleanup_error_does_not_change_publication_or_hide_source_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    fail: bool,
) -> None:
    should_fail = False

    @asset(table="main.orders", name="orders")
    async def orders() -> list[dict[str, int]]:
        if should_fail:
            raise RuntimeError("original extraction error")
        return [{"id": 1}]

    config = AssquackConfig(home=tmp_path)
    first = asyncio.run(orders(assquack_config=config))
    with open_database(config) as connection:
        old = seed_run(
            connection,
            config.resolved_fragment_directory,
            "f" * 32,
            "success",
            1,
            asset_id=first.asset_id,
        )
    original_unlink = Path.unlink

    def deny_old_cleanup(path: Path, missing_ok: bool = False) -> None:
        if path.parent == old:
            raise PermissionError("synthetic cleanup denial")
        original_unlink(path, missing_ok=missing_ok)

    monkeypatch.setattr(Path, "unlink", deny_old_cleanup)
    should_fail = fail
    if fail:
        with pytest.raises(RuntimeError, match="original extraction error"):
            asyncio.run(orders(assquack_config=config))
    else:
        result = asyncio.run(orders(assquack_config=config))
        assert result.run_id != first.run_id
    assert old.exists()
    assert "synthetic cleanup denial" in caplog.text
    with duckdb.connect(str(config.resolved_database_path)) as connection:
        assert connection.execute(
            "SELECT status, error FROM assquack_meta.runs ORDER BY runtime DESC LIMIT 1"
        ).fetchone() == (
            ("failed", "original extraction error") if fail else ("success", None)
        )
        if fail:
            assert connection.execute(
                "SELECT _qa_run_id FROM main.orders"
            ).fetchone() == (first.run_id,)


def test_cache_hit_recovers_interrupted_runs_without_reextracting(
    tmp_path: Path,
) -> None:
    calls = 0

    @asset(table="main.orders", name="orders")
    async def orders() -> list[dict[str, int]]:
        nonlocal calls
        calls += 1
        return [{"id": calls}]

    config = AssquackConfig(home=tmp_path)
    first = asyncio.run(orders(assquack_config=config))
    root = config.resolved_fragment_directory
    with open_database(config) as connection:
        stale = [
            seed_run(
                connection, root, str(day) * 32, "running", day, asset_id=first.asset_id
            )
            for day in range(1, 5)
        ]
        unrelated = seed_run(connection, root, "f" * 32, "running", 1, asset_id="other")

    cached = asyncio.run(orders.cache_first()(assquack_config=config))

    assert cached.cached and cached.run_id == first.run_id
    assert calls == 1
    assert not stale[0].exists()
    assert all(path.exists() for path in stale[1:])
    assert unrelated.exists()
    assert fragment_run_directory(root, "orders", first.run_id).exists()
    with duckdb.connect(str(config.resolved_database_path)) as connection:
        assert connection.execute(
            "SELECT count(*) FROM assquack_meta.runs WHERE status = 'failed' "
            "AND error = 'Interrupted before publication completed'"
        ).fetchone() == (4,)
        assert connection.execute(
            "SELECT status FROM assquack_meta.runs WHERE asset_id = 'other'"
        ).fetchone() == ("running",)


def test_catalog_cleanup_failure_preserves_successful_result(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    @asset(table="main.orders", name="orders")
    async def orders() -> list[dict[str, int]]:
        return [{"id": 1}]

    def fail_selection(self: MetadataRepository, asset_id: str) -> list[str]:
        raise duckdb.IOException("synthetic catalog cleanup failure")

    monkeypatch.setattr(
        MetadataRepository, "find_obsolete_fragment_runs", fail_selection
    )
    config = AssquackConfig(home=tmp_path)
    result = asyncio.run(orders(assquack_config=config))
    assert result.row_count == 1
    assert "synthetic catalog cleanup failure" in caplog.text
    with duckdb.connect(str(config.resolved_database_path)) as connection:
        assert connection.execute(
            "SELECT status FROM assquack_meta.runs"
        ).fetchone() == ("success",)


def test_equal_timestamps_still_retain_exactly_three_failed_runs(
    tmp_path: Path,
) -> None:
    config = AssquackConfig(home=tmp_path)
    root = config.resolved_fragment_directory
    with open_database(config) as connection:
        directories = [
            seed_run(connection, root, str(number) * 32, "failed", 1)
            for number in range(1, 6)
        ]
        metadata = MetadataRepository(connection)
        prune_fragments(metadata, root, "asset-id", "orders")
        retained = {path for path in directories if path.exists()}
        assert len(retained) == 3
        prune_fragments(metadata, root, "asset-id", "orders")
        assert {path for path in directories if path.exists()} == retained
        assert connection.execute(
            "SELECT count(*) FROM assquack_meta.runs"
        ).fetchone() == (5,)


def test_cancelled_capture_keeps_evidence_and_retry_starts_fresh(
    tmp_path: Path,
) -> None:
    interrupted = True
    entered = asyncio.Event()

    @asset(table="main.cancelled", name="source")
    async def source() -> AsyncIterator[list[dict[str, int]]]:
        yield [{"id": 1 if interrupted else 2}]
        if interrupted:
            entered.set()
            await asyncio.Event().wait()

    config = AssquackConfig(
        home=tmp_path,
        chunk_size=1,
        ingestion=IngestionConfig(fragment_target_mb=0.000001),
    )

    async def cancel_capture() -> None:
        task = asyncio.create_task(source(assquack_config=config))
        await asyncio.wait_for(entered.wait(), timeout=5)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(cancel_capture())
    with duckdb.connect(str(config.resolved_database_path)) as connection:
        row = connection.execute(
            "SELECT run_id, status FROM assquack_meta.runs"
        ).fetchone()
        assert row is not None and row[1] == "failed"
        failed_id = row[0]
    failed_directory = fragment_run_directory(
        config.resolved_fragment_directory, "source", failed_id
    )
    assert (failed_directory / "fragment=000000.ndjson").read_text() == '{"id":1}\n'
    assert not (failed_directory / "_COMPLETE").exists()

    interrupted = False
    result = asyncio.run(source(assquack_config=config))
    assert result.run_id != failed_id
    assert failed_directory.exists()
    with duckdb.connect(str(config.resolved_database_path)) as connection:
        assert connection.execute("SELECT id FROM main.cancelled").fetchall() == [(2,)]


def test_staging_cleanup_failure_does_not_report_committed_run_as_failed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    @asset(table="main.orders", name="orders")
    async def orders() -> list[dict[str, int]]:
        return [{"id": 1}]

    monkeypatch.setattr(
        pipeline,
        "drop_shaped_staging",
        Mock(side_effect=duckdb.IOException("staging cleanup failed")),
    )
    config = AssquackConfig(home=tmp_path)
    result = asyncio.run(orders(assquack_config=config))
    assert result.row_count == 1
    assert "staging cleanup failed" in caplog.text
    with duckdb.connect(str(config.resolved_database_path)) as connection:
        assert connection.execute(
            "SELECT status FROM assquack_meta.runs"
        ).fetchone() == ("success",)
        assert connection.execute("SELECT id FROM main.orders").fetchall() == [(1,)]


def test_abort_cleanup_failure_does_not_mask_extraction_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    @asset(table="main.orders", name="orders")
    async def orders() -> list[dict[str, int]]:
        raise RuntimeError("original extraction error")

    monkeypatch.setattr(
        pipeline.NDJSONFragmentWriter,
        "abort",
        Mock(side_effect=OSError("abort cleanup failed")),
    )
    config = AssquackConfig(home=tmp_path)
    with pytest.raises(RuntimeError, match="original extraction error"):
        asyncio.run(orders(assquack_config=config))
    assert "abort cleanup failed" in caplog.text
    with duckdb.connect(str(config.resolved_database_path)) as connection:
        assert connection.execute(
            "SELECT status, error FROM assquack_meta.runs"
        ).fetchone() == ("failed", "original extraction error")
