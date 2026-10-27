from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import urlsplit
from urllib.request import url2pathname

import duckdb

from assquack._errors import ExportError
from assquack._exports.authentication.base import Authenticator


class LocalPreparationStrategy:
    kind = "local"

    @property
    def fingerprint_inputs(self) -> dict[str, object]:
        return {}

    def resolve_uri(self, uri: str, auth: Authenticator) -> str:
        if "://" in uri:
            parsed = urlsplit(uri)
            if (
                parsed.query
                or parsed.fragment
                or parsed.netloc not in ("", "localhost")
            ):
                raise ExportError(
                    "File URIs require a local path without query or host."
                )
            path = Path(url2pathname(parsed.path))
        else:
            path = Path(uri)
        return str(path.resolve())

    @contextmanager
    def prepare(
        self, connection: duckdb.DuckDBPyConnection, uri: str, auth: Authenticator
    ) -> Generator[None, None, None]:
        Path(uri).parent.mkdir(parents=True, exist_ok=True)
        with auth.authenticate(connection, uri):
            yield
