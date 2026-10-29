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


class SyntheticProvider:
    def __init__(self, account: str = "exampleaccount", fail: bool = False):
        self.account = account
        self.fail = fail
        self.loads = 0
        self.requested: list[str] = []

    async def load(self) -> None:
        await asyncio.sleep(0)
        self.loads += 1
        if self.fail:
            raise ValueError("sig=synthetic-sensitive")

    def get(self, name: str) -> str | None:
        assert self.loads > 0
        self.requested.append(name)
        return {
            "AZURE_STORAGE_SAS_TOKEN": "sig=synthetic-sensitive",
            "AZURE_STORAGE_ACCOUNT_NAME": self.account,
        }[name]


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

    monkeypatch.setattr(
        "assquack._materialization.pipeline.open_database", intercepted_open
    )
    monkeypatch.setattr("assquack._result.open_database", intercepted_open)
    return connections


@pytest.mark.parametrize("explicit", [False, True])
@pytest.mark.parametrize("running_loop", [False, True])
def test_injected_async_provider_used_by_both_paths(
    tmp_path, intercepted_connections, explicit, running_loop
):
    provider = SyntheticProvider()
    destination = FileSystemDestination(
        base_uri="abfss://exampleaccount.dfs.core.windows.net/lake", auth="sas"
    )
    destination._credential_provider = provider
    config = AssquackConfig(home=tmp_path, exports=destination)

    @asset(table="main.orders", export=None if explicit else "orders.parquet")
    def orders():
        return [{"id": 1}]

    async def run():
        result = await orders(assquack_config=config)
        if explicit and running_loop:
            result.export_parquet()
        return result

    result = asyncio.run(run())
    if explicit and not running_loop:
        result.export_parquet()
    assert provider.loads == 1
    assert provider.requested == [
        "AZURE_STORAGE_SAS_TOKEN",
        "AZURE_STORAGE_ACCOUNT_NAME",
    ]
    assert "synthetic-sensitive" not in config.model_dump_json()
    assert "synthetic-sensitive" not in repr(config)
    assert "_credential_provider" not in config.model_dump_json()
    statements = [
        call.args[0] for call in intercepted_connections[-1].execute.call_args_list
    ]
    assert any(sql.startswith("CREATE TEMPORARY SECRET") for sql in statements)
    assert any(sql.startswith("DROP TEMPORARY SECRET") for sql in statements)
    with duckdb.connect(str(config.resolved_database_path)) as db:
        assert db.execute("SELECT count(*) FROM assquack_meta.exports").fetchone() == (
            1,
        )


def test_local_does_not_load_provider(tmp_path):
    destination = FileSystemDestination(base_uri=str(tmp_path))
    provider = SyntheticProvider(fail=True)
    destination._credential_provider = provider
    target = destination.resolve("orders.parquet", asset_name="orders")[0]
    destination.export(MagicMock(), TableReference("main", "orders"), target)
    assert provider.loads == 0 and provider.requested == []


def test_provider_failure_is_safe_and_has_no_manifest(
    tmp_path, intercepted_connections
):
    destination = FileSystemDestination(base_uri="abfss://lake", auth="sas")
    destination._credential_provider = SyntheticProvider(fail=True)

    @asset(table="main.orders", export="orders.parquet")
    def orders():
        return [{"id": 1}]

    config = AssquackConfig(home=tmp_path, exports=destination)
    with pytest.raises(ConfigurationError) as error:
        asyncio.run(orders(assquack_config=config))
    assert "synthetic-sensitive" not in "".join(traceback.format_exception(error.value))
    assert not config.resolved_database_path.exists()


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
    result.export_parquet()
    assert events == ["enter", "exit", "enter", "exit"]


def test_registry_duplicate_unknown_compatibility_and_isolation():
    preparation = default_preparations()
    with pytest.raises(ValueError, match="already registered"):
        preparation.register("file", preparation.select("local.parquet"))
    with pytest.raises(ExportError, match="Unsupported"):
        preparation.select("synthetic://file")
    auth = default_authenticators()
    with pytest.raises(ValueError, match="already registered"):
        auth.register("local", "auto", lambda _: LocalAuthenticator())
    with pytest.raises(ConfigurationError, match="Unsupported"):
        auth.select("local", "sas", SyntheticProvider())
    auth.register("local", "custom", lambda _: LocalAuthenticator())
    with pytest.raises(ConfigurationError, match="Unsupported"):
        default_authenticators().select("local", "custom", SyntheticProvider())


class SyntheticDestination(ExportDestination):
    _written: list[str] = PrivateAttr(default_factory=list)

    def resolve(
        self, export: str | Mapping[str, str], *, asset_name: str
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
        assert connection.execute("SELECT id FROM main.orders").fetchall() == [(1,)]
        self._written.append(target.resolved_uri)


def test_generic_destination_does_not_require_file_formats(tmp_path):
    destination = SyntheticDestination()

    @asset(table="main.orders", export="destination-specific declaration")
    def orders():
        return [{"id": 1}]

    config = AssquackConfig(home=tmp_path, exports=destination)
    asyncio.run(orders(assquack_config=config))
    assert destination._written == ["record:orders"]
    assert asyncio.run(orders.cache_first()(assquack_config=config)).cached
    with duckdb.connect(str(config.resolved_database_path)) as db:
        assert db.execute("SELECT format FROM assquack_meta.exports").fetchall() == [
            ("record",)
        ]


@pytest.mark.parametrize("explicit", [False, True])
def test_partial_multi_destination_failure_records_only_completed_outputs(
    tmp_path, monkeypatch, explicit
):
    from assquack._exports.writer.file import DuckDBFileWriter

    @asset(table="main.orders", export=None if explicit else "orders.parquet")
    def orders():
        return [{"id": 1}]

    config = AssquackConfig(
        home=tmp_path,
        exports=[
            FileSystemDestination(base_uri=str(tmp_path / name))
            for name in ("a", "b", "c")
        ],
    )
    original = DuckDBFileWriter.write

    def fail_second(self, connection, source, target, uri):
        if Path(uri).parent.name == "b":
            raise ValueError("synthetic output failure")
        original(self, connection, source, target, uri)

    monkeypatch.setattr(DuckDBFileWriter, "write", fail_second)
    with pytest.raises(ValueError, match="synthetic output failure"):
        result = asyncio.run(orders(assquack_config=config))
        if explicit:
            result.export_parquet()
    filename = "main.orders.parquet" if explicit else "orders.parquet"
    with duckdb.connect(str(config.resolved_database_path)) as db:
        assert db.execute(
            "SELECT resolved_uri FROM assquack_meta.exports"
        ).fetchall() == [(str(tmp_path / "a" / filename),)]
        assert db.execute("SELECT id FROM main.orders").fetchall() == [(1,)]
    assert not (tmp_path / "c" / filename).exists()
    monkeypatch.setattr(DuckDBFileWriter, "write", original)
    result = asyncio.run(orders(assquack_config=config))
    if explicit:
        result.export_parquet()
    assert (tmp_path / "c" / filename).exists()


def test_each_destination_uses_its_own_provider(tmp_path, intercepted_connections):
    destinations = []
    providers = []
    for account in ("firstaccount", "secondaccount"):
        provider = SyntheticProvider(account)
        destination = FileSystemDestination(
            base_uri=f"abfss://{account}.dfs.core.windows.net/lake", auth="sas"
        )
        destination._credential_provider = provider
        destinations.append(destination)
        providers.append(provider)

    @asset(table="main.orders", export="orders.parquet")
    def orders():
        return [{"id": 1}]

    asyncio.run(
        orders(assquack_config=AssquackConfig(home=tmp_path, exports=destinations))
    )
    registrations = [
        call.args[1]["connection_string"]
        for call in intercepted_connections[0].execute.call_args_list
        if call.args[0].startswith("CREATE TEMPORARY")
    ]
    assert registrations[0].startswith("AccountName=firstaccount;")
    assert registrations[1].startswith("AccountName=secondaccount;")
    assert [p.loads for p in providers] == [1, 1]


def test_filesystem_mapping_is_present_in_json_schema():
    schema = AssquackConfig.model_json_schema()
    fields = schema["$defs"]["FileSystemDestination"]["properties"]
    assert set(fields) == {"base_uri", "auth"}


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
