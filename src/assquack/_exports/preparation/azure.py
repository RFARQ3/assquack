import sys
from collections.abc import Generator
from contextlib import contextmanager
from typing import cast
from urllib.parse import urlparse

import duckdb

from assquack._errors import ConfigurationError, ExportError
from assquack._exports.authentication.azure import AzureAuthenticator
from assquack._exports.authentication.base import Authenticator


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
            "an account supplied by SAS authentication."
        )


class AzurePreparationStrategy:
    kind = "azure"

    @property
    def fingerprint_inputs(self) -> dict[str, object]:
        return {}

    def resolve_uri(self, uri: str, auth: Authenticator) -> str:
        # The registry pairs this preparation with an Azure authenticator.
        azure_auth = cast(AzureAuthenticator, auth)
        validate_azure_target(uri, azure_auth.account_name)
        return uri

    @contextmanager
    def prepare(
        self, connection: duckdb.DuckDBPyConnection, uri: str, auth: Authenticator
    ) -> Generator[None, None, None]:
        azure_auth = cast(AzureAuthenticator, auth)
        validate_azure_target(uri, azure_auth.account_name)
        # Deployments provision extensions; Assquack never installs them here.
        connection.execute("LOAD azure")
        # TODO: Remove this transport override once the supported Azure extension
        # artifacts include the CA discovery fix and default transport is verified
        # in our Linux deployment images, without certificate-path symlinks.
        # https://github.com/duckdb/duckdb-azure/issues/185
        # https://github.com/duckdb/duckdb-azure/pull/186
        # The default SDK transport can use a compile-time Red Hat CA path on
        # Debian/Ubuntu. Explicit curl enables DuckDB's distro CA-bundle search;
        # otherwise HTTPS can fail before token acquisition, misleadingly reported
        # as a ChainedTokenCredential failure. Related Azure SDK reports:
        # https://github.com/Azure/azure-sdk-for-cpp/issues/4983
        # https://github.com/Azure/azure-sdk-for-cpp/issues/4738
        # Keep default elsewhere: forcing curl broke Windows certificate validation
        # in Space Calculator's verification. Apply on this export connection before
        # authentication creates a client; setting it in a startup process is not
        # sufficient. Before removal, verify token acquisition and Azure reads/writes
        # on Linux plus Windows/macOS regressions, update the platform tests/docs,
        # and ensure cached extension binaries are upgraded too. A merged upstream
        # PR or a Python DuckDB upgrade alone does not establish those conditions.
        transport = "curl" if sys.platform == "linux" else "default"
        connection.execute("SET azure_transport_option_type = ?", [transport])
        with auth.authenticate(connection, uri):
            yield
