from __future__ import annotations

import json
import os
import re
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any, BinaryIO

from assquack._errors import MaterializationError
from assquack._materialization.models import FragmentFile

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

        directory.mkdir(parents=True, exist_ok=True)

    def write_rows(self, rows: Iterable[Mapping[str, Any]]) -> int:
        if self._finished:
            raise RuntimeError("Cannot write rows after fragments are complete.")

        written = 0
        for row in rows:
            self._open_fragment()
            assert self._file is not None

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
            self._file.write(record)
            self._rows_in_fragment += 1
            self._bytes_in_fragment += len(record)
            written += 1

            if self._bytes_in_fragment >= self._target_bytes:
                self._complete_fragment()

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

        index = len(self._fragments)
        completed_path = self._directory / f"fragment={index:06d}.ndjson"
        self._partial_path.replace(completed_path)
        self._fragments.append(
            FragmentFile(
                index=index, path=str(completed_path), row_count=self._rows_in_fragment
            )
        )

        self._file = None
        self._partial_path = None
        self._rows_in_fragment = 0
        self._bytes_in_fragment = 0
