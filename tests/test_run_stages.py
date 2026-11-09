from __future__ import annotations

import asyncio
import importlib
import threading
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import Mock

import duckdb
import pytest

from assquack import AssquackConfig, FileSystemDestination, asset
from assquack._run import ACTIVE_RUNS
from assquack._storage.database import transaction
from assquack._storage.metadata import MetadataRepository
from database_helpers import inspect_database

resolve_module = importlib.import_module("assquack._pipeline.resolve")
stage_module = importlib.import_module("assquack._pipeline.stage")
publish_module = importlib.import_module("assquack._pipeline.publish")
export_module = importlib.import_module("assquack._pipeline.export")


def test_materialize_progress_is_saved_before_the_producer_runs(tmp_path: Path):
    config = AssquackConfig(home=tmp_path, exports=None)

    @asset("main.progress")
    async def source():
        with inspect_database(config) as connection:
            assert connection.execute(
                "SELECT resolve_status, materialize_status, stage_status, "
                "publish_status, export_status, outcome FROM assquack_meta.runs"
            ).fetchone() == (
                "success",
                "running",
                "unstarted",
                "unstarted",
                "unstarted",
                "running",
            )
        await asyncio.sleep(0)
        return [{"id": 1}]

    asyncio.run(source(assquack_config=config))
    with inspect_database(config) as connection:
        assert connection.execute(
            "SELECT resolve_status, materialize_status, stage_status, "
            "publish_status, export_status, outcome FROM assquack_meta.runs"
        ).fetchone() == (
            "success",
            "success",
            "success",
            "success",
            "skipped",
            "success",
        )
    assert not ACTIVE_RUNS


@pytest.mark.parametrize("failed_stage", ["materialize", "stage", "publish", "export"])
def test_failed_stage_is_recorded_without_losing_the_published_table(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failed_stage: str
):
    config = AssquackConfig(home=tmp_path, exports=str(tmp_path / "exports"))
    failure = RuntimeError(f"synthetic {failed_stage} failure")
    fail_producer = False
    value = 1

    @asset("main.progress", export="progress.parquet")
    def source():
        if fail_producer:
            raise failure
        return [{"id": value}]

    asyncio.run(source(assquack_config=config))
    value = 2

    def fail(*args, **kwargs):
        raise failure

    if failed_stage == "materialize":
        fail_producer = True
    elif failed_stage == "stage":
        monkeypatch.setattr(stage_module, "replace_schema_strategy", fail)
    elif failed_stage == "publish":
        monkeypatch.setattr(publish_module.ReplacePublicationStrategy, "publish", fail)
    else:
        monkeypatch.setattr(export_module, "write_exports", fail)

    with pytest.raises(RuntimeError) as raised:
        asyncio.run(source(assquack_config=config))
    assert raised.value is failure

    with inspect_database(config) as connection:
        row = connection.execute(
            "SELECT resolve_status, materialize_status, stage_status, "
            "publish_status, export_status, outcome, failed_stage, error_type, "
            "error_message, status FROM assquack_meta.runs "
            "ORDER BY runtime DESC LIMIT 1"
        ).fetchone()
        assert row is not None
        stages = ["resolve", "materialize", "stage", "publish", "export"]
        position = stages.index(failed_stage)
        assert row[:5] == tuple(
            "success"
            if index < position
            else "failed"
            if index == position
            else "unstarted"
            for index in range(5)
        )
        assert row[5:9] == ("failed", failed_stage, "RuntimeError", str(failure))
        assert row[9] == ("success" if failed_stage == "export" else "failed")
        assert connection.execute("SELECT id FROM main.progress").fetchall() == [
            (2 if failed_stage == "export" else 1,)
        ]
        assert connection.execute(
            "SELECT count(*) FROM information_schema.tables "
            "WHERE table_schema = 'assquack_stage'"
        ).fetchone() == (0,)
    assert not ACTIVE_RUNS


def test_producer_mutates_caller_arguments_without_changing_resolved_identity(
    tmp_path: Path,
):
    from assquack._pipeline.resolve import resolve_request

    @asset("main.{selection[endpoint]}", export="{selection[endpoint]}.parquet")
    def source(selection, *, same_selection):
        assert selection is same_selection
        selection["endpoint"] = "changed_by_producer"
        selection["ids"].append(3)
        return [{"id": value} for value in selection["ids"]]

    selection = {"endpoint": "orders", "ids": [1, 2]}
    config = AssquackConfig(home=tmp_path, exports=str(tmp_path / "exports"))
    expected = resolve_request(
        source._request((selection,), {"same_selection": selection}, config=config)
    )
    result = asyncio.run(
        source(selection, same_selection=selection, assquack_config=config)
    )

    assert selection == {"endpoint": "changed_by_producer", "ids": [1, 2, 3]}
    assert result.table.table_name == "orders"
    assert result.row_count == 3
    assert result.asset_id == expected.asset_id
    assert (tmp_path / "exports/orders.parquet").is_file()
    with inspect_database(config) as connection:
        assert connection.execute(
            "SELECT asset_id FROM assquack_meta.runs WHERE run_id = ?", [result.run_id]
        ).fetchone() == (expected.asset_id,)


def test_resolve_failure_releases_destinations_after_state_save_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    released = []
    failure = RuntimeError("resolve state save failed")
    original_save = MetadataRepository.save_run_state

    @contextmanager
    def destinations(*args, **kwargs):
        try:
            yield ()
        finally:
            released.append(True)

    def save(repository, run):
        if run.resolve_status == "success":
            raise failure
        original_save(repository, run)

    @asset("main.progress")
    def source():
        raise AssertionError("Resolve failed before the producer could run")

    monkeypatch.setattr(resolve_module, "resolve_exports", destinations)
    monkeypatch.setattr(MetadataRepository, "save_run_state", save)
    with pytest.raises(RuntimeError) as raised:
        asyncio.run(source(assquack_config=AssquackConfig(home=tmp_path)))
    assert raised.value is failure
    assert released == [True]
    assert not ACTIVE_RUNS


def test_destination_release_does_not_replace_the_producer_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    failure = ValueError("producer failed")

    @contextmanager
    def destinations(*args, **kwargs):
        try:
            yield ()
        finally:
            raise RuntimeError("destination release failed")

    @asset("main.progress")
    def source():
        raise failure

    monkeypatch.setattr(resolve_module, "resolve_exports", destinations)
    with pytest.raises(ValueError) as raised:
        asyncio.run(source(assquack_config=AssquackConfig(home=tmp_path)))
    assert raised.value is failure
    assert not ACTIVE_RUNS


def test_failure_state_save_does_not_replace_the_producer_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    failure = ValueError("producer failed")
    original_save = MetadataRepository.save_run_state

    def save(repository, run):
        if run.outcome == "failed":
            raise RuntimeError("failure state save failed")
        original_save(repository, run)

    @asset("main.progress")
    def source():
        raise failure

    monkeypatch.setattr(MetadataRepository, "save_run_state", save)
    with pytest.raises(ValueError) as raised:
        asyncio.run(source(assquack_config=AssquackConfig(home=tmp_path)))
    assert raised.value is failure
    assert not ACTIVE_RUNS


def test_final_state_save_failure_does_not_report_a_successful_call(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    config = AssquackConfig(home=tmp_path, exports=None)
    failure = RuntimeError("final state save failed")
    original_save = MetadataRepository.save_run_state

    def save(repository, run):
        if run.outcome == "success":
            raise failure
        original_save(repository, run)

    @asset("main.progress")
    def source():
        return [{"id": 1}]

    monkeypatch.setattr(MetadataRepository, "save_run_state", save)
    with pytest.raises(RuntimeError) as raised:
        asyncio.run(source(assquack_config=config))
    assert raised.value is failure
    with inspect_database(config) as connection:
        assert connection.execute(
            "SELECT publish_status, outcome, error_type, error_message, "
            "export_status, failed_stage "
            "FROM assquack_meta.runs"
        ).fetchone() == (
            "success",
            "failed",
            "RuntimeError",
            str(failure),
            "skipped",
            None,
        )
        assert connection.execute("SELECT id FROM main.progress").fetchall() == [(1,)]
    assert not ACTIVE_RUNS


def test_config_uses_provider_with_a_runtime_lock(tmp_path: Path):
    class Provider:
        def __init__(self):
            self.lock = threading.Lock()

        async def load(self):
            pass

        def get(self, name):
            return None

    destination = FileSystemDestination(base_uri=str(tmp_path / "exports"))
    provider = Provider()
    destination._credential_provider = provider
    config = AssquackConfig(home=tmp_path, exports=destination)

    @asset("main.progress", export="progress.parquet")
    def source():
        return [{"id": 1}]

    result = asyncio.run(source(assquack_config=config))
    assert result.row_count == 1
    assert (tmp_path / "exports/progress.parquet").is_file()
    assert destination._credential_provider is provider


def test_rollback_failure_does_not_replace_the_original_transaction_error():
    failure = ValueError("publication failed")
    statements = []

    def execute(sql):
        statements.append(sql)
        if sql == "ROLLBACK":
            raise RuntimeError("rollback failed")

    connection = Mock()
    connection.execute.side_effect = execute

    with pytest.raises(ValueError) as raised, transaction(connection):
        raise failure
    assert raised.value is failure
    assert statements == ["BEGIN TRANSACTION", "ROLLBACK"]


def test_cached_export_cannot_label_a_concurrent_replacement_as_the_old_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    config = AssquackConfig(home=tmp_path, exports=str(tmp_path / "exports"))

    @asset("main.shared", export="shared.parquet")
    def original():
        return [{"id": 1}]

    @asset("main.shared")
    def replacement():
        return [{"id": 2}]

    first = asyncio.run(original(assquack_config=config))
    with inspect_database(config) as connection:
        connection.execute("DELETE FROM assquack_meta.exports")

    original_resolve = resolve_module.resolve_exports
    published = threading.Event()
    errors = []
    workers = []

    def publish_replacement():
        try:
            asyncio.run(replacement(assquack_config=config))
        except BaseException as error:
            errors.append(error)
        finally:
            published.set()

    @contextmanager
    def resolve_with_competing_publication(*args, **kwargs):
        if not workers:
            worker = threading.Thread(target=publish_replacement)
            workers.append(worker)
            worker.start()
            # A protected cache hit blocks replacement until its export is done.
            published.wait(0.2)
        with original_resolve(*args, **kwargs) as exports:
            yield exports

    monkeypatch.setattr(
        resolve_module, "resolve_exports", resolve_with_competing_publication
    )
    try:
        cached = asyncio.run(original.cache_first()(assquack_config=config))
    finally:
        for worker in workers:
            worker.join(5)
    assert all(not worker.is_alive() for worker in workers)
    assert not errors
    assert cached.cached and cached.run_id == first.run_id
    assert duckdb.read_parquet(str(tmp_path / "exports/shared.parquet")).project(
        "id, _qa_run_id"
    ).fetchall() == [(1, first.run_id)]


@pytest.mark.parametrize("empty", [False, True])
def test_cache_requires_the_requested_asset_to_still_occupy_the_table(
    tmp_path: Path, empty
):
    calls = []
    config = AssquackConfig(home=tmp_path, exports=None)

    @asset("main.shared", name="shared")
    def source(value: int):
        calls.append(value)
        return [] if empty else [{"id": value}]

    first = asyncio.run(source.cache_first()(1, assquack_config=config))
    second = asyncio.run(source.cache_first()(2, assquack_config=config))
    refreshed = asyncio.run(source.cache_first()(1, assquack_config=config))
    assert calls == [1, 2, 1]
    assert not refreshed.cached
    assert refreshed.run_id not in (first.run_id, second.run_id)


def test_retained_result_export_records_the_run_that_now_occupies_the_table(
    tmp_path: Path,
):
    config = AssquackConfig(home=tmp_path, exports=None)

    @asset("main.shared")
    def source(value: int):
        return [{"id": value}] * value

    old = asyncio.run(source(1, assquack_config=config))
    current = asyncio.run(source(2, assquack_config=config))
    path = old.export("retained.parquet")
    assert duckdb.read_parquet(path).project("id").fetchall() == [(2,), (2,)]
    with inspect_database(config) as connection:
        assert connection.execute(
            "SELECT asset_id, run_id, row_count FROM assquack_meta.exports"
        ).fetchone() == (current.asset_id, current.run_id, 2)


def test_publication_pointer_failure_rolls_back_table_and_success_metadata(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    config = AssquackConfig(home=tmp_path, exports=None)
    value = 1

    @asset("main.progress")
    def source():
        return [{"id": value}]

    first = asyncio.run(source(assquack_config=config))
    original_record = MetadataRepository.record_publication

    def fail_after_pointer_write(repository, table, run_id):
        original_record(repository, table, run_id)
        raise RuntimeError("pointer save failed")

    value = 2
    monkeypatch.setattr(
        MetadataRepository, "record_publication", fail_after_pointer_write
    )
    with pytest.raises(RuntimeError, match="pointer save failed"):
        asyncio.run(source(assquack_config=config))
    with inspect_database(config) as connection:
        assert connection.execute("SELECT id FROM main.progress").fetchall() == [(1,)]
        assert connection.execute(
            "SELECT run_id FROM assquack_meta.current_publications"
        ).fetchone() == (first.run_id,)
        assert connection.execute(
            "SELECT status, publish_status, materialized_at FROM assquack_meta.runs "
            "WHERE run_id != ?",
            [first.run_id],
        ).fetchone() == ("failed", "failed", None)
