from __future__ import annotations

import importlib
import inspect
import json
import os
import re
from collections.abc import AsyncIterable, Iterable, Mapping
from pathlib import Path
from typing import Any, BinaryIO, cast

import duckdb

from assquack._config import AssquackConfig
from assquack._errors import MaterializationError
from assquack._materialization.models import FragmentFile
from assquack._result import AssquackResult
from assquack._storage.database import open_database
from assquack._storage.sql import quote_literal, quote_table

_UNSAFE_PARTITION_VALUE = re.compile(r"[^A-Za-z0-9_.-]+")


def fragment_run_directory(root: Path, asset_name: str, run_id: str) -> Path:
    """Build a local, Hive-style path for one asset materialization run."""

    safe_asset_name = _UNSAFE_PARTITION_VALUE.sub("_", asset_name).strip("_")
    if not safe_asset_name:
        safe_asset_name = "asset"

    return root / f"asset={safe_asset_name}" / f"run_id={run_id}"


class NDJSONFragmentWriter:
    """Write JSON-shaped rows into bounded, completed local fragments."""

    def __init__(self, directory: Path, *, target_bytes: int) -> None:
        if target_bytes < 1:
            raise ValueError("Fragment target_bytes must be positive.")

        self._directory = directory
        self._target_bytes = target_bytes
        self._file: BinaryIO | None = None
        self._partial_path: Path | None = None
        self._rows_in_fragment = 0
        self._bytes_in_fragment = 0
        self._fragments: list[FragmentFile] = []
        self._finished = False
        self._copy_index = 0

        # Existing evidence may outlive its catalog; never reuse a run directory.
        directory.mkdir(parents=True, exist_ok=False)

    def write_rows(self, rows: Mapping[str, Any] | Iterable[Mapping[str, Any]]) -> int:
        if self._finished:
            raise RuntimeError("Cannot write rows after fragments are complete.")

        records = (
            (cast(Mapping[str, Any], rows),) if isinstance(rows, Mapping) else rows
        )

        written = 0
        for row in records:
            try:
                encoded = json.dumps(
                    dict(row),
                    ensure_ascii=False,
                    separators=(",", ":"),
                )
            except (TypeError, ValueError) as error:
                raise MaterializationError(
                    "Asset rows must contain JSON-serializable values before "
                    "they can be written to local NDJSON fragments."
                ) from error

            # Count the exact bytes written, including the NDJSON record delimiter.
            record = (encoded + "\n").encode("utf-8")
            self._open_fragment()
            assert self._file is not None
            self._file.write(record)
            self._rows_in_fragment += 1
            self._bytes_in_fragment += len(record)
            written += 1

            if self._bytes_in_fragment >= self._target_bytes:
                self._complete_fragment()

        return written

    def write_relation(self, relation: duckdb.DuckDBPyRelation) -> int:
        """COPY native rows, then register only nonempty completed files."""
        if self._finished:
            raise RuntimeError("Cannot write rows after fragments are complete.")
        if self._file is not None:
            self._complete_fragment()

        # Each invocation has its own names, including empty or failed COPY output.
        prefix = f"copy_{self._copy_index:06d}_"
        self._copy_index += 1
        # DuckDB's relation.query(COPY ...) creates a persistent source view.
        # Stage only during capture so read-only relations can use native JSON
        # COPY without mutating their source or requiring pandas/Arrow.
        intermediate = self._directory / f"{prefix}input.parquet"
        intermediate_sql = quote_literal(str(intermediate))
        try:
            # Serialize with the source session before transport: Parquet alone
            # can round huge integers and lose timezone/interval/union details.
            relation.project("to_json(COLUMNS(*))").write_parquet(str(intermediate))
            with duckdb.connect() as connection:
                connection.execute(
                    f"COPY (SELECT * FROM read_parquet({intermediate_sql})) "
                    f"TO {quote_literal(str(self._directory))} "
                    f"(FORMAT JSON, ARRAY false, FILE_SIZE_BYTES {self._target_bytes}, "
                    "FILE_EXTENSION partial, OVERWRITE_OR_IGNORE true, "
                    f"FILENAME_PATTERN {quote_literal(prefix + '{i}')})"
                )
        finally:
            intermediate.unlink(missing_ok=True)

        written = 0
        for partial_path in sorted(self._directory.glob(f"{prefix}*.partial")):
            # JSON COPY has no per-file RETURN_STATS. Count delimiters in bounded
            # buffers; escaped newlines inside records do not contain a literal LF.
            with partial_path.open("rb") as source:
                row_count = sum(
                    block.count(b"\n")
                    for block in iter(lambda: source.read(1024 * 1024), b"")
                )
            if row_count == 0:
                partial_path.unlink()
                continue
            self._register_fragment(partial_path, row_count)
            written += row_count
        return written

    def finish(self) -> list[FragmentFile]:
        if not self._finished:
            if self._file is not None:
                self._complete_fragment()

            marker = self._directory / "_COMPLETE"
            marker.touch(exist_ok=False)
            self._finished = True

        return list(self._fragments)

    def abort(self) -> None:
        if self._file is not None:
            self._file.close()
            self._file = None

    def _open_fragment(self) -> None:
        if self._file is not None:
            return

        index = len(self._fragments)
        self._partial_path = self._directory / f"fragment={index:06d}.partial"
        self._file = self._partial_path.open("xb")
        self._rows_in_fragment = 0
        self._bytes_in_fragment = 0

    def _complete_fragment(self) -> None:
        assert self._file is not None
        assert self._partial_path is not None

        self._file.flush()
        os.fsync(self._file.fileno())
        self._file.close()

        self._register_fragment(self._partial_path, self._rows_in_fragment)

        self._file = None
        self._partial_path = None
        self._rows_in_fragment = 0
        self._bytes_in_fragment = 0

    def _register_fragment(self, partial_path: Path, row_count: int) -> None:
        index = len(self._fragments)
        completed_path = self._directory / f"fragment={index:06d}.ndjson"
        partial_path.replace(completed_path)
        self._fragments.append(
            FragmentFile(index=index, path=str(completed_path), row_count=row_count)
        )


async def write_fragments(
    value: object, writer: NDJSONFragmentWriter, config: AssquackConfig
) -> int:
    """Dispatch each yielded value before considering generic iteration."""
    # Import here because asset execution itself imports the capture pipeline.
    from assquack._api import AssquackAsset

    if value is None:
        return 0
    if isinstance(value, Mapping):
        return writer.write_rows(cast(Mapping[str, Any], value))
    if isinstance(value, AssquackAsset):
        value = await value(assquack_config=config)
    if isinstance(value, AssquackResult):
        connection = open_database(value.config)
        try:
            relation = connection.table(
                quote_table(value.table.schema_name, value.table.table_name)
            )
            return writer.write_relation(relation)
        finally:
            connection.close()
    if isinstance(value, duckdb.DuckDBPyRelation):
        return writer.write_relation(value)

    # Optional native libraries are imported only for their own objects, keeping
    # ordinary Python capture independent of pandas and Arrow installations.
    modules = {base.__module__.split(".")[0] for base in type(value).__mro__}
    if "pandas" in modules or "pyarrow" in modules:
        with duckdb.connect() as connection:
            if "pandas" in modules:
                pd = importlib.import_module("pandas")

                if isinstance(value, pd.DataFrame):
                    if cast(Any, value).empty:
                        return 0
                    return writer.write_relation(cast(Any, connection).from_df(value))
            if "pyarrow" in modules:
                pa = importlib.import_module("pyarrow")

                if isinstance(value, (pa.Table, pa.RecordBatch)):
                    if cast(Any, value).num_rows == 0:
                        return 0
                    return writer.write_relation(connection.from_arrow(value))

    written = 0
    if isinstance(value, AsyncIterable):
        iterator = aiter(cast(AsyncIterable[object], value))
        try:
            async for item in iterator:
                written += await write_fragments(item, writer, config)
        except BaseException as error:
            await _close_failed_generator(iterator, error)
            raise
        return written
    if isinstance(value, Iterable) and not isinstance(value, (str, bytes, bytearray)):
        iterator = iter(cast(Iterable[object], value))
        try:
            for item in iterator:
                written += await write_fragments(item, writer, config)
        except BaseException as error:
            await _close_failed_generator(iterator, error)
            raise
        return written
    raise MaterializationError(
        "Assets must return or yield mappings, pandas DataFrames, Arrow tables or "
        "record batches, DuckDB relations, Assquack results/assets, or iterables "
        "of these values. Decode HTTP responses before returning their JSON data."
    )


async def _close_failed_generator(iterator: object, error: BaseException) -> None:
    # Generator scopes must unwind in the consuming task, before its asset context
    # exits. Later garbage collection may run in a different ContextVar context.
    try:
        if inspect.isasyncgen(iterator):
            await iterator.aclose()
        elif inspect.isgenerator(iterator):
            iterator.close()
    except BaseException as cleanup_error:
        error.add_note(f"Generator cleanup also failed: {cleanup_error}")
