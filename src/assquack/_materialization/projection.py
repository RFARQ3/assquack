from __future__ import annotations

from assquack._materialization.models import ProjectionSpec
from assquack._storage.sql import quote_identifier, quote_literal


def projection_expression(spec: ProjectionSpec) -> str:
    path = quote_literal(spec.source_field)
    return (
        f"try_cast(variant_extract(_qa_payload, {path}) "
        f"AS {spec.duckdb_type}) AS {quote_identifier(spec.column_name)}"
    )


def projection_list(specs: list[ProjectionSpec]) -> str:
    return ",\n                ".join(projection_expression(spec) for spec in specs)
