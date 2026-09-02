from __future__ import annotations

from pathlib import Path
from urllib.parse import urlparse

import duckdb

from assquack._errors import ExportError
from assquack._exports.models import ExportTarget
from assquack._storage.sql import quote_literal, quote_table
from assquack._storage.tables import TableReference


class ParquetExportWriter:
    def write(
        self,
        connection: duckdb.DuckDBPyConnection,
        source: TableReference,
        target: ExportTarget,
    ) -> None:
        has_uri_scheme = "://" in target.resolved_uri
        parsed = urlparse(target.resolved_uri) if has_uri_scheme else None
        if parsed is not None and parsed.scheme != "file":
            # TODO: Route ABFSS through DuckDB's Azure extension once configured.
            raise ExportError("The transient Parquet writer supports local paths only.")

        local_path = Path(
            parsed.path if parsed is not None and parsed.scheme == "file"
            else target.resolved_uri
        )
        local_path.parent.mkdir(parents=True, exist_ok=True)
        connection.execute(
            f"""
            COPY (
                SELECT * FROM {quote_table(source.schema_name, source.table_name)}
            ) TO {quote_literal(str(local_path))} (FORMAT parquet)
            """
        )
