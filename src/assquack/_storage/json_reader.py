from __future__ import annotations

import re
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import TypeVar

import duckdb

from assquack._storage.sql import quote_literal

DEFAULT_MAXIMUM_OBJECT_SIZE = 32 * 1024 * 1024
_SCAN_BUFFER_BYTES = 1024 * 1024
_Result = TypeVar("_Result")
_OVERSIZED_RECORD = re.compile(
    r'^Invalid Input Error: "maximum_object_size" of [0-9]+ bytes exceeded '
    r"while reading file .* \(>[0-9]+ bytes\)\.\n "
    r'Try increasing "maximum_object_size"\.',
    re.DOTALL,
)

# Fragment directory names identify working storage, not source columns.
# Hive inference would overwrite payload fields named asset or run_id.
JSON_READER_OPTIONS = (
    "format='newline_delimited', "
    "hive_partitioning=false, "
    "union_by_name=true, "
    "sample_size=-1, "
    "field_appearance_threshold=0, "
    "map_inference_threshold=-1"
)


def read_json_sql(
    paths: Sequence[str], maximum_object_size: int = DEFAULT_MAXIMUM_OBJECT_SIZE
) -> str:
    path_list = ", ".join(quote_literal(path.replace("\\", "/")) for path in paths)
    return (
        f"read_json([{path_list}], {JSON_READER_OPTIONS}, "
        f"maximum_object_size={maximum_object_size})"
    )


def read_json_objects_sql(
    path: str, maximum_object_size: int = DEFAULT_MAXIMUM_OBJECT_SIZE
) -> str:
    normalized_path = path.replace("\\", "/")
    return (
        f"read_json_objects({quote_literal(normalized_path)}, "
        f"format='newline_delimited', maximum_object_size={maximum_object_size})"
    )


class JsonReadRecovery:
    """Share a measured reader limit between reads of one completed extraction."""

    def __init__(self) -> None:
        self.maximum_object_size = DEFAULT_MAXIMUM_OBJECT_SIZE
        self._recovered = False

    def read(
        self, paths: Sequence[str], operation: Callable[[int], _Result]
    ) -> _Result:
        try:
            return operation(self.maximum_object_size)
        except duckdb.InvalidInputException as error:
            if self._recovered or not _OVERSIZED_RECORD.match(str(error)):
                raise

        # Only DuckDB's record-size failure warrants touching the files again.
        # Retain the measured limit for staging, without changing durable metadata.
        self._recovered = True
        self.maximum_object_size = max(
            self.maximum_object_size, largest_record_bytes(paths)
        )
        return operation(self.maximum_object_size)


def largest_record_bytes(paths: Sequence[str]) -> int:
    """Count serialized bytes without retaining a whole record in memory."""
    largest = 0
    for path in paths:
        record_bytes_so_far = 0
        previous_byte = b""
        with Path(path).open("rb") as fragment:
            # A record can span buffers. Carry its byte count into the next
            # buffer, and exclude a carriage return only just before a newline.
            while chunk := fragment.read(_SCAN_BUFFER_BYTES):
                segment_start = 0
                while (newline_index := chunk.find(b"\n", segment_start)) >= 0:
                    length = record_bytes_so_far + newline_index - segment_start
                    last_byte = (
                        chunk[newline_index - 1 : newline_index]
                        if newline_index > segment_start
                        else previous_byte
                    )
                    largest = max(largest, length - (last_byte == b"\r"))
                    record_bytes_so_far = 0
                    previous_byte = b""
                    segment_start = newline_index + 1
                record_bytes_so_far += len(chunk) - segment_start
                if segment_start < len(chunk):
                    previous_byte = chunk[-1:]
        largest = max(largest, record_bytes_so_far)
    return largest
