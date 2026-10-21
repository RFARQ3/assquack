from __future__ import annotations

import inspect
from time import perf_counter
from uuid import uuid4

from assquack._cache import find_cached_run
from assquack._config import AssquackConfig
from assquack._errors import ExportError
from assquack._exports.parquet import ParquetExportWriter
from assquack._exports.targets import parse_export_target
from assquack._materialization.commit import CommitStrategy, ReplaceCommitStrategy
from assquack._materialization.fragments import (
    NDJSONFragmentWriter,
    fragment_run_directory,
)
from assquack._materialization.inference import (
    InferenceState,
    merge_json_structures,
    resolve_reader_projections,
)
from assquack._materialization.models import (
    MaterializationRequest,
    StagingTables,
)
from assquack._materialization.normalize import normalize
from assquack._materialization.observation import observe_fragment
from assquack._materialization.staging import (
    create_shaped_staging,
    describe_fragments,
    drop_shaped_staging,
)
from assquack._result import AssquackResult
from assquack._storage.database import open_database
from assquack._storage.locking import writer_lock
from assquack._storage.metadata import MetadataRepository
from assquack._storage.models import AssetRecord
from assquack._storage.tables import TableReference


async def materialize(
    request: MaterializationRequest,
    config: AssquackConfig,
    strategy: CommitStrategy | None = None,
) -> AssquackResult:
    """Normalize, stage, observe, shape, and publish one asset run."""

    started = perf_counter()
    strategy = strategy or ReplaceCommitStrategy()

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
            )
        )

        if request.use_cache:
            cache_target_uri = None
            cache_target_is_valid = True
            if request.export is not None:
                try:
                    cache_target_uri = parse_export_target(
                        request.export,
                        asset_name=request.asset_name,
                        config=config,
                    ).resolved_uri
                except ExportError:
                    cache_target_is_valid = False

            cached = None
            if cache_target_is_valid:
                cached = find_cached_run(
                    connection,
                    metadata,
                    asset_id=request.asset_id,
                    table=request.table,
                    expiration=request.cache_expiration,
                    required_export_uri=cache_target_uri,
                )
            if cached is not None:
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

        run_id = str(uuid4())
        token = run_id.replace("-", "")
        staging = StagingTables(
            shaped=TableReference(
                "assquack_stage", f"shaped_{request.asset_id[:12]}_{token}"
            ),
        )
        metadata.start_run(run_id, request.asset_id)
        fragment_writer = NDJSONFragmentWriter(
            fragment_run_directory(
                config.resolved_fragment_directory,
                request.asset_name,
                run_id,
            ),
            max_rows=config.ingestion.fragment_max_rows,
        )

        committed = False
        row_count = 0
        try:
            value = request.fn(*request.arguments, **request.keyword_arguments)
            if inspect.isawaitable(value):
                value = await value

            async for chunk in normalize(value, chunk_size=config.chunk_size):
                row_count += fragment_writer.write_rows(chunk.rows)

            fragments = fragment_writer.finish()
            inference = InferenceState()
            for fragment in fragments:
                inference.observe(observe_fragment(connection, fragment))

            previous_schema = metadata.find_latest_schema(request.asset_id)
            previous_json_structure = metadata.find_latest_json_structure(
                request.asset_id
            )
            observed_schema = describe_fragments(connection, fragments)
            projections = resolve_reader_projections(
                observed_schema,
                previous_schema,
            )
            metadata.record_schema(
                request.asset_id,
                run_id,
                [item.as_dict() for item in projections],
                merge_json_structures(
                    previous_json_structure or {},
                    inference.json_structure,
                ),
                inference.observations(),
            )
            create_shaped_staging(
                connection,
                staging.shaped,
                fragments,
                projections,
                [name for name, _ in observed_schema],
                run_id,
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
            drop_shaped_staging(connection, staging.shaped)
        except BaseException as error:
            fragment_writer.abort()
            if not committed:
                metadata.mark_run_failed(
                    run_id,
                    str(error),
                    int((perf_counter() - started) * 1_000),
                )
            connection.close()
            raise

        try:
            if request.export is not None:
                if not config.exports.enabled:
                    raise ExportError(
                        f"Export target {request.export!r} was declared, but exports "
                        "are disabled. Set ASSQUACK_EXPORTS__ENABLED=true, or pass "
                        "AssquackConfig(exports=ExportsConfig(enabled=True))."
                    )

                target = parse_export_target(
                    request.export,
                    asset_name=request.asset_name,
                    config=config,
                )
                ParquetExportWriter().write(connection, request.table, target)
                metadata.record_export(
                    run_id=run_id,
                    asset_id=request.asset_id,
                    alias=target.alias,
                    uri=target.logical_uri,
                    resolved_uri=target.resolved_uri,
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
