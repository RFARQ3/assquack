import duckdb

from assquack._errors import ExportError
from assquack._exports.models import ExportTarget
from assquack._storage.sql import quote_table
from assquack._storage.tables import TableReference

_COPY_OPTIONS: dict[str, tuple[dict[str, object], str]] = {
    "parquet": ({}, "FORMAT parquet"),
    "csv": ({"header": True}, "FORMAT csv, HEADER true"),
    "json": ({"array": False}, "FORMAT json, ARRAY false"),
}


class DuckDBFileWriter:
    """Serialize one table through DuckDB with the supported format options."""

    def write(
        self,
        connection: duckdb.DuckDBPyConnection,
        source: TableReference,
        target: ExportTarget,
        uri: str,
    ) -> None:
        try:
            expected, options = _COPY_OPTIONS[target.format]
        except KeyError:
            raise ExportError("Unsupported export format.") from None
        if target.options != expected:
            raise ExportError("Custom export options are not implemented.")
        table = quote_table(source.schema_name, source.table_name)
        connection.execute(
            f"COPY (SELECT * FROM {table}) TO $uri ({options})",
            {"uri": uri},
        )
