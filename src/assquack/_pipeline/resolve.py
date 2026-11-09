from __future__ import annotations

import hashlib
import inspect
import json
import warnings
from collections.abc import Mapping
from contextlib import ExitStack
from dataclasses import dataclass, replace
from datetime import timedelta

from assquack._errors import MissingAssetSchemaWarning
from assquack._exports.destinations import ExportDestination
from assquack._exports.models import ExportTarget
from assquack._exports.resolution import resolve_exports
from assquack._options import AssetMode, validate_options, validate_table
from assquack._request import AssquackRequest
from assquack._run import ACTIVE_RUNS, AssquackRun
from assquack._storage.cache import find_cached_run
from assquack._storage.database import database_catalog, database_operation
from assquack._storage.metadata import MetadataRepository
from assquack._storage.records import AssetRecord
from assquack._storage.sql import sanitize_identifier
from assquack._storage.tables import TableReference
from assquack._templates import AssetTemplateFormatter


@dataclass(frozen=True, slots=True)
class ResolvedAsset:
    asset_id: str
    asset_name: str
    asset_signature: str
    table: TableReference
    export: str | Mapping[str, str] | None
    mode: AssetMode
    use_cache: bool
    cache_expiration: timedelta | None


def resolve_request(request: AssquackRequest) -> ResolvedAsset:
    """Resolve names, exports and identity from the captured call arguments."""
    validate_options(request.table_template, request.mode, request.cache_expiration)
    arguments, keywords = request.execution_arguments()
    signature = inspect.signature(request.fn)
    bound = signature.bind(*arguments, **keywords)
    formatter = AssetTemplateFormatter(bound.args, bound)
    name = formatter.format(request.name_template or request.table_template)
    table_name = formatter.format(request.table_template)
    export = request.export_template
    if isinstance(export, str):
        export = formatter.format(export)
    elif export is not None:
        export = {"uri": formatter.format(export["uri"]), "format": export["format"]}
    identity = json.dumps(
        {
            "function": f"{request.fn.__module__}.{request.fn.__qualname__}",
            "name": name,
            "arguments": bound.arguments,
        },
        default=repr,
        sort_keys=True,
        separators=(",", ":"),
    )
    return ResolvedAsset(
        hashlib.sha256(identity.encode()).hexdigest(),
        name,
        str(signature),
        resolve_table(table_name, name),
        export,
        request.mode,
        request.use_cache,
        request.cache_expiration,
    )


def resolve(
    run: AssquackRun,
    resources: ExitStack,
) -> tuple[tuple[ExportDestination, ExportTarget], ...]:
    """Resolve identifiers, recover metadata, check the cache and prepare exports.

    The caller owns resource release and stage status. A cache hit retains the
    database lock in resources so its table cannot change before export finishes.
    """
    run.asset = resolve_request(run.request)
    run.identify()
    run.catalog = database_catalog(run.config)
    run.asset = replace(
        run.resolved, table=replace(run.resolved.table, catalog_name=run.catalog)
    )
    asset = run.resolved
    with database_operation(run.config) as connection:
        run.metadata = MetadataRepository(connection, run.catalog)
        run.repository.save_asset(
            AssetRecord(
                asset.asset_id,
                asset.asset_name,
                asset.asset_signature,
                asset.table.schema_name,
                asset.table.table_name,
                asset.mode,
            )
        )
        run.repository.recover_interrupted_runs(
            asset.asset_id,
            tuple(
                active_id
                for catalog, active_id in ACTIVE_RUNS
                if catalog == run.catalog
            ),
        )
        if asset.use_cache:
            run.cached = find_cached_run(
                connection,
                run.repository,
                asset_id=asset.asset_id,
                table=asset.table,
                expiration=asset.cache_expiration,
            )
        if run.cached is None:
            run.repository.start_run(run.id, asset.asset_id, run.runtime)
            run.persisted = True
            ACTIVE_RUNS.add((run.catalog, run.id))
            run.candidate_table = TableReference(
                "assquack_stage", f"shaped_{run.id}", run.catalog
            )
        else:
            # Keep this table unchanged from cache selection through exports.
            # Cached calls do no awaited producer work while this lock is held.
            resources.enter_context(database_operation(run.config))
            run.row_count = run.cached.row_count
    outputs = resolve_exports(
        asset.export,
        asset.asset_name,
        run.config.export_destination if asset.export is not None else None,
    )
    exports = resources.enter_context(outputs)
    return exports


def resolve_table(table_name: str, asset_name: str) -> TableReference:
    validate_table(table_name)
    if "." in table_name:
        schema, table = table_name.split(".", maxsplit=1)
    else:
        warnings.warn(
            f"Asset {asset_name!r} does not define a DuckDB schema; using 'main'. "
            "Set table='schema.table' to define one explicitly.",
            MissingAssetSchemaWarning,
            stacklevel=3,
        )
        schema, table = "main", table_name
    return TableReference(sanitize_identifier(schema), sanitize_identifier(table))
