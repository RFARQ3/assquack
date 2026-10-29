"""Internal query interfaces shared by assets and materialization."""

from assquack._query.asset import execute_query, query_dataframe
from assquack._query.context import query_context
from assquack._query.general import query
from assquack._query.result import AssquackQueryResult

__all__ = [
    "AssquackQueryResult",
    "execute_query",
    "query",
    "query_context",
    "query_dataframe",
]
