import logging
from unittest.mock import MagicMock

import duckdb
import pytest

from assquack._errors import ConfigurationError
from assquack._exports.authentication.azure import CredentialChainAuth, SasAuth
from assquack._exports.authentication.registry import (
    AuthenticationRegistry,
    default_authenticators,
)

LOGGER = "assquack._exports.authentication.registry"


@pytest.fixture
def environment(monkeypatch, caplog):
    values = {}
    monkeypatch.setattr(
        "assquack._exports.authentication.credentials.getenv", values.get
    )
    caplog.set_level(logging.INFO, logger=LOGGER)
    return values


@pytest.mark.parametrize(
    "inputs,chain",
    [
        (
            {"AZURE_FEDERATED_TOKEN_FILE": "/private/federated-token-canary"},
            "workload_identity",
        ),
        ({"AZURE_CLIENT_SECRET": "client-secret-canary"}, "env"),
        ({"AZURE_CLIENT_CERTIFICATE_PATH": "/private/certificate-canary"}, "env"),
        (
            {
                "AZURE_CLIENT_SECRET": "client-secret-canary",
                "AZURE_FEDERATED_TOKEN_FILE": "/private/federated-token-canary",
            },
            "env;workload_identity",
        ),
    ],
)
@pytest.mark.parametrize("method", ["auto", "credential_chain"])
def test_chain_selection_logs_only_presence_and_configured_chain(
    environment, caplog, inputs, chain, method
):
    environment.update(
        AZURE_TENANT_ID="tenant-canary",
        AZURE_CLIENT_ID="client-canary",
        AZURE_AUTHORITY_HOST="https://authority-canary.invalid",
        **inputs,
    )
    authentication = default_authenticators().select("azure", method)
    assert isinstance(authentication, CredentialChainAuth)
    assert authentication.chain == chain
    assert len(caplog.records) == 2
    presence, selection = caplog.records
    assert presence.levelno == selection.levelno == logging.INFO
    assert presence.name == selection.name == LOGGER
    assert presence.args == (
        ", ".join(authentication.inputs),
        ", ".join(
            name
            for name in (
                "AZURE_CLIENT_SECRET",
                "AZURE_CLIENT_CERTIFICATE_PATH",
                "AZURE_FEDERATED_TOKEN_FILE",
                *(
                    ("AZURE_STORAGE_SAS_TOKEN", "AZURE_STORAGE_ACCOUNT_NAME")
                    if method == "auto"
                    else ()
                ),
            )
            if name not in inputs
        ),
    )
    assert selection.getMessage() == (
        "Azure authentication selected: method=credential_chain; "
        f"configured chain={chain}."
    )

    # Check the stored records, not just formatted output: queued handlers retain args.
    for record in caplog.records:
        assert record.exc_info is None
        assert all(isinstance(argument, str) for argument in record.args)
        for value in environment.values():
            assert value not in repr(record.__dict__)


@pytest.mark.parametrize("method", ["auto", "sas"])
def test_sas_selection_logs_method_without_values(environment, caplog, method):
    environment.update(
        AZURE_STORAGE_ACCOUNT_NAME="privateaccountcanary",
        AZURE_STORAGE_SAS_TOKEN="sig=sas-token-canary",
    )
    authentication = default_authenticators().select("azure", method)
    assert isinstance(authentication, SasAuth)
    assert len(caplog.records) == 2
    assert (
        "present=[AZURE_STORAGE_SAS_TOKEN, AZURE_STORAGE_ACCOUNT_NAME]" in caplog.text
    )
    assert caplog.records[1].getMessage() == (
        "Azure authentication selected: method=sas; configured chain=not applicable."
    )
    for value in environment.values():
        assert value not in repr([record.__dict__ for record in caplog.records])


@pytest.mark.parametrize("blank", [None, "", " \t\n"])
def test_missing_inputs_are_logged_before_selection_fails(environment, caplog, blank):
    environment.update(
        AZURE_TENANT_ID="tenant-canary",
        AZURE_CLIENT_ID=blank,
        AZURE_FEDERATED_TOKEN_FILE="/private/nonexistent-token-canary",
    )
    with pytest.raises(ConfigurationError, match="requires"):
        default_authenticators().select("azure", "credential_chain")
    assert len(caplog.records) == 1
    assert caplog.records[0].args == (
        "AZURE_TENANT_ID, AZURE_FEDERATED_TOKEN_FILE",
        "AZURE_AUTHORITY_HOST, AZURE_CLIENT_ID, AZURE_CLIENT_SECRET, "
        "AZURE_CLIENT_CERTIFICATE_PATH",
    )
    assert "selected" not in caplog.text
    assert "canary" not in repr(caplog.records[0].__dict__)


def test_invalid_sas_logs_presence_without_claiming_success(environment, caplog):
    environment.update(
        AZURE_STORAGE_ACCOUNT_NAME="privateaccountcanary",
        AZURE_STORAGE_SAS_TOKEN="invalid-token-canary",
    )
    with pytest.raises(ConfigurationError, match="encoded SAS"):
        default_authenticators().select("azure", "sas")
    assert len(caplog.records) == 1
    assert (
        "present=[AZURE_STORAGE_SAS_TOKEN, AZURE_STORAGE_ACCOUNT_NAME]" in caplog.text
    )
    assert "selected" not in caplog.text
    assert "canary" not in repr(caplog.records[0].__dict__)


def test_local_selection_does_not_read_inputs_or_emit_authentication_logs(
    monkeypatch, caplog
):
    caplog.set_level(logging.INFO, logger=LOGGER)
    read = MagicMock(side_effect=AssertionError("Local auth must not read inputs"))
    monkeypatch.setattr("assquack._exports.authentication.credentials.getenv", read)
    default_authenticators().select("local", "auto")
    read.assert_not_called()
    assert not caplog.records


def test_registered_method_name_is_used_in_selection_log(environment, caplog):
    environment["AZURE_FEDERATED_TOKEN_FILE"] = "/private/nonexistent-token-canary"
    registry = AuthenticationRegistry()
    registry.register("azure", "custom", lambda inputs: CredentialChainAuth(inputs))
    registry.select("azure", "auto")
    assert "method=custom" in caplog.records[-1].getMessage()


def test_selection_logging_does_not_change_native_authentication_failure(
    environment, caplog
):
    environment.update(
        AZURE_TENANT_ID="tenant-canary",
        AZURE_CLIENT_ID="client-canary",
        AZURE_FEDERATED_TOKEN_FILE="/private/nonexistent-token-canary",
    )
    authentication = default_authenticators().select("azure", "auto")
    native = duckdb.IOException("Original backend error with arbitrary useful detail")
    connection = MagicMock(spec=duckdb.DuckDBPyConnection)
    connection.execute.side_effect = native
    with (
        pytest.raises(duckdb.IOException) as caught,
        authentication.authenticate(connection, "abfss://example/lake/file"),
    ):
        pytest.fail("Registration must fail")
    assert caught.value is native
    assert len(caplog.records) == 2
    assert "method=credential_chain; configured chain=workload_identity" in caplog.text
    assert "canary" not in repr([record.__dict__ for record in caplog.records])
