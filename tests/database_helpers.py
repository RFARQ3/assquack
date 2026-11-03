"""Test-only inspection of the attached destination without closing its engine."""

from collections.abc import Generator
from contextlib import contextmanager

import duckdb

from assquack import AssquackConfig
from assquack._storage.database import database_catalog, database_operation
from assquack._storage.sql import quote_identifier


@contextmanager
def inspect_database(
    config: AssquackConfig, *, bootstrap: bool = False
) -> Generator[duckdb.DuckDBPyConnection, None, None]:
    # This scope is only for immediate synchronous assertions, never returned
    # relations or application code. Production never changes USE per task.
    with database_operation(config, bootstrap=bootstrap) as connection:
        row = connection.execute("SELECT current_database()").fetchone()
        assert row is not None
        previous = row[0]
        connection.execute(f"USE {quote_identifier(database_catalog(config))}")
        try:
            yield connection
        finally:
            connection.execute(f"USE {quote_identifier(previous)}")
