from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from traceback import clear_frames

import duckdb
from pydantic import BaseModel, ConfigDict, PrivateAttr

from assquack._exports.authentication.base import Authenticator
from assquack._exports.models import ExportTarget
from assquack._exports.preparation.registry import PreparationStrategy
from assquack._storage.tables import TableReference


@dataclass(repr=False)
class _ResolvedState:
    preparation: PreparationStrategy
    authentication: Authenticator = field(repr=False)
    targets: tuple[ExportTarget, ...]


class ExportDestination(BaseModel, ABC):
    """Resolve declarations and export the committed table to concrete targets."""

    model_config = ConfigDict(extra="forbid")

    _resolved: _ResolvedState | None = PrivateAttr(default=None)

    @abstractmethod
    def resolve(
        self, export: str | Mapping[str, str], *, asset_name: str
    ) -> tuple[ExportTarget, ...]: ...

    def _capture_resolution(
        self,
        fingerprint: str,
        preparation: PreparationStrategy,
        authentication: Authenticator,
        targets: tuple[ExportTarget, ...],
    ) -> tuple[ExportTarget, ...]:
        """Retain resolved inputs and attach the shared fingerprint to targets."""
        targets = tuple(
            replace(target, destination_fingerprint=fingerprint) for target in targets
        )
        self._resolved = _ResolvedState(preparation, authentication, targets)
        return targets

    def _release(self) -> None:
        """Release private inputs captured for an operation."""
        self._resolved = None

    @abstractmethod
    def export(
        self,
        connection: duckdb.DuckDBPyConnection,
        source: TableReference,
        target: ExportTarget,
    ) -> None: ...


def clear_exception_locals(error: BaseException) -> None:
    """Caught errors must not retain captured credentials through completed frames."""
    pending = [error]
    visited: set[int] = set()
    while pending:
        current = pending.pop()
        if id(current) in visited:
            continue
        visited.add(id(current))
        if current.__traceback__ is not None:
            clear_frames(current.__traceback__)
        if current.__cause__ is not None:
            pending.append(current.__cause__)
        if current.__context__ is not None:
            pending.append(current.__context__)
