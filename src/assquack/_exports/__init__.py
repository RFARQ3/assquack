"""Compatibility export strategies for committed asset tables."""

from __future__ import annotations

from collections.abc import Generator, Mapping, Sequence
from contextlib import contextmanager

from assquack._errors import ExportError
from assquack._exports.destinations import ExportDestination
from assquack._exports.models import ExportTarget


@contextmanager
def resolve_exports(
    export: str | Mapping[str, str] | None,
    asset_name: str,
    destinations: Sequence[ExportDestination],
) -> Generator[tuple[tuple[ExportDestination, ExportTarget], ...], None, None]:
    """Own destination state until cache lookup and all writes have finished."""
    resolved: list[tuple[ExportDestination, ExportTarget]] = []
    operation_destinations: list[ExportDestination] = []
    seen: dict[str, str] = {}
    try:
        if export is not None:
            for configured in destinations:
                destination = configured.model_copy()
                destination._release()  # pyright: ignore[reportPrivateUsage]
                operation_destinations.append(destination)
                for target in destination.resolve(export, asset_name=asset_name):
                    path = target.resolved_uri
                    fingerprint = target.destination_fingerprint
                    if not fingerprint:
                        raise ExportError(
                            "Resolved destinations must supply a fingerprint."
                        )
                    if path in seen and seen[path] != fingerprint:
                        raise ExportError(
                            "Different destination configurations resolve "
                            "to the same write path."
                        )
                    seen[path] = fingerprint
                    resolved.append((destination, target))
        yield tuple(resolved)
    finally:
        for destination in operation_destinations:
            destination._release()  # pyright: ignore[reportPrivateUsage]
        resolved.clear()
        operation_destinations.clear()
