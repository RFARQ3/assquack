"""Internal query interfaces shared by assets and materialization."""

from assquack._query.asset import execute_query, query_dataframe

__all__ = [
    "execute_query",
    "query_dataframe",
]
