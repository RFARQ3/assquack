from __future__ import annotations

from collections.abc import Sequence

from assquack._storage.sql import quote_literal

JSON_READER_OPTIONS = (
    "format='newline_delimited', "
    "union_by_name=true, "
    "sample_size=-1, "
    "field_appearance_threshold=0, "
    "map_inference_threshold=-1, "
    "maximum_object_size=33554432"
)


def read_json_sql(paths: Sequence[str]) -> str:
    path_list = ", ".join(quote_literal(path.replace("\\", "/")) for path in paths)
    return f"read_json([{path_list}], {JSON_READER_OPTIONS})"


def read_json_objects_sql(path: str) -> str:
    normalized_path = path.replace("\\", "/")
    return (
        f"read_json_objects({quote_literal(normalized_path)}, "
        "format='newline_delimited', maximum_object_size=33554432)"
    )
