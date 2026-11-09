from unittest.mock import MagicMock

import pytest
from pydantic import SecretStr

from assquack import FileSystemDestination
from assquack._errors import ConfigurationError, ExportError
from assquack._exports.authentication.azure import CredentialChainAuth, SasAuth
from assquack._exports.authentication.credentials import (
    has_required_inputs,
)
from assquack._exports.authentication.local import LocalAuthenticator
from assquack._exports.authentication.registry import default_authenticators
from assquack._storage.tables import TableReference

CHAIN = {
    "AZURE_TENANT_ID": "tenant",
    "AZURE_CLIENT_ID": "client",
    "AZURE_FEDERATED_TOKEN_FILE": "synthetic.jwt",
}
SAS = {
    "AZURE_STORAGE_ACCOUNT_NAME": "exampleaccount",
    "AZURE_STORAGE_SAS_TOKEN": "sig=synthetic",
}


@pytest.fixture(autouse=True)
def environment(monkeypatch):
    values = {}
    monkeypatch.setattr(
        "assquack._exports.authentication.credentials.getenv", values.get
    )
    return values


@pytest.mark.parametrize(
    "available,combinations,expected",
    [
        ({}, ((),), True),
        ({}, (), False),
        ({"a": "value"}, (("a", "b"),), False),
        ({"a": "value", "b": "value"}, (("a", "b"),), True),
        ({"c": "value"}, (("a", "b"), ("c",)), True),
    ],
)
def test_required_inputs_accept_any_complete_combination(
    available, combinations, expected
):
    inputs = {name: SecretStr(value) for name, value in available.items()}
    assert has_required_inputs(inputs, combinations) is expected


@pytest.mark.parametrize(
    "chain_inputs",
    [{}, {"AZURE_CLIENT_ID": "client"}, {**CHAIN, "AZURE_FEDERATED_TOKEN_FILE": " "}],
)
def test_auto_selects_sas_when_chain_is_absent_or_incomplete(
    chain_inputs, environment, monkeypatch
):
    environment.update({**chain_inputs, **SAS})
    read = MagicMock(side_effect=environment.get)
    monkeypatch.setattr("assquack._exports.authentication.credentials.getenv", read)
    auth = default_authenticators().select("azure", "auto")
    assert isinstance(auth, SasAuth)
    names = [call.args[0] for call in read.call_args_list]
    assert len(names) == len(set(names))
    assert auth.fingerprint_inputs == {
        "account_name": "exampleaccount",
        "token": SecretStr("sig=synthetic"),
    }


@pytest.mark.parametrize(
    "method,expected",
    [
        ("auto", CredentialChainAuth),
        ("credential_chain", CredentialChainAuth),
        ("sas", SasAuth),
    ],
)
def test_complete_credentials_respect_priority_and_explicit_choice(
    method, expected, environment
):
    environment.update({**CHAIN, **SAS})
    auth = default_authenticators().select("azure", method)
    assert isinstance(auth, expected)
    if isinstance(auth, CredentialChainAuth):
        assert set(auth.inputs) == set(CHAIN)


@pytest.mark.parametrize(
    "method,values,missing",
    [
        ("credential_chain", SAS, "AZURE_TENANT_ID"),
        ("sas", CHAIN, "AZURE_STORAGE_SAS_TOKEN"),
        (
            "sas",
            {**CHAIN, "AZURE_STORAGE_ACCOUNT_NAME": "exampleaccount"},
            "AZURE_STORAGE_SAS_TOKEN",
        ),
        (
            "auto",
            {"AZURE_CLIENT_ID": "client", "AZURE_STORAGE_SAS_TOKEN": "sig=synthetic"},
            "AZURE_STORAGE_ACCOUNT_NAME",
        ),
        ("auto", {}, "AZURE_TENANT_ID"),
    ],
)
def test_incomplete_inputs_raise_without_switching_explicit_method(
    method, values, missing, environment
):
    environment.update(values)
    with pytest.raises(ConfigurationError, match=missing) as caught:
        default_authenticators().select("azure", method)
    assert "sig=synthetic" not in str(caught.value)


@pytest.mark.parametrize("method", ["auto", "none"])
def test_local_authentication_never_loads_credentials(method, monkeypatch):
    read = MagicMock(
        side_effect=AssertionError("Local exports must not read credentials")
    )
    monkeypatch.setattr("assquack._exports.authentication.credentials.getenv", read)
    assert isinstance(
        default_authenticators().select("local", method), LocalAuthenticator
    )
    read.assert_not_called()


@pytest.mark.parametrize("method", ["auto", "credential_chain"])
def test_failed_chain_authentication_never_attempts_sas(method, environment):
    destination = FileSystemDestination(
        base_uri="abfss://exampleaccount.dfs.core.windows.net/lake", auth=method
    )
    environment.update({**CHAIN, **SAS})
    target = destination.resolve("orders.parquet", asset_name="orders")[0]
    connection = MagicMock()

    def execute(sql, parameters=None):
        if sql.startswith("CREATE TEMPORARY SECRET"):
            raise RuntimeError("sig=synthetic-sensitive")

    connection.execute.side_effect = execute
    with pytest.raises(ExportError) as caught:
        destination.export(connection, TableReference("main", "orders"), target)
    registrations = [
        call
        for call in connection.execute.call_args_list
        if call.args[0].startswith("CREATE TEMPORARY SECRET")
    ]
    assert len(registrations) == 1
    assert "PROVIDER credential_chain" in registrations[0].args[0]
    assert "synthetic-sensitive" not in str(caught.value)
    assert destination._resolved is None


def test_auto_validates_selected_sas_without_native_fallback(environment):
    environment.update({**SAS, "AZURE_STORAGE_SAS_TOKEN": "malformed"})
    with pytest.raises(ConfigurationError, match="encoded SAS query"):
        default_authenticators().select(
            "azure",
            "auto",
        )
