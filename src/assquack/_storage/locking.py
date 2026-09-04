from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from time import sleep
from types import TracebackType
from typing import BinaryIO

if sys.platform == "win32":
    import msvcrt
else:
    import fcntl

_LOCK_RETRY_SECONDS = 0.05


class WriterLock:
    """Serialize writers across async tasks and operating-system processes."""

    def __init__(self, database_path: Path) -> None:
        self._lock_path = Path(f"{database_path.resolve()}.lock")
        self._handle: BinaryIO | None = None

    def __enter__(self) -> None:
        handle = _open_lock_file(self._lock_path)
        try:
            while not _try_lock(handle):
                sleep(_LOCK_RETRY_SECONDS)
        except BaseException:
            handle.close()
            raise

        self._handle = handle

    def __exit__(
        self,
        exception_type: type[BaseException] | None,
        exception: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self._release()

    async def __aenter__(self) -> None:
        handle = _open_lock_file(self._lock_path)
        try:
            while not _try_lock(handle):
                await asyncio.sleep(_LOCK_RETRY_SECONDS)
        except BaseException:
            handle.close()
            raise

        self._handle = handle

    async def __aexit__(
        self,
        exception_type: type[BaseException] | None,
        exception: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self._release()

    def _release(self) -> None:
        handle = self._handle
        if handle is None:
            return

        try:
            _unlock(handle)
        finally:
            handle.close()
            self._handle = None


def writer_lock(database_path: Path) -> WriterLock:
    return WriterLock(database_path)


def _open_lock_file(lock_path: Path) -> BinaryIO:
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    handle = lock_path.open("a+b")
    handle.seek(0, 2)
    if handle.tell() == 0:
        handle.write(b"\0")
        handle.flush()
    return handle


if sys.platform == "win32":

    def _try_lock(handle: BinaryIO) -> bool:
        handle.seek(0)
        try:
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError:
            return False
        return True

    def _unlock(handle: BinaryIO) -> None:
        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)

else:

    def _try_lock(handle: BinaryIO) -> bool:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return False
        return True

    def _unlock(handle: BinaryIO) -> None:
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
