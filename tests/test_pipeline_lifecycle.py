from __future__ import annotations

import asyncio
import traceback
from pathlib import Path

import pytest

import assquack._pipeline as pipeline
from assquack import AssquackConfig, asset
from assquack._run import ACTIVE_RUNS
from assquack._storage.metadata import MetadataRepository
from database_helpers import inspect_database


@pytest.mark.parametrize(
    "failed_stage", ["resolve", "materialize", "stage", "publish", "export"]
)
def test_pipeline_records_failure_when_a_stage_helper_raises(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failed_stage: str
):
    config = AssquackConfig(home=tmp_path, exports=str(tmp_path / "exports"))
    failure = ValueError(f"{failed_stage} helper failed")
    original_resolve = pipeline.resolve

    @asset("main.lifecycle", export="lifecycle.parquet")
    def source():
        return [{"id": 1}]

    def fail(run, *args):
        if failed_stage == "resolve":
            original_resolve(run, *args)
        with inspect_database(config) as connection:
            assert connection.execute(
                "SELECT outcome FROM assquack_meta.runs"
            ).fetchone() == ("running",)
        raise failure

    async def fail_materialize(run):
        fail(run)

    monkeypatch.setattr(
        pipeline,
        failed_stage,
        fail_materialize if failed_stage == "materialize" else fail,
    )
    with pytest.raises(ValueError) as raised:
        asyncio.run(source(assquack_config=config))
    assert raised.value is failure
    assert any(f"Assquack {failed_stage} failed" in note for note in failure.__notes__)
    assert "fail" in [
        frame.name for frame in traceback.extract_tb(failure.__traceback__)
    ]
    with inspect_database(config) as connection:
        row = connection.execute(
            "SELECT export_status, outcome, failed_stage, error_type "
            "FROM assquack_meta.runs"
        ).fetchone()
        assert row == (
            "failed" if failed_stage == "export" else "unstarted",
            "failed",
            failed_stage,
            "ValueError",
        )
        assert connection.execute(
            "SELECT count(*) FROM information_schema.tables "
            "WHERE table_schema = 'assquack_stage'"
        ).fetchone() == (0,)
    assert not ACTIVE_RUNS


def test_publish_success_save_failure_rolls_back_the_table_and_publication(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    config = AssquackConfig(home=tmp_path, exports=None)
    value = 1

    @asset("main.lifecycle")
    def source():
        return [{"id": value}]

    first = asyncio.run(source(assquack_config=config))
    failure = RuntimeError("publish success save failed")
    original_save = MetadataRepository.mark_run_successful

    def save(repository, run_id, **kwargs):
        original_save(repository, run_id, **kwargs)
        raise failure

    value = 2
    monkeypatch.setattr(MetadataRepository, "mark_run_successful", save)
    with pytest.raises(RuntimeError) as raised:
        asyncio.run(source(assquack_config=config))
    assert raised.value is failure
    with inspect_database(config) as connection:
        assert connection.execute("SELECT id FROM main.lifecycle").fetchall() == [(1,)]
        assert connection.execute(
            "SELECT run_id FROM assquack_meta.current_publications"
        ).fetchone() == (first.run_id,)
        assert connection.execute(
            "SELECT status, failed_stage, materialized_at "
            "FROM assquack_meta.runs WHERE run_id != ?",
            [first.run_id],
        ).fetchone() == ("failed", "publish", None)
    assert not ACTIVE_RUNS


def test_pipeline_records_and_reraises_cancellation_from_materialize(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    config = AssquackConfig(home=tmp_path, exports=None)
    cancellation = asyncio.CancelledError("caller cancelled")

    @asset("main.lifecycle")
    def source():
        pytest.fail("The substituted materialize helper must raise first")

    async def cancel(run):
        raise cancellation

    monkeypatch.setattr(pipeline, "materialize", cancel)
    with pytest.raises(asyncio.CancelledError) as raised:
        asyncio.run(source(assquack_config=config))
    assert raised.value is cancellation
    assert any("Assquack materialize failed" in note for note in cancellation.__notes__)
    with inspect_database(config) as connection:
        assert connection.execute(
            "SELECT status, failed_stage, error_type, outcome FROM assquack_meta.runs"
        ).fetchone() == ("failed", "materialize", "CancelledError", "failed")
    assert not ACTIVE_RUNS
