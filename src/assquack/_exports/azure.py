from __future__ import annotations

import logging
from collections.abc import Generator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Protocol
from urllib.parse import urlparse
from uuid import uuid4

import duckdb

from assquack._config import ExportsConfig
from assquack._errors import ConfigurationError, ExportError
from assquack._exports.azure_credentials import (
    AzureSasCredentials,
    AzureSasProvider,
    EnvironmentSasProvider,
)
from assquack._storage.sql import quote_identifier

AZURE_SCHEMES = frozenset({"az", "azure", "abfss"})
_LOGGER = logging.getLogger(__name__)


class AzureAuthStrategy(Protocol):
    @property
    def account_name(self) -> str | None: ...

    def register(
        self, connection: duckdb.DuckDBPyConnection, name: str, scope: str
    ) -> None: ...


@dataclass(frozen=True)
class CredentialChainAuth:
    config: ExportsConfig

    @property
    def account_name(self) -> str | None:
        return self.config.azure_account_name

    def register(
        self, connection: duckdb.DuckDBPyConnection, name: str, scope: str
    ) -> None:
        parameters = {"scope": scope}
        options = ["TYPE azure", "PROVIDER credential_chain", "SCOPE $scope"]
        if self.config.azure_chain is not None:
            options.append("CHAIN $chain")
            parameters["chain"] = self.config.azure_chain
        if self.account_name is not None:
            options.append("ACCOUNT_NAME $account_name")
            parameters["account_name"] = self.account_name
        connection.execute(
            f"CREATE TEMPORARY SECRET {name} ({', '.join(options)})", parameters
        )


@dataclass(frozen=True)
class SasAuth:
    credentials: AzureSasCredentials

    @property
    def account_name(self) -> str:
        return self.credentials.account_name

    def register(
        self, connection: duckdb.DuckDBPyConnection, name: str, scope: str
    ) -> None:
        # DuckDB checks AccountName for fully qualified URIs. The SDK uses the
        # HTTPS endpoint and preserves the already encoded SAS query unchanged.
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


def select_azure_auth(
    config: ExportsConfig, provider: AzureSasProvider
) -> AzureAuthStrategy:
    if config.azure_auth == "credential_chain":
        return CredentialChainAuth(config)
    credentials = provider.resolve()
    if credentials is None:
        if config.azure_auth == "sas":
            raise ConfigurationError(
                "SAS authentication requires AZURE_STORAGE_SAS_TOKEN and "
                "AZURE_STORAGE_ACCOUNT_NAME."
            )
        return CredentialChainAuth(config)
    if config.azure_chain is not None:
        raise ConfigurationError(
            "SAS credentials conflict with azure_chain; select "
            "azure_auth='credential_chain' or remove azure_chain."
        )
    if config.azure_account_name not in (None, credentials.account_name):
        raise ConfigurationError("Azure SAS account conflicts with azure_account_name.")
    return SasAuth(credentials)


def validate_azure_target(uri: str, account_name: str | None) -> None:
    parsed = urlparse(uri)
    endpoint = (
        ".dfs.core.windows.net"
        if parsed.scheme == "abfss"
        else ".blob.core.windows.net"
    )
    if parsed.query or parsed.fragment or parsed.username or parsed.password:
        raise ExportError(
            "Azure export URIs must not contain credentials or query strings."
        )
    if not parsed.netloc or not parsed.path or parsed.path.endswith("/"):
        raise ExportError("Azure exports require a container and an output file path.")
    if parsed.netloc.endswith(endpoint):
        if not parsed.path.lstrip("/").partition("/")[2]:
            raise ExportError(
                "Fully qualified Azure URIs require a container and file."
            )
        if account_name is not None and parsed.netloc != account_name + endpoint:
            raise ConfigurationError(
                "Azure export URI and authentication account differ."
            )
    elif "." in parsed.netloc or ":" in parsed.netloc:
        raise ExportError("Azure exports require a supported public-cloud endpoint.")
    elif account_name is None:
        raise ExportError(
            "Azure exports require a fully qualified DuckDB URI or "
            "exports.azure_account_name."
        )


@contextmanager
def azure_export_auth(
    connection: duckdb.DuckDBPyConnection,
    uri: str,
    config: ExportsConfig,
    provider: AzureSasProvider | None = None,
) -> Generator[None, None, None]:
    """Resolve credentials at use, register on the COPY connection, then clean up."""
    strategy = select_azure_auth(config, provider or EnvironmentSasProvider())
    validate_azure_target(uri, strategy.account_name)
    secret_name = quote_identifier(f"assquack_export_{uuid4().hex}")
    registered = False
    try:
        # Deployments provision extensions; Assquack never installs them here.
        connection.execute("LOAD azure")
        strategy.register(connection, secret_name, uri.rsplit("/", 1)[0] + "/")
        registered = True
        yield
    except BaseException as error:
        if registered:
            try:
                connection.execute(f"DROP TEMPORARY SECRET {secret_name}")
            except Exception:
                _LOGGER.warning("Could not remove temporary Azure export secret.")
        # SDK errors can echo a signed URL or connection string. Never expose
        # these diagnostics through the public exception or its printed chain.
        if isinstance(strategy, SasAuth) and isinstance(error, Exception):
            raise ExportError(
                "Azure SAS export failed; check credentials and permissions."
            ) from None
        raise
    else:
        try:
            connection.execute(f"DROP TEMPORARY SECRET {secret_name}")
        except Exception:
            if isinstance(strategy, SasAuth):
                raise ExportError(
                    "Could not remove temporary Azure export secret."
                ) from None
            raise
