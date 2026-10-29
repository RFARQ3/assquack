from __future__ import annotations

import weakref
from tempfile import TemporaryDirectory
from typing import Any, Self

import duckdb


class AssquackQueryResult:
    """An eagerly evaluated DuckDB result, independent of its source databases.

    Use as a context manager or call close() to release its table and spill files.
    Returning or yielding it from an asset transfers cleanup to materialization.
    """

    def __init__(
        self,
        connection: duckdb.DuckDBPyConnection,
        directory: TemporaryDirectory[str],
    ) -> None:
        self._connection = connection
        self._cleanup = weakref.finalize(self, _close_result, connection, directory)

    @property
    def relation(self) -> duckdb.DuckDBPyRelation:
        """Access the native relation while this result remains open."""
        return self._connection.table("temp.main.assquack_query_result")

    def fetchall(self) -> list[tuple[Any, ...]]:
        return self.relation.fetchall()

    def close(self) -> None:
        self._cleanup()

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *args: object) -> None:
        self.close()


def _close_result(
    connection: duckdb.DuckDBPyConnection, directory: TemporaryDirectory[str]
) -> None:
    try:
        connection.close()
    finally:
        directory.cleanup()
