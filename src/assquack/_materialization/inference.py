from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

from assquack._materialization.models import (
    BatchSchemaEvidence,
    JsonStructure,
    ProjectionSpec,
)
from assquack._storage.models import SchemaObservation
from assquack._storage.sql import quote_identifier

_SIMPLE_JSON_KEY = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_NUMERIC_TYPES = {"BIGINT", "UBIGINT", "DOUBLE"}


@dataclass(slots=True)
class ObservationEvidence:
    path: str
    observed_type: str
    present_count: int = 0
    null_count: int = 0
    total_count: int = 0


@dataclass(slots=True)
class InferenceState:
    json_structure: JsonStructure = field(default_factory=dict)
    evidence: dict[tuple[str, str], ObservationEvidence] = field(
        default_factory=dict
    )
    total_rows: int = 0

    def observe(self, batch: BatchSchemaEvidence) -> None:
        previous_total = self.total_rows
        batch_total = batch.total_count
        self.total_rows += batch_total
        self.json_structure = merge_json_structures(
            self.json_structure,
            batch.json_structure,
        )

        for item in self.evidence.values():
            item.total_count += batch_total

        for observed in batch.observations:
            key = (observed.path, observed.observed_type)
            item = self.evidence.get(key)
            if item is None:
                item = ObservationEvidence(
                    path=observed.path,
                    observed_type=observed.observed_type,
                    total_count=previous_total + batch_total,
                )
                self.evidence[key] = item

            item.present_count += observed.present_count
            item.null_count += observed.null_count

    def observations(self) -> list[SchemaObservation]:
        return [
            SchemaObservation(
                path=item.path,
                observed_type=item.observed_type,
                present_count=item.present_count,
                null_count=item.null_count,
                total_count=item.total_count,
            )
            for item in sorted(
                self.evidence.values(),
                key=lambda item: (item.path, item.observed_type),
            )
        ]

    def present_count(self, path: str, *observed_types: str) -> int:
        accepted = set(observed_types)
        return sum(
            item.present_count
            for item in self.evidence.values()
            if item.path == path
            and (not accepted or item.observed_type in accepted)
        )


def infer_projections(
    state: InferenceState,
    previous_schema: list[dict[str, str]],
    previous_json_structure: JsonStructure | None = None,
) -> list[ProjectionSpec]:
    """Resolve top-level projections from DuckDB's recursive JSON structure."""

    previous = {
        item["column_name"]: ProjectionSpec(**item) for item in previous_schema
    }
    resolved = dict(previous)
    structure = merge_json_structures(
        previous_json_structure or {},
        state.json_structure,
    )
    if not isinstance(structure, dict):
        return [resolved[name] for name in sorted(resolved)]

    for name in sorted(structure):
        path = _json_path(name)
        inferred_type = resolve_duckdb_type(structure[name], state, path)
        if inferred_type is None:
            continue

        old = previous.get(name)
        if old is not None:
            inferred_type = _widen_types(old.duckdb_type, inferred_type)

        resolved[name] = ProjectionSpec(
            source_field=path,
            column_name=name,
            duckdb_type=inferred_type,
        )

    return [resolved[name] for name in sorted(resolved)]


def merge_json_structures(
    left: JsonStructure,
    right: JsonStructure,
) -> JsonStructure:
    """Merge structures reported by DuckDB for separate staged batches."""

    if left == right:
        return left
    if left in ({}, [], "NULL"):
        return right
    if right in ({}, [], "NULL"):
        return left
    if left == "JSON" or right == "JSON":
        return "JSON"

    if isinstance(left, dict) and isinstance(right, dict):
        return {
            key: merge_json_structures(
                left.get(key, "NULL"),
                right.get(key, "NULL"),
            )
            for key in sorted(left.keys() | right.keys())
        }

    if isinstance(left, list) and isinstance(right, list):
        if not left:
            return right
        if not right:
            return left
        return [merge_json_structures(left[0], right[0])]

    if isinstance(left, str) and isinstance(right, str):
        if {left, right} <= _NUMERIC_TYPES:
            return "DOUBLE" if "DOUBLE" in {left, right} else "BIGINT"
        return "VARCHAR"

    return "JSON"


def resolve_duckdb_type(
    structure: JsonStructure,
    state: InferenceState | None = None,
    path: str | None = None,
) -> str | None:
    """Translate DuckDB JSON structure output into a valid cast target."""

    if isinstance(structure, str):
        if structure == "NULL":
            return None
        if structure == "JSON":
            return "VARIANT"
        return structure

    if isinstance(structure, dict):
        if state is not None and path is not None:
            object_count = state.present_count(path, "OBJECT")
            if object_count == 0:
                return None
            if any(
                state.present_count(_child_json_path(path, name)) < object_count
                for name in structure
            ):
                return "VARIANT"

        members = [
            (
                name,
                resolve_duckdb_type(
                    value,
                    state,
                    _child_json_path(path, name) if path is not None else None,
                ),
            )
            for name, value in sorted(structure.items())
        ]
        typed_members = [
            f"{quote_identifier(name)} {duckdb_type}"
            for name, duckdb_type in members
            if duckdb_type is not None
        ]
        if not typed_members:
            return None
        return f"STRUCT({', '.join(typed_members)})"

    if isinstance(structure, list):
        if not structure:
            return None
        element_type = resolve_duckdb_type(
            structure[0],
            state,
            f"{path}[*]" if path is not None else None,
        )
        if element_type in (None, "VARIANT"):
            return None if element_type is None else "VARIANT"
        return f"{element_type}[]"

    return "VARIANT"


def _json_path(name: str) -> str:
    if _SIMPLE_JSON_KEY.fullmatch(name):
        return f"$.{name}"
    return f"$.{json.dumps(name)}"


def _child_json_path(path: str, name: str) -> str:
    if _SIMPLE_JSON_KEY.fullmatch(name):
        return f"{path}.{name}"
    return f"{path}.{json.dumps(name)}"


def _widen_types(previous: str, observed: str) -> str:
    if previous == observed:
        return previous
    if {previous, observed} <= _NUMERIC_TYPES:
        return "DOUBLE" if "DOUBLE" in {previous, observed} else "BIGINT"
    if previous.startswith("STRUCT(") and observed.startswith("STRUCT("):
        return observed
    if previous.endswith("[]") and observed.endswith("[]"):
        return observed
    if "VARIANT" in {previous, observed}:
        return "VARIANT"
    return "VARCHAR"
