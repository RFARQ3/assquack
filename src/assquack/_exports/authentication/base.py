from __future__ import annotations

from collections.abc import Mapping
from contextlib import AbstractContextManager
from typing import Protocol

import duckdb


class Authenticator(Protocol):
    @property
    def fingerprint_inputs(self) -> Mapping[str, object]: ...

    def authenticate(
        self, connection: duckdb.DuckDBPyConnection, uri: str
    ) -> AbstractContextManager[None]: ...
