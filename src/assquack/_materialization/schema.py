from __future__ import annotations

import json
import re
from collections.abc import Callable, Mapping
from types import MappingProxyType
from typing import Protocol

import duckdb

from assquack._config import SchemaConfig
from assquack._materialization.models import FragmentFile, ProjectionSpec
from assquack._materialization.staging import describe_fragments


class SchemaStrategy(Protocol):
    def resolve(self) -> list[ProjectionSpec]: ...


class CurrentRunSchemaStrategy:
    """Use only the columns and types inferred from this extraction."""

    def __init__(
        self,
        connection: duckdb.DuckDBPyConnection,
        fragments: Callable[[], list[FragmentFile]],
    ) -> None:
        self._connection = connection
        self._fragments = fragments

    def resolve(self) -> list[ProjectionSpec]:
        observed_schema = describe_fragments(self._connection, self._fragments())
        return [
            ProjectionSpec(
                source_field=(
                    f"$.{name}"
                    if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name)
                    else f"$.{json.dumps(name)}"
                ),
                column_name=name,
                duckdb_type="VARIANT" if duckdb_type == "JSON" else duckdb_type,
            )
            for name, duckdb_type in sorted(observed_schema)
        ]


_STRATEGIES: Mapping[
    str,
    Callable[
        [duckdb.DuckDBPyConnection, Callable[[], list[FragmentFile]]], SchemaStrategy
    ],
] = MappingProxyType({"current_run": CurrentRunSchemaStrategy})


def replace_schema_strategy(
    config: SchemaConfig,
    connection: duckdb.DuckDBPyConnection,
    fragments: Callable[[], list[FragmentFile]],
) -> SchemaStrategy:
    """Construct a run-local strategy from the framework's fixed registry."""
    try:
        factory = _STRATEGIES[config.replace]
    except KeyError:
        raise ValueError(
            f"Unsupported replacement schema strategy: {config.replace!r}"
        ) from None
    return factory(connection, fragments)
