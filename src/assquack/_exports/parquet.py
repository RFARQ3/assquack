from __future__ import annotations

from pathlib import Path
from urllib.parse import urlparse
from urllib.request import url2pathname

import duckdb

from assquack._config import ExportsConfig
from assquack._errors import ExportError
from assquack._exports.azure import AZURE_SCHEMES, azure_export_auth
from assquack._exports.models import ExportTarget
from assquack._storage.sql import quote_table
from assquack._storage.tables import TableReference


class DuckDBExportWriter:
    copy_options = "FORMAT parquet"

    def __init__(self, config: ExportsConfig) -> None:
        self.config = config

    def write(
        self,
        connection: duckdb.DuckDBPyConnection,
        source: TableReference,
        target: ExportTarget,
    ) -> None:
        has_uri_scheme = "://" in target.resolved_uri
        parsed = urlparse(target.resolved_uri) if has_uri_scheme else None
        if parsed is not None and parsed.scheme in AZURE_SCHEMES:
            with azure_export_auth(connection, target.resolved_uri, self.config):
                self._copy(connection, source, target.resolved_uri)
            return
        if parsed is not None and parsed.scheme != "file":
            raise ExportError("Exports support local paths and Azure URIs only.")

        local_path = Path(
            url2pathname(parsed.path)
            if parsed is not None and parsed.scheme == "file"
            else target.resolved_uri
        )
        local_path.parent.mkdir(parents=True, exist_ok=True)
        self._copy(connection, source, str(local_path))

    def _copy(
        self,
        connection: duckdb.DuckDBPyConnection,
        source: TableReference,
        uri: str,
    ) -> None:
        connection.execute(
            f"""
            COPY (
                SELECT * FROM {quote_table(source.schema_name, source.table_name)}
            ) TO $uri ({self.copy_options})
            """,
            {"uri": uri},
        )


class ParquetExportWriter(DuckDBExportWriter):
    pass


class CsvExportWriter(DuckDBExportWriter):
    copy_options = "FORMAT csv, HEADER true"


class JsonExportWriter(DuckDBExportWriter):
    # MAD.Prefect writes one JSON object per line, even with a .json suffix.
    copy_options = "FORMAT json, ARRAY false"
