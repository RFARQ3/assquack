from __future__ import annotations

import logging
from collections.abc import Generator
from contextlib import contextmanager
from dataclasses import dataclass
from uuid import uuid4

import duckdb
from pydantic import SecretStr

from assquack._exports.authentication.credentials import (
    CHAIN_REQUIREMENTS,
    AzureSasCredentials,
    has_required_inputs,
)
from assquack._exports.diagnostics import azure_export_error
from assquack._storage.sql import quote_identifier

_LOGGER = logging.getLogger(__name__)


class AzureAuthenticator:
    @property
    def account_name(self) -> str | None:
        return None

    def register(
        self, connection: duckdb.DuckDBPyConnection, name: str, scope: str
    ) -> None:
        raise NotImplementedError

    @contextmanager
    def authenticate(
        self, connection: duckdb.DuckDBPyConnection, uri: str
    ) -> Generator[None, None, None]:
        """Keep the temporary secret scoped to this output and remove it on failure."""
        name = quote_identifier(f"assquack_export_{uuid4().hex}")
        failure = None
        try:
            self.register(connection, name, uri.rsplit("/", 1)[0] + "/")
        except Exception as error:
            failure = azure_export_error("authentication", error)
        # Do not chain native diagnostics. The destination also detaches context
        # that contextlib can attach while unwinding a failed write.
        if failure is not None:
            raise failure from None

        try:
            yield
        except BaseException as error:
            try:
                connection.execute(f"DROP TEMPORARY SECRET {name}")
            except Exception as cleanup_error:
                _LOGGER.warning("%s", azure_export_error("cleanup", cleanup_error))
            if isinstance(error, Exception):
                failure = azure_export_error("write", error)
            else:
                raise
        else:
            try:
                connection.execute(f"DROP TEMPORARY SECRET {name}")
            except Exception as error:
                failure = azure_export_error("cleanup", error)
        if failure is not None:
            raise failure from None


@dataclass(frozen=True)
class CredentialChainAuth(AzureAuthenticator):
    inputs: dict[str, SecretStr]

    @property
    def chain(self) -> str:
        return ";".join(
            dict.fromkeys(
                method
                for method, required in CHAIN_REQUIREMENTS
                if has_required_inputs(self.inputs, (required,))
            )
        )

    @property
    def fingerprint_inputs(self) -> dict[str, object]:
        return {"chain": self.chain, "environment": self.inputs}

    def register(
        self, connection: duckdb.DuckDBPyConnection, name: str, scope: str
    ) -> None:
        connection.execute(
            f"CREATE TEMPORARY SECRET {name} "
            "(TYPE azure, PROVIDER credential_chain, CHAIN $chain, SCOPE $scope)",
            {"chain": self.chain, "scope": scope},
        )


@dataclass(frozen=True)
class SasAuth(AzureAuthenticator):
    credentials: AzureSasCredentials

    @property
    def fingerprint_inputs(self) -> dict[str, object]:
        return {
            "account_name": self.credentials.account_name,
            "token": self.credentials.token,
        }

    @property
    def account_name(self) -> str:
        return self.credentials.account_name

    def register(
        self, connection: duckdb.DuckDBPyConnection, name: str, scope: str
    ) -> None:
        connection_string = (
            f"AccountName={self.account_name};"
            f"BlobEndpoint=https://{self.account_name}.blob.core.windows.net;"
            f"SharedAccessSignature={self.credentials.token.get_secret_value()}"
        )
        connection.execute(
            f"CREATE TEMPORARY SECRET {name} (TYPE azure, PROVIDER config, "
            "CONNECTION_STRING $connection_string, SCOPE $scope)",
            {"connection_string": connection_string, "scope": scope},
        )
