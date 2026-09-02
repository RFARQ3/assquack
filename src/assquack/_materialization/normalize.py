from __future__ import annotations

from collections.abc import AsyncIterable, AsyncIterator, Iterable, Mapping
from typing import Any

from assquack._errors import MaterializationError
from assquack._materialization.models import NormalizedChunk


async def normalize(
    value: object,
    *,
    chunk_size: int,
) -> AsyncIterator[NormalizedChunk]:
    """Flatten supported values and yield bounded chunks of mapping rows."""

    pending: list[dict[str, Any]] = []
    batch_id = 0

    async for row in _iter_rows(value):
        pending.append(dict(row))
        if len(pending) < chunk_size:
            continue

        yield NormalizedChunk(batch_id=batch_id, rows=pending)
        pending = []
        batch_id += 1

    if pending:
        yield NormalizedChunk(batch_id=batch_id, rows=pending)


async def _iter_rows(value: object) -> AsyncIterator[Mapping[str, Any]]:
    if value is None:
        return
    if isinstance(value, Mapping):
        yield value
        return

    # A pandas DataFrame is cheap to support without making pandas a dependency.
    to_dict = getattr(value, "to_dict", None)
    if callable(to_dict) and value.__class__.__name__ == "DataFrame":
        for row in to_dict(orient="records"):
            yield row
        return

    if isinstance(value, AsyncIterable):
        async for item in value:
            async for row in _iter_rows(item):
                yield row
        return
    if isinstance(value, Iterable) and not isinstance(value, (str, bytes, bytearray)):
        for item in value:
            async for row in _iter_rows(item):
                yield row
        return

    # TODO: Add native Arrow, DuckDB relation, and httpx.Response normalization.
    raise MaterializationError(
        "Prototype assets must return or yield mappings, lists of mappings, "
        "iterables of mapping batches, or a pandas DataFrame."
    )
