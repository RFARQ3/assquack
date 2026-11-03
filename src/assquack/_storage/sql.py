from __future__ import annotations

import re

_SAFE_IDENTIFIER = re.compile(r"[^a-zA-Z0-9_]+")


def quote_identifier(value: str) -> str:
    return f'"{value.replace(chr(34), chr(34) * 2)}"'


def quote_table(
    schema_name: str, table_name: str, catalog_name: str | None = None
) -> str:
    parts = [schema_name, table_name]
    if catalog_name is not None:
        parts.insert(0, catalog_name)
    return ".".join(quote_identifier(part) for part in parts)


def quote_literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def sanitize_identifier(value: str, *, fallback: str = "asset") -> str:
    sanitized = _SAFE_IDENTIFIER.sub("_", value).strip("_").lower()
    if not sanitized:
        return fallback
    if sanitized[0].isdigit():
        return f"_{sanitized}"
    return sanitized


def identifier_key(value: str) -> str:
    """DuckDB identifier comparison folds ASCII letters, not Unicode letters."""
    return value.translate(
        str.maketrans("ABCDEFGHIJKLMNOPQRSTUVWXYZ", "abcdefghijklmnopqrstuvwxyz")
    )
