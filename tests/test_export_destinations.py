import asyncio
import traceback
from collections.abc import Generator, Mapping
from contextlib import contextmanager
from pathlib import Path
from typing import cast
from unittest.mock import MagicMock

import duckdb
import pytest
from pydantic import PrivateAttr

from assquack import AssquackConfig, ExportDestination, FileSystemDestination, asset
from assquack._errors import ConfigurationError, ExportError
from assquack._exports.authentication.base import Authenticator
from assquack._exports.authentication.local import LocalAuthenticator
from assquack._exports.authentication.registry import default_authenticators
from assquack._exports.models import ExportTarget
from assquack._exports.preparation.registry import default_preparations
from assquack._storage.database import open_database
from assquack._storage.tables import TableReference
from database_helpers import inspect_database


@pytest.fixture(autouse=True)
def environment(monkeypatch):
    values = {}
    monkeypatch.setattr(
        "assquack._exports.authentication.credentials.getenv", values.get
    )
    return values


@pytest.fixture
def intercepted_connections(monkeypatch):
    connections: list[MagicMock] = []

    def intercepted_open(config):
        real = open_database(config)
        proxy = MagicMock(wraps=real)

        def execute(sql, parameters=None):
            if sql.strip().startswith(
                ("LOAD azure", "CREATE TEMPORARY", "DROP TEMPORARY", "COPY")
            ):
                return proxy
            return real.execute(sql, parameters)

        proxy.execute.side_effect = execute
        connections.append(proxy)
        return cast(duckdb.DuckDBPyConnection, proxy)

    monkeypatch.setattr("assquack._storage.database.open_database", intercepted_open)
    return connections


@pytest.mark.parametrize("explicit", [False, True])
@pytest.mark.parametrize("running_loop", [False, True])
def test_environment_credentials_used_by_both_export_paths(
    tmp_path, intercepted_connections, explicit, running_loop, environment, monkeypatch
):
    environment.update(
        AZURE_STORAGE_ACCOUNT_NAME="exampleaccount",
        AZURE_STORAGE_SAS_TOKEN="sig=synthetic-sensitive",
    )
    read = MagicMock(side_effect=environment.get)
    monkeypatch.setattr("assquack._exports.authentication.credentials.getenv", read)
    destination = FileSystemDestination(
        base_uri="abfss://exampleaccount.dfs.core.windows.net/lake", auth="sas"
    )
    config = AssquackConfig(home=tmp_path, exports=destination)

    @asset(table="main.orders", export=None if explicit else "orders.parquet")
    def orders():
        return [{"id": 1}]

    async def run():
        result = await orders(assquack_config=config)
        if explicit and running_loop:
            result.export(destination)
        return result

    result = asyncio.run(run())
    if explicit and not running_loop:
        result.export(destination)
    assert [call.args[0] for call in read.call_args_list] == [
        "AZURE_STORAGE_SAS_TOKEN",
        "AZURE_STORAGE_ACCOUNT_NAME",
    ]
    assert "synthetic-sensitive" not in config.model_dump_json()
    assert "synthetic-sensitive" not in repr(config)
    statements = [
        call.args[0]
        for call in next(
            c
            for c in intercepted_connections
            if any(
                call.args[0].strip().startswith("CREATE TEMPORARY")
                for call in c.execute.call_args_list
            )
        ).execute.call_args_list
    ]
    assert any(sql.startswith("CREATE TEMPORARY SECRET") for sql in statements)
    assert any(sql.startswith("DROP TEMPORARY SECRET") for sql in statements)
    with inspect_database(config) as db:
        assert db.execute("SELECT count(*) FROM assquack_meta.exports").fetchone() == (
            1,
        )


def test_local_does_not_read_credentials(tmp_path, monkeypatch):
    destination = FileSystemDestination(base_uri=str(tmp_path))
    read = MagicMock(
        side_effect=AssertionError("Local export must not read credentials")
    )
    monkeypatch.setattr("assquack._exports.authentication.credentials.getenv", read)
    target = destination.resolve("orders.parquet", asset_name="orders")[0]
    destination.export(MagicMock(), TableReference("main", "orders"), target)
    read.assert_not_called()


def test_invalid_credentials_are_safe_and_have_no_manifest(
    tmp_path, intercepted_connections, environment
):
    destination = FileSystemDestination(base_uri="abfss://lake", auth="sas")
    environment.update(
        AZURE_STORAGE_ACCOUNT_NAME="exampleaccount",
        AZURE_STORAGE_SAS_TOKEN="invalid-synthetic-sensitive",
    )

    @asset(table="main.orders", export="orders.parquet")
    def orders():
        pytest.fail("Credential resolution must fail before the producer runs")

    config = AssquackConfig(home=tmp_path, exports=destination)
    with pytest.raises(ConfigurationError) as error:
        asyncio.run(orders(assquack_config=config))
    assert "synthetic-sensitive" not in "".join(traceback.format_exception(error.value))
    with inspect_database(config) as db:
        assert db.execute("SELECT count(*) FROM assquack_meta.exports").fetchone() == (
            0,
        )
        assert db.execute(
            "SELECT resolve_status, materialize_status, outcome, failed_stage, "
            "error_type, error_message FROM assquack_meta.runs"
        ).fetchone() == (
            "failed",
            "unstarted",
            "failed",
            "resolve",
            "ConfigurationError",
            "Azure SAS token must be an encoded SAS query token.",
        )


class SyntheticPreparation:
    kind = "synthetic"

    @property
    def fingerprint_inputs(self):
        return {"output_root": str(self.output.parent)}

    def resolve_uri(self, uri, auth):
        return str(self.output)

    def __init__(self, output: Path):
        self.output = output

    @contextmanager
    def prepare(
        self, connection: duckdb.DuckDBPyConnection, uri: str, auth: Authenticator
    ) -> Generator[None, None, None]:
        with auth.authenticate(connection, uri):
            yield


class TrackedAuth:
    @property
    def fingerprint_inputs(self):
        return {}

    def __init__(self, events: list[str]):
        self.events = events

    @contextmanager
    def authenticate(
        self, connection: duckdb.DuckDBPyConnection, uri: str
    ) -> Generator[None, None, None]:
        self.events.append("enter")
        try:
            yield
        finally:
            self.events.append("exit")


def test_registered_preparation_and_auth_use_shared_writer(tmp_path):
    output = tmp_path / "output.parquet"
    events: list[str] = []
    destination = FileSystemDestination(base_uri="synthetic://output", auth="tracked")
    destination._preparations.register("synthetic", SyntheticPreparation(output))
    destination._authenticators.register(
        "synthetic", "tracked", lambda _: TrackedAuth(events)
    )

    @asset(table="main.orders", export="orders.parquet")
    def orders():
        return [{"id": 1}]

    result = asyncio.run(
        orders(assquack_config=AssquackConfig(home=tmp_path, exports=destination))
    )
    assert events == ["enter", "exit"]
    assert output.read_bytes().startswith(b"PAR1")
    result.export(destination)
    assert events == ["enter", "exit", "enter", "exit"]


def test_registry_duplicate_unknown_compatibility_and_isolation():
    preparation = default_preparations()
    with pytest.raises(ValueError, match="already registered"):
        preparation.register("file", preparation.select("local.parquet"))
    with pytest.raises(ExportError, match="Unsupported"):
        preparation.select("synthetic://file")
    auth = default_authenticators()
    with pytest.raises(ValueError, match="already registered"):
        auth.register("local", "none", lambda _: LocalAuthenticator())
    with pytest.raises(ConfigurationError, match="Unsupported"):
        auth.select("local", "sas")
    auth.register("local", "custom", lambda _: LocalAuthenticator())
    with pytest.raises(ConfigurationError, match="Unsupported"):
        default_authenticators().select("local", "custom")


class SyntheticDestination(ExportDestination):
    _written: list[str] = PrivateAttr(default_factory=list)

    def resolve(
        self, export: str | Mapping[str, str] | None, *, asset_name: str
    ) -> tuple[ExportTarget, ...]:
        # A non-file destination is free to interpret the declaration itself.
        return (
            ExportTarget(
                None,
                "record:orders",
                "record:orders",
                format="record",
                destination_fingerprint="synthetic-record-config",
            ),
        )

    def export(
        self,
        connection: duckdb.DuckDBPyConnection,
        source: TableReference,
        target: ExportTarget,
    ) -> None:
        assert connection.execute(f"SELECT id FROM {source.sql}").fetchall() == [(1,)]
        self._written.append(target.resolved_uri)


def test_generic_destination_does_not_require_file_formats(tmp_path):
    destination = SyntheticDestination()

    @asset(table="main.orders", export="destination-specific declaration")
    def orders():
        return [{"id": 1}]

    config = AssquackConfig(home=tmp_path, exports=destination)
    result = asyncio.run(orders(assquack_config=config))
    assert destination._written == ["record:orders"]
    assert result.export(destination) == "record:orders"
    assert destination._written == ["record:orders", "record:orders"]
    assert asyncio.run(orders.cache_first()(assquack_config=config)).cached
    with inspect_database(config) as db:
        assert db.execute("SELECT format FROM assquack_meta.exports").fetchall() == [
            ("record",),
            ("record",),
        ]


def test_partial_multi_format_failure_retries_only_unfinished_outputs(
    tmp_path, monkeypatch
):
    from assquack._exports.writer import DuckDBFileWriter

    extractions = []
    writes = []

    @asset(table="main.orders", export="orders.parquet|csv|json")
    def orders():
        extractions.append(1)
        return [{"id": 1}]

    config = AssquackConfig(home=tmp_path, exports=str(tmp_path / "out"))
    original = DuckDBFileWriter.write

    def fail_second(self, connection, source, target, uri):
        writes.append(target.format)
        if target.format == "csv":
            raise ValueError("synthetic output failure")
        original(self, connection, source, target, uri)

    monkeypatch.setattr(DuckDBFileWriter, "write", fail_second)
    with pytest.raises(ValueError, match="synthetic output failure"):
        asyncio.run(orders.cache_first()(assquack_config=config))
    with inspect_database(config) as db:
        assert db.execute(
            "SELECT resolved_uri FROM assquack_meta.exports"
        ).fetchall() == [(str(tmp_path / "out/orders.parquet"),)]
        assert db.execute("SELECT id FROM main.orders").fetchall() == [(1,)]
    assert not (tmp_path / "out/orders.json").exists()

    def record_write(self, connection, source, target, uri):
        writes.append(target.format)
        original(self, connection, source, target, uri)

    monkeypatch.setattr(DuckDBFileWriter, "write", record_write)
    result = asyncio.run(orders.cache_first()(assquack_config=config))
    assert result.cached and len(extractions) == 1
    assert writes == ["parquet", "csv", "csv", "json"]
    assert (tmp_path / "out/orders.json").exists()
    with inspect_database(config) as db:
        assert db.execute("SELECT count(*) FROM assquack_meta.exports").fetchone() == (
            3,
        )


def test_filesystem_mapping_is_present_in_json_schema():
    schema = AssquackConfig.model_json_schema()
    fields = schema["$defs"]["FileSystemDestination"]["properties"]
    assert set(fields) == {"base_uri", "auth", "path", "format"}


@pytest.mark.parametrize(
    "path",
    [
        r"E:\exports\orders.parquet",
        "E:/exports/orders.parquet",
        "/exports/orders.parquet",
    ],
)
def test_absolute_local_path_keeps_location_with_remote_base(path):
    destination = FileSystemDestination(
        base_uri="abfss://exampleaccount.dfs.core.windows.net/lake"
    )
    target = destination.resolve(path, asset_name="orders")[0]
    assert target.resolved_uri == str(Path(path).resolve())
