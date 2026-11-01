from __future__ import annotations

import inspect
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from functools import partial
from typing import Any, cast

import duckdb

from assquack._query.connection import QuerySession


def validate_select(sql: str) -> None:
    statements = duckdb.extract_statements(sql)
    if len(statements) != 1 or statements[0].type != duckdb.StatementType.SELECT:
        raise ValueError("Assquack queries require one read-only SELECT statement.")


def select_relation(
    session: QuerySession,
    sql: str,
    params: Sequence[object] | Mapping[str, object] | None = None,
    namespace: Mapping[str, object] | None = None,
) -> duckdb.DuckDBPyRelation:
    """Bind one SELECT using a temporary namespace of native relations."""
    connection = session.connection
    validate_select(sql)

    bindings = {
        name: value.relation if isinstance(value, AssquackQueryResult) else value
        for name, value in (namespace or {}).items()
    }
    helper = "__assquack_query__"
    while helper in bindings:
        helper += "_"
    bindings[helper] = partial(connection.sql, sql, params=params)

    # DuckDB recognises native relations, not our wrapper. Only this disposable
    # evaluation frame receives the adapted values; SQL is never Python code.
    try:
        return cast(duckdb.DuckDBPyRelation, eval(f"{helper}()", {}, bindings))
    except duckdb.InvalidInputException as error:
        if "another Connection" in str(error):
            raise ValueError(
                "Cannot join query results from different connections. "
                "Read them in the same task/execution from the same database. "
                "Cross-database queries are not supported."
            ) from error
        raise
    finally:
        bindings.clear()


@dataclass(frozen=True)
class AssquackQueryResult:
    """A lazy native relation with its owning read connection."""

    _session: QuerySession
    relation: duckdb.DuckDBPyRelation

    def query(
        self,
        sql: str,
        params: Sequence[object] | Mapping[str, object] | None = None,
    ) -> AssquackQueryResult:
        """Query result variables visible directly to the calling function."""
        frame = inspect.currentframe()
        try:
            if frame is None or frame.f_back is None:
                raise RuntimeError(
                    "Query variable lookup requires a Python caller frame."
                )
            namespace = frame.f_back.f_globals | frame.f_back.f_locals
        finally:
            del frame
        try:
            relation = select_relation(self._session, sql, params, namespace)
        finally:
            namespace.clear()
        return AssquackQueryResult(self._session, relation)

    def fetchall(self) -> list[tuple[Any, ...]]:
        return self.relation.fetchall()

    def fetchone(self) -> tuple[Any, ...] | None:
        return self.relation.fetchone()
