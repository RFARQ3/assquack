from __future__ import annotations

from collections.abc import Mapping
from contextlib import AbstractContextManager
from typing import Protocol
from urllib.parse import urlsplit

import duckdb

from assquack._errors import ExportError
from assquack._exports.authentication.base import Authenticator


class PreparationStrategy(Protocol):
    @property
    def kind(self) -> str: ...

    @property
    def fingerprint_inputs(self) -> Mapping[str, object]: ...

    def resolve_uri(self, uri: str, auth: Authenticator) -> str: ...

    def prepare(
        self, connection: duckdb.DuckDBPyConnection, uri: str, auth: Authenticator
    ) -> AbstractContextManager[None]: ...


class PreparationRegistry:
    def __init__(self) -> None:
        self._strategies: dict[str, PreparationStrategy] = {}

    def register(self, scheme: str, strategy: PreparationStrategy) -> None:
        if scheme in self._strategies:
            raise ValueError(f"Preparation already registered for scheme {scheme!r}.")
        self._strategies[scheme] = strategy

    def select(self, uri: str) -> PreparationStrategy:
        scheme = urlsplit(uri).scheme.lower() if "://" in uri else ""
        try:
            return self._strategies[scheme]
        except KeyError:
            raise ExportError(f"Unsupported export URI scheme: {scheme!r}.") from None


def default_preparations() -> PreparationRegistry:
    from assquack._exports.preparation.azure import AzurePreparationStrategy
    from assquack._exports.preparation.local import LocalPreparationStrategy

    registry = PreparationRegistry()
    for scheme in ("", "file"):
        registry.register(scheme, LocalPreparationStrategy())
    for scheme in ("az", "azure", "abfss"):
        registry.register(scheme, AzurePreparationStrategy())
    return registry
