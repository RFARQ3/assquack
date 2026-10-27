from unittest.mock import MagicMock

import pytest

from assquack import FileSystemDestination
from assquack._errors import ConfigurationError
from assquack._exports.authentication.credentials import CHAIN_ENVIRONMENT_NAMES
from assquack._storage.tables import TableReference


class Provider:
    def __init__(self, values):
        self.values = values
        self.loads = 0
        self.reads = []

    async def load(self):
        self.loads += 1

    def get(self, name):
        self.reads.append(name)
        return self.values.get(name)


def destination(values, method="credential_chain"):
    result = FileSystemDestination(
        base_uri="abfss://exampleaccount.dfs.core.windows.net/lake", auth=method
    )
    provider = Provider(values)
    result._credential_provider = provider
    return result, provider


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
def test_supported_chain_inputs_are_captured_and_restrict_duckdb(extra, chain, method):
    values = {"AZURE_TENANT_ID": "tenant", "AZURE_CLIENT_ID": "client", **extra}
    output, provider = destination(values, method)
    targets = output.resolve("orders.parquet|json", asset_name="orders")
    assert len({target.destination_fingerprint for target in targets}) == 1
    assert provider.loads == 1
    assert all(provider.reads.count(name) == 1 for name in CHAIN_ENVIRONMENT_NAMES)
    assert "synthetic-secret" not in repr(output)
    assert "synthetic-secret" not in output.model_dump_json()
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
    assert provider.loads == 1
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
def test_incomplete_chain_fails_before_export(values):
    output, _ = destination(values)
    with pytest.raises(ConfigurationError, match="AZURE_TENANT_ID") as caught:
        output.resolve("orders.parquet", asset_name="orders")
    assert "synthetic-secret" not in str(caught.value)
    assert output._resolved is None


@pytest.mark.parametrize("name", CHAIN_ENVIRONMENT_NAMES)
def test_each_captured_environment_input_affects_fingerprint(name):
    values = {
        "AZURE_TENANT_ID": "tenant",
        "AZURE_CLIENT_ID": "client",
        "AZURE_CLIENT_SECRET": "synthetic-first",
    }
    output, _ = destination(values)
    first = output.resolve("orders.parquet", asset_name="orders")[0]
    values[name] = "synthetic-changed"
    second = output.resolve("other.csv", asset_name="orders")[0]
    assert first.destination_fingerprint != second.destination_fingerprint
