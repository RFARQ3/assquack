from __future__ import annotations

import os
from collections.abc import Mapping, Sequence
from pathlib import Path

from assquack._config import AssquackConfig
from assquack._query.connection import query_session
from assquack._query.context import current_query_config
from assquack._query.result import AssquackQueryResult, select_relation, validate_select


async def query(
    sql: str,
    params: Sequence[object] | Mapping[str, object] | None = None,
) -> AssquackQueryResult:
    """Return a connection-owning result reading one configured database."""
    config = _resolve_config()
    path = config.resolved_database_path
    if not path.is_file():
        raise FileNotFoundError(f"Query source database does not exist: {path}")

    validate_select(sql)
    session = query_session(config)
    return AssquackQueryResult(session, select_relation(session, sql, params))


def _resolve_config() -> AssquackConfig:
    config = current_query_config()
    if config is not None:
        return config

    database_path = os.environ.get("ASSQUACK_DATABASE_PATH", "").strip()
    home = os.environ.get("ASSQUACK_HOME", "").strip()
    if not database_path and not home:
        raise ValueError(
            "Outside an asset, assquack.query requires assquack.use_config(config), "
            "ASSQUACK_DATABASE_PATH or ASSQUACK_HOME identifying an existing database."
        )
    # Blank settings are absent here, rather than paths to the working directory.
    return AssquackConfig(
        home=Path(home) if home else Path.cwd(),
        database_path=Path(database_path) if database_path else None,
    )
