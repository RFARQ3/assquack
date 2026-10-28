from __future__ import annotations

import logging
import re
import stat
from pathlib import Path

from assquack._materialization.fragments import fragment_run_directory
from assquack._storage.metadata import MetadataRepository

_LOGGER = logging.getLogger(__name__)
_RUN_ID = re.compile(r"(?:[0-9a-f]{32}|[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12})")
_FRAGMENT_FILE = re.compile(
    r"(?:_COMPLETE|fragment=\d{6,}\.(?:ndjson|partial)|copy_\d{6,}_\d+\.partial)"
)


def prune_fragments(
    metadata: MetadataRepository, root: Path, asset_id: str, asset_name: str
) -> None:
    """Best-effort retention under the writer lock; never change a run's outcome."""

    try:
        obsolete = metadata.find_obsolete_fragment_runs(asset_id)
    except Exception:
        _LOGGER.warning(
            "Could not select obsolete fragments for %s", asset_id, exc_info=True
        )
        return

    for run_id in obsolete:
        try:
            _remove_run_directory(root, asset_name, run_id)
        except Exception:
            _LOGGER.warning(
                "Could not prune fragments for run %s", run_id, exc_info=True
            )


def _reject_redirected_path(path: Path) -> None:
    # lstat checks the original spelling: resolve() alone would conceal links.
    for part in (*reversed(path.parents), path):
        info = part.lstat()
        if stat.S_ISLNK(info.st_mode) or (
            getattr(info, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT
        ):
            raise ValueError(f"Refusing redirected fragment path: {part}")


def _remove_run_directory(root: Path, asset_name: str, run_id: str) -> None:
    if _RUN_ID.fullmatch(run_id) is None:
        raise ValueError(f"Unsafe fragment run identifier: {run_id!r}")
    if ".." in root.parts:
        raise ValueError(f"Unsafe fragment root: {root}")

    root = root.absolute()
    directory = fragment_run_directory(root, asset_name, run_id)
    try:
        _reject_redirected_path(directory)
    except FileNotFoundError:
        return
    if directory.resolve().parent.parent != root.resolve():
        raise ValueError(f"Fragment directory escapes configured root: {directory}")

    # Capture produces only flat files. Unknown content is retained for inspection;
    # never recurse into directories supplied by another process or application.
    files = list(directory.iterdir())
    for path in files:
        _reject_redirected_path(path)
        if not stat.S_ISREG(path.lstat().st_mode) or not _FRAGMENT_FILE.fullmatch(
            path.name
        ):
            raise ValueError(f"Unexpected content in fragment directory: {path}")
    for path in files:
        _reject_redirected_path(path)
        path.unlink()
    _reject_redirected_path(directory)
    directory.rmdir()
