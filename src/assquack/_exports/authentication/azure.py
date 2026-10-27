from __future__ import annotations

import logging
from collections.abc import Generator
from contextlib import contextmanager
from dataclasses import dataclass
from uuid import uuid4

import duckdb
from pydantic import SecretStr

from assquack._errors import ConfigurationError, ExportError
from assquack._exports.authentication.credentials import (
    CHAIN_REQUIREMENTS,
    AzureSasCredentials,
)
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
        registered = False
        try:
            self.register(connection, name, uri.rsplit("/", 1)[0] + "/")
            registered = True
            yield
        except BaseException as error:
            if registered:
                try:
                    connection.execute(f"DROP TEMPORARY SECRET {name}")
                except Exception:
                    _LOGGER.warning("Could not remove temporary Azure export secret.")
            # Native diagnostics may contain signed URLs even for identity auth.
            if isinstance(error, Exception):
                raise ExportError(
                    "Azure export failed; check authentication and permissions."
                ) from None
            raise
        else:
            try:
                connection.execute(f"DROP TEMPORARY SECRET {name}")
            except Exception:
                raise ExportError(
                    "Could not remove temporary Azure export secret."
                ) from None


@dataclass(frozen=True)
class CredentialChainAuth(AzureAuthenticator):
    inputs: dict[str, SecretStr]

    def __post_init__(self) -> None:
        if not self.chain:
            raise ConfigurationError(
                "Credential-chain authentication requires AZURE_TENANT_ID and "
                "AZURE_CLIENT_ID plus AZURE_CLIENT_SECRET, "
                "AZURE_CLIENT_CERTIFICATE_PATH, or AZURE_FEDERATED_TOKEN_FILE. "
                "CLI and managed-identity fallback are disabled."
            )

    @property
    def chain(self) -> str:
        return ";".join(
            dict.fromkeys(
                method
                for method, required in CHAIN_REQUIREMENTS
                if all(name in self.inputs for name in required)
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
