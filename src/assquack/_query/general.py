from __future__ import annotations

import inspect
import json
import os
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, cast

import duckdb

from assquack._config import AssquackConfig
from assquack._query.context import current_query_config
from assquack._storage.database import (
    CONNECTION_LOCK,
    database_operation,
    default_connection,
)
from assquack._storage.sql import identifier_key, quote_identifier, quote_literal


async def query(
    sql: str,
    params: Sequence[object] | Mapping[str, object] | None = None,
) -> duckdb.DuckDBPyRelation:
    """Bind a native relation without changing the default database or transaction."""
    config = _resolve_config()
    if config is None:
        with CONNECTION_LOCK:
            connection = default_connection()
            connection.execute("SET python_scan_all_frames = true")
            statement, bindings = qualify_sql(connection, sql, params)
            return connection.sql(statement, params=bindings)

    from assquack._storage.database import is_attached

    if not is_attached(config):
        raise ValueError(
            "This query configuration is not attached. Call config.attach() first."
        )
    with database_operation(config, bootstrap=False) as connection:
        statement, bindings = qualify_sql(connection, sql, params)
        return connection.sql(statement, params=bindings)


def _resolve_config() -> AssquackConfig | None:
    config = current_query_config()
    if config is not None:
        return config
    database_path = os.environ.get("ASSQUACK_DATABASE_PATH", "").strip()
    home = os.environ.get("ASSQUACK_HOME", "").strip()
    if (
        not database_path
        and not home
        and not os.environ.get("ASSQUACK_ATTACHMENT_NAME", "").strip()
    ):
        return None
    config = AssquackConfig(
        home=Path(home) if home else Path.cwd(),
        database_path=Path(database_path) if database_path else None,
    )
    config.attach()
    return config


def qualify_sql(
    connection: duckdb.DuckDBPyConnection,
    sql: str,
    params: Sequence[object] | Mapping[str, object] | None = None,
) -> tuple[str, dict[str, object] | None]:
    """Bind scoped table names in DuckDB's parsed SQL, preserving native scans."""
    serialized = connection.execute("SELECT json_serialize_sql(?)", [sql]).fetchone()
    assert serialized is not None
    tree = json.loads(serialized[0])
    if tree.get("error"):
        raise ValueError(f"Cannot qualify this scoped query: {tree['error_message']}")

    bindings = (
        dict(params)
        if isinstance(params, Mapping)
        else {str(index): value for index, value in enumerate(params, 1)}
        if params is not None
        else None
    )
    original_parameters = _parameter_names(tree)
    catalogs = [
        name
        for (name,) in connection.execute(
            "SELECT database_name FROM duckdb_databases() "
            "WHERE NOT internal AND database_name <> 'memory'"
        ).fetchall()
    ]
    catalog = catalogs[0] if len(catalogs) == 1 else None

    # Only identify replacement-scan names. DuckDB itself binds their objects;
    # no frame is evaluated, modified, retained or adapted to a custom wrapper.
    replacements: set[str] = set()
    frame = inspect.currentframe()
    try:
        while frame is not None:
            for name, value in (frame.f_globals | frame.f_locals).items():
                if isinstance(value, duckdb.DuckDBPyRelation) or type(
                    value
                ).__module__.split(".")[0] in {"pandas", "pyarrow"}:
                    replacements.add(identifier_key(name))
            frame = frame.f_back
    finally:
        del frame

    tables = {
        identifier_key(name)
        for (name,) in connection.execute(
            "SELECT table_name FROM information_schema.tables"
        ).fetchall()
    }

    def table_name(value: str, visible: frozenset[str]) -> str:
        parts = _identifier_parts(value)
        reference: dict[str, Any] = {
            "type": "BASE_TABLE",
            "table_name": parts[-1],
            "schema_name": parts[-2] if len(parts) > 1 else "",
            "catalog_name": parts[-3] if len(parts) > 2 else "",
        }
        qualify(reference, visible)
        return ".".join(
            quote_identifier(reference[key])
            for key in ("catalog_name", "schema_name", "table_name")
            if reference.get(key)
        )

    def table_argument(node: dict[str, Any], visible: frozenset[str]) -> dict[str, Any]:
        if node.get("class") == "PARAMETER":
            key = identifier_key(node["identifier"])
            matches = [
                value
                for name, value in (bindings or {}).items()
                if identifier_key(name) == key
            ]
            if not matches:
                raise ValueError(f"Missing query_table parameter: {node['identifier']}")
            value = matches[0]
            if isinstance(value, str):
                expression = quote_literal(table_name(value, visible))
            elif isinstance(value, (list, tuple)) and all(
                isinstance(item, str) for item in cast(Sequence[object], value)
            ):
                expression = (
                    "["
                    + ",".join(
                        quote_literal(table_name(item, visible))
                        for item in cast(Sequence[str], value)
                    )
                    + "]"
                )
            else:
                raise ValueError("query_table requires a table name or list of names.")
            row = connection.execute(
                "SELECT json_serialize_sql(?)", ["SELECT " + expression]
            ).fetchone()
            assert row is not None
            return json.loads(row[0])["statements"][0]["node"]["select_list"][0]
        if node.get("class") == "CONSTANT" and node["value"]["type"]["id"] == "VARCHAR":
            node["value"]["value"] = table_name(node["value"]["value"], visible)
            return node
        if node.get("class") == "CAST":
            node["child"] = table_argument(node["child"], visible)
            return node
        if (
            node.get("class") == "FUNCTION"
            and node.get("function_name") == "list_value"
        ):
            node["children"] = [
                table_argument(child, visible) for child in node["children"]
            ]
            return node
        raise ValueError(
            "Scoped query_table requires literal or parameterized table names. "
            "Pass a string or list of names as its first argument."
        )

    def qualify(node: Any, visible: frozenset[str]) -> None:
        if isinstance(node, list):
            for child in cast(list[Any], node):
                qualify(child, visible)
            return
        if not isinstance(node, dict):
            return

        node = cast(dict[str, Any], node)

        # A nonrecursive CTE can read a physical table with its own name.
        # Earlier CTEs are visible in later definitions, then in the body.
        ctes = node.get("cte_map", {}).get("map", [])
        for cte in ctes:
            qualify(cte["value"], visible)
            visible = visible | {identifier_key(cte["key"])}
        if node.get("type") == "RECURSIVE_CTE_NODE":
            # The anchor reads physical tables or outer CTEs. Only the recursive
            # branch can see this CTE's own name.
            qualify(node["left"], visible)
            qualify(node["right"], visible | {identifier_key(node["cte_name"])})
            for key, value in node.items():
                if key not in {"left", "right", "cte_map"}:
                    qualify(value, visible)
            return
        if node.get("type") == "TABLE_FUNCTION":
            function = node["function"]
            if identifier_key(function.get("function_name", "")) == "query_table":
                if function.get("catalog") or function.get("schema"):
                    raise ValueError(
                        "Qualified query_table functions/macros are unsupported. "
                        "Use unqualified query_table with database.schema.table names."
                    )
                function["children"][0] = table_argument(
                    function["children"][0], visible
                )

        if node.get("type") == "BASE_TABLE" and not node.get("catalog_name"):
            name = node["table_name"]
            schema = node.get("schema_name")
            if not schema and identifier_key(name) in visible:
                return
            if (
                not schema
                and identifier_key(name) in replacements
                and identifier_key(name) not in tables
            ):
                return
            # DuckDB's quoted filename syntax is a native file replacement scan.
            if (
                not schema
                and identifier_key(name) not in tables
                and (
                    "/" in name
                    or "\\" in name
                    or Path(name).suffix.lower()
                    in {".csv", ".json", ".ndjson", ".parquet"}
                )
            ):
                return
            if len(catalogs) > 1:
                raise ValueError(
                    "Multiple user databases are attached; physical table references "
                    "require database.schema.table qualification. "
                    f"Qualify {name!r} explicitly."
                )
            if catalog is not None:
                node["catalog_name"] = catalog
                node["schema_name"] = schema or "main"
        for key, value in node.items():
            if key != "cte_map":
                qualify(value, visible)

    qualify(tree, frozenset())
    result = connection.execute(
        "SELECT json_deserialize_sql(?)", [json.dumps(tree)]
    ).fetchone()
    assert result is not None
    if bindings is not None:
        consumed = original_parameters - _parameter_names(tree)
        bindings = {
            name: value
            for name, value in bindings.items()
            if identifier_key(name) not in consumed
        }
    return result[0], bindings


def _parameter_names(node: Any) -> set[str]:
    if isinstance(node, list):
        return set[str]().union(
            *(_parameter_names(item) for item in cast(list[Any], node))
        )
    if isinstance(node, dict):
        fields = cast(dict[str, Any], node)
        if fields.get("class") == "PARAMETER":
            return {identifier_key(fields["identifier"])}
        return set[str]().union(*(_parameter_names(value) for value in fields.values()))
    return set()


def _identifier_parts(name: str) -> list[str]:
    """Split DuckDB's query_table name syntax without splitting quoted dots."""
    parts: list[str] = []
    part = ""
    quoted = False
    index = 0
    while index < len(name):
        char = name[index]
        if char == '"':
            if quoted and index + 1 < len(name) and name[index + 1] == '"':
                part += '"'
                index += 1
            else:
                quoted = not quoted
        elif char == "." and not quoted:
            parts.append(part)
            part = ""
        else:
            part += char
        index += 1
    parts.append(part)
    if quoted or len(parts) > 3 or not all(parts):
        raise ValueError(f"Invalid query_table name: {name!r}")
    return parts
