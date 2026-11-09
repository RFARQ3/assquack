"""Resolve, materialize, stage, publish and export one asset call."""

from __future__ import annotations

import logging
from contextlib import ExitStack

from assquack._pipeline.export import export
from assquack._pipeline.materialize import materialize
from assquack._pipeline.publish import publish
from assquack._pipeline.resolve import resolve
from assquack._pipeline.stage import drop_shaped_staging, stage
from assquack._request import AssquackRequest
from assquack._result import AssquackResult
from assquack._run import ACTIVE_RUNS, AssquackRun
from assquack._storage.database import CONNECTION_LOCK, database_operation, transaction
from assquack._storage.retention import prune_fragments

_LOGGER = logging.getLogger(__name__)


async def run_pipeline(request: AssquackRequest) -> AssquackResult:
    """Run each stage and record its progress, success or failure here.

    The work helpers do not manage stage outcomes. Publication and its success
    record commit together; a later export failure leaves that table available.
    """
    run = AssquackRun(request)
    resources = ExitStack()
    try:
        # 1. Resolve asset identity, check the cache and prepare the export destination.
        try:
            run.resolve_status = "running"
            run.save_state()
            exports = resolve(run, resources)
            run.resolve_status = "success"
            run.save_state()
        except BaseException as error:
            run.fail("resolve", error)
            raise

        # 2. Materialize the asset's data into NDJSON fragment files.
        if run.cached is None:
            try:
                run.materialize_status = "running"
                run.save_state()
                await materialize(run)
                run.materialize_status = "success"
                run.save_state()
            except BaseException as error:
                run.fail("materialize", error)
                raise
        else:
            run.materialize_status = "skipped"

        # Hold the database lock through staging (3), publication (4) and export (5).
        with database_operation(run.config) as connection:

        # 3. Stage the fragments using DuckDB's inferred columns and types.
            if run.cached is None:
                try:
                    run.stage_status = "running"
                    run.save_state()
                    stage(run)
                    run.stage_status = "success"
                    run.save_state()
                except BaseException as error:
                    run.fail("stage", error)
                    raise
            else:
                run.stage_status = "skipped"

        # 4. Publish the table and its success metadata in one transaction.
            if run.cached is None:
                try:
                    run.publish_status = "running"
                    run.save_state()
                    with transaction(connection):
                        publish(run)
                        run.publish_status = "success"
                        run.save_state()
                except BaseException as error:
                    run.fail("publish", error)
                    raise
            else:
                run.publish_status = "skipped"

        # 5. Export the publication, retaining each completed file's record.
            try:
                if exports:
                    run.export_status = "running"
                    run.save_state()
                    export(run, exports)
                    run.export_status = "success"
                else:
                    run.export_status = "skipped"
                run.save_state()
            except BaseException as error:
                run.fail("export", error)
                raise

            # Record successful completion of the overall run.
            try:
                run.outcome = "success"
                run.save_state()
            except BaseException as error:
                run.fail(None, error)
                raise

        return AssquackResult(
            config=run.config,
            asset_id=run.resolved.asset_id,
            asset_name=run.resolved.asset_name,
            run_id=run.cached.run_id if run.cached else run.id,
            table=run.resolved.table,
            row_count=run.row_count,
            cached=run.cached is not None,
        )
    finally:
        cleanup(run)
        try:
            resources.close()
        except Exception:
            _LOGGER.warning(
                "Could not release export destination for run %s",
                run.run_id,
                exc_info=True,
            )


def cleanup(run: AssquackRun) -> None:
    """Drop the candidate table and prune fragments after the run's outcome is known."""
    if run.metadata is None:
        return
    try:
        with database_operation(run.config) as connection:
            if run.candidate_table is not None:
                try:
                    drop_shaped_staging(connection, run.candidate_table)
                except Exception:
                    _LOGGER.warning(
                        "Could not drop staging for run %s", run.run_id, exc_info=True
                    )
            prune_fragments(
                run.repository,
                run.config.resolved_fragment_directory,
                run.resolved.asset_id,
                run.resolved.asset_name,
            )
    except Exception:
        _LOGGER.warning("Could not clean up run %s", run.run_id, exc_info=True)
    finally:
        if run.persisted and run.catalog is not None:
            with CONNECTION_LOCK:
                ACTIVE_RUNS.discard((run.catalog, run.id))
