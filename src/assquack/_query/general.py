from __future__ import annotations

import os
from collections.abc import Mapping, Sequence
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import cast

import duckdb

from assquack._config import AssquackConfig
from assquack._query.context import current_query_config
from assquack._query.result import AssquackQueryResult
from assquack._storage.sql import quote_identifier, quote_literal


async def query(
    sql: str,
    params: Sequence[object] | Mapping[str, object] | None = None,
    *,
    sources: Mapping[str, AssquackConfig] | None = None,
) -> AssquackQueryResult:
    """Read existing local databases and detach sources before returning rows."""
    selected = _resolve_sources(sources)
    config = next(iter(selected.values()))
    paths = [value.resolved_database_path.resolve() for value in selected.values()]
    if len(set(paths)) != len(paths):
        raise ValueError("Query sources must identify distinct database paths.")
    for path in paths:
        if not path.is_file():
            raise FileNotFoundError(f"Query source database does not exist: {path}")

    config.resolved_temp_directory.mkdir(parents=True, exist_ok=True)
    directory = TemporaryDirectory(
        prefix="assquack-query-", dir=config.resolved_temp_directory
    )
    connection = None
    try:
        connection = duckdb.connect(
            config={
                "threads": config.duckdb.threads,
                "memory_limit": config.duckdb.memory_limit,
                "temp_directory": directory.name,
            }
        )
        statements = connection.extract_statements(sql)
        if len(statements) != 1 or statements[0].type != duckdb.StatementType.SELECT:
            raise ValueError("assquack.query requires one read-only SELECT statement.")
        for extension in config.duckdb.extensions:
            connection.execute(f"LOAD {quote_identifier(extension)}")

        for alias, path in zip(selected, paths, strict=True):
            connection.execute(
                f"ATTACH {quote_literal(str(path))} AS {quote_identifier(alias)} "
                "(READ_ONLY)"
            )
        if sources is None:
            connection.execute('USE "assquack_source"')

        # Materialize only the query output using DuckDB's native types and spill
        # support. Source files can then be released before output publication.
        connection.execute(
            "CREATE TEMP TABLE assquack_query_result AS " + statements[0].query,
            params,
        )
        connection.execute('USE "memory"')
        for alias in selected:
            connection.execute(f"DETACH {quote_identifier(alias)}")
        return AssquackQueryResult(connection, directory)
    except BaseException:
        if connection is not None:
            connection.close()
        directory.cleanup()
        raise


def _resolve_sources(
    sources: object,
) -> dict[str, AssquackConfig]:
    if sources is not None:
        if not isinstance(sources, Mapping) or not sources:
            raise ValueError(
                "Query sources must be a non-empty alias-to-config mapping."
            )
        seen: set[str] = set()
        validated: dict[str, AssquackConfig] = {}
        for alias, config in cast(Mapping[object, object], sources).items():
            if (
                not isinstance(alias, str)
                or not alias.strip()
                or "\0" in alias
                or alias.lower() in {"memory", "temp", "system"}
            ):
                raise ValueError(
                    "Query source aliases must be nonblank SQL catalog names."
                )
            if alias.lower() in seen:
                raise ValueError("Query source aliases must be distinct ignoring case.")
            seen.add(alias.lower())
            if not isinstance(config, AssquackConfig):
                raise TypeError("Every query source must be an AssquackConfig.")
            validated[alias] = config
        return validated

    config = current_query_config()
    if config is None:
        database_path = os.environ.get("ASSQUACK_DATABASE_PATH", "").strip()
        home = os.environ.get("ASSQUACK_HOME", "").strip()
        if not database_path and not home:
            raise ValueError(
                "Outside an asset, query requires sources, ASSQUACK_DATABASE_PATH "
                "or ASSQUACK_HOME identifying an existing database."
            )
        # Blank settings are absent here, rather than paths to the working directory.
        config = AssquackConfig(
            home=Path(home) if home else Path.cwd(),
            database_path=Path(database_path) if database_path else None,
        )
    return {"assquack_source": config}
