from contextlib import AbstractContextManager, nullcontext

import duckdb


class LocalAuthenticator:
    @property
    def fingerprint_inputs(self) -> dict[str, object]:
        return {}

    def authenticate(
        self, connection: duckdb.DuckDBPyConnection, uri: str
    ) -> AbstractContextManager[None]:
        return nullcontext()
