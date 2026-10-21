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

_SIMPLE_JSON_KEY = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_NUMERIC_TYPES = {"BIGINT", "UBIGINT", "DOUBLE"}


@dataclass(slots=True)
class ObservationEvidence:
    path: str
    observed_type: str
    present_count: int = 0
    null_count: int = 0
    total_count: int = 0


def _empty_json_structure() -> JsonStructure:
    return {}


def _empty_observations() -> dict[tuple[str, str], ObservationEvidence]:
    return {}


@dataclass(slots=True)
class InferenceState:
    json_structure: JsonStructure = field(default_factory=_empty_json_structure)
    evidence: dict[tuple[str, str], ObservationEvidence] = field(
        default_factory=_empty_observations
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


def resolve_reader_projections(
    observed_schema: list[tuple[str, str]],
    previous_schema: list[dict[str, str]],
) -> list[ProjectionSpec]:
    """Merge native JSON reader columns with the latest published schema."""

    resolved = {item["column_name"]: ProjectionSpec(**item) for item in previous_schema}
    for name, observed_type in observed_schema:
        duckdb_type = "VARIANT" if observed_type == "JSON" else observed_type
        previous = resolved.get(name)
        if previous is not None:
            duckdb_type = _widen_types(previous.duckdb_type, duckdb_type)

        resolved[name] = ProjectionSpec(
            source_field=_json_path(name),
            column_name=name,
            duckdb_type=duckdb_type,
        )

    return [resolved[name] for name in sorted(resolved)]


def merge_json_structures(
    left: JsonStructure,
    right: JsonStructure,
) -> JsonStructure:
    """Merge structures reported by DuckDB for separate fragments."""

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


def _json_path(name: str) -> str:
    if _SIMPLE_JSON_KEY.fullmatch(name):
        return f"$.{name}"
    return f"$.{json.dumps(name)}"


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
