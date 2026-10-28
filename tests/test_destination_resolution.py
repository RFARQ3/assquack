import asyncio
import gc
import weakref
from pathlib import Path
from typing import cast
from unittest.mock import MagicMock

import duckdb
import pytest
from pydantic import SecretStr

from assquack import AssquackConfig, FileSystemDestination, asset
from assquack._errors import ExportError
from assquack._exports import resolve_exports
from assquack._exports.authentication.local import LocalAuthenticator
from assquack._exports.destinations.fingerprint import hash_configuration
from assquack._exports.preparation.azure import AzurePreparationStrategy
from assquack._storage.tables import TableReference


class Credentials:
    def __init__(self):
        self.token = "sig=synthetic-first"
        self.loads = 0
        self.reads = []

    async def load(self):
        self.loads += 1

    def get(self, name):
        self.reads.append(name)
        return {
            "AZURE_STORAGE_SAS_TOKEN": self.token,
            "AZURE_STORAGE_ACCOUNT_NAME": "exampleaccount",
        }[name]


def sas_destination():
    destination = FileSystemDestination(base_uri="abfss://lake", auth="sas")
    provider = Credentials()
    destination._credential_provider = provider
    return destination, provider


def test_fingerprint_is_deterministic_typed_and_not_per_output(tmp_path):
    destination, provider = sas_destination()
    first = destination.resolve("orders.parquet|json", asset_name="orders")
    second = destination.resolve("different.csv", asset_name="different")
    assert {target.destination_fingerprint for target in (*first, *second)} == {
        first[0].destination_fingerprint
    }
    provider.token = "sig=synthetic-second"
    changed = destination.resolve("orders.parquet", asset_name="orders")
    assert changed[0].destination_fingerprint != first[0].destination_fingerprint
    assert "synthetic" not in repr(destination)
    assert "synthetic" not in destination.model_dump_json()
    assert hash_configuration({"b": SecretStr("synthetic"), "a": 1}) == (
        hash_configuration({"a": 1, "b": SecretStr("synthetic")})
    )
    assert hash_configuration({"a": 1}) != hash_configuration({"a": "1"})


def test_resolve_captures_once_and_export_does_not_reselect(monkeypatch):
    destination, provider = sas_destination()
    targets = destination.resolve("orders.parquet|json", asset_name="orders")
    provider.token = "sig=changed-after-resolution"
    monkeypatch.setattr(
        destination._authenticators, "select", MagicMock(side_effect=AssertionError)
    )
    monkeypatch.setattr(
        destination._preparations, "select", MagicMock(side_effect=AssertionError)
    )
    connection = MagicMock(spec=duckdb.DuckDBPyConnection)
    for target in targets:
        destination.export(connection, TableReference("main", "orders"), target)
    registrations = [
        call.args[1]["connection_string"]
        for call in connection.execute.call_args_list
        if call.args[0].startswith("CREATE TEMPORARY")
    ]
    assert len(registrations) == 2
    assert all("synthetic-first" in value for value in registrations)
    assert provider.loads == 1
    assert len(provider.reads) == 2
    assert destination._resolved is None


def test_collision_rule_allows_equal_configuration_and_rejects_different(tmp_path):
    destination = FileSystemDestination(base_uri=str(tmp_path))
    with resolve_exports(
        "orders.parquet", "orders", [destination, destination]
    ) as outputs:
        assert len(outputs) == 2
        assert outputs[0][0] is not outputs[1][0]
        copies = [cast(FileSystemDestination, item[0]) for item in outputs]
    assert all(copy._resolved is None for copy in copies)
    assert destination._resolved is None

    other = FileSystemDestination(base_uri=str(tmp_path), auth="none")
    with (
        pytest.raises(ExportError, match="same write path"),
        resolve_exports("orders.parquet", "orders", [destination, other]),
    ):
        pytest.fail("conflicting outputs must fail before execution")


def test_resolution_context_releases_on_failure_and_success(tmp_path):
    destination, provider = sas_destination()
    resolved = destination
    with (
        pytest.raises(RuntimeError, match="synthetic failure"),
        resolve_exports("orders.parquet", "orders", [destination]) as outputs,
    ):
        resolved = cast(FileSystemDestination, outputs[0][0])
        assert resolved._resolved is not None
        raise RuntimeError("synthetic failure")
    assert resolved._resolved is None
    assert destination._resolved is None
    assert provider.loads == 1


def test_fingerprint_cache_matching_preserves_asset_identity(tmp_path, monkeypatch):
    destination, provider = sas_destination()
    captured = []

    def synthetic_export(self, connection, source, target):
        captured.append(self)
        assert self._resolved is not None

    monkeypatch.setattr(FileSystemDestination, "export", synthetic_export)
    config = AssquackConfig(home=tmp_path, exports=destination)
    extractions = []

    @asset("orders.parquet", table="main.orders")
    def orders():
        extractions.append(1)
        return [{"id": 1}]

    first = asyncio.run(orders.cache_first()(assquack_config=config))
    cached = asyncio.run(orders.cache_first()(assquack_config=config))
    provider.token = "sig=synthetic-rotated"
    changed = asyncio.run(orders.cache_first()(assquack_config=config))
    assert cached.cached and changed.cached
    assert first.run_id == changed.run_id
    assert first.asset_id == changed.asset_id
    assert len(extractions) == 1 and provider.loads == 3
    assert all(item._resolved is None for item in captured)
    assert destination._resolved is None
    with duckdb.connect(str(config.resolved_database_path)) as connection:
        rows = connection.execute(
            "SELECT resolved_uri, destination_fingerprint FROM assquack_meta.exports"
        ).fetchall()
    assert len(rows) == 2 and rows[0][0] == rows[1][0]
    assert rows[0][1] != rows[1][1]
    assert all(len(row[1]) == 64 for row in rows)


def test_resolved_local_path_is_writer_path(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    destination = FileSystemDestination(base_uri="relative")
    target = destination.resolve("orders.parquet", asset_name="orders")[0]
    connection = MagicMock(spec=duckdb.DuckDBPyConnection)
    destination.export(connection, TableReference("main", "orders"), target)
    assert target.resolved_uri == str(tmp_path / "relative" / "orders.parquet")
    assert connection.execute.call_args.args[1] == {"uri": target.resolved_uri}
    assert Path(target.resolved_uri).is_absolute()


def test_non_azure_authentication_inputs_affect_destination_fingerprint(tmp_path):
    class GenericAuth(LocalAuthenticator):
        def __init__(self, password):
            self.password = SecretStr(password)

        @property
        def fingerprint_inputs(self):
            return {"password": self.password, "realm": "synthetic"}

    destination = FileSystemDestination(base_uri=str(tmp_path), auth="custom")
    password = "synthetic-first"
    destination._authenticators.register(
        "local", "custom", lambda _: GenericAuth(password)
    )
    first = destination.resolve("orders.parquet", asset_name="orders")[0]
    password = "synthetic-second"
    second = destination.resolve("orders.parquet", asset_name="orders")[0]
    assert first.resolved_uri == second.resolved_uri
    assert first.destination_fingerprint != second.destination_fingerprint


def test_partial_resolution_failure_releases_earlier_capture(tmp_path, monkeypatch):
    first, provider = sas_destination()
    captured = []
    original = FileSystemDestination.resolve

    def observe(self, export, *, asset_name):
        captured.append(self)
        return original(self, export, asset_name=asset_name)

    monkeypatch.setattr(FileSystemDestination, "resolve", observe)
    invalid = FileSystemDestination(base_uri="unsupported://example")
    with (
        pytest.raises(ExportError, match="Unsupported"),
        resolve_exports("orders.parquet", "orders", [first, invalid]),
    ):
        pytest.fail("resolution must fail")
    assert len(captured) == 2
    assert all(item._resolved is None for item in captured)
    assert first._resolved is None and provider.loads == 1


def test_interleaved_operations_retain_their_own_captured_credentials():
    destination, provider = sas_destination()
    with resolve_exports("orders.parquet", "orders", [destination]) as first:
        provider.token = "sig=synthetic-second"
        with resolve_exports("orders.parquet", "orders", [destination]) as second:
            assert (
                first[0][1].destination_fingerprint
                != second[0][1].destination_fingerprint
            )
            for outputs, token in [
                (first, "synthetic-first"),
                (second, "synthetic-second"),
            ]:
                connection = MagicMock(spec=duckdb.DuckDBPyConnection)
                operation, target = outputs[0]
                operation.export(connection, TableReference("main", "orders"), target)
                registration = next(
                    call.args[1]["connection_string"]
                    for call in connection.execute.call_args_list
                    if call.args[0].startswith("CREATE TEMPORARY")
                )
                assert token in registration
    assert destination._resolved is None and provider.loads == 2


def test_export_failure_releases_state_and_temporary_secret():
    destination, _ = sas_destination()
    target = destination.resolve("orders.parquet|json", asset_name="orders")[0]
    connection = MagicMock(spec=duckdb.DuckDBPyConnection)

    def execute(sql, parameters=None):
        if sql.startswith("COPY"):
            raise RuntimeError("synthetic write failure")

    connection.execute.side_effect = execute
    with pytest.raises(ExportError, match="Azure export failed"):
        destination.export(connection, TableReference("main", "orders"), target)
    assert destination._resolved is None
    assert connection.execute.call_args.args[0].startswith("DROP TEMPORARY SECRET")


def test_rejected_old_target_releases_new_resolution():
    destination, _ = sas_destination()
    old = destination.resolve("orders.parquet", asset_name="orders")[0]
    destination.resolve("other.parquet", asset_name="orders")
    with pytest.raises(ExportError, match="current resolve"):
        destination.export(MagicMock(), TableReference("main", "orders"), old)
    assert destination._resolved is None


def test_held_failure_does_not_retain_captured_authentication():
    destination, _ = sas_destination()
    target = destination.resolve("orders.parquet", asset_name="orders")[0]
    assert destination._resolved is not None
    authentication = weakref.ref(destination._resolved.authentication)

    class Connection:
        def execute(self, sql, parameters=None):
            if sql.startswith("COPY"):
                raise RuntimeError("synthetic failure")

    with pytest.raises(ExportError) as caught:
        destination.export(
            cast(duckdb.DuckDBPyConnection, Connection()),
            TableReference("main", "orders"),
            target,
        )
    gc.collect()
    assert caught.value is not None
    assert authentication() is None


def test_held_resolution_failure_releases_authentication(monkeypatch):
    destination, _ = sas_destination()
    references = []

    class FailingPreparation(AzurePreparationStrategy):
        def resolve_uri(self, uri, auth):
            references.append(weakref.ref(auth))
            raise ExportError("synthetic path validation failure")

    monkeypatch.setattr(
        destination._preparations, "select", lambda _: FailingPreparation()
    )
    with pytest.raises(ExportError, match="path validation") as caught:
        destination.resolve("orders.parquet", asset_name="orders")
    gc.collect()
    assert caught.value is not None
    assert references[0]() is None
    assert destination._resolved is None


def test_held_provider_validation_failure_clears_loader_values():
    destination, provider = sas_destination()
    provider.token = "invalid-synthetic-token"
    with pytest.raises(Exception) as caught:
        destination.resolve("orders.parquet", asset_name="orders")
    traceback = caught.value.__traceback__
    while traceback is not None:
        if traceback.tb_frame.f_code.co_name in {"load_credentials", "__post_init__"}:
            assert not traceback.tb_frame.f_locals
        traceback = traceback.tb_next
    assert destination._resolved is None
