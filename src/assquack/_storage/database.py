from __future__ import annotations

import os
from collections.abc import Generator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from threading import RLock

import duckdb

from assquack._config import AssquackConfig
from assquack._storage.sql import identifier_key, quote_identifier, quote_literal
from assquack._storage.tables import bootstrap_database

ASSQUACK_STORAGE_VERSION = "v1.5.0"
CONNECTION_LOCK = RLock()


@dataclass(frozen=True)
class _EngineSettings:
    threads: int
    memory_limit: str
    temp_directory: Path
    extensions: tuple[str, ...]


_connection: duckdb.DuckDBPyConnection | None = None
_settings: _EngineSettings | None = None
_catalogs: dict[str, str] = {}
_bootstrapped: set[str] = set()
_default_selected = False


def default_connection() -> duckdb.DuckDBPyConnection:
    """Return the actual module default; changing it mid-lifetime is unsupported."""
    connection = duckdb.default_connection()
    if _connection is not None and connection is not _connection:
        raise RuntimeError(
            "DuckDB's default connection changed while Assquack was active."
        )
    return connection


def database_catalog(config: AssquackConfig) -> str:
    requested = identifier_key(config.attachment_name)
    path = os.path.normcase(str(config.resolved_database_path.resolve()))
    existing = _catalogs.get(path)
    return existing if existing and identifier_key(existing) == requested else requested


def connect_database(config: AssquackConfig) -> duckdb.DuckDBPyConnection:
    """Attach a destination once. The caller must never close the shared connection."""
    global _connection, _settings, _default_selected
    with CONNECTION_LOCK:
        connection = default_connection()
        requested = _EngineSettings(
            config.duckdb.threads,
            config.duckdb.memory_limit,
            config.resolved_temp_directory.resolve(),
            config.duckdb.extensions,
        )
        if _settings is None:
            requested.temp_directory.mkdir(parents=True, exist_ok=True)
            connection.execute(f"SET threads TO {requested.threads}")
            connection.execute(
                f"SET memory_limit TO {quote_literal(requested.memory_limit)}"
            )
            connection.execute(
                f"SET temp_directory TO {quote_literal(str(requested.temp_directory))}"
            )
            for extension in requested.extensions:
                connection.execute(f"LOAD {quote_identifier(extension)}")
            _settings = requested
            _connection = connection
        else:
            explicit = config.duckdb.model_fields_set
            conflicts = [
                name
                for name in ("threads", "memory_limit", "extensions")
                if name in explicit
                and getattr(requested, name) != getattr(_settings, name)
            ]
            if (
                "temp_directory" in explicit
                and config.duckdb.temp_directory is not None
                and requested.temp_directory != _settings.temp_directory
            ):
                conflicts.append("temp_directory")
            if conflicts:
                raise ValueError(
                    "Conflicting DuckDB engine settings: "
                    + ", ".join(conflicts)
                    + ". The first attachment establishes shared engine settings; "
                    "omit these fields to inherit them or explicitly match."
                )

        path = os.path.normcase(str(config.resolved_database_path.resolve()))
        catalog = database_catalog(config)
        # DuckDB catalog names are case insensitive. Check both name and path
        # before ATTACH so aliases cannot redirect an existing destination.
        for name, attached_path in connection.execute(
            "SELECT database_name, path FROM duckdb_databases()"
        ).fetchall():
            same_path = (
                bool(attached_path)
                and os.path.normcase(str(Path(attached_path).resolve())) == path
            )
            if identifier_key(name) == identifier_key(catalog):
                if not same_path:
                    raise ValueError(
                        f"Attachment name {catalog!r} is already used "
                        "by another database."
                    )
                _catalogs[path] = name
                break
            if same_path:
                raise ValueError(
                    f"Database is already attached as {name!r}; "
                    "use that attachment_name."
                )
        else:
            Path(path).parent.mkdir(parents=True, exist_ok=True)
            connection.execute(
                f"ATTACH {quote_literal(path)} AS {quote_identifier(catalog)} "
                f"(STORAGE_VERSION {quote_literal(ASSQUACK_STORAGE_VERSION)})"
            )
            _catalogs[path] = catalog

        # Select only the first Assquack destination. Later calls must preserve
        # the application's own USE or search_path choices.
        if not _default_selected:
            connection.execute(f"USE {quote_identifier(catalog)}")
            _default_selected = True
        return connection


def open_database(config: AssquackConfig) -> duckdb.DuckDBPyConnection:
    with CONNECTION_LOCK:
        connection = connect_database(config)
        catalog = database_catalog(config)
        if catalog not in _bootstrapped:
            bootstrap_database(connection, catalog)
            _bootstrapped.add(catalog)
        return connection


@contextmanager
def database_operation(
    config: AssquackConfig, *, bootstrap: bool = True
) -> Generator[duckdb.DuckDBPyConnection, None, None]:
    """Protect a complete synchronous operation; never await inside this scope."""
    with CONNECTION_LOCK:
        yield open_database(config) if bootstrap else connect_database(config)


@contextmanager
def transaction(
    connection: duckdb.DuckDBPyConnection,
) -> Generator[duckdb.DuckDBPyConnection, None, None]:
    with CONNECTION_LOCK:
        connection.execute("BEGIN TRANSACTION")
        try:
            yield connection
            connection.execute("COMMIT")
        except BaseException:
            connection.execute("ROLLBACK")
            raise
