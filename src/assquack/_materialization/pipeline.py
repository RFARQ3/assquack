from __future__ import annotations

import hashlib
import inspect
import logging
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
)
from assquack._materialization.json_reader import JsonReadRecovery
from assquack._materialization.models import (
    MaterializationRequest,
    StagingTables,
)
from assquack._materialization.normalize import normalize
from assquack._materialization.retention import prune_fragments
from assquack._materialization.schema import replace_schema_strategy
from assquack._materialization.staging import (
    create_shaped_staging,
    drop_shaped_staging,
)
from assquack._result import AssquackResult
from assquack._storage.database import open_database
from assquack._storage.locking import writer_lock
from assquack._storage.metadata import MetadataRepository
from assquack._storage.models import AssetRecord
from assquack._storage.tables import TableReference

_LOGGER = logging.getLogger(__name__)


async def materialize(
    request: MaterializationRequest,
    config: AssquackConfig,
) -> AssquackResult:
    """Capture fragments, infer the current schema, and publish one asset run."""

    started = perf_counter()
    strategy = commit_strategy(request.mode)
    with resolve_exports(
        request.export, request.asset_name, config.export_destinations
    ) as exports:
        async with writer_lock(config.resolved_database_path):
            connection = open_database(config)
            metadata = MetadataRepository(connection)
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

            # Owning the database lock proves earlier attempts cannot still be writing.
            metadata.recover_interrupted_runs(request.asset_id)
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
                    try:
                        _write_exports(
                            connection,
                            metadata,
                            request,
                            exports,
                            run_id=cached.run_id,
                            row_count=cached.row_count,
                            skip_completed=True,
                        )
                    finally:
                        connection.close()
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
            staging = StagingTables(
                shaped=TableReference("assquack_stage", f"shaped_{run_id}"),
            )
            # A duplicate must fail before capture without changing the existing run.
            try:
                metadata.start_run(run_id, request.asset_id, runtime)
            except BaseException:
                connection.close()
                raise

            fragment_writer = None
            committed = False
            row_count = 0
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
                metadata.claim_run_fragments(run_id)
                value = request.fn(*request.arguments, **request.keyword_arguments)
                if inspect.isawaitable(value):
                    value = await value

                async for chunk in normalize(value, chunk_size=config.chunk_size):
                    row_count += fragment_writer.write_rows(chunk.rows)

                fragments = fragment_writer.finish()
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

                duration_ms = int((perf_counter() - started) * 1_000)
                strategy.publish(
                    connection,
                    metadata,
                    target=request.table,
                    staging=staging,
                    run_id=run_id,
                    row_count=row_count,
                    duration_ms=duration_ms,
                )
                committed = True
            except BaseException as error:
                if fragment_writer is not None:
                    try:
                        fragment_writer.abort()
                    except Exception:
                        _LOGGER.warning(
                            "Could not close fragments for run %s",
                            run_id,
                            exc_info=True,
                        )
                if not committed:
                    metadata.mark_run_failed(
                        run_id,
                        str(error),
                        int((perf_counter() - started) * 1_000),
                    )
                prune_fragments(
                    metadata,
                    config.resolved_fragment_directory,
                    request.asset_id,
                    request.asset_name,
                )
                connection.close()
                raise

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
            try:
                _write_exports(
                    connection,
                    metadata,
                    request,
                    exports,
                    run_id=run_id,
                    row_count=row_count,
                )

                return AssquackResult(
                    config=config,
                    asset_id=request.asset_id,
                    asset_name=request.asset_name,
                    run_id=run_id,
                    table=request.table,
                    row_count=row_count,
                )
            finally:
                connection.close()


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
