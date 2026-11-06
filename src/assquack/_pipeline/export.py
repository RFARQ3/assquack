from __future__ import annotations

from assquack._exports.destinations import ExportDestination
from assquack._exports.models import ExportTarget
from assquack._exports.operations import write_exports
from assquack._run import AssquackRun
from assquack._storage.database import database_operation


def export(
    run: AssquackRun, exports: tuple[tuple[ExportDestination, ExportTarget], ...]
) -> None:
    """Write requested exports and record each file that completes successfully."""
    with database_operation(run.config) as connection:
        write_exports(
            connection,
            run.repository,
            run.resolved.table,
            exports,
            asset_id=run.resolved.asset_id,
            run_id=run.cached.run_id if run.cached else run.id,
            row_count=run.row_count,
            skip_completed=run.cached is not None,
        )
