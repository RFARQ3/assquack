from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

import duckdb

from assquack._config import AssquackConfig
from assquack._storage.sql import quote_identifier, quote_literal
from assquack._storage.tables import bootstrap_database


def open_database(config: AssquackConfig) -> duckdb.DuckDBPyConnection:
    database_path = config.resolved_database_path
    database_path.parent.mkdir(parents=True, exist_ok=True)
    config.resolved_temp_directory.mkdir(parents=True, exist_ok=True)

    connection = duckdb.connect(str(database_path))
    connection.execute(f"SET threads TO {config.duckdb.threads}")
    connection.execute(
        f"SET memory_limit TO {quote_literal(config.duckdb.memory_limit)}"
    )
    connection.execute(
        f"SET temp_directory TO {quote_literal(str(config.resolved_temp_directory))}"
    )

    for extension in config.duckdb.extensions:
        # Prototype assumes configured extensions are already installed or bundled.
        connection.execute(f"LOAD {quote_identifier(extension)}")

    bootstrap_database(connection)
    return connection


@contextmanager
def transaction(
    connection: duckdb.DuckDBPyConnection,
) -> Iterator[duckdb.DuckDBPyConnection]:
    connection.execute("BEGIN TRANSACTION")
    try:
        yield connection
    except BaseException:
        connection.execute("ROLLBACK")
        raise
    else:
        connection.execute("COMMIT")
