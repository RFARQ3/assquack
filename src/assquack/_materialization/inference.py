from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from assquack._materialization.models import ProjectionSpec
from assquack._storage.models import SchemaObservation


@dataclass(slots=True)
class FieldEvidence:
    observed_types: Counter[str] = field(default_factory=Counter)
    present_count: int = 0
    null_count: int = 0
    total_count: int = 0


@dataclass(slots=True)
class InferenceState:
    fields: dict[str, FieldEvidence] = field(default_factory=dict)
    total_rows: int = 0

    def observe(self, rows: list[dict[str, Any]]) -> None:
        chunk_count = len(rows)
        previous_total = self.total_rows
        self.total_rows += chunk_count

        for evidence in self.fields.values():
            evidence.total_count += chunk_count

        for row in rows:
            for name, value in row.items():
                if name.startswith("_qa_"):
                    continue
                evidence = self.fields.get(name)
                if evidence is None:
                    evidence = FieldEvidence(total_count=previous_total + chunk_count)
                    self.fields[name] = evidence

                evidence.present_count += 1
                if value is None:
                    evidence.null_count += 1
                else:
                    evidence.observed_types[_observed_type(value)] += 1

    def observations(self) -> list[SchemaObservation]:
        observations: list[SchemaObservation] = []
        for name in sorted(self.fields):
            evidence = self.fields[name]
            if not evidence.observed_types:
                observations.append(
                    SchemaObservation(
                        path=f"$.{name}",
                        observed_type="NULL",
                        present_count=evidence.present_count,
                        null_count=evidence.null_count,
                        total_count=evidence.total_count,
                    )
                )
                continue
            for observed_type in sorted(evidence.observed_types):
                observations.append(
                    SchemaObservation(
                        path=f"$.{name}",
                        observed_type=observed_type,
                        present_count=evidence.present_count,
                        null_count=evidence.null_count,
                        total_count=evidence.total_count,
                    )
                )
        return observations


def infer_projections(
    state: InferenceState,
    previous_schema: list[dict[str, str]],
) -> list[ProjectionSpec]:
    """Resolve deterministic top-level scalar projections, retaining old fields."""

    previous = {
        item["source_field"]: ProjectionSpec(**item) for item in previous_schema
    }
    resolved = dict(previous)

    for name in sorted(state.fields):
        observed = state.fields[name].observed_types
        inferred_type = _resolve_type(set(observed))
        if inferred_type is None:
            # TODO: Promote stable nested objects/lists after conflict policy exists.
            continue

        old = previous.get(name)
        if old is not None:
            inferred_type = _widen_types(old.duckdb_type, inferred_type)
        resolved[name] = ProjectionSpec(name, name, inferred_type)

    return [resolved[name] for name in sorted(resolved)]


def _observed_type(value: object) -> str:
    if isinstance(value, bool):
        return "BOOLEAN"
    if isinstance(value, int):
        return "BIGINT"
    if isinstance(value, (float, Decimal)):
        return "DOUBLE"
    if isinstance(value, datetime):
        return "TIMESTAMPTZ"
    if isinstance(value, date):
        return "DATE"
    if isinstance(value, str):
        return "VARCHAR"
    if isinstance(value, dict):
        return "OBJECT"
    if isinstance(value, (list, tuple)):
        return "LIST"
    return "VARCHAR"


def _resolve_type(observed: set[str]) -> str | None:
    if not observed:
        return None
    if observed <= {"BIGINT", "DOUBLE"}:
        return "DOUBLE" if "DOUBLE" in observed else "BIGINT"
    if "OBJECT" in observed or "LIST" in observed:
        return None
    if len(observed) == 1:
        return next(iter(observed))
    return "VARCHAR"


def _widen_types(previous: str, observed: str) -> str:
    if previous == observed:
        return previous
    if {previous, observed} <= {"BIGINT", "DOUBLE"}:
        return "DOUBLE"
    return "VARCHAR"
