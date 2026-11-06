from __future__ import annotations

import inspect
import logging
from decimal import Decimal
from math import ceil

from assquack._run import AssquackRun
from assquack._storage.database import database_operation
from assquack._storage.fragment_writer import (
    NDJSONFragmentWriter,
    fragment_run_directory,
    write_fragments,
)

_LOGGER = logging.getLogger(__name__)


async def materialize(run: AssquackRun) -> None:
    """Call the producer and write its returned or yielded data to fragments."""
    writer = None
    try:
        writer = NDJSONFragmentWriter(
            fragment_run_directory(
                run.config.resolved_fragment_directory, run.resolved.asset_name, run.id
            ),
            target_bytes=ceil(
                Decimal(str(run.config.ingestion.fragment_target_mb)) * 1_000_000
            ),
        )
        with database_operation(run.config):
            run.repository.claim_run_fragments(run.id)

        # Producer code may await or call other assets. No database lock spans it.
        arguments, keywords = run.request.execution_arguments()
        value = run.request.fn(*arguments, **keywords)
        if inspect.isawaitable(value):
            value = await value
        run.row_count = await write_fragments(value, writer, run.config)
        run.fragments = writer.finish()
    finally:
        if writer is not None:
            try:
                writer.abort()
            except Exception:
                _LOGGER.warning(
                    "Could not close fragments for run %s", run.id, exc_info=True
                )
