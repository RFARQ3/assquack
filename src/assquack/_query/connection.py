from __future__ import annotations

import asyncio
import os
from collections.abc import Generator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from weakref import WeakValueDictionary

import duckdb

from assquack._config import AssquackConfig
from assquack._storage.database import connect_database


@dataclass(eq=False)
class QuerySession:
    connection: duckdb.DuckDBPyConnection


@dataclass
class _Connections:
    task: object | None
    sessions: WeakValueDictionary[str, QuerySession] = field(
        default_factory=WeakValueDictionary[str, QuerySession]
    )


_CONNECTIONS: ContextVar[_Connections | None] = ContextVar(
    "assquack_query_connections", default=None
)


def _current_task() -> object | None:
    try:
        return asyncio.current_task()
    except RuntimeError:
        return None


@contextmanager
def connection_scope() -> Generator[None, None, None]:
    """Give an asset execution its own pool and restore the parent's afterward."""
    token = _CONNECTIONS.set(_Connections(_current_task()))
    try:
        yield
    finally:
        _CONNECTIONS.reset(token)


def query_session(config: AssquackConfig) -> QuerySession:
    """Reuse a live result's connection in this task, starting a fresh read."""
    connections = _CONNECTIONS.get()
    if connections is None or connections.task is not _current_task():
        # ContextVars are inherited by child tasks; their mutable pools must not be.
        connections = _Connections(_current_task())
        _CONNECTIONS.set(connections)

    path = os.path.normcase(str(config.resolved_database_path.resolve()))
    session = connections.sessions.get(path)
    if session is None:
        connection = connect_database(config)
        connection.execute("BEGIN TRANSACTION READ ONLY")
        session = QuerySession(connection)
        connections.sessions[path] = session
    else:
        # Root reads see newly published tables. Derived queries do not refresh;
        # all lazy results on this connection share its current read transaction.
        session.connection.execute("ROLLBACK")
        session.connection.execute("BEGIN TRANSACTION READ ONLY")
    return session
