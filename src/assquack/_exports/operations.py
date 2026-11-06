from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING

import duckdb

from assquack._errors import ExportError
from assquack._exports.destinations import ExportDestination, FileSystemDestination
from assquack._exports.models import ExportTarget
from assquack._exports.resolution import resolve_exports
from assquack._storage.database import database_catalog, database_operation
from assquack._storage.metadata import MetadataRepository
from assquack._storage.tables import TableReference

if TYPE_CHECKING:
    from assquack._result import AssquackResult


def export_result(
    result: AssquackResult, destination: str | ExportDestination
) -> str | list[str]:
    """Resolve a manual target, then coordinate writing and manifest recording."""
    declaration = destination if isinstance(destination, str) else None
    configured = (
        FileSystemDestination(base_uri=str(result.config.home / "exports"))
        if isinstance(destination, str)
        else destination
    )
    with resolve_exports(declaration, result.asset_name, [configured]) as exports:
        with database_operation(result.config) as connection:
            catalog = database_catalog(result.config)
            metadata = MetadataRepository(connection, catalog)
            table = replace(result.table, catalog_name=catalog)
            publication = metadata.find_current_publication(table)
            if publication is None:
                raise ExportError(
                    "The table has no current Assquack publication to export."
                )
            write_exports(
                connection,
                metadata,
                table,
                exports,
                asset_id=publication.asset_id,
                run_id=publication.run_id,
                row_count=publication.row_count,
            )
        uris = [target.resolved_uri for _, target in exports]
        return uris[0] if len(uris) == 1 else uris


def write_exports(
    connection: duckdb.DuckDBPyConnection,
    metadata: MetadataRepository,
    source: TableReference,
    exports: tuple[tuple[ExportDestination, ExportTarget], ...],
    *,
    asset_id: str,
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
        destination.export(connection, source, target)
        metadata.record_export(
            run_id=run_id,
            asset_id=asset_id,
            alias=target.alias,
            uri=target.logical_uri,
            resolved_uri=target.resolved_uri,
            destination_fingerprint=target.destination_fingerprint,
            row_count=row_count,
            format_name=target.format,
            role=target.role,
            options=target.options,
        )
