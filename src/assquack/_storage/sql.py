from __future__ import annotations

import re

_SAFE_IDENTIFIER = re.compile(r"[^a-zA-Z0-9_]+")


def quote_identifier(value: str) -> str:
    return f'"{value.replace(chr(34), chr(34) * 2)}"'


def quote_table(schema_name: str, table_name: str) -> str:
    return f"{quote_identifier(schema_name)}.{quote_identifier(table_name)}"


def quote_literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def sanitize_identifier(value: str, *, fallback: str = "asset") -> str:
    sanitized = _SAFE_IDENTIFIER.sub("_", value).strip("_").lower()
    if not sanitized:
        return fallback
    if sanitized[0].isdigit():
        return f"_{sanitized}"
    return sanitized
