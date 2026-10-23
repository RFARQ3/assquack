from __future__ import annotations

from typing import Protocol

import duckdb

from assquack._config import ExportsConfig
from assquack._errors import ExportError
from assquack._exports.models import ExportTarget
from assquack._exports.parquet import (
    CsvExportWriter,
    JsonExportWriter,
    ParquetExportWriter,
)
from assquack._storage.tables import TableReference


class ExportWriter(Protocol):
    def write(
        self,
        connection: duckdb.DuckDBPyConnection,
        source: TableReference,
        target: ExportTarget,
    ) -> None: ...


def export_writer(format_name: str, config: ExportsConfig) -> ExportWriter:
    writers = {
        "parquet": ParquetExportWriter,
        "csv": CsvExportWriter,
        "json": JsonExportWriter,
    }
    try:
        writer = writers[format_name]
    except KeyError:
        raise ExportError(f"Unsupported export format: {format_name!r}.") from None
    return writer(config)
