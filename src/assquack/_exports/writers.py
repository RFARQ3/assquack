from __future__ import annotations

from typing import Protocol

import duckdb

from assquack._exports.models import ExportTarget
from assquack._storage.tables import TableReference


class ExportWriter(Protocol):
    def write(
        self,
        connection: duckdb.DuckDBPyConnection,
        source: TableReference,
        target: ExportTarget,
    ) -> None: ...
