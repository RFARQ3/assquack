from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Protocol

import duckdb

from assquack._config import SchemaConfig
from assquack._storage.fragment_writer import FragmentFile
from assquack._storage.json_reader import JsonReadRecovery, read_json_sql


class SchemaStrategy(Protocol):
    def resolve(self) -> list[ProjectionSpec]: ...


class CurrentRunSchemaStrategy:
    """Use only the columns and types inferred from this extraction."""

    def __init__(
        self,
        connection: duckdb.DuckDBPyConnection,
        fragments: Callable[[], list[FragmentFile]],
        reader: JsonReadRecovery | None = None,
    ) -> None:
        self._connection = connection
        self._fragments = fragments
        self._reader = reader or JsonReadRecovery()

    def resolve(self) -> list[ProjectionSpec]:
        observed_schema = describe_fragments(
            self._connection, self._fragments(), self._reader
        )
        return [
            ProjectionSpec(
                column_name=name,
                duckdb_type=duckdb_type,
            )
            for name, duckdb_type in sorted(observed_schema)
            # Publication supplies provenance for this run, including when the
            # source is another result table containing its own metadata.
            if name.lower() not in {"_qa_run_id", "_qa_loaded_at"}
        ]


_STRATEGIES: Mapping[
    str,
    Callable[
        [
            duckdb.DuckDBPyConnection,
            Callable[[], list[FragmentFile]],
            JsonReadRecovery | None,
        ],
        SchemaStrategy,
    ],
] = MappingProxyType({"current_run": CurrentRunSchemaStrategy})


def replace_schema_strategy(
    config: SchemaConfig,
    connection: duckdb.DuckDBPyConnection,
    fragments: Callable[[], list[FragmentFile]],
    reader: JsonReadRecovery | None = None,
) -> SchemaStrategy:
    """Construct a run-local strategy from the framework's fixed registry."""
    try:
        factory = _STRATEGIES[config.replace]
    except KeyError:
        raise ValueError(
            f"Unsupported replacement schema strategy: {config.replace!r}"
        ) from None
    return factory(connection, fragments, reader)


def describe_fragments(
    connection: duckdb.DuckDBPyConnection,
    fragments: list[FragmentFile],
    reader: JsonReadRecovery | None = None,
) -> list[tuple[str, str]]:
    if not fragments:
        return []

    paths = [fragment.path for fragment in fragments]
    reader = reader or JsonReadRecovery()
    rows = reader.read(
        paths,
        lambda limit: connection.execute(
            f"DESCRIBE SELECT * FROM {read_json_sql(paths, limit)}"
        ).fetchall(),
    )
    return [(row[0], row[1]) for row in rows]


@dataclass(frozen=True, slots=True)
class ProjectionSpec:
    column_name: str
    duckdb_type: str
