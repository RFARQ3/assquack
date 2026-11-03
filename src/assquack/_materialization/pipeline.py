from __future__ import annotations

import hashlib
import inspect
import logging
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
from assquack._materialization.commit import commit_strategy
from assquack._materialization.fragments import (
    NDJSONFragmentWriter,
    fragment_run_directory,
    write_fragments,
)
from assquack._materialization.json_reader import JsonReadRecovery
from assquack._materialization.models import (
    MaterializationRequest,
    StagingTables,
)
from assquack._materialization.retention import prune_fragments
from assquack._materialization.schema import replace_schema_strategy
from assquack._materialization.staging import (
    create_shaped_staging,
    drop_shaped_staging,
)
from assquack._query import query_context
from assquack._result import AssquackResult
from assquack._storage.database import database_catalog, database_operation
from assquack._storage.metadata import MetadataRepository
from assquack._storage.models import AssetRecord
from assquack._storage.tables import TableReference

_LOGGER = logging.getLogger(__name__)
# Capture runs remain live while user code awaits or invokes another asset.
# Access is protected by database_operation, including across OS threads.
_ACTIVE_RUNS: set[tuple[str, str]] = set()


async def materialize(
    request: MaterializationRequest,
    config: AssquackConfig,
) -> AssquackResult:
    """Capture outside the database lock, then publish one destination atomically."""
    started = perf_counter()
    strategy = commit_strategy(request.mode)
    with resolve_exports(
        request.export, request.asset_name, config.export_destinations
    ) as exports:
        with database_operation(config) as connection:
            catalog = database_catalog(config)
            request = replace(
                request, table=replace(request.table, catalog_name=catalog)
            )
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
            if request.use_cache:
                cached = find_cached_run(
                    connection,
                    metadata,
                    asset_id=request.asset_id,
                    table=request.table,
                    expiration=request.cache_expiration,
                )
                if cached is not None:
                    _write_exports(
                        connection,
                        metadata,
                        request,
                        exports,
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

            runtime = datetime.now(UTC)
            run_id = hashlib.md5(
                f"{request.asset_id}:{runtime.isoformat()}".encode()
            ).hexdigest()
            metadata.start_run(run_id, request.asset_id, runtime)
            _ACTIVE_RUNS.add((catalog, run_id))

        staging = StagingTables(
            shaped=TableReference("assquack_stage", f"shaped_{run_id}", catalog),
        )
        fragment_writer = None
        committed = False
        try:
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

            # No transaction or lock spans user code, including async generators.
            # Nested assets can publish before this source resumes capture.
            with query_context(config):
                value = request.fn(*request.arguments, **request.keyword_arguments)
                if inspect.isawaitable(value):
                    value = await value
                row_count = await write_fragments(value, fragment_writer, config)
            fragments = fragment_writer.finish()

            # Everything from inference through exports is synchronous. Other
            # tasks cannot commit or roll back any part of this publication.
            with database_operation(config) as connection:
                reader = JsonReadRecovery()
                schema_strategy = replace_schema_strategy(
                    config.schema, connection, fragment_writer.finish, reader
                )
                projections = schema_strategy.resolve()
                create_shaped_staging(
                    connection,
                    staging.shaped,
                    fragments,
                    projections,
                    run_id,
                    reader,
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
                try:
                    drop_shaped_staging(connection, staging.shaped)
                except Exception:
                    _LOGGER.warning(
                        "Could not drop staging for published run %s",
                        run_id,
                        exc_info=True,
                    )
                prune_fragments(
                    metadata,
                    config.resolved_fragment_directory,
                    request.asset_id,
                    request.asset_name,
                )
                _write_exports(
                    connection,
                    metadata,
                    request,
                    exports,
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


def _write_exports(
    connection: duckdb.DuckDBPyConnection,
    metadata: MetadataRepository,
    request: MaterializationRequest,
    exports: tuple[tuple[ExportDestination, ExportTarget], ...],
    *,
    run_id: str,
    row_count: int,
    skip_completed: bool = False,
) -> None:
    """Record each successful output so retries preserve partial progress."""
    for destination, target in exports:
        if skip_completed and metadata.has_export(
            run_id, target.resolved_uri, target.destination_fingerprint
        ):
            continue
        destination.export(connection, request.table, target)
        metadata.record_export(
            run_id=run_id,
            asset_id=request.asset_id,
            alias=target.alias,
            uri=target.logical_uri,
            resolved_uri=target.resolved_uri,
            destination_fingerprint=target.destination_fingerprint,
            row_count=row_count,
            format_name=target.format,
            role=target.role,
            options=target.options,
        )
