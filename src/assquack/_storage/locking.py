from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from threading import Lock, RLock

_registry_guard = Lock()
_database_locks: dict[str, RLock] = {}


@contextmanager
def writer_lock(database_path: Path) -> Iterator[None]:
    """Serialize prototype writers in this Python process by database path."""

    key = str(database_path.resolve())
    with _registry_guard:
        lock = _database_locks.setdefault(key, RLock())

    # TODO: Add an inter-process file lock for multi-process materializers.
    with lock:
        yield
