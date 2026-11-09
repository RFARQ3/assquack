"""Compatibility export strategies for committed asset tables."""

from __future__ import annotations

from collections.abc import Generator, Mapping
from contextlib import contextmanager

from assquack._errors import ExportError
from assquack._exports.destinations import ExportDestination
from assquack._exports.models import ExportTarget


@contextmanager
def resolve_exports(
    export: str | Mapping[str, str] | None,
    asset_name: str,
    configured: ExportDestination | None,
) -> Generator[tuple[tuple[ExportDestination, ExportTarget], ...], None, None]:
    """Own destination state until cache lookup and all writes have finished."""
    resolved: list[tuple[ExportDestination, ExportTarget]] = []
    destination = configured.model_copy() if configured is not None else None
    try:
        if destination is not None:
            destination._release()  # pyright: ignore[reportPrivateUsage]
            for target in destination.resolve(export, asset_name=asset_name):
                if not target.destination_fingerprint:
                    raise ExportError(
                        "Resolved destinations must supply a fingerprint."
                    )
                resolved.append((destination, target))
        yield tuple(resolved)
    finally:
        if destination is not None:
            destination._release()  # pyright: ignore[reportPrivateUsage]
        resolved.clear()
