from __future__ import annotations

import hashlib
import inspect
import logging
from contextlib import contextmanager
from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal
from math import ceil
from time import perf_counter

import duckdb

from assquack._cache import find_cached_run
from assquack._config import AssquackConfig
from assquack._exports import resolve_exports
from assquack._exports.destinations import ExportDestination
from assquack._exports.models import ExportTarget
from assquack._exports.operations import write_exports
from assquack._materialization.commit import commit_strategy
from assquack._materialization.fragments import (
    NDJSONFragmentWriter,
    fragment_run_directory,
    write_fragments,
)
from assquack._materialization.json_reader import JsonReadRecovery
from assquack._materialization.models import (
    FragmentFile,
    MaterializationRequest,
    StagingTables,
)
from assquack._materialization.retention import prune_fragments
from assquack._materialization.schema import replace_schema_strategy
from assquack._materialization.staging import (
    create_shaped_staging,
    drop_shaped_staging,
)
from assquack._result import AssquackResult
from assquack._storage.database import database_catalog, database_operation
from assquack._storage.metadata import MetadataRepository
from assquack._storage.models import AssetRecord
from assquack._storage.tables import TableReference

_LOGGER = logging.getLogger(__name__)
# Track runs that are still executing so another call does not mark them failed.
# Read and update this set while holding the database_operation lock.
_ACTIVE_RUNS: set[tuple[str, str]] = set()


async def materialize(
    request: MaterializationRequest,
    config: AssquackConfig,
) -> AssquackResult:
    """Run an asset, save its data in DuckDB, write exports, and return the result.

    Reuse the existing table when the cache policy allows it. Otherwise, call
    the asset's function without holding the database lock, write its output
    to files, and build a staging table from those files. Replace the asset's
    table and record success in one transaction, then clean up and export.
    An export failure does not undo the table replacement.
    """
    started = perf_counter()
    with _resolve_execution(request, config) as (strategy, exports):
        with database_operation(config) as connection:
            request, metadata, catalog = _prepare_asset(connection, request, config)
            cached = _try_cached_result(connection, metadata, request, config, exports)
            if cached is not None:
                return cached

            run_id = _start_run(metadata, request, catalog)

        staging = StagingTables(
            shaped=TableReference("assquack_stage", f"shaped_{run_id}", catalog),
        )
        fragment_writer = None
        committed = False
        try:
            # Keep the writer here so the error handler can close it if recording
            # the run's ownership of its fragment directory fails.
            fragment_writer = NDJSONFragmentWriter(
                fragment_run_directory(
                    config.resolved_fragment_directory,
                    request.asset_name,
                    run_id,
                ),
                target_bytes=ceil(
                    Decimal(str(config.ingestion.fragment_target_mb)) * 1_000_000
                ),
            )
            with database_operation(config):
                metadata.claim_run_fragments(run_id)

            # Release the database lock while the asset's function runs. It may
            # wait for API data or call another asset that needs the database.
            row_count, fragments = await _capture_data(request, fragment_writer, config)

            # Hold the database lock from schema inference through exports.
            # These calls do not await, so another task cannot use the shared
            # connection to commit or roll back this run's transaction.
            with database_operation(config) as connection:
                _prepare_staging(
                    connection, config, fragment_writer, fragments, staging, run_id
                )
                strategy.publish(
                    connection,
                    metadata,
                    target=request.table,
                    staging=staging,
                    run_id=run_id,
                    row_count=row_count,
                    duration_ms=int((perf_counter() - started) * 1_000),
                )
                committed = True
                _cleanup(connection, metadata, request, config, staging, run_id)
                write_exports(
                    connection,
                    metadata,
                    request.table,
                    exports,
                    asset_id=request.asset_id,
                    run_id=run_id,
                    row_count=row_count,
                )
        except BaseException as error:
            if fragment_writer is not None:
                try:
                    fragment_writer.abort()
                except Exception:
                    _LOGGER.warning(
                        "Could not close fragments for run %s", run_id, exc_info=True
                    )
            with database_operation(config) as connection:
                if not committed:
                    metadata.mark_run_failed(
                        run_id,
                        str(error),
                        int((perf_counter() - started) * 1_000),
                    )
                    connection.execute(f"DROP TABLE IF EXISTS {staging.shaped.sql}")
                prune_fragments(
                    metadata,
                    config.resolved_fragment_directory,
                    request.asset_id,
                    request.asset_name,
                )
            raise
        finally:
            with database_operation(config):
                _ACTIVE_RUNS.discard((catalog, run_id))

        return AssquackResult(
            config=config,
            asset_id=request.asset_id,
            asset_name=request.asset_name,
            run_id=run_id,
            table=request.table,
            row_count=row_count,
        )


@contextmanager
def _resolve_execution(request: MaterializationRequest, config: AssquackConfig):
    """Choose how to update the asset's table and where to write its export files.

    Use the request's mode to select the table update strategy. Resolve the
    export declaration into output paths, then provide the strategy and export
    destinations to materialize().

    Table update strategies can include replacing the whole table, merging
    records, or appending rows. Assquack currently supports replacement only.
    """
    strategy = commit_strategy(request.mode)
    with resolve_exports(
        request.export,
        request.asset_name,
        config.export_destinations if request.export is not None else [],
    ) as exports:
        yield strategy, exports


def _prepare_asset(
    connection: duckdb.DuckDBPyConnection,
    request: MaterializationRequest,
    config: AssquackConfig,
):
    """Save the asset's metadata and clean up records and files from earlier runs.

    Add the configured database name to the table reference and save the asset
    definition. Mark interrupted runs as failed, leaving active runs alone.
    Delete old fragment files according to the retention rules.
    """
    catalog = database_catalog(config)
    request = replace(request, table=replace(request.table, catalog_name=catalog))
    metadata = MetadataRepository(connection, catalog)
    metadata.save_asset(
        AssetRecord(
            asset_id=request.asset_id,
            asset_name=request.asset_name,
            asset_signature=request.asset_signature,
            schema_name=request.table.schema_name,
            table_name=request.table.table_name,
            materialization_mode=request.mode,
        )
    )
    metadata.recover_interrupted_runs(
        request.asset_id,
        tuple(
            active_id
            for active_catalog, active_id in _ACTIVE_RUNS
            if active_catalog == catalog
        ),
    )
    prune_fragments(
        metadata,
        config.resolved_fragment_directory,
        request.asset_id,
        request.asset_name,
    )
    return request, metadata, catalog


def _try_cached_result(
    connection: duckdb.DuckDBPyConnection,
    metadata: MetadataRepository,
    request: MaterializationRequest,
    config: AssquackConfig,
    exports: tuple[tuple[ExportDestination, ExportTarget], ...],
):
    """Reuse an existing table if caching is enabled and the result has not expired.

    Return None if there is no usable cached result. Otherwise, write exports
    that have not been recorded as complete and return an AssquackResult
    pointing to the existing table, without calling the asset's function.
    """
    if not request.use_cache:
        return None
    cached = find_cached_run(
        connection,
        metadata,
        asset_id=request.asset_id,
        table=request.table,
        expiration=request.cache_expiration,
    )
    if cached is None:
        return None

    write_exports(
        connection,
        metadata,
        request.table,
        exports,
        asset_id=request.asset_id,
        run_id=cached.run_id,
        row_count=cached.row_count,
        skip_completed=True,
    )
    return AssquackResult(
        config=config,
        asset_id=request.asset_id,
        asset_name=request.asset_name,
        run_id=cached.run_id,
        table=request.table,
        row_count=cached.row_count,
        cached=True,
    )


def _start_run(
    metadata: MetadataRepository, request: MaterializationRequest, catalog: str
):
    """Create a run ID, record the run as running, and return its ID.

    Add it to the active runs set so another asset call does not mistake it
    for an interrupted run and mark it failed.
    """
    runtime = datetime.now(UTC)
    run_id = hashlib.md5(
        f"{request.asset_id}:{runtime.isoformat()}".encode()
    ).hexdigest()
    metadata.start_run(run_id, request.asset_id, runtime)
    _ACTIVE_RUNS.add((catalog, run_id))
    return run_id


async def _capture_data(
    request: MaterializationRequest,
    fragment_writer: NDJSONFragmentWriter,
    config: AssquackConfig,
):
    """Call the asset's function and write its output to NDJSON fragment files.

    Await the function's result if needed, then consume its returned or yielded
    data. Mark the files complete and return the row count and file list.
    """
    value = request.fn(*request.arguments, **request.keyword_arguments)
    if inspect.isawaitable(value):
        value = await value
    row_count = await write_fragments(value, fragment_writer, config)
    return row_count, fragment_writer.finish()


def _prepare_staging(
    connection: duckdb.DuckDBPyConnection,
    config: AssquackConfig,
    fragment_writer: NDJSONFragmentWriter,
    fragments: list[FragmentFile],
    staging: StagingTables,
    run_id: str,
):
    """Read this run's fragment files to determine columns and build a staging table.

    Infer column names and types, then load the data into the staging table.
    If a JSON record needs a larger reader size limit, reuse that limit for
    both schema inference and loading. Leave the existing asset table unchanged.
    """
    reader = JsonReadRecovery()
    schema_strategy = replace_schema_strategy(
        config.schema, connection, fragment_writer.finish, reader
    )
    projections = schema_strategy.resolve()
    create_shaped_staging(
        connection, staging.shaped, fragments, projections, run_id, reader
    )


def _cleanup(
    connection: duckdb.DuckDBPyConnection,
    metadata: MetadataRepository,
    request: MaterializationRequest,
    config: AssquackConfig,
    staging: StagingTables,
    run_id: str,
):
    """Drop the staging table and delete old fragment files under the retention rules.

    The asset's table has already been updated. Log cleanup failures without
    treating that successful update as a failed run.
    """
    try:
        drop_shaped_staging(connection, staging.shaped)
    except Exception:
        _LOGGER.warning(
            "Could not drop staging for published run %s", run_id, exc_info=True
        )
    prune_fragments(
        metadata,
        config.resolved_fragment_directory,
        request.asset_id,
        request.asset_name,
    )
