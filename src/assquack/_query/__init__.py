"""Internal query interfaces shared by assets and materialization."""

from assquack._query.asset import execute_query, query_dataframe
from assquack._query.context import query_context, use_config
from assquack._query.general import query

__all__ = [
    "execute_query",
    "query",
    "query_context",
    "query_dataframe",
    "use_config",
]
