import traceback
from contextlib import contextmanager
from unittest.mock import MagicMock

import duckdb
import pytest

from assquack import FileSystemDestination
from assquack._errors import ConfigurationError, ExportError
from assquack._exports.authentication.azure import CredentialChainAuth, SasAuth
from assquack._exports.authentication.credentials import (
    EnvironmentCredentialProvider,
    load_credentials,
)
from assquack._exports.authentication.registry import default_authenticators
from assquack._exports.preparation.azure import AzurePreparationStrategy


def select_azure_auth(method, provider):
    return default_authenticators().select("azure", method, provider)


@contextmanager
def azure_export_auth(connection, uri, method):
    auth = select_azure_auth(method, EnvironmentCredentialProvider())
    with AzurePreparationStrategy().prepare(connection, uri, auth):
        yield


URI = "abfss://exampleaccount.dfs.core.windows.net/lake/orders.parquet"


@pytest.fixture(autouse=True)
def environment(monkeypatch):
    # Replace the read boundary without retrieving any real credentials.
    values: dict[str, str] = {}
    monkeypatch.setattr(
        "assquack._exports.authentication.credentials.getenv", values.get
    )
    return values


@pytest.mark.parametrize(
    "token,account",
    [
        (None, None),
        ("", " "),
        ("sig=fake%3D", "exampleaccount"),
        (" ?sig=fake%3D ", " exampleaccount "),
        ("sig=fake", None),
        (None, "exampleaccount"),
        (" ", "exampleaccount"),
        ("sig=fake", ""),
        ("malformed-token", "INVALID ACCOUNT"),
    ],
)
@pytest.mark.parametrize("method", ["auto", "credential_chain"])
def test_chain_does_not_read_sas_values(token, account, method):
    values = {
        "AZURE_STORAGE_SAS_TOKEN": token,
        "AZURE_STORAGE_ACCOUNT_NAME": account,
        "AZURE_TENANT_ID": "tenant",
        "AZURE_CLIENT_ID": "client",
        "AZURE_FEDERATED_TOKEN_FILE": "synthetic.jwt",
    }
    provider = MagicMock(spec=EnvironmentCredentialProvider)
    provider.get.side_effect = values.get
    assert isinstance(select_azure_auth(method, provider), CredentialChainAuth)
    provider.load.assert_awaited_once()
    requested = [call.args[0] for call in provider.get.call_args_list]
    assert "AZURE_STORAGE_SAS_TOKEN" not in requested
    assert "AZURE_STORAGE_ACCOUNT_NAME" not in requested


def test_explicit_chain_requires_environment_inputs():
    provider = MagicMock(spec=EnvironmentCredentialProvider)
    provider.get.return_value = None
    with pytest.raises(ConfigurationError, match="AZURE_TENANT_ID"):
        select_azure_auth("credential_chain", provider)
    provider.load.assert_awaited_once()


def test_explicit_sas_requires_credentials(environment):
    with pytest.raises(ConfigurationError, match="requires AZURE_STORAGE"):
        select_azure_auth("sas", EnvironmentCredentialProvider())


@pytest.mark.parametrize(
    "token",
    [
        "?",
        "https://host/file?sig=fake",
        "sig=fake;AccountKey=bad",
        "sig=fake\ncontrol",
        "sig=",
        "??sig=fake",
    ],
)
def test_invalid_sas_is_rejected_without_disclosure(token, environment):
    environment.update(
        AZURE_STORAGE_ACCOUNT_NAME="exampleaccount", AZURE_STORAGE_SAS_TOKEN=token
    )
    with pytest.raises(ConfigurationError) as error:
        load_credentials(EnvironmentCredentialProvider())
    assert token not in str(error.value)


@pytest.mark.parametrize("scheme", ["az", "azure", "abfss"])
@pytest.mark.parametrize("qualified", [False, True])
def test_sas_registration_connection_string_and_scope(scheme, qualified, environment):
    token = "sv=2025-01-05&sig=fake%2B%2F%3D"
    environment.update(
        AZURE_STORAGE_ACCOUNT_NAME="exampleaccount", AZURE_STORAGE_SAS_TOKEN="?" + token
    )
    endpoint = "dfs" if scheme == "abfss" else "blob"
    prefix = f"exampleaccount.{endpoint}.core.windows.net/" if qualified else ""
    uri = f"{scheme}://{prefix}lake/O'Brien/orders.parquet"
    connection = MagicMock(spec=duckdb.DuckDBPyConnection)
    with azure_export_auth(connection, uri, "sas"):
        pass
    sql, parameters = connection.execute.call_args_list[1].args
    assert "PROVIDER config" in sql
    assert token not in sql
    assert parameters == {
        "connection_string": "AccountName=exampleaccount;BlobEndpoint=https://exampleaccount.blob.core.windows.net;SharedAccessSignature="
        + token,
        "scope": uri.rsplit("/", 1)[0] + "/",
    }
    with duckdb.connect(config={"autoload_known_extensions": False}) as parser:
        assert len(parser.extract_statements(sql)) == 1
    assert (
        connection.execute.call_args_list[-1]
        .args[0]
        .startswith("DROP TEMPORARY SECRET")
    )


@pytest.mark.parametrize("stage", ["LOAD", "CREATE", "COPY", "DROP", "COPY_AND_DROP"])
def test_sas_errors_do_not_expose_signed_diagnostics(stage, environment, caplog):
    token = "sig=synthetic-sensitive"
    environment.update(
        AZURE_STORAGE_ACCOUNT_NAME="exampleaccount", AZURE_STORAGE_SAS_TOKEN=token
    )
    connection = MagicMock(spec=duckdb.DuckDBPyConnection)

    def execute(sql, parameters=None):
        if sql.startswith(stage) or (
            stage == "COPY_AND_DROP" and sql.startswith(("COPY", "DROP"))
        ):
            raise RuntimeError("signed URL?" + token)

    connection.execute.side_effect = execute
    with (
        pytest.raises(ExportError) as error,
        azure_export_auth(connection, URI, "sas"),
    ):
        connection.execute("COPY synthetic")
    assert token not in "".join(traceback.format_exception(error.value))
    assert token not in caplog.text
    assert error.value.__suppress_context__


@pytest.mark.parametrize(
    "uri",
    [
        URI + "?sig=fake",
        URI + "#sig=fake",
        URI.replace("exampleaccount", "otheraccount"),
        "abfss://exampleaccount.dfs.core.windows.net/lake",
        "abfss://exampleaccount.dfs.core.windows.net:443/lake/orders.parquet",
        "abfss://exampleaccount.dfs.custom.invalid/lake/orders.parquet",
    ],
)
def test_sas_rejects_signed_or_mismatched_targets(uri, environment):
    environment.update(
        AZURE_STORAGE_ACCOUNT_NAME="exampleaccount", AZURE_STORAGE_SAS_TOKEN="sig=fake"
    )
    connection = MagicMock(spec=duckdb.DuckDBPyConnection)
    with (
        pytest.raises((ConfigurationError, ExportError)),
        azure_export_auth(connection, uri, "sas"),
    ):
        pass
    connection.execute.assert_not_called()


def test_sas_credentials_are_resolved_at_use_time(environment):
    config = "sas"
    with pytest.raises(ConfigurationError, match="requires AZURE_STORAGE"):
        select_azure_auth(config, EnvironmentCredentialProvider())
    environment.update(
        AZURE_STORAGE_ACCOUNT_NAME="exampleaccount", AZURE_STORAGE_SAS_TOKEN="sig=fake"
    )
    assert isinstance(
        select_azure_auth(config, EnvironmentCredentialProvider()), SasAuth
    )
    assert "sig=fake" not in repr(config)
    assert "sig=fake" not in FileSystemDestination(base_uri=URI).model_dump_json()


@pytest.mark.parametrize(
    "account", ["UPPERCASE", "a;BlobEndpoint=https://evil", "a b", "ab"]
)
def test_invalid_account_is_rejected_without_disclosure(account, environment):
    environment.update(
        AZURE_STORAGE_ACCOUNT_NAME=account, AZURE_STORAGE_SAS_TOKEN="sig=fake"
    )
    with pytest.raises(ConfigurationError) as error:
        load_credentials(EnvironmentCredentialProvider())
    assert account not in str(error.value)
