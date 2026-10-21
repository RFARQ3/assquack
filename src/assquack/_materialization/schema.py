from __future__ import annotations

import json
import re
from typing import Protocol

from assquack._config import SchemaConfig
from assquack._materialization.models import ProjectionSpec


class SchemaStrategy(Protocol):
    def resolve(
        self, observed_schema: list[tuple[str, str]]
    ) -> list[ProjectionSpec]: ...


class CurrentRunSchemaStrategy:
    """Use only the columns and types inferred from this extraction."""

    def resolve(self, observed_schema: list[tuple[str, str]]) -> list[ProjectionSpec]:
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


def replace_schema_strategy(config: SchemaConfig) -> SchemaStrategy:
    if config.replace == "current_run":
        return CurrentRunSchemaStrategy()
    raise ValueError(f"Unsupported replacement schema strategy: {config.replace!r}")
