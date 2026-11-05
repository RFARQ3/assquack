from unittest.mock import MagicMock

import pytest
from pydantic import SecretStr

from assquack import FileSystemDestination
from assquack._errors import ConfigurationError, ExportError
from assquack._exports.authentication.azure import CredentialChainAuth, SasAuth
from assquack._exports.authentication.credentials import (
    EnvironmentCredentialProvider,
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


def provider_for(values):
    provider = MagicMock(spec=EnvironmentCredentialProvider)
    provider.get.side_effect = values.get
    return provider


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
def test_auto_selects_sas_when_chain_is_absent_or_incomplete(chain_inputs):
    provider = provider_for({**chain_inputs, **SAS})
    auth = default_authenticators().select("azure", "auto", provider)
    assert isinstance(auth, SasAuth)
    provider.load.assert_awaited_once()
    names = [call.args[0] for call in provider.get.call_args_list]
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
def test_complete_credentials_respect_priority_and_explicit_choice(method, expected):
    auth = default_authenticators().select(
        "azure", method, provider_for({**CHAIN, **SAS})
    )
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
    method, values, missing
):
    with pytest.raises(ConfigurationError, match=missing) as caught:
        default_authenticators().select("azure", method, provider_for(values))
    assert "sig=synthetic" not in str(caught.value)


@pytest.mark.parametrize("method", ["auto", "none"])
def test_local_authentication_never_loads_credentials(method):
    provider = provider_for({})
    assert isinstance(
        default_authenticators().select("local", method, provider), LocalAuthenticator
    )
    provider.load.assert_not_called()
    provider.get.assert_not_called()


@pytest.mark.parametrize("method", ["auto", "credential_chain"])
def test_failed_chain_authentication_never_attempts_sas(method):
    destination = FileSystemDestination(
        base_uri="abfss://exampleaccount.dfs.core.windows.net/lake", auth=method
    )
    provider = provider_for({**CHAIN, **SAS})
    destination._credential_provider = provider
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
    provider.load.assert_awaited_once()


def test_auto_validates_selected_sas_without_native_fallback():
    with pytest.raises(ConfigurationError, match="encoded SAS query"):
        default_authenticators().select(
            "azure",
            "auto",
            provider_for({**SAS, "AZURE_STORAGE_SAS_TOKEN": "malformed"}),
        )
