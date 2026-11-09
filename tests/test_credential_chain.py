from unittest.mock import MagicMock

import pytest

from assquack import FileSystemDestination
from assquack._errors import ConfigurationError
from assquack._exports.authentication.credentials import CHAIN_ENVIRONMENT_NAMES
from assquack._storage.tables import TableReference


def destination(monkeypatch, values, method="credential_chain"):
    read = MagicMock(side_effect=values.get)
    monkeypatch.setattr("assquack._exports.authentication.credentials.getenv", read)
    result = FileSystemDestination(
        base_uri="abfss://exampleaccount.dfs.core.windows.net/lake", auth=method
    )
    return result, read


@pytest.mark.parametrize(
    "extra,chain",
    [
        ({"AZURE_CLIENT_SECRET": "synthetic-secret"}, "env"),
        ({"AZURE_CLIENT_CERTIFICATE_PATH": "synthetic.pem"}, "env"),
        ({"AZURE_FEDERATED_TOKEN_FILE": "synthetic.jwt"}, "workload_identity"),
        (
            {
                "AZURE_CLIENT_SECRET": "synthetic-secret",
                "AZURE_FEDERATED_TOKEN_FILE": "synthetic.jwt",
            },
            "env;workload_identity",
        ),
    ],
)
@pytest.mark.parametrize("method", ["auto", "credential_chain"])
def test_supported_chain_inputs_are_captured_and_restrict_duckdb(
    extra, chain, method, monkeypatch
):
    values = {"AZURE_TENANT_ID": "tenant", "AZURE_CLIENT_ID": "client", **extra}
    output, read = destination(monkeypatch, values, method)
    targets = output.resolve("orders.parquet|json", asset_name="orders")
    assert len({target.destination_fingerprint for target in targets}) == 1
    names = [call.args[0] for call in read.call_args_list]
    assert all(names.count(name) == 1 for name in CHAIN_ENVIRONMENT_NAMES)
    assert "synthetic-secret" not in repr(output)
    assert "synthetic-secret" not in output.model_dump_json()
    reads_before_export = read.call_count
    connection = MagicMock()
    for target in targets:
        output.export(connection, TableReference("main", "orders"), target)
    registrations = [
        call
        for call in connection.execute.call_args_list
        if call.args[0].startswith("CREATE TEMPORARY SECRET")
    ]
    assert all(call.args[1]["chain"] == chain for call in registrations)
    assert all("CHAIN $chain" in call.args[0] for call in registrations)
    assert read.call_count == reads_before_export
    assert output._resolved is None


@pytest.mark.parametrize(
    "values",
    [
        {},
        {"AZURE_CLIENT_ID": "client"},
        {"AZURE_TENANT_ID": "tenant", "AZURE_CLIENT_ID": "client"},
        {"AZURE_TENANT_ID": "tenant", "AZURE_CLIENT_SECRET": "synthetic-secret"},
        {
            "AZURE_TENANT_ID": "tenant",
            "AZURE_CLIENT_ID": "client",
            "AZURE_CLIENT_SECRET": "",
        },
    ],
)
@pytest.mark.parametrize("method", ["auto", "credential_chain"])
def test_incomplete_chain_fails_before_export(values, method, monkeypatch):
    output, _ = destination(monkeypatch, values, method)
    with pytest.raises(ConfigurationError, match="AZURE_TENANT_ID") as caught:
        output.resolve("orders.parquet", asset_name="orders")
    assert "synthetic-secret" not in str(caught.value)
    assert output._resolved is None


@pytest.mark.parametrize("name", CHAIN_ENVIRONMENT_NAMES)
def test_each_captured_environment_input_affects_fingerprint(name, monkeypatch):
    values = {
        "AZURE_TENANT_ID": "tenant",
        "AZURE_CLIENT_ID": "client",
        "AZURE_CLIENT_SECRET": "synthetic-first",
    }
    output, _ = destination(monkeypatch, values)
    first = output.resolve("orders.parquet", asset_name="orders")[0]
    values[name] = "synthetic-changed"
    second = output.resolve("other.csv", asset_name="orders")[0]
    assert first.destination_fingerprint != second.destination_fingerprint
