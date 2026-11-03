import duckdb
import pytest

from assquack._materialization import pipeline
from assquack._storage import database


@pytest.fixture(autouse=True)
def isolated_default_engine():
    """Each test represents a fresh process, including its engine settings."""
    original = duckdb.default_connection()
    connection = duckdb.connect()
    duckdb.set_default_connection(connection)
    database._connection = None
    database._settings = None
    database._catalogs.clear()
    database._bootstrapped.clear()
    pipeline._ACTIVE_RUNS.clear()
    try:
        yield connection
    finally:
        database._connection = None
        database._settings = None
        database._catalogs.clear()
        database._bootstrapped.clear()
        pipeline._ACTIVE_RUNS.clear()
        duckdb.set_default_connection(original)
        connection.close()
