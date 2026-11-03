from __future__ import annotations

import duckdb


def validate_select(sql: str) -> None:
    statements = duckdb.extract_statements(sql)
    if len(statements) != 1 or statements[0].type != duckdb.StatementType.SELECT:
        raise ValueError("Assquack queries require one read-only SELECT statement.")
